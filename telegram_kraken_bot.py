#!/usr/bin/python3

import json
import logging
import os
import sys
import time
import threading

import krakenex
import requests

from enum import Enum
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, ConversationHandler, RegexHandler

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger()

# Read configuration
with open("config.json") as config_file:
    config = json.load(config_file)

# Set bot token, get dispatcher and job queue
updater = Updater(token=config["bot_token"])
dispatcher = updater.dispatcher
job_queue = updater.job_queue

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# General Enum for keyboard
GeneralEnum = Enum("GeneralEnum", "YES NO CANCEL")


# Create a button menu to show in Telegram messages
def build_menu(buttons, n_cols=1, header_buttons=None, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]

    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)

    return menu


# Custom keyboard that shows all available commands
def keyboard_cmds():
    command_buttons = [
        KeyboardButton("/trade"),
        KeyboardButton("/orders"),
        KeyboardButton("/balance"),
        KeyboardButton("/price"),
        KeyboardButton("/value"),
        KeyboardButton("/bot")
    ]

    return ReplyKeyboardMarkup(build_menu(command_buttons, n_cols=3))


# Generic custom keyboard that shows YES and NO
def keyboard_confirm():
    buttons = [
        KeyboardButton(GeneralEnum.YES.name),
        KeyboardButton(GeneralEnum.NO.name)
    ]

    return ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))


# Check order status and send message if order closed
def monitor_order(bot, job):
    req_data = dict()
    req_data["txid"] = job.context["order_txid"]

    # Send request to get info on specific order
    res_data = kraken.query_private("QueryOrders", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(job.context["chat_id"], text=beautify(res_data["error"][0]))
        # Stop this job
        job.schedule_removal()
        return

    # Save information about order
    order_info = res_data["result"][job.context["order_txid"]]

    # Check if order was canceled. If so, stop monitoring
    if order_info["status"] == "canceled":
        # Stop this job
        job.schedule_removal()

    # Check if trade was executed. If so, stop monitoring and send message
    elif order_info["status"] == "closed":
        msg = "Trade executed:\n" + job.context["order_txid"] + "\n" + trim_zeros(order_info["descr"]["order"])
        bot.send_message(chat_id=job.context["chat_id"], text=msg)
        # Stop this job
        job.schedule_removal()


# Monitor status changes of open orders
def monitor_open_orders():
    if config["check_trade"].lower() == "true":
        # Send request for open orders to Kraken
        res_data = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            updater.bot.send_message(chat_id=config["user_id"], text=beautify(res_data["error"][0]))
            return

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                order_txid = str(order)

                # Get time in seconds from config
                check_trade_time = config["check_trade_time"]
                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=config["user_id"], order_txid=order_txid)

                # Add Job to JobQueue to check status of order
                job_queue.run_repeating(monitor_order, check_trade_time, context=context_data)


# Remove trailing zeros to get clean values
def trim_zeros(value_to_trim):
    if isinstance(value_to_trim, float):
        return ('%.8f' % value_to_trim).rstrip('0').rstrip('.')
    elif isinstance(value_to_trim, str):
        str_list = value_to_trim.split(" ")
        for i in range(len(str_list)):
            old_str = str_list[i]
            if old_str.replace(".", "").isdigit():
                new_str = str(('%.8f' % float(old_str)).rstrip('0').rstrip('.'))
                str_list[i] = new_str
        return " ".join(str_list)
    else:
        return value_to_trim


