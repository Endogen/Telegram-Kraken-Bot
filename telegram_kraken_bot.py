#!/usr/bin/python3

import json
import logging
import os
import sys
import time
import threading
import datetime
import requests
import krakenex
import inspect

from enum import Enum, auto

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, ParseMode
from telegram.ext import Updater, CommandHandler, ConversationHandler, RegexHandler, MessageHandler
from telegram.ext.filters import Filters


# Check if file 'config.json' exists. Exit if not.
if os.path.isfile("config.json"):
    # Read configuration
    with open("config.json") as config_file:
        config = json.load(config_file)
else:
    exit("No configuration file 'config.json' found")

# Set up logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger()

# Add a file handlers to the logger
if config["log_to_file"]:
    # Create a file handler for logging
    handler = logging.FileHandler('debug.log')
    handler.setLevel(logging.DEBUG)

    # Format file handler
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    handler.setFormatter(formatter)

    # Add file handler to logger
    logger.addHandler(handler)

# Set bot token, get dispatcher and job queue
updater = Updater(token=config["bot_token"])
dispatcher = updater.dispatcher
job_queue = updater.job_queue

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# Lists for cached objects
trades = list()
orders = list()


# Enum for workflow handler
class WorkflowEnum(Enum):
    TRADE_BUY_SELL = auto()
    TRADE_CURRENCY = auto()
    TRADE_SELL_ALL_CONFIRM = auto()
    TRADE_PRICE = auto()
    TRADE_VOL_TYPE = auto()
    TRADE_VOLUME = auto()
    TRADE_CONFIRM = auto()
    ORDERS_CLOSE = auto()
    ORDERS_CLOSE_ORDER = auto()
    PRICE_CURRENCY = auto()
    VALUE_CURRENCY = auto()
    BOT_SUB_CMD = auto()
    CHART_CURRENCY = auto()
    HISTORY_NEXT = auto()
    FUNDING_CURRENCY = auto()
    FUNDING_CHOOSE = auto()
    WITHDRAW_WALLET = auto()
    WITHDRAW_VOLUME = auto()
    WITHDRAW_CONFIRM = auto()
    SETTINGS_CHANGE = auto()
    SETTINGS_SAVE = auto()
    SETTINGS_CONFIRM = auto()


# Enum for keyboard buttons
class KeyboardEnum(Enum):
    BUY = auto()
    SELL = auto()
    VOLUME = auto()
    ALL = auto()
    YES = auto()
    NO = auto()
    CANCEL = auto()
    CLOSE_ORDER = auto()
    CLOSE_ALL = auto()
    UPDATE_CHECK = auto()
    UPDATE = auto()
    RESTART = auto()
    SHUTDOWN = auto()
    NEXT = auto()
    DEPOSIT = auto()
    WITHDRAW = auto()
    SETTINGS = auto()

    def clean(self):
        return self.name.replace("_", " ")


# Issue Kraken API requests
def kraken_api(method, data=None, private=False, return_error=True, retries=None):
    # Get arguments of this function
    frame = inspect.currentframe()
    args, _, _, values = inspect.getargvalues(frame)

    # Get name of caller function
    caller = inspect.currentframe().f_back.f_code.co_name

    # Log caller of this function and all arguments
    logger.debug(caller + " - Kraken API args: " + str([(i, values[i]) for i in args]))

    try:
        if private:
            return kraken.query_private(method, data)
        else:
            return kraken.query_public(method, data)

    except Exception as ex:
        logger.error(str(ex))
        ex_name = type(ex).__name__

        # Handle specific exceptions
        if "Incorrect padding" in str(ex):
            msg = "Please verify that your Kraken API keys are valid"
            return {"error": [msg]}

        # Is 'retry on error' enabled?
        if config["retries"]:
            # It's the first call, start retrying
            if retries is None:
                retries = config["retries_counter"]
                return kraken_api(method, data, private, return_error, retries)
            # If 'retries' is bigger then 0, decrement it and retry again
            elif retries > 0:
                retries -= 1
                return kraken_api(method, data, private, return_error, retries)
            # Return last error if returning of errors is enabled
            else:
                return {"error": [" " + ex_name + ":" + str(ex)]} if return_error else None
        # Return error if returning of errors is enabled
        else:
            return {"error": [" " + ex_name + ":" + str(ex)]} if return_error else None


# Decorator to restrict access if user is not the same as in config
def restrict_access(func):
    def _restrict_access(bot, update):
        chat_id = get_chat_id(update)
        if str(chat_id) != config["user_id"]:
            if config["show_access_denied"]:
                # Inform user who tried to access
                bot.send_message(chat_id, text="Access denied")

                # Inform owner of bot
                msg = "Access denied for user %s" % chat_id
                bot.send_message(config["user_id"], text=msg)

            logger.info(msg)
            return
        else:
            return func(bot, update)
    return _restrict_access


