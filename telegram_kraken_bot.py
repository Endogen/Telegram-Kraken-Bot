#!/usr/bin/python3

import json
import logging
import os
import sys
import time

import krakenex
import requests

from enum import Enum
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, replymarkup
from telegram.ext import Updater, CommandHandler, Job, ConversationHandler, RegexHandler, MessageHandler, Filters

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger()

# Read configuration
with open("config.json") as config_file:
    config = json.load(config_file)

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# Set bot token
updater = Updater(token=config["bot_token"])

# Get dispatcher and job queue
dispatcher = updater.dispatcher
job_queue = updater.job_queue

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


# Check order status and send message if changed
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
        msg = "Trade executed: " + job.context["order_txid"] + "\n" + trim_zeros(order_info["descr"]["order"])
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

    # Send request to Kraken to get current balance of all currencies
    res_data = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data["error"]:
        update.message.reply_text(beautify(res_data["error"][0]))
        return

    msg = ""

    for currency_key, currency_value in res_data["result"].items():
        display_value = trim_zeros(currency_value)
        if display_value is not "0":
            msg += currency_key + ": " + display_value + "\n"

    update.message.reply_text(msg)


# Enum for 'trade' workflow
TRADE_BUY_SELL, TRADE_CURRENCY, TRADE_PRICE, TRADE_VOL_TYPE, TRADE_VOLUME, TRADE_CONFIRM = range(6)
# Enum for 'trade' keyboards
TradeEnum = Enum("TradeEnum", "BUY SELL EURO VOLUME")


# FIXME: After a while it will not get triggered by '/trade' anymore
# FIXME: Issue should be that Kraken request times out and then we don't send a ConversationHandler.END
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


def trade_buy_sell(bot, update, chat_data):
    chat_data["buysell"] = update.message.text

    reply_msg = "Enter currency"

    # TODO: Add buttons dynamically - call Kraken and get all available currencies
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


def trade_currency(bot, update, chat_data):
    chat_data["currency"] = "X" + update.message.text

    reply_msg = "Enter price per unit"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_PRICE


def trade_price(bot, update, chat_data):
    chat_data["price"] = update.message.text

    reply_msg = "How to enter the volume? Or skip and use /all"

    buttons = [
        KeyboardButton(TradeEnum.EURO.name),
        KeyboardButton(TradeEnum.VOLUME.name)
    ]

    cancel_btn = [
        KeyboardButton(GeneralEnum.CANCEL.name)
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_VOL_TYPE


def trade_vol_type(bot, update, chat_data):
    chat_data["vol_type"] = update.message.text

    reply_msg = "Enter volume"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return TRADE_VOLUME


def trade_volume(bot, update, chat_data):
    # Determine the volume
    # Entered '/all'
    if update.message.text == "/all":
        if chat_data["buysell"] == TradeEnum.BUY.name:
            req_data = dict()
            req_data["asset"] = "Z" + config["trade_to_currency"]

            # Send request to Kraken to get current trade balance of all currencies
            res_data = kraken.query_private("TradeBalance", req_data)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                update.message.reply_text(beautify(res_data["error"][0]))
                return

            euros = res_data["result"]["tb"]
            # Calculate volume depending on full euro balance and round it to 8 digits
            chat_data["volume"] = "{0:.8f}".format(float(euros) / float(chat_data["price"]))

        if chat_data["buysell"] == TradeEnum.SELL.name:
            # FIXME: This will give me all available BTC. But some of them are already part of a sell order.
            # FIXME: What i need is not the balance, but the 'free' BTCs that i can still sell
            # FIXME: Current balance of BTC minus all open BTC orders
            # Send request to Kraken to get euro balance to calculate volume
            res_data = kraken.query_private("Balance")

            # If Kraken replied with an error, show it
            if res_data["error"]:
                update.message.reply_text(beautify(res_data["error"][0]))
                return

            current_volume = res_data["result"][chat_data["currency"]]
            # Get volume from balance and round it to 8 digits
            chat_data["volume"] = "{0:.8f}".format(float(current_volume))

    # Entered EURO
    elif chat_data["vol_type"] == TradeEnum.EURO.name:
        amount = float(update.message.text)
        price_per_unit = float(chat_data["price"])
        chat_data["volume"] = "{0:.8f}".format(amount / price_per_unit)

    # Entered VOLUME
    elif chat_data["vol_type"] == TradeEnum.VOLUME.name:
        chat_data["volume"] = "{0:.8f}".format(float(update.message.text))

    trade_str = chat_data["buysell"].lower() + " " + \
                trim_zeros(chat_data["volume"]) + " " + \
                chat_data["currency"][1:] + " @ limit " + \
                chat_data["price"]

    if config["confirm_action"].lower() == "true":
        reply_msg = "Place this order?\n" + trade_str

        buttons = [
            KeyboardButton(GeneralEnum.YES.name),
            KeyboardButton(GeneralEnum.NO.name)
        ]

        reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))

        update.message.reply_text(reply_msg, reply_markup=reply_mrk)

        return TRADE_CONFIRM

    else:
        trade_confirm(bot, update, chat_data)


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
        update.message.reply_text(res_data_add_order["error"][0])
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
            update.message.reply_text(res_data_query_order["error"][0])
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

    # Send request for open orders to Kraken
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

        update.message.reply_text("Orders closed:\n" + "\n".join(closed_orders), reply_markup=keyboard_cmds())

    else:
        update.message.reply_text("No open orders", reply_markup=keyboard_cmds())

    return ConversationHandler.END