# Get balance of all currencies
def balance_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Retrieving data...")

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        update.message.reply_text(beautify(res_data_balance["error"][0]))
        return

    # Send request to Kraken to get open orders
    res_data_orders = kraken.query_private("OpenOrders")

    # If Kraken replied with an error, show it
    if res_data_orders["error"]:
        update.message.reply_text(beautify(res_data_orders["error"][0]))
        return

    msg = ""

    for currency_key, currency_value in res_data_balance["result"].items():
        available_value = currency_value

        if currency_key.startswith("X"):
            currency_key = currency_key[1:]

        if config["trade_to_currency"] in currency_key:
            currency_key = config["trade_to_currency"]
        else:
            # Go through all open orders and check if an sell-order exists for the currency
            if res_data_orders["result"]["open"]:
                for order in res_data_orders["result"]["open"]:
                    order_desc = res_data_orders["result"]["open"][order]["descr"]["order"]
                    order_desc_list = order_desc.split(" ")

                    order_currency = order_desc_list[2][:-len(config["trade_to_currency"])]
                    order_volume = order_desc_list[1]
                    order_type = order_desc_list[0]

                    if currency_key == order_currency:
                        if order_type == "sell":
                            available_value = str(float(available_value) - float(order_volume))

        if trim_zeros(currency_value) is not "0":
            msg += currency_key + ": " + trim_zeros(currency_value) + "\n"

            # If sell orders exist for this currency, show available volume too
            if currency_value is not available_value:
                msg = msg[:-len("\n")] + " (Available: " + trim_zeros(available_value) + ")\n"

    update.message.reply_text(msg)


# Enum for 'trade' workflow
TRADE_BUY_SELL, TRADE_CURRENCY, TRADE_PRICE, TRADE_VOL_TYPE, TRADE_VOLUME, TRADE_CONFIRM = range(6)
# Enum for 'trade' keyboards
TradeEnum = Enum("TradeEnum", "BUY SELL EURO VOLUME ALL")


# Create orders to buy or sell currencies with price limit - choose 'buy' or 'sell'
def trade_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    reply_msg = "Buy or sell?"

    buttons = [
        KeyboardButton(TradeEnum.BUY.name),
        KeyboardButton(TradeEnum.SELL.name)
    ]

    cancel_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return TRADE_BUY_SELL


# Save if buy or sell order and choose the currency to trade
def trade_buy_sell(bot, update, chat_data):
    chat_data["buysell"] = update.message.text

    reply_msg = "Enter currency"

    buttons = [
        KeyboardButton("XBT"),
        KeyboardButton("ETH"),
        KeyboardButton("XMR")
    ]

    cancel_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return TRADE_CURRENCY


# Save currency to trade and enter price per unit to trade
def trade_currency(bot, update, chat_data):
    chat_data["currency"] = "X" + update.message.text

    reply_msg = "Enter price per unit"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return TRADE_PRICE