# Get balance of all currencies
@restrict_access
def balance_cmd(bot, update):
    update.message.reply_text("Retrieving data...")

    # Send request to Kraken to get current balance of all currencies
    res_balance = kraken_api("Balance", private=True)

    # If Kraken replied with an error, show it
    if res_balance["error"]:
        error = btfy(res_balance["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Send request to Kraken to get open orders
    res_orders = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_orders["error"]:
        error = btfy(res_orders["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    msg = ""

    # Go over all currencies in your balance
    for currency_key, currency_value in res_balance["result"].items():
        available_value = currency_value

        if currency_key.startswith("X"):
            currency_key = currency_key[1:]

        if config["trade_to_currency"] in currency_key:
            currency_key = config["trade_to_currency"]
        else:
            # Go through all open orders and check if a sell-order exists for the currency
            if res_orders["result"]["open"]:
                for order in res_orders["result"]["open"]:
                    order_desc = res_orders["result"]["open"][order]["descr"]["order"]
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

    update.message.reply_text(bold(msg), parse_mode=ParseMode.MARKDOWN)


# Create orders to buy or sell currencies with price limit - choose 'buy' or 'sell'
@restrict_access
def trade_cmd(bot, update):
    reply_msg = "Buy or sell?"

    buttons = [
        KeyboardButton(KeyboardEnum.BUY.clean()),
        KeyboardButton(KeyboardEnum.SELL.clean())
    ]

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.TRADE_BUY_SELL


# Save if BUY or SELL order and choose the currency to trade
def trade_buy_sell(bot, update, chat_data):
    chat_data["buysell"] = update.message.text

    reply_msg = "Choose currency"

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    # If SELL chosen, then include button 'ALL' to sell everything
    if chat_data["buysell"].upper() == KeyboardEnum.SELL.clean():
        cancel_btn.insert(0, KeyboardButton(KeyboardEnum.ALL.clean()))

    reply_mrk = ReplyKeyboardMarkup(build_menu(coin_buttons(), n_cols=3, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.TRADE_CURRENCY


# Show confirmation to sell all assets
def trade_sell_all(bot, update):
    msg = "Sell `all` assets to current market price? All open orders will be closed!"
    update.message.reply_text(msg, reply_markup=keyboard_confirm(), parse_mode=ParseMode.MARKDOWN)

    return WorkflowEnum.TRADE_SELL_ALL_CONFIRM


# Sells all assets for there respective current market value
def trade_sell_all_confirm(bot, update):
    if update.message.text == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text("Preparing to sell everything...")

    # Send request for open orders to Kraken
    res_open_orders = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_open_orders["error"]:
        error = btfy(res_open_orders["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Close all currently open orders
    if res_open_orders["result"]["open"]:
        for order in res_open_orders["result"]["open"]:
            req_data = dict()
            req_data["txid"] = order

            # Send request to Kraken to cancel orders
            res_open_orders = kraken_api("CancelOrder", data=req_data, private=True)

            # If Kraken replied with an error, show it
            if res_open_orders["error"]:
                error = "Not possible to close order\n" + order + "\n" + btfy(res_open_orders["error"][0])
                update.message.reply_text(error)
                logger.error(error)
                return

    # Send request to Kraken to get current balance of all currencies
    res_balance = kraken_api("Balance", private=True)

    # If Kraken replied with an error, show it
    if res_balance["error"]:
        error = btfy(res_balance["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Go over all assets and sell them
    for asset, amount in res_balance["result"].items():
        # Current asset is not a crypto-currency - skip it
        if asset.endswith(config["trade_to_currency"]):
            continue
        # Filter out currencies that have a volume of 0
        if amount == "0.0000000000":
            continue

        req_data = dict()
        req_data["type"] = "sell"
        req_data["pair"] = asset + "Z" + config["trade_to_currency"]
        req_data["ordertype"] = "market"
        req_data["volume"] = amount

        # Send request to create order to Kraken
        res_add_order = kraken_api("AddOrder", data=req_data, private=True)

        # If Kraken replied with an error, show it
        if res_add_order["error"]:
            error = btfy(res_add_order["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            continue

        order_txid = res_add_order["result"]["txid"][0]

        # Add Job to JobQueue to check status of created order (if setting is enabled)
        if config["check_trade"]:
            trade_time = config["check_trade_time"]
            context = dict(order_txid=order_txid)
            job_queue.run_repeating(order_state_check, trade_time, context=context)

    msg = "Created orders to sell all assets"
    update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# Save currency to trade and enter price per unit to trade
def trade_currency(bot, update, chat_data):
    chat_data["currency"] = update.message.text

    reply_msg = "Enter price per unit"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return WorkflowEnum.TRADE_PRICE


# Save price per unit and choose how to enter the
# trade volume (euro, volume or all available funds)
def trade_price(bot, update, chat_data):
    chat_data["price"] = update.message.text

    reply_msg = "How to enter the volume?"

    buttons = [
        KeyboardButton(config["trade_to_currency"].upper()),
        KeyboardButton(KeyboardEnum.VOLUME.clean())
    ]

    cancel_btn = [
        KeyboardButton(KeyboardEnum.ALL.clean()),
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return WorkflowEnum.TRADE_VOL_TYPE


# Save volume type decision and enter volume
def trade_vol_type(bot, update, chat_data):
    chat_data["vol_type"] = update.message.text

    reply_msg = "Enter volume"
    reply_mrk = ReplyKeyboardRemove()

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return WorkflowEnum.TRADE_VOLUME


# Volume type 'ALL' chosen - meaning that
# all available EURO funds will be used
def trade_vol_type_all(bot, update, chat_data):
    update.message.reply_text("Calculating volume...")

    if chat_data["buysell"] == KeyboardEnum.BUY.clean():
        # Send request to Kraken to get current balance of all currencies
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        available_euros = float(0)
        for currency_key, currency_value in res_balance["result"].items():
            if config["trade_to_currency"] in currency_key:
                available_euros = float(currency_value)
                break

        # Calculate volume depending on available euro balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(available_euros / float(chat_data["price"]))

    if chat_data["buysell"] == KeyboardEnum.SELL.clean():
        # Send request to Kraken to get euro balance to calculate volume
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        # Send request to Kraken to get open orders
        res_orders = kraken_api("OpenOrders", private=True)

        # If Kraken replied with an error, show it
        if res_orders["error"]:
            error = btfy(res_orders["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        # Lookup volume of chosen currency
        for currency, currency_volume in res_balance["result"].items():
            if chat_data["currency"] in currency:
                available_volume = currency_volume
                break

        # Go through all open orders and check if sell-orders exists for the currency
        # If yes, subtract there volume from the available volume
        if res_orders["result"]["open"]:
            for order in res_orders["result"]["open"]:
                order_desc = res_orders["result"]["open"][order]["descr"]["order"]
                order_desc_list = order_desc.split(" ")

                order_currency = order_desc_list[2][:-len(config["trade_to_currency"])]
                order_volume = order_desc_list[1]
                order_type = order_desc_list[0]

                if chat_data["currency"] in order_currency:
                    if order_type == "sell":
                        available_volume = str(float(available_volume) - float(order_volume))

        # Get volume from balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(float(available_volume))

    # If available volume is 0, return without creating a trade
    if chat_data["volume"] == "0.00000000":
        msg = "Available " + chat_data["currency"] + " volume is 0"
        update.message.reply_text(msg, reply_markup=keyboard_cmds())
        return ConversationHandler.END
    else:
        show_trade_conf(update, chat_data)

    return WorkflowEnum.TRADE_CONFIRM


# Calculate the volume depending on chosen volume type (EURO or VOLUME)
def trade_volume(bot, update, chat_data):
    # Entered currency from config (EUR, USD, ...)
    if chat_data["vol_type"] == config["trade_to_currency"].upper():
        amount = float(update.message.text)
        price_per_unit = float(chat_data["price"])
        chat_data["volume"] = "{0:.8f}".format(amount / price_per_unit)

    # Entered VOLUME
    elif chat_data["vol_type"] == KeyboardEnum.VOLUME.clean():
        chat_data["volume"] = "{0:.8f}".format(float(update.message.text))

    show_trade_conf(update, chat_data)

    return WorkflowEnum.TRADE_CONFIRM


# Calculate total value and show order description and confirmation for order creation
# This method is used in 'trade_volume' and in 'trade_vol_type_all'
def show_trade_conf(update, chat_data):
    # Show confirmation for placing order
    trade_str = (chat_data["buysell"].lower() + " " +
                 trim_zeros(chat_data["volume"]) + " " +
                 chat_data["currency"] + " @ limit " +
                 chat_data["price"])

    # Calculate total value of order
    total_value = "{0:.2f}".format(float(chat_data["volume"]) * float(chat_data["price"]))
    total_value_str = "(Value: " + str(total_value) + " " + config["trade_to_currency"] + ")"

    reply_msg = "Place this order?\n" + trade_str + "\n" + total_value_str

    update.message.reply_text(reply_msg, reply_markup=keyboard_confirm())


# The user has to confirm placing the order
def trade_confirm(bot, update, chat_data):
    if update.message.text == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text("Placing order...")

    req_data = dict()
    req_data["type"] = chat_data["buysell"].lower()
    req_data["price"] = chat_data["price"]
    req_data["ordertype"] = "limit"
    req_data["volume"] = chat_data["volume"]

    # If currency is BCH then use different pair string
    if chat_data["currency"] == "BCH":
        req_data["pair"] = chat_data["currency"] + config["trade_to_currency"]
    else:
        req_data["pair"] = "X" + chat_data["currency"] + "Z" + config["trade_to_currency"]

    # Send request to create order to Kraken
    res_add_order = kraken_api("AddOrder", req_data, private=True)

    # If Kraken replied with an error, show it
    if res_add_order["error"]:
        error = btfy(res_add_order["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # If there is a transaction id then the order was placed successfully
    if res_add_order["result"]["txid"]:
        order_txid = res_add_order["result"]["txid"][0]

        req_data = dict()
        req_data["txid"] = order_txid

        # Send request to get info on specific order
        res_query_order = kraken_api("QueryOrders", data=req_data, private=True)

        # If Kraken replied with an error, show it
        if res_query_order["error"]:
            error = btfy(res_query_order["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        if res_query_order["result"][order_txid]:
            order_desc = res_query_order["result"][order_txid]["descr"]["order"]
            msg = "Order placed:\n" + order_txid + "\n" + trim_zeros(order_desc)
            update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

            # Add Job to JobQueue to check status of created order (if enabled)
            if config["check_trade"]:
                trade_time = config["check_trade_time"]
                context = dict(order_txid=order_txid)
                job_queue.run_repeating(order_state_check, trade_time, context=context)
        else:
            update.message.reply_text("No order with TXID " + order_txid)

    else:
        update.message.reply_text("Undefined state: no error and no TXID")

    return ConversationHandler.END


# Show and manage orders
@restrict_access
def orders_cmd(bot, update):
    update.message.reply_text("Retrieving orders...")

    # Send request to Kraken to get open orders
    res_data = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Reset global orders list
    global orders
    orders = list()

    # Go through all open orders and show them to the user
    if res_data["result"]["open"]:
        for order_id, order_details in res_data["result"]["open"].items():
            # Add order to global order list so that it can be used later
            # without requesting data from Kraken again
            orders.append({order_id: order_details})

            order_desc = trim_zeros(order_details["descr"]["order"])
            update.message.reply_text(bold(order_id + "\n" + order_desc), parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text("No open orders")
        return ConversationHandler.END

    reply_msg = "What do you want to do?"

    buttons = [
        KeyboardButton(KeyboardEnum.CLOSE_ORDER.clean()),
        KeyboardButton(KeyboardEnum.CLOSE_ALL.clean())
    ]

    close_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=close_btn))

    update.message.reply_text(reply_msg, reply_markup=reply_mrk)
    return WorkflowEnum.ORDERS_CLOSE


# Choose what to do with the open orders
def orders_choose_order(bot, update):
    buttons = list()

    # Go through all open orders and create a button
    if orders:
        for order in orders:
            order_id = next(iter(order), None)
            buttons.append(KeyboardButton(order_id))
    else:
        update.message.reply_text("No open orders")
        return ConversationHandler.END

    msg = "Which order to close?"

    close_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=1, footer_buttons=close_btn))

    update.message.reply_text(msg, reply_markup=reply_mrk)
    return WorkflowEnum.ORDERS_CLOSE_ORDER


# Close all open orders
def orders_close_all(bot, update):
    update.message.reply_text("Closing orders...")

    closed_orders = list()

    if orders:
        for order in orders:
            order_id = next(iter(order), None)

            # Send request to Kraken to cancel orders
            res_data = kraken_api("CancelOrder", data={"txid": order_id}, private=True)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                error = "Not possible to close order\n" + order_id + "\n" + btfy(res_data["error"][0])
                update.message.reply_text(error)
                logger.error(error)
            else:
                closed_orders.append(order_id)

        if closed_orders:
            msg = bold("Orders closed:\n" + "\n".join(closed_orders))
            update.message.reply_text(msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)
        else:
            update.message.reply_text("No orders closed")
            return
    else:
        update.message.reply_text("No open orders", reply_markup=keyboard_cmds())

    return ConversationHandler.END


# Close the specified order
def orders_close_order(bot, update):
    update.message.reply_text("Closing order...")

    req_data = dict()
    req_data["txid"] = update.message.text

    # Send request to Kraken to cancel order
    res_data = kraken_api("CancelOrder", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    msg = bold("Order closed:\n" + req_data["txid"])
    update.message.reply_text(msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# Show the last trade price for a currency
@restrict_access
def price_cmd(bot, update):
    reply_msg = "Choose currency"

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(coin_buttons(), n_cols=3, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.PRICE_CURRENCY


# Choose for which currency to show the last trade price
def price_currency(bot, update):
    update.message.reply_text("Retrieving data...")

    req_data = dict()

    # If currency is BCH then use different pair string
    if update.message.text == "BCH":
        req_data["pair"] = update.message.text + config["trade_to_currency"]
    else:
        req_data["pair"] = "X" + update.message.text + "Z" + config["trade_to_currency"]

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken_api("Ticker", data=req_data, private=False)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    currency = update.message.text
    last_trade_price = trim_zeros(res_data["result"][req_data["pair"]]["c"][0])

    msg = bold(currency + ": " + last_trade_price + " " + config["trade_to_currency"])
    update.message.reply_text(msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# Show the current real money value for a certain asset or for all assets combined
@restrict_access
def value_cmd(bot, update):
    reply_msg = "Choose currency"

    footer_btns = [
        KeyboardButton(KeyboardEnum.ALL.clean()),
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(coin_buttons(), n_cols=3, footer_buttons=footer_btns))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.VALUE_CURRENCY


# Choose for which currency you want to know the current value
def value_currency(bot, update):
    update.message.reply_text("Retrieving current value...")

    # Get balance of all currencies
    if update.message.text == KeyboardEnum.ALL.clean():
        req_asset = dict()
        req_asset["asset"] = config["trade_to_currency"]

        # Send request to Kraken tp obtain the combined balance of all currencies
        res_trade_balance = kraken_api("TradeBalance", data=req_asset, private=True)

        # If Kraken replied with an error, show it
        if res_trade_balance["error"]:
            error = btfy(res_trade_balance["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        # Show only 2 digits after decimal place
        total_value_euro = "{0:.2f}".format(float(res_trade_balance["result"]["eb"]))

        # Generate message to user
        msg = "Overall: " + total_value_euro + " " + config["trade_to_currency"]

        update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    # Get balance of a specific coin
    else:
        # Send request to Kraken to get current balance of all currencies
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        req_price = dict()
        if update.message.text == "BCH":
            req_price["pair"] = update.message.text + config["trade_to_currency"]
        else:
            req_price["pair"] = "X" + update.message.text + "Z" + config["trade_to_currency"]

        # Send request to Kraken to get current trading price for currency-pair
        res_price = kraken_api("Ticker", data=req_price, private=False)

        # If Kraken replied with an error, show it
        if res_price["error"]:
            error = btfy(res_price["error"][0])
            update.message.reply_text(error)
            logger.error(error)
            return

        # Get last trade price
        pair = list(res_price["result"].keys())[0]
        last_price = res_price["result"][pair]["c"][0]

        value_euro = float(0)

        for currency, currency_balance in res_balance["result"].items():
            if update.message.text in currency:
                # Calculate value by multiplying balance with last trade price
                value_euro = float(currency_balance) * float(last_price)

        # Show only 2 digits after decimal place
        value_euro = "{0:.2f}".format(value_euro)

        msg = update.message.text + ": " + value_euro + " " + config["trade_to_currency"]

        # Add last trade price to msg
        last_trade_price = "{0:.2f}".format(float(last_price))
        msg += "\n(Ticker: " + last_trade_price + " " + config["trade_to_currency"] + ")"

        update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# Reloads the custom keyboard
@restrict_access
def reload_cmd(bot, update):
    msg = "Reloading keyboard..."
    update.message.reply_text(msg, reply_markup=keyboard_cmds())


# Returns a string representation of a trade. Looks like this:
# sell 0.03752345 ETH-EUR @ limit 267.5 on 2017-08-22 22:18:22
def get_trade_str(trade):
    # Format pair-string from 'XXBTZEUR' to 'XXBT-EUR'
    for i in range(3, 10, 1):
        if trade["pair"][i:].startswith("Z"):
            pair_str = trade["pair"][:i] + "-" + trade["pair"][i + 1:]
            break

    # Format pair-string from 'XXBT-EUR' to 'XBT-EUR'
    pair_str = pair_str[1:] if pair_str.startswith("X") else pair_str

    trade_str = (trade["type"] + " " +
                 trim_zeros(trade["vol"]) + " " +
                 pair_str + " @ limit " +
                 trim_zeros(trade["price"]) + " on " +
                 datetime_from_timestamp(trade["time"]))

    return trade_str


# Shows executed trades with volume and price
@restrict_access
def history_cmd(bot, update):
    update.message.reply_text("Retrieving history data...")

    # Send request to Kraken to get trades history
    res_trades = kraken_api("TradesHistory", private=True)

    # If Kraken replied with an error, show it
    if res_trades["error"]:
        error = btfy(res_trades["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Reset global trades list
    global trades
    trades = list()

    # Add all trades to global list
    for trade_id, trade_details in res_trades["result"]["trades"].items():
        trades.append(trade_details)

    if trades:
        # Sort global list with trades - on executed time
        trades = sorted(trades, key=lambda k: k['time'], reverse=True)

        buttons = [
            KeyboardButton(KeyboardEnum.NEXT.clean())
        ]

        cancel_btn = [
            KeyboardButton(KeyboardEnum.CANCEL.clean())
        ]

        # Get number of first items in list (latest trades)
        for items in range(config["history_items"]):
            newest_trade = next(iter(trades), None)

            total_value = "{0:.2f}".format(float(newest_trade["price"]) * float(newest_trade["vol"]))
            msg = get_trade_str(newest_trade) + " (Value: " + total_value + " EUR)"

            reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=1, footer_buttons=cancel_btn))
            update.message.reply_text(bold(msg), reply_markup=reply_mrk, parse_mode=ParseMode.MARKDOWN)

            # Remove the first item in the trades list
            trades.remove(newest_trade)

        return WorkflowEnum.HISTORY_NEXT
    else:
        update.message.reply_text("No item in trade history", reply_markup=keyboard_cmds())

        return ConversationHandler.END


# Save if BUY, SELL or ALL trade history and choose how many entries to list
def history_next(bot, update):
    if trades:
        # Get number of first items in list (latest trades)
        for items in range(config["history_items"]):
            newest_trade = next(iter(trades), None)

            total_value = "{0:.2f}".format(float(newest_trade["price"]) * float(newest_trade["vol"]))
            msg = get_trade_str(newest_trade) + " (Value: " + total_value + " EUR)"

            update.message.reply_text(bold(msg), parse_mode=ParseMode.MARKDOWN)

            # Remove the first item in the trades list
            trades.remove(newest_trade)

        return WorkflowEnum.HISTORY_NEXT
    else:
        update.message.reply_text("Trade history is empty", reply_markup=keyboard_cmds())

        return ConversationHandler.END


# Shows sub-commands to control the bot
@restrict_access
def bot_cmd(bot, update):
    reply_msg = "What do you want to do?"

    buttons = [
        KeyboardButton(KeyboardEnum.UPDATE_CHECK.clean()),
        KeyboardButton(KeyboardEnum.UPDATE.clean()),
        KeyboardButton(KeyboardEnum.RESTART.clean()),
        KeyboardButton(KeyboardEnum.SHUTDOWN.clean()),
        KeyboardButton(KeyboardEnum.SETTINGS.clean()),
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.BOT_SUB_CMD


# Execute chosen sub-cmd of 'bot' cmd
def bot_sub_cmd(bot, update):
    # Update check
    if update.message.text == KeyboardEnum.UPDATE_CHECK.clean():
        update.message.reply_text(get_update_state())
        return

    # Update
    elif update.message.text == KeyboardEnum.UPDATE.clean():
        return update_cmd(bot, update)

    # Restart
    elif update.message.text == KeyboardEnum.RESTART.clean():
        restart_cmd(bot, update)

    # Shutdown
    elif update.message.text == KeyboardEnum.SHUTDOWN.clean():
        shutdown_cmd(bot, update)

    # Cancel
    elif update.message.text == KeyboardEnum.CANCEL.clean():
        return cancel(bot, update)


# Show links to Kraken currency charts
@restrict_access
def chart_cmd(bot, update):
    reply_msg = "Choose currency"

    buttons = list()
    for coin, url in config["coin_charts"].items():
        buttons.append(KeyboardButton(coin))

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.CHART_CURRENCY


# Get chart URL for every coin in config
def chart_currency(bot, update):
    currency = update.message.text

    for coin, url in config["coin_charts"].items():
        if currency == coin:
            update.message.reply_text(url, reply_markup=keyboard_cmds())
            break

    return ConversationHandler.END


# Choose currency to deposit or withdraw funds to / from
@restrict_access
def funding_cmd(bot, update):
    reply_msg = "Choose currency"

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(coin_buttons(), n_cols=3, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.FUNDING_CURRENCY


# Choose withdraw or deposit
def funding_currency(bot, update, chat_data):
    chat_data["currency"] = update.message.text.upper()

    reply_msg = "What do you want to do?"

    buttons = [
        KeyboardButton(KeyboardEnum.DEPOSIT.clean()),
        KeyboardButton(KeyboardEnum.WITHDRAW.clean())
    ]

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.FUNDING_CHOOSE


# Get wallet addresses to deposit to
def funding_deposit(bot, update, chat_data):
    update.message.reply_text("Retrieving wallets to deposit...")

    req_data = dict()
    req_data["asset"] = chat_data["currency"]

    # Send request to Kraken to get trades history
    res_dep_meth = kraken_api("DepositMethods", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_dep_meth["error"]:
        error = btfy(res_dep_meth["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    req_data["method"] = res_dep_meth["result"][0]["method"]

    # Send request to Kraken to get trades history
    res_dep_addr = kraken_api("DepositAddresses", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_dep_addr["error"]:
        error = btfy(res_dep_addr["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Wallet found
    if res_dep_addr["result"]:
        for wallet in res_dep_addr["result"]:
            expire_info = datetime_from_timestamp(wallet["expiretm"]) if wallet["expiretm"] != "0" else "No"
            msg = wallet["address"] + "\nExpire: " + expire_info
            update.message.reply_text(bold(msg), parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard_cmds())
    # No wallet found
    else:
        update.message.reply_text("No wallet found", reply_markup=keyboard_cmds())

    return ConversationHandler.END


def funding_withdraw(bot, update):
    update.message.reply_text("Enter target wallet name", reply_markup=ReplyKeyboardRemove())

    return WorkflowEnum.WITHDRAW_WALLET


def funding_withdraw_wallet(bot, update, chat_data):
    chat_data["wallet"] = update.message.text

    update.message.reply_text("Enter " + chat_data["currency"] + " volume to withdraw")

    return WorkflowEnum.WITHDRAW_VOLUME


def funding_withdraw_volume(bot, update, chat_data):
    chat_data["volume"] = update.message.text

    volume = chat_data["volume"]
    currency = chat_data["currency"]
    wallet = chat_data["wallet"]
    reply_msg = "Withdraw " + volume + " " + currency + " from wallet " + wallet + "?"

    update.message.reply_text(reply_msg, reply_markup=keyboard_confirm())

    return WorkflowEnum.WITHDRAW_CONFIRM


# Withdraw funds from wallet
def funding_withdraw_confirm(bot, update, chat_data):
    if update.message.text == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text("Withdrawal initiated...")

    req_data = dict()
    req_data["asset"] = chat_data["currency"]
    req_data["key"] = chat_data["wallet"]
    req_data["amount"] = chat_data["volume"]

    # Send request to Kraken to get withdrawal info to lookup fee
    res_data = kraken_api("WithdrawInfo", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # Add up volume and fee and set the new value as 'amount'
    volume_and_fee = float(req_data["amount"]) + float(res_data["result"]["fee"])
    req_data["amount"] = str(volume_and_fee)

    # Send request to Kraken to withdraw digital currency
    res_data = kraken_api("Withdraw", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        logger.error(error)
        return

    # If a REFID exists, the withdrawal was initiated
    if res_data["refid"]:
        update.message.reply_text("Withdrawal executed\nREFID: " + res_data["refid"])
    else:
        update.message.reply_text("Undefined state: no error and no REFID")

    return ConversationHandler.END


# Download newest script, update the currently running one and restart.
# If 'config.json' changed, update it also
@restrict_access
def update_cmd(bot, update):
    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_script = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified
    if github_script.status_code == 304:
        msg = "You are running the latest version"
        update.message.reply_text(msg, reply_markup=keyboard_cmds())
    # Status code 200 = OK
    elif github_script.status_code == 200:
        # Get github 'config.json' file
        last_slash_index = config["update_url"].rfind("/")
        github_config_path = config["update_url"][:last_slash_index + 1] + "config.json"
        github_config_file = requests.get(github_config_path)
        github_config = json.loads(github_config_file.text)

        # Compare current config keys with
        # config keys from github-config
        if set(config) != set(github_config):
            # Go through all keys in github-config and
            # if they are not present in current config, add them
            for key, value in github_config.items():
                if key not in config:
                    config[key] = value

        # Save current ETag (hash) of bot script in github-config
        e_tag = github_script.headers.get("ETag")
        config["update_hash"] = e_tag

        # Save changed github-config as new config
        with open("config.json", "w") as cfg:
            json.dump(config, cfg, indent=4)

        # Get the name of the currently running script
        path_split = os.path.split(str(sys.argv[0]))
        filename = path_split[len(path_split)-1]

        # Save the content of the remote file
        with open(filename, "w") as file:
            file.write(github_script.text)

        # Restart the bot
        restart_cmd(bot, update)

    # Every other status code
    else:
        msg = "Update not executed. Unexpected status code: " + github_script.status_code
        update.message.reply_text(msg, reply_markup=keyboard_cmds())

    return ConversationHandler.END


# This needs to be run on a new thread because calling 'updater.stop()' inside a
# handler (shutdown_cmd) causes a deadlock because it waits for itself to finish
def shutdown():
    updater.stop()
    updater.is_idle = False


# Terminate this script
@restrict_access
def shutdown_cmd(bot, update):
    update.message.reply_text("Shutting down...", reply_markup=ReplyKeyboardRemove())

    # See comments on the 'shutdown' function
    threading.Thread(target=shutdown).start()


# Restart this python script
@restrict_access
def restart_cmd(bot, update):
    update.message.reply_text("Bot is restarting...", reply_markup=ReplyKeyboardRemove())

    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)


# Get current settings
@restrict_access
def settings_cmd(bot, update):
    settings = str()
    buttons = list()

    # Go through all settings in config file
    for key, value in config.items():
        settings += key + " = " + str(value) + "\n\n"
        buttons.append(KeyboardButton(key.upper()))

    # Send message with all current settings (key & value)
    update.message.reply_text(settings)

    cancel_btn = [
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    msg = "Choose key to change value"

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=cancel_btn))
    update.message.reply_text(msg, reply_markup=reply_mrk)

    return WorkflowEnum.SETTINGS_CHANGE


# Change setting
def settings_change(bot, update, chat_data):
    chat_data["setting"] = update.message.text.lower()

    # Don't allow to change setting 'user_id'
    if update.message.text == "USER_ID":
        update.message.reply_text("It's not possible to change USER_ID value")
        return

    msg = "Enter new value"

    update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())

    return WorkflowEnum.SETTINGS_SAVE


# Save new value for chosen setting
def settings_save(bot, update, chat_data):
    new_value = update.message.text

    # Check if new value is a boolean
    if new_value.lower() == "true":
        chat_data["value"] = True
    elif new_value.lower() == "false":
        chat_data["value"] = False
    else:
        # Check if new value is an integer ...
        try:
            chat_data["value"] = int(new_value)
        # ... if not, save as string
        except ValueError:
            chat_data["value"] = new_value

    msg = "Save new value and restart bot?"
    update.message.reply_text(msg, reply_markup=keyboard_confirm())

    return WorkflowEnum.SETTINGS_CONFIRM


# Confirm saving new setting and restart bot
def settings_confirm(bot, update, chat_data):
    if update.message.text == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    # Set new value in config dictionary
    config[chat_data["setting"]] = chat_data["value"]

    # Save changed config as new one
    with open("config.json", "w") as cfg:
        json.dump(config, cfg, indent=4)

    update.message.reply_text("New value saved")

    # Restart bot to activate new setting
    restart_cmd(bot, update)


# Will show a cancel message, end the conversation and show the default keyboard
def cancel(bot, update):
    update.message.reply_text("Canceled...", reply_markup=keyboard_cmds())
    return ConversationHandler.END


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
        KeyboardButton("/chart"),
        KeyboardButton("/history"),
        KeyboardButton("/funding"),
        KeyboardButton("/bot")
    ]

    return ReplyKeyboardMarkup(build_menu(command_buttons, n_cols=3))


# Generic custom keyboard that shows YES and NO
def keyboard_confirm():
    buttons = [
        KeyboardButton(KeyboardEnum.YES.clean()),
        KeyboardButton(KeyboardEnum.NO.clean())
    ]

    return ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))


# Create a list with a button for every coin in config
def coin_buttons():
    buttons = list()

    for coin in config["used_coins"]:
        buttons.append(KeyboardButton(coin))

    return buttons


# Check order state and send message if order closed
def order_state_check(bot, job):
    req_data = dict()
    req_data["txid"] = job.context["order_txid"]

    # Send request to get info on specific order
    res_data = kraken_api("QueryOrders", data=req_data, private=True)

    # If Kraken replied with an error, return without notification
    if res_data["error"]:
        if config["send_error"]:
            src = "Order state check:\n"
            error = btfy(res_data["error"][0])
            updater.bot.send_message(chat_id=config["user_id"], text=src + error)
            logger.error(error)
        return

    # Save information about order
    order_info = res_data["result"][job.context["order_txid"]]

    # Check if order was canceled. If so, stop monitoring
    if order_info["status"] == "canceled":
        # Stop this job
        job.schedule_removal()
        return

    # Check if trade was executed. If so, stop monitoring and send message
    if order_info["status"] == "closed":
        msg = "Trade executed:\n" + job.context["order_txid"] + "\n" + trim_zeros(order_info["descr"]["order"])
        bot.send_message(chat_id=config["user_id"], text=bold(msg), parse_mode=ParseMode.MARKDOWN)
        # Stop this job
        job.schedule_removal()


# Monitor status changes of previously created open orders
def monitor_open_orders():
    if config["check_trade"]:
        # Send request for open orders to Kraken
        res_data = kraken_api("OpenOrders", private=True, return_error=False)

        # If Kraken replied with an error, show it
        if res_data["error"]:
            src = "Monitoring orders:\n"
            error = btfy(res_data["error"][0])
            updater.bot.send_message(chat_id=config["user_id"], text=src + error)
            logger.error(error)
            return

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                # Save order transaction ID
                order_txid = str(order)
                # Save time in seconds from config
                check_trade_time = config["check_trade_time"]

                # Add Job to JobQueue to check status of order
                context = dict(order_txid=order_txid)
                job_queue.run_repeating(order_state_check, check_trade_time, context=context)


# Show welcome message, update-state and keyboard for commands
def init():
    msg = "Kraken-Bot is running!\n" + get_update_state()
    updater.bot.send_message(config["user_id"], msg, reply_markup=keyboard_cmds())


# Converts a Unix timestamp to a datatime object with format 'Y-m-d H:M:S'
def datetime_from_timestamp(unix_timestamp):
    return datetime.datetime.fromtimestamp(int(unix_timestamp)).strftime('%Y-%m-%d %H:%M:%S')


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


# Add asterisk as prefix and suffix for a string
# Will make the text bold if used with Markdown
def bold(text):
    return "*" + text + "*"


# Beautifies Kraken error messages
def btfy(text):
    index = text.find(":")

    # Character wasn't found
    if index == -1:
        return text

    return "ERROR (" + text[1:index] + "): " + text[index + 1:]


# Handle all telegram and telegram.ext related errors
def handle_telegram_error(bot, update, error):
    error_str = "Update '%s' caused error '%s'" % (update, error)

    logger.error(error_str)

    if config["send_error"]:
        updater.bot.send_message(chat_id=config["user_id"], text=error_str)


# Log all errors
dispatcher.add_error_handler(handle_telegram_error)


# Returns regex representation of OR for all coins in config
def regex_coin_or():
    coins_regex_or = str()

    for coin in config["used_coins"]:
        coins_regex_or += coin + "|"

    return coins_regex_or[:-1]


# Return regex representation of OR for all settings in config
def regex_settings_or():
    settings_regex_or = str()

    for key, value in config.items():
        settings_regex_or += key.upper() + "|"

    return settings_regex_or[:-1]


# Add command handlers to dispatcher
dispatcher.add_handler(CommandHandler("update", update_cmd))
dispatcher.add_handler(CommandHandler("restart", restart_cmd))
dispatcher.add_handler(CommandHandler("shutdown", shutdown_cmd))
dispatcher.add_handler(CommandHandler("balance", balance_cmd))
dispatcher.add_handler(CommandHandler("reload", reload_cmd))


# FUNDING conversation handler
funding_handler = ConversationHandler(
    entry_points=[CommandHandler('funding', funding_cmd)],
    states={
        WorkflowEnum.FUNDING_CURRENCY:
            [RegexHandler("^(" + regex_coin_or() + ")$", funding_currency, pass_chat_data=True),
             RegexHandler("^(CANCEL)$", cancel)],
        WorkflowEnum.FUNDING_CHOOSE:
            [RegexHandler("^(DEPOSIT)$", funding_deposit, pass_chat_data=True),
             RegexHandler("^(WITHDRAW)$", funding_withdraw),
             RegexHandler("^(CANCEL)$", cancel)],
        WorkflowEnum.WITHDRAW_WALLET:
            [MessageHandler(Filters.text, funding_withdraw_wallet, pass_chat_data=True)],
        WorkflowEnum.WITHDRAW_VOLUME:
            [MessageHandler(Filters.text, funding_withdraw_volume, pass_chat_data=True)],
        WorkflowEnum.WITHDRAW_CONFIRM:
            [RegexHandler("^(YES|NO)$", funding_withdraw_confirm, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(funding_handler)


# HISTORY conversation handler
history_handler = ConversationHandler(
    entry_points=[CommandHandler('history', history_cmd)],
    states={
        WorkflowEnum.HISTORY_NEXT:
            [RegexHandler("^(NEXT)$", history_next),
             RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(history_handler)


# CHART conversation handler
chart_handler = ConversationHandler(
    entry_points=[CommandHandler('chart', chart_cmd)],
    states={
        WorkflowEnum.CHART_CURRENCY:
            [RegexHandler("^(" + regex_coin_or() + ")$", chart_currency),
             RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(chart_handler)


# ORDERS conversation handler
orders_handler = ConversationHandler(
    entry_points=[CommandHandler('orders', orders_cmd)],
    states={
        WorkflowEnum.ORDERS_CLOSE:
            [RegexHandler("^(CLOSE ORDER)$", orders_choose_order),
             RegexHandler("^(CLOSE ALL)$", orders_close_all),
             RegexHandler("^(CANCEL)$", cancel)],
        WorkflowEnum.ORDERS_CLOSE_ORDER:
            [RegexHandler("^(CANCEL)$", cancel),
             RegexHandler("^[A-Z0-9]{6}-[A-Z0-9]{5}-[A-Z0-9]{6}$", orders_close_order)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(orders_handler)


# TRADE conversation handler
trade_handler = ConversationHandler(
    entry_points=[CommandHandler('trade', trade_cmd)],
    states={
        WorkflowEnum.TRADE_BUY_SELL:
            [RegexHandler("^(BUY|SELL)$", trade_buy_sell, pass_chat_data=True),
             RegexHandler("^(CANCEL)$", cancel)],
        WorkflowEnum.TRADE_CURRENCY:
            [RegexHandler("^(" + regex_coin_or() + ")$", trade_currency, pass_chat_data=True),
             RegexHandler("^(CANCEL)$", cancel),
             RegexHandler("^(ALL)$", trade_sell_all)],
        WorkflowEnum.TRADE_SELL_ALL_CONFIRM:
            [RegexHandler("^(YES|NO)$", trade_sell_all_confirm)],
        WorkflowEnum.TRADE_PRICE:
            [RegexHandler("^((?=.*?\d)\d*[.]?\d*)$", trade_price, pass_chat_data=True)],
        WorkflowEnum.TRADE_VOL_TYPE:
            [RegexHandler("^(EUR|USD|CAD|GBP|JPY|VOLUME)$", trade_vol_type, pass_chat_data=True),
             RegexHandler("^(ALL)$", trade_vol_type_all, pass_chat_data=True),
             RegexHandler("^(CANCEL)$", cancel)],
        WorkflowEnum.TRADE_VOLUME:
            [RegexHandler("^((?=.*?\d)\d*[.]?\d*)$", trade_volume, pass_chat_data=True)],
        WorkflowEnum.TRADE_CONFIRM:
            [RegexHandler("^(YES|NO)$", trade_confirm, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(trade_handler)


# PRICE conversation handler
price_handler = ConversationHandler(
    entry_points=[CommandHandler('price', price_cmd)],
    states={
        WorkflowEnum.PRICE_CURRENCY:
            [RegexHandler("^(" + regex_coin_or() + ")$", price_currency),
             RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(price_handler)


# VALUE conversation handler
value_handler = ConversationHandler(
    entry_points=[CommandHandler('value', value_cmd)],
    states={
        WorkflowEnum.VALUE_CURRENCY:
            [RegexHandler("^(" + regex_coin_or() + "|ALL)$", value_currency),
             RegexHandler("^(CANCEL)$", cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(value_handler)


# Will return the SETTINGS_CHANGE state for a conversation handler
# This way the state is reusable
def get_settings_change_state():
    return [WorkflowEnum.SETTINGS_CHANGE,
            [RegexHandler("^(" + regex_settings_or() + ")$", settings_change, pass_chat_data=True),
             RegexHandler("^(CANCEL)$", cancel)]]


# Will return the SETTINGS_SAVE state for a conversation handler
# This way the state is reusable
def get_settings_save_state():
    return [WorkflowEnum.SETTINGS_SAVE,
            [MessageHandler(Filters.text, settings_save, pass_chat_data=True)]]


# Will return the SETTINGS_CONFIRM state for a conversation handler
# This way the state is reusable
def get_settings_confirm_state():
    return [WorkflowEnum.SETTINGS_CONFIRM,
            [RegexHandler("^(YES|NO)$", settings_confirm, pass_chat_data=True)]]


# BOT conversation handler
bot_handler = ConversationHandler(
    entry_points=[CommandHandler('bot', bot_cmd)],
    states={
        WorkflowEnum.BOT_SUB_CMD:
            [RegexHandler("^(UPDATE CHECK|UPDATE|RESTART|SHUTDOWN)$", bot_sub_cmd),
             RegexHandler("^(SETTINGS)$", settings_cmd),
             RegexHandler("^(CANCEL)$", cancel)],
        get_settings_change_state()[0]: get_settings_change_state()[1],
        get_settings_save_state()[0]: get_settings_save_state()[1],
        get_settings_confirm_state()[0]: get_settings_confirm_state()[1]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(bot_handler)


# SETTINGS conversation handler
settings_handler = ConversationHandler(
    entry_points=[CommandHandler('settings', settings_cmd)],
    states={
        get_settings_change_state()[0]: get_settings_change_state()[1],
        get_settings_save_state()[0]: get_settings_save_state()[1],
        get_settings_confirm_state()[0]: get_settings_confirm_state()[1]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(settings_handler)


# Start polling to handle all user input
# Dismiss all still open commands
updater.start_polling(clean=True)

# Show welcome-message, update-state and commands-keyboard
init()

# Monitor status changes of open orders
monitor_open_orders()

# Run the bot until you press Ctrl-C or the process receives SIGINT,
# SIGTERM or SIGABRT. This should be used most of the time, since
# start_polling() is non-blocking and will stop the bot gracefully.
updater.idle()