def orders_choose_order(bot, update):
    update.message.reply_text("Looking up open orders...")

    # Send request for open orders to Kraken
    res_data = kraken.query_private("OpenOrders")

    # TODO: Change error string to 'Kraken error: ' .replace("EQuery:", "") - Create own method?
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


# TODO: How to assure that user doesnt enter bullshit here? We really need only a valid TXID
# TODO: Maybe use RegexHandler again and check for two occurrences of '-'? --> O55BGK-VSYTV-ADZLV7 & 6-5-6
# TODO: Or work with dynamic Enums here?
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
# TODO: Create dynamic Enum for currencies
# https://stackoverflow.com/questions/33690064/dynamically-create-an-enum-with-custom-values-in-python


# Callback for the 'price' command - choose currency
def price_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    reply_msg = "Enter currency"

    # TODO: Add buttons dynamically - call Kraken and get all available currencies
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


def price_currency(bot, update):
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

    # TODO: Add buttons dynamically - call Kraken and get all available currencies
    # TODO: Use modulo to determine number of columns
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


def value_currency(bot, update):
    # TODO: Edit this msg later on with the value to show - do this globally (bot.editMessage...)
    update.message.reply_text("Calculating value...")

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        update.message.reply_text(res_data_balance["error"][0])
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
        update.message.reply_text(res_data_price["error"][0])
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


# Terminate this script
def shutdown_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Shutting down...", reply_markup=ReplyKeyboardRemove())

    exit()


# Restart this python script
def restart_cmd(bot, update):
    if not is_user_valid(bot, update):
        return cancel(bot, update)

    update.message.reply_text("Bot is restarting...", reply_markup=ReplyKeyboardRemove())

    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)


def cancel(bot, update):
    update.message.reply_text("Canceled...", reply_markup=keyboard_cmds())
    return ConversationHandler.END


# TODO: Timeout errors (while calling Kraken) will not get shown because i only handle python-telegram-bot exceptions
def error(bot, update, error):
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


# Enriches or replaces text based on predefined pattern
def beautify(text):
    if "EQuery" in text:
        return text.replace("EQuery", "Kraken Error")


# Log all errors
dispatcher.add_error_handler(error)

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
                             MessageHandler(Filters.text, orders_close_order)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(orders_handler)


# TODO: Do not use 'all' cmd, but add button
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
                         CommandHandler("all", trade_volume, pass_chat_data=True),
                         RegexHandler("^(CANCEL)$", cancel)],
        TRADE_VOLUME: [RegexHandler("^(((?=.*?\d)\d*[.]?\d*)|(/all))$", trade_volume, pass_chat_data=True)],
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

# Show welcome message, update state, keyboard for commands
message = "Up and running!\n" + get_update_state()
updater.bot.send_message(config["user_id"], message, reply_markup=keyboard_cmds())

# Monitor status changes of open orders
monitor_open_orders()