# Save price per unit and choose how to enter the
# trade volume (euro, volume or all available funds)
def trade_price(bot, update, chat_data):
    chat_data["price"] = update.message.text

    reply_msg = "How to enter the volume?"

    buttons = [
        KeyboardButton(TradeEnum.EURO.name),
        KeyboardButton(TradeEnum.VOLUME.name)
    ]

    cancel_btn = [
        KeyboardButton(TradeEnum.ALL.name),
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return TRADE_VOL_TYPE


# Save volume type decision and enter volume
def trade_vol_type(bot, update, chat_data):
    chat_data["vol_type"] = update.message.text

    reply_msg = "Enter volume"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return TRADE_VOLUME


# Volume type 'ALL' chosen - meaning that
# all available EURO funds will be used
def trade_vol_type_all(bot, update, chat_data):
    update.message.reply_text("Calculating volume...")

    if chat_data["buysell"] == TradeEnum.BUY.name:
        req_data = dict()
        req_data["asset"] = "Z" + config["trade_to_currency"]

        # Send request to Kraken to get current trade balance of all currencies
        res_data_balance = kraken.query_private("TradeBalance", req_data)

        # If Kraken replied with an error, show it
        if res_data_balance["error"]:
            update.message.reply_text(beautify(res_data_balance["error"][0]))
            return

        euros = res_data_balance["result"]["tb"]
        # Calculate volume depending on full euro balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(float(euros) / float(chat_data["price"]))

    if chat_data["buysell"] == TradeEnum.SELL.name:
        # Send request to Kraken to get euro balance to calculate volume
        res_data_balance = kraken.query_private("Balance")

        # If Kraken replied with an error, show it
        if res_data_balance["error"]:
            update.message.reply_text(beautify(res_data_balance["error"][0]))
            return

        # Send request to Kraken to get open orders
        res_data_orders = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data_orders["error"]:
            update.message.reply_text(beautify(res_data_orders["error"][0]))
            return

        available_volume = res_data_balance["result"][chat_data["currency"]]

        current_currency = chat_data["currency"]
        if current_currency.startswith("X"):
            current_currency = current_currency[1:]

        # Go through all open orders and check if an sell-order exists for the currency
        if res_data_orders["result"]["open"]:
            for order in res_data_orders["result"]["open"]:
                order_desc = res_data_orders["result"]["open"][order]["descr"]["order"]
                order_desc_list = order_desc.split(" ")

                order_currency = order_desc_list[2][:-len(config["trade_to_currency"])]
                order_volume = order_desc_list[1]
                order_type = order_desc_list[0]

                if current_currency == order_currency:
                    if order_type == "sell":
                        available_volume = str(float(available_volume) - float(order_volume))

        # Get volume from balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(float(available_volume))

    # Show confirmation for placing order
    trade_str = (chat_data["buysell"].lower() + " " +
                 trim_zeros(chat_data["volume"]) + " " +
                 chat_data["currency"][1:] + " @ limit " +
                 chat_data["price"])

    reply_msg = "Place this order?\n" + trade_str

    update.message.reply_text(reply_msg, reply_markup=keyboard_confirm())
    return TRADE_CONFIRM


# Calculate the volume depending on chosen volume type (EURO or VOLUME)
def trade_volume(bot, update, chat_data):
    # Entered EURO
    if chat_data["vol_type"] == TradeEnum.EURO.name:
        amount = float(update.message.text)
        price_per_unit = float(chat_data["price"])
        chat_data["volume"] = "{0:.8f}".format(amount / price_per_unit)

    # Entered VOLUME
    elif chat_data["vol_type"] == TradeEnum.VOLUME.name:
        chat_data["volume"] = "{0:.8f}".format(float(update.message.text))

    # Show confirmation for placing order
    trade_str = (chat_data["buysell"].lower() + " " +
                 trim_zeros(chat_data["volume"]) + " " +
                 chat_data["currency"][1:] + " @ limit " +
                 chat_data["price"])

    reply_msg = "Place this order?\n" + trade_str

    update.message.reply_text(reply_msg, reply_markup=keyboard_confirm())
    return TRADE_CONFIRM


# The user has to confirm placing the order
def trade_confirm(bot, update, chat_data):
    if update.message.text == GeneralEnum.NO.name:
        return cancel(bot, update)

    update.message.reply_text("Placing order...")

    req_data = dict()
    req_data["type"] = chat_data["buysell"].lower()
    req_data["pair"] = chat_data["currency"] + "Z" + config["trade_to_currency"]
    req_data["price"] = chat_data["price"]
    req_data["ordertype"] = "limit"
    req_data["volume"] = chat_data["volume"]

    # Send request to create order to Kraken
    res_data_add_order = kraken.query_private("AddOrder", req_data)

    # If Kraken replied with an error, show it
    if res_data_add_order["error"]:
        update.message.reply_text(beautify(res_data_add_order["error"][0]))
        return

    # If there is a transaction id then the order was placed successfully
    if res_data_add_order["result"]["txid"]:
        add_order_txid = res_data_add_order["result"]["txid"][0]

        req_data = dict()
        req_data["txid"] = add_order_txid

        # Send request to get info on specific order
        res_data_query_order = kraken.query_private("QueryOrders", req_data)

        # If Kraken replied with an error, show it
        if res_data_query_order["error"]:
            update.message.reply_text(beautify(res_data_query_order["error"][0]))
            return

        if res_data_query_order["result"][add_order_txid]:
            order_desc = res_data_query_order["result"][add_order_txid]["descr"]["order"]
            msg = "Order placed:\n" + add_order_txid + "\n" + trim_zeros(order_desc)
            update.message.reply_text(msg, reply_markup=keyboard_cmds())

            if config["check_trade"].lower() == "true":
                # Get time in seconds from config
                check_trade_time = config["check_trade_time"]
                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=update.message.chat_id, order_txid=add_order_txid)

                # Create job to check status of newly created order
                job_queue.run_repeating(monitor_order, check_trade_time, context=context_data)

        else:
            update.message.reply_text("No order with TXID " + add_order_txid)

    else:
        update.message.reply_text("Undefined state: no error and no TXID")

    return ConversationHandler.END


# Enum for 'orders' workflow
ORDERS_CLOSE, ORDERS_CLOSE_ORDER = range(2)
# Enum for 'orders' keyboards
OrdersEnum = Enum("OrdersEnum", "CLOSE_ORDER CLOSE_ALL")


# Show and manage orders
def orders_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Retrieving data...")

    # Send request to Kraken to get open orders
    res_data = kraken.query_private("OpenOrders")

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    # Go through all open orders and show them to the user
    if res_data["result"]["open"]:
        for order in res_data["result"]["open"]:
            order_desc = trim_zeros(res_data["result"]["open"][order]["descr"]["order"])
            update.message.reply_text(order + "\n" + order_desc)

    else:
        update.message.reply_text("No open orders")
        return ConversationHandler.END

    reply_msg = "What do you want to do?"

    buttons = [
        KeyboardButton(OrdersEnum.CLOSE_ORDER.name.replace("_", " ")),
        KeyboardButton(OrdersEnum.CLOSE_ALL.name.replace("_", " "))
    ]

    close_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=close_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return ORDERS_CLOSE


# Choose what to do with the open orders
def orders_choose_order(bot, update):
    update.message.reply_text("Looking up open orders...")

    # Send request for open orders to Kraken
    res_data = kraken.query_private("OpenOrders")

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    buttons = list()

    # Go through all open orders and create a button
    if res_data["result"]["open"]:
        for order in res_data["result"]["open"]:
            buttons.append(KeyboardButton(order))

    else:
        update.message.reply_text("No open orders")
        return ConversationHandler.END

    msg = "Which order to close?"

    close_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=1, footer_buttons=close_btn))

    update.message.reply_text(msg, reply_markup=reply_mrk)
    return ORDERS_CLOSE_ORDER


# Close all open orders
def orders_close_all(bot, update):
    update.message.reply_text("Closing orders...")

    # Send request for open orders to Kraken
    res_data = kraken.query_private("OpenOrders")

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    closed_orders = list()
    if res_data["result"]["open"]:
        for order in res_data["result"]["open"]:
            req_data = dict()
            req_data["txid"] = order

            # Send request to Kraken to cancel orders
            res_data = kraken.query_private("CancelOrder", req_data)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                update.message.reply_text("Error closing order\n" + order + "\n" + beautify(res_data["error"][0]))
            else:
                closed_orders.append(order)

        if closed_orders:
            update.message.reply_text("Orders closed:\n" + "\n".join(closed_orders), reply_markup=keyboard_cmds())

    else:
        update.message.reply_text("No open orders", reply_markup=keyboard_cmds())

    return ConversationHandler.END


# Close the specified order
def orders_close_order(bot, update):
    update.message.reply_text("Closing order...")

    req_data = dict()
    req_data["txid"] = update.message.text

    # Send request to Kraken to cancel order
    res_data = kraken.query_private("CancelOrder", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    update.message.reply_text("Order closed:\n" + req_data["txid"], reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Enum for 'price' workflow
PRICE_CURRENCY = range(1)


# Callback for the 'price' command - choose currency
def price_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    reply_msg = "Enter currency"

    buttons = [
        KeyboardButton("XBT"),
        KeyboardButton("ETH"),
        KeyboardButton("XMR")
    ]

    cancel_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return PRICE_CURRENCY


# Choose for which currency you want to know the last trade price
def price_currency(bot, update):
    update.message.reply_text("Retrieving data...")

    req_data = dict()
    req_data["pair"] = "X" + update.message.text + "Z" + config["trade_to_currency"]

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken.query_public("Ticker", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    currency = update.message.text
    last_trade_price = trim_zeros(res_data["result"][req_data["pair"]]["c"][0])

    update.message.reply_text(currency + ": " + last_trade_price, reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Enum for 'value' workflow
VALUE_CURRENCY = range(1)
# Enum for 'value' keyboards
ValueEnum = Enum("ValueEnum", "ALL")


# Show the current real money value for a certain asset or for all assets combined
def value_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    reply_msg = "Enter currency"

    buttons = [
        KeyboardButton("XBT"),
        KeyboardButton("BCH"),
        KeyboardButton("ETH"),
        KeyboardButton("XMR")
    ]

    footer_btns = [
        KeyboardButton(ValueEnum.ALL.name),
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=footer_btns))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return VALUE_CURRENCY


# Choose for which currency you want to know the current value
def value_currency(bot, update):
    update.message.reply_text("Retrieving data...")

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        update.message.reply_text(beautify(res_data_balance["error"][0]))
        return

    req_data_price = dict()
    req_data_price["pair"] = ""

    # Get balance of all currencies
    if update.message.text == ValueEnum.ALL.name:
        msg = "Overall: "

        for currency_name, currency_amount in res_data_balance["result"].items():
            if currency_name.endswith(config["trade_to_currency"]):
                continue
            # FIXME: Workaround for buggy Kraken API
            if "BCH" in currency_name:
                continue

            req_data_price["pair"] += currency_name + "Z" + config["trade_to_currency"] + ","

    # Get balance of a specific currency
    else:
        msg = update.message.text + ": "
        req_data_price["pair"] = "X" + update.message.text + "Z" + config["trade_to_currency"] + ","

    # Remove last comma from 'pair' string
    req_data_price["pair"] = req_data_price["pair"][:-1]

    # Send request to Kraken to get current trading price for currency-pair
    res_data_price = kraken.query_public("Ticker", req_data_price)

    # If Kraken replied with an error, show it
    if res_data_price["error"]:
        update.message.reply_text(beautify(res_data_price["error"][0]))
        return

    total_value_euro = float(0)

    for currency_pair_name, currency_price in res_data_price["result"].items():
        # Remove trade-to-currency from currency pair to get the pure currency
        currency_without_pair = currency_pair_name[:-len("Z" + config["trade_to_currency"])]
        currency_balance = res_data_balance["result"][currency_without_pair]

        # Calculate total value by multiplying currency asset with last trade price
        total_value_euro += float(currency_balance) * float(currency_price["c"][0])

    # Show only 2 digits after decimal place
    total_value_euro = "{0:.2f}".format(total_value_euro)

    msg += total_value_euro + " " + config["trade_to_currency"]

    # Check if we have only one currency. If true, add last trade price to msg
    if len(res_data_price["result"]) == 1:
        pair = list(res_data_price["result"].keys())[0]
        last_price = res_data_price["result"][pair]["c"][0]
        last_trade_price = "{0:.2f}".format(float(last_price))
        msg += "\n(Ticker: " + last_trade_price + " " + config["trade_to_currency"] + ")"

    update.message.reply_text(msg, reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Enum for 'bot' workflow
BOT_SUB_CMD = range(1)
# Enum for 'bot' keyboards
BotEnum = Enum("BotEnum", "UPDATE_CHECK UPDATE RESTART SHUTDOWN")


# Shows sub-commands to control the bot
def bot_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    reply_msg = "What do you want to do?"

    buttons = [
        KeyboardButton(BotEnum.UPDATE_CHECK.name.replace("_", " ")),
        KeyboardButton(BotEnum.UPDATE.name),
        KeyboardButton(BotEnum.RESTART.name),
        KeyboardButton(BotEnum.SHUTDOWN.name)
    ]

    cancel_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return BOT_SUB_CMD


# Execute chosen sub-cmd of 'bot' cmd
def bot_sub_cmd(bot, update):
    # Update check
    if update.message.text == BotEnum.UPDATE_CHECK.name.replace("_", " "):
        update.message.reply_text(get_update_state())
        return

    # Update
    elif update.message.text == BotEnum.UPDATE.name:
        return update_cmd(bot, update)

    # Restart
    elif update.message.text == BotEnum.RESTART.name:
        restart_cmd(bot, update)

    # Shutdown
    elif update.message.text == BotEnum.SHUTDOWN.name:
        shutdown_cmd(bot, update)

    elif update.message.text == GeneralEnum.CANCEL.name:
        return cancel(bot, update)


# Download newest script, update the currently running script and restart
def update_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified
    if github_file.status_code == 304:
        msg = "You are running the latest version"
        update.message.reply_text(msg, reply_markup=keyboard_cmds())
    # Status code 200 = OK
    elif github_file.status_code == 200:
        # Save current ETag (hash) in configuration file
        with open("config.json", "w") as cfg:
            e_tag = github_file.headers.get("ETag")
            config["update_hash"] = e_tag
            json.dump(config, cfg)

        # Get the name of the currently running script
        path_split = os.path.split(str(sys.argv[0]))
        filename = path_split[len(path_split)-1]

        # Save the content of the remote file
        with open(filename, "w") as file:
            file.write(github_file.text)

        update.message.reply_text("Restarting...", reply_markup=ReplyKeyboardRemove())

        # Restart the bot
        restart_cmd(bot, update)
    # Every other status code
    else:
        msg = "Update not executed. Unexpected status code: " + github_file.status_code
        update.message.reply_text(msg, reply_markup=keyboard_cmds())

    return ConversationHandler.END


# This function needs to be run on a new thread because calling 'updater.stop()` inside a
# handler (shutdown_cmd) causes a deadlock because it waits for itself to finish.
def shutdown():
    updater.stop()
    updater.is_idle = False


# Terminate this script
def shutdown_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Shutting down...", reply_markup=ReplyKeyboardRemove())

    threading.Thread(target=shutdown).start() # See comments on the shutdown function


# Restart this python script
def restart_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Bot is restarting...", reply_markup=ReplyKeyboardRemove())

    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)


# Will show a cancel message, end the conversation and show the default keyboard
def cancel(bot, update):
    update.message.reply_text("Canceled...", reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Log all telegram and telegram.ext related errors
def handle_error(bot, update, error):
    logger.error("Update '%s' caused error '%s'" % (update, error))


# Check if GitHub hosts a different script then the currently running one
def get_update_state():
    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified (remote file has same hash, is the same version)
    if github_file.status_code == 304:
        msg = "Bot is up to date"
    # Status code 200 = OK (remote file has different hash, is not the same version)
    elif github_file.status_code == 200:
        msg = "New version available. Get it with /update"
    # Every other status code
    else:
        msg = "Update check not possible. Unexpected status code: " + github_file.status_code

    return msg


# Return chat ID for an update object
def get_chat_id(update=None):
    if update:
        if update.message:
            return update.message.chat_id
        elif update.callback_query:
            return update.callback_query.from_user["id"]
    else:
        return config["user_id"]


# Check if user is valid and send message to user if not
def is_user_valid(bot, update):
    chat_id = get_chat_id(update)
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        logger.info("Access denied for user %s" % chat_id)
        return False
    else:
        return True


# Enriches or replaces text based on hardcoded patterns
def beautify(text):
    if "EQuery" in text:
        return text.replace("EQuery:", "Kraken Error (Query): ")
    elif "EGeneral" in text:
        return text.replace("EGeneral:", "Kraken Error (General): ")
    elif "EService" in text:
        return text.replace("EService:", "Kraken Error (Service): ")
    elif "EAPI" in text:
        return text.replace("EAPI:", "Kraken Error (API): ")
    elif "EOrder" in text:
        return text.replace("EOrder:", "Kraken Error (Order): ")

    return text


# Log all errors
dispatcher.add_error_handler(handle_error)

# Add handlers to dispatcher
dispatcher.add_handler(CommandHandler("update", update_cmd))
dispatcher.add_handler(CommandHandler("restart", restart_cmd))
dispatcher.add_handler(CommandHandler("shutdown", shutdown_cmd))
dispatcher.add_handler(CommandHandler("balance", balance_cmd))


# ORDERS command handler
orders_handler = ConversationHandler(
    entry_points=[CommandHandler('orders', orders_cmd)],
    states={
        ORDERS_CLOSE: [RegexHandler("^(CLOSE ORDER)$", orders_choose_order),
                       RegexHandler("^(CLOSE ALL)$", orders_close_all),
                       RegexHandler("^(CANCEL)$", cancel)],
        ORDERS_CLOSE_ORDER: [RegexHandler("^(CANCEL)$", cancel),
                             RegexHandler("^[A-Z0-9]{6}-[A-Z0-9]{5}-[A-Z0-9]{6}$", orders_close_order)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(orders_handler)


# TRADE command handler
trade_handler = ConversationHandler(
    entry_points=[CommandHandler('trade', trade_cmd)],
    states={
        TRADE_BUY_SELL: [RegexHandler("^(BUY|SELL)$", trade_buy_sell, pass_chat_data=True),
                         RegexHandler("^(CANCEL)$", cancel)],
        TRADE_CURRENCY: [RegexHandler("^(XBT|ETH|XMR)$", trade_currency, pass_chat_data=True),
                         RegexHandler("^(CANCEL)$", cancel)],
        TRADE_PRICE: [RegexHandler("^((?=.*?\d)\d*[.]?\d*)$", trade_price, pass_chat_data=True)],
        TRADE_VOL_TYPE: [RegexHandler("^(EURO|VOLUME)$", trade_vol_type, pass_chat_data=True),
                         RegexHandler("^(ALL)$", trade_vol_type_all, pass_chat_data=True),
                         RegexHandler("^(CANCEL)$", cancel)],
        TRADE_VOLUME: [RegexHandler("^((?=.*?\d)\d*[.]?\d*)$", trade_volume, pass_chat_data=True)],
        TRADE_CONFIRM: [RegexHandler("^(YES|NO)$", trade_confirm, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(trade_handler)


# PRICE command handler
price_handler = ConversationHandler(
    entry_points=[CommandHandler('price', price_cmd)],
    states={
        PRICE_CURRENCY: [RegexHandler("^(XBT|ETH|XMR)$", price_currency),
                         RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(price_handler)


# VALUE command handler
value_handler = ConversationHandler(
    entry_points=[CommandHandler('value', value_cmd)],
    states={
        VALUE_CURRENCY: [RegexHandler("^(XBT|BCH|ETH|XMR|ALL)$", value_currency),
                         RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(value_handler)


# BOT command handler
bot_handler = ConversationHandler(
    entry_points=[CommandHandler('bot', bot_cmd)],
    states={
        BOT_SUB_CMD: [RegexHandler("^(UPDATE CHECK|UPDATE|RESTART|SHUTDOWN)$", bot_sub_cmd),
                      RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(bot_handler)


# Start the bot
updater.start_polling()

# Show welcome message, update state and keyboard for commands
message = "KrakenBot is up and running!\n" + get_update_state()
updater.bot.send_message(config["user_id"], message, reply_markup=keyboard_cmds())

# Monitor status changes of open orders
monitor_open_orders()

# Run the bot until you press Ctrl-C or the process receives SIGINT,
# SIGTERM or SIGABRT. This should be used most of the time, since
# start_polling() is non-blocking and will stop the bot gracefully.
# updater.idle()
