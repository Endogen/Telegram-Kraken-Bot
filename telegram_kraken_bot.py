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
import re

from enum import Enum, auto
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, ParseMode
from telegram.ext import Updater, CommandHandler, ConversationHandler, RegexHandler, MessageHandler
from telegram.ext.filters import Filters
from bs4 import BeautifulSoup

# Emojis for messages
emo_e = "‼"  # Error
emo_w = "⏳"  # Wait
emo_f = "🏁"  # Finished
emo_n = "🔔"  # Notify
emo_b = "✨"  # Beginning
emo_c = "❌"  # Cancel
emo_t = "👍"  # Top
emo_d = "☑"  # Done
emo_g = "👋"  # Goodbye
emo_q = "❓"  # Question

# Check if file 'config.json' exists. Exit if not.
if os.path.isfile("config.json"):
    # Read configuration
    with open("config.json") as config_file:
        config = json.load(config_file)
else:
    exit("No configuration file 'config.json' found")

# Set up logging

# Formatter string for logging
formatter_str = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
date_format = "%y%m%d"

# Folder name for logfiles
log_dir = "log"

# Do not use the logger directly. Use function 'log(msg, severity)'
logging.basicConfig(level=config["log_level"], format=formatter_str)
logger = logging.getLogger()

# Current date for logging
date = datetime.datetime.now().strftime(date_format)

# Add a file handlers to the logger if enabled
if config["log_to_file"]:
    # If log directory doesn't exist, create it
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Create a file handler for logging
    file_path = os.path.join(log_dir, date + ".log")
    handler = logging.FileHandler(file_path, encoding="utf-8")
    handler.setLevel(config["log_level"])

    # Format file handler
    formatter = logging.Formatter(formatter_str)
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

# Cached objects
trades = list()
orders = list()
assets = dict()

# 'Z' + base currency from config
base_currency = str()


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
    API_STATE = auto()

    def clean(self):
        return self.name.replace("_", " ")


# Log an event and save it in a file with current date as name
def log(severity, msg):
    # Add file handler to logger if enabled
    if config["log_to_file"]:
        now = datetime.datetime.now().strftime(date_format)

        # If current date not the same as initial one, create new FileHandler
        if str(now) != str(date):
            # Remove old handlers
            for hdlr in logger.handlers[:]:
                logger.removeHandler(hdlr)

            new_hdlr = logging.FileHandler(file_path, encoding="utf-8")
            new_hdlr.setLevel(config["log_level"])

            # Format file handler
            new_hdlr.setFormatter(formatter)

            # Add file handler to logger
            logger.addHandler(new_hdlr)

    logger.log(severity, msg)


# Issue Kraken API requests
def kraken_api(method, data=None, private=False, retries=None):
    # Get arguments of this function
    frame = inspect.currentframe()
    args, _, _, values = inspect.getargvalues(frame)

    # Get name of caller function
    caller = inspect.currentframe().f_back.f_code.co_name

    # Log caller of this function and all arguments
    log(logging.DEBUG, caller + " - args: " + str([(i, values[i]) for i in args]))

    try:
        if private:
            return kraken.query_private(method, data)
        else:
            return kraken.query_public(method, data)

    except Exception as ex:
        log(logging.ERROR, str(ex))

        ex_name = type(ex).__name__

        # Handle the following exceptions immediately without retrying

        # Mostly this means that the API keys are not correct
        if "Incorrect padding" in str(ex):
            msg = "Incorrect padding: please verify that your Kraken API keys are valid"
            return {"error": [msg]}
        # No need to retry if the API service is not available right now
        elif "Service:Unavailable" in str(ex):  # TODO: Test it
            msg = "Service: Unavailable"
            return {"error": [msg]}

        # Is retrying on error enabled?
        if config["retries"]:
            # It's the first call, start retrying
            if retries is None:
                retries = config["retries_counter"]
                return kraken_api(method, data, private, retries)
            # If 'retries' is bigger then 0, decrement it and retry again
            elif retries > 0:
                retries -= 1
                return kraken_api(method, data, private, retries)
            # Return error from last Kraken request
            else:
                return {"error": [ex_name + ":" + str(ex)]}
        # Retrying on error not enabled, return error from last Kraken request
        else:
            return {"error": [ex_name + ":" + str(ex)]}


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

            log(logging.WARNING, msg)
            return
        else:
            return func(bot, update)
    return _restrict_access


# Get balance of all currencies
@restrict_access
def balance_cmd(bot, update):
    update.message.reply_text(emo_w + " Retrieving balance...")

    # Send request to Kraken to get current balance of all currencies
    res_balance = kraken_api("Balance", private=True)

    # If Kraken replied with an error, show it
    if res_balance["error"]:
        error = btfy(res_balance["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    # Send request to Kraken to get open orders
    res_orders = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_orders["error"]:
        error = btfy(res_orders["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    msg = str()

    # Go over all currencies in your balance
    for currency_key, currency_value in res_balance["result"].items():
        available_value = currency_value

        # Get clean asset name
        currency_key = assets[currency_key]["altname"]

        # Go through all open orders and check if an order exists for the currency
        if res_orders["result"]["open"]:
            for order in res_orders["result"]["open"]:
                order_desc = res_orders["result"]["open"][order]["descr"]["order"]
                order_desc_list = order_desc.split(" ")

                order_type = order_desc_list[0]
                order_volume = order_desc_list[1]
                price_per_coin = order_desc_list[5]

                # Check if current asset is a fiat-currency (EUR, USD, ...)
                if currency_key == config["base_currency"] and order_type == "buy":
                    available_value = float(available_value) - (float(order_volume) * float(price_per_coin))
                # Current asset is a coin and not a fiat currency
                else:
                    order_currency = order_desc_list[2][:-len(config["base_currency"])]
                    # Reduce current volume for coin if open sell-order exists
                    if currency_key == order_currency and order_type == "sell":
                        available_value = float(available_value) - float(order_volume)

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
    chat_data["buysell"] = update.message.text.lower()

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
    msg = " Sell `all` assets to current market price? All open orders will be closed!"
    update.message.reply_text(emo_q + msg, reply_markup=keyboard_confirm(), parse_mode=ParseMode.MARKDOWN)

    return WorkflowEnum.TRADE_SELL_ALL_CONFIRM


# Sells all assets for there respective current market value
def trade_sell_all_confirm(bot, update):
    if update.message.text.upper() == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text(emo_w + " Preparing to sell everything...")

    # Send request for open orders to Kraken
    res_open_orders = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_open_orders["error"]:
        error = btfy(res_open_orders["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
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
                log(logging.ERROR, error)
                return

    # Send request to Kraken to get current balance of all currencies
    res_balance = kraken_api("Balance", private=True)

    # If Kraken replied with an error, show it
    if res_balance["error"]:
        error = btfy(res_balance["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    # Go over all assets and sell them
    for balance_asset, amount in res_balance["result"].items():
        # Current asset is not a crypto-currency - skip it
        if balance_asset == base_currency:
            continue

        # Filter out currencies that have a volume of 0
        if amount == "0.0000000000":
            continue

        # Get clean asset name
        balance_asset = assets[balance_asset]["altname"]

        # Make sure that the order size is at least the minimum order limit
        if balance_asset in config["min_order_size"]:
            if float(amount) < float(config["min_order_size"][balance_asset]):
                msg_error = emo_e + " Not possible to sell " + balance_asset + ": volume to low"
                msg_next = emo_w + " Selling next asset..."

                update.message.reply_text(msg_error + "\n" + msg_next)
                log(logging.WARNING, msg_error)
                continue
        else:
            log(logging.WARNING, "No minimum order limit in config for coin " + balance_asset)
            continue

        req_data = dict()
        req_data["type"] = "sell"
        req_data["pair"] = balance_asset + base_currency
        req_data["ordertype"] = "market"
        req_data["volume"] = amount

        # Send request to create order to Kraken
        res_add_order = kraken_api("AddOrder", data=req_data, private=True)

        # If Kraken replied with an error, show it
        if res_add_order["error"]:
            error = btfy(res_add_order["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            continue

        order_txid = res_add_order["result"]["txid"][0]

        # Add Job to JobQueue to check status of created order (if setting is enabled)
        if config["check_trade"]:
            trade_time = config["check_trade_time"]
            context = dict(order_txid=order_txid)
            job_queue.run_repeating(order_state_check, trade_time, context=context)

    msg = emo_f + " Created orders to sell all assets"
    update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# Save currency to trade and enter price per unit to trade
def trade_currency(bot, update, chat_data):
    chat_data["currency"] = update.message.text.upper()

    reply_msg = "Enter price per unit"

    update.message.reply_text(reply_msg, reply_markup=ReplyKeyboardRemove())
    return WorkflowEnum.TRADE_PRICE


# Save price per unit and choose how to enter the
# trade volume (euro, volume or all available funds)
def trade_price(bot, update, chat_data):
    chat_data["price"] = update.message.text

    reply_msg = "How to enter the volume?"

    buttons = [
        KeyboardButton(config["base_currency"].upper()),
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
    chat_data["vol_type"] = update.message.text.upper()

    reply_msg = "Enter volume"

    update.message.reply_text(reply_msg, reply_markup=ReplyKeyboardRemove())
    return WorkflowEnum.TRADE_VOLUME


# Volume type 'ALL' chosen - meaning that
# all available EURO funds will be used
def trade_vol_type_all(bot, update, chat_data):
    update.message.reply_text(emo_w + " Calculating volume...")

    if chat_data["buysell"].upper() == KeyboardEnum.BUY.clean():
        # Send request to Kraken to get current balance of all currencies
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Get amount of available fiat currency
        available_euros = float(res_balance["result"][base_currency])

        # Send request to Kraken to get open orders
        res_orders = kraken_api("OpenOrders", private=True)

        # If Kraken replied with an error, show it
        if res_orders["error"]:
            error = btfy(res_orders["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Go through all open orders and check if buy-orders exist
        # If yes, subtract their value from the total of base currency
        if res_orders["result"]["open"]:
            for order in res_orders["result"]["open"]:
                order_desc = res_orders["result"]["open"][order]["descr"]["order"]
                order_desc_list = order_desc.split(" ")

                coin_price = order_desc_list[5][:-len(config["base_currency"])]
                order_volume = order_desc_list[1]
                order_type = order_desc_list[0]

                if order_type == "buy":
                    available_euros = float(available_euros) - (float(order_volume) * float(coin_price))

        # Calculate volume depending on available euro balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(available_euros / float(chat_data["price"]))

    if chat_data["buysell"].upper() == KeyboardEnum.SELL.clean():
        # Send request to Kraken to get euro balance to calculate volume
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Send request to Kraken to get open orders
        res_orders = kraken_api("OpenOrders", private=True)

        # If Kraken replied with an error, show it
        if res_orders["error"]:
            error = btfy(res_orders["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Lookup volume of chosen currency
        for asset, data in assets.items():
            if data["altname"] == chat_data["currency"]:
                available_volume = res_balance["result"][asset]
                break

        # Go through all open orders and check if sell-orders exists for the currency
        # If yes, subtract their volume from the available volume
        if res_orders["result"]["open"]:
            for order in res_orders["result"]["open"]:
                order_desc = res_orders["result"]["open"][order]["descr"]["order"]
                order_desc_list = order_desc.split(" ")

                order_currency = order_desc_list[2][:-len(config["base_currency"])]
                order_volume = order_desc_list[1]
                order_type = order_desc_list[0]

                if chat_data["currency"] in order_currency:
                    if order_type == "sell":
                        available_volume = str(float(available_volume) - float(order_volume))

        # Get volume from balance and round it to 8 digits
        chat_data["volume"] = "{0:.8f}".format(float(available_volume))

    # If available volume is 0, return without creating a trade
    if chat_data["volume"] == "0.00000000":
        msg = emo_e + " Available " + chat_data["currency"] + " volume is 0"
        update.message.reply_text(msg, reply_markup=keyboard_cmds())
        return ConversationHandler.END
    else:
        show_trade_conf(update, chat_data)

    return WorkflowEnum.TRADE_CONFIRM


# Calculate the volume depending on chosen volume type (EURO or VOLUME)
def trade_volume(bot, update, chat_data):
    # Entered currency from config (EUR, USD, ...)
    if chat_data["vol_type"] == config["base_currency"].upper():
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
    total_value_str = "(Value: " + str(total_value) + " " + config["base_currency"] + ")"

    reply_msg = " Place this order?\n" + trade_str + "\n" + total_value_str
    update.message.reply_text(emo_q + reply_msg, reply_markup=keyboard_confirm())


# The user has to confirm placing the order
def trade_confirm(bot, update, chat_data):
    if update.message.text.upper() == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text(emo_w + " Placing order...")

    req_data = dict()
    req_data["type"] = chat_data["buysell"].lower()
    req_data["price"] = chat_data["price"]
    req_data["ordertype"] = "limit"
    req_data["volume"] = chat_data["volume"]

    # If currency is BCH then use different pair string
    if chat_data["currency"] == "BCH":
        req_data["pair"] = chat_data["currency"] + config["base_currency"]
    else:
        for asset, data in assets.items():
            if data["altname"] == chat_data["currency"]:
                req_data["pair"] = asset + base_currency
                break

    # Send request to create order to Kraken
    res_add_order = kraken_api("AddOrder", req_data, private=True)

    # If Kraken replied with an error, show it
    if res_add_order["error"]:
        error = btfy(res_add_order["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
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
            log(logging.ERROR, error)
            return

        if res_query_order["result"][order_txid]:
            order_desc = res_query_order["result"][order_txid]["descr"]["order"]
            msg = emo_f + " Order placed:\n" + order_txid + "\n" + trim_zeros(order_desc)
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
    update.message.reply_text(emo_w + " Retrieving orders...")

    # Send request to Kraken to get open orders
    res_data = kraken_api("OpenOrders", private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
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
        update.message.reply_text(bold("No open orders"), parse_mode=ParseMode.MARKDOWN)
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
    update.message.reply_text(emo_w + " Closing orders...")

    closed_orders = list()

    if orders:
        for x in range(0, len(orders)):
            order_id = next(iter(orders[x]), None)

            # Send request to Kraken to cancel orders
            res_data = kraken_api("CancelOrder", data={"txid": order_id}, private=True)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                error = "Order not closed:\n" + order_id + "\n" + res_data["error"][0]
                update.message.reply_text(btfy(error))
                log(logging.ERROR, error)

                # If we are currently not closing the last order,
                # show message that we a continuing with the next one
                if x+1 != len(orders):
                    update.message.reply_text(emo_w + " Closing next order...")
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
    update.message.reply_text(emo_w + " Closing order...")

    req_data = dict()
    req_data["txid"] = update.message.text

    # Send request to Kraken to cancel order
    res_data = kraken_api("CancelOrder", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    msg = emo_f + " " + bold("Order closed:\n" + req_data["txid"])
    update.message.reply_text(msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# Show the last trade price for a currency
@restrict_access
def price_cmd(bot, update):
    # If single-price option is active, get prices for all coins
    if config["single_price"]:
        update.message.reply_text(emo_w + " Retrieving prices...")

        req_data = dict()
        req_data["pair"] = str()

        # Find currency names
        for coin in config["used_coins"]:
            for asset, data in assets.items():
                if coin == data["altname"]:
                    # If currency is BCH, use different pair string
                    if coin == "BCH":
                        req_data["pair"] += asset + config["base_currency"] + ","
                        break
                    # Regular way to combine asset and base currency
                    else:
                        req_data["pair"] += asset + base_currency + ","
                        break

        # Get rid of last comma
        req_data["pair"] = req_data["pair"][:-1]

        # Send request to Kraken to get current trading price for currency-pair
        res_data = kraken_api("Ticker", data=req_data, private=False)

        # If Kraken replied with an error, show it
        if res_data["error"]:
            error = btfy(res_data["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        msg = str()

        for pair, data in res_data["result"].items():
            # If currency is BCH, use different method to get coin name
            if "BCH" in pair:
                coin = pair[:-len(config["base_currency"])]
            else:
                coin = assets[pair[:-len(base_currency)]]["altname"]

            last_trade_price = trim_zeros(data["c"][0])
            msg += coin + ": " + last_trade_price + " " + config["base_currency"] + "\n"

        update.message.reply_text(bold(msg), parse_mode=ParseMode.MARKDOWN)

        return ConversationHandler.END

    # Let user choose for which coin to get the price
    else:
        reply_msg = "Choose currency"

        cancel_btn = [
            KeyboardButton(KeyboardEnum.CANCEL.clean())
        ]

        reply_mrk = ReplyKeyboardMarkup(build_menu(coin_buttons(), n_cols=3, footer_buttons=cancel_btn))
        update.message.reply_text(reply_msg, reply_markup=reply_mrk)

        return WorkflowEnum.PRICE_CURRENCY


# Choose for which currency to show the last trade price
def price_currency(bot, update):
    update.message.reply_text(emo_w + " Retrieving price...")

    req_data = dict()

    # If currency is BCH then use different pair string
    if update.message.text.upper() == "BCH":
        req_data["pair"] = update.message.text + config["base_currency"]
    else:
        for asset, data in assets.items():
            if data["altname"] == update.message.text.upper():
                req_data["pair"] = asset + base_currency
                break

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken_api("Ticker", data=req_data, private=False)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        error = btfy(res_data["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    currency = update.message.text.upper()
    last_trade_price = trim_zeros(res_data["result"][req_data["pair"]]["c"][0])

    msg = bold(currency + ": " + last_trade_price + " " + config["base_currency"])
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
    update.message.reply_text(emo_w + " Retrieving current value...")

    # Get balance of all currencies
    if update.message.text.upper() == KeyboardEnum.ALL.clean():
        req_asset = dict()
        req_asset["asset"] = config["base_currency"]

        # Send request to Kraken tp obtain the combined balance of all currencies
        res_trade_balance = kraken_api("TradeBalance", data=req_asset, private=True)

        # If Kraken replied with an error, show it
        if res_trade_balance["error"]:
            error = btfy(res_trade_balance["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Show only 2 digits after decimal place
        total_value_euro = "{0:.2f}".format(float(res_trade_balance["result"]["eb"]))

        # Generate message to user
        msg = "Overall: " + total_value_euro + " " + config["base_currency"]

        update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    # Get balance of a specific coin
    else:
        # Send request to Kraken to get current balance of all currencies
        res_balance = kraken_api("Balance", private=True)

        # If Kraken replied with an error, show it
        if res_balance["error"]:
            error = btfy(res_balance["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        req_price = dict()

        if update.message.text.upper() == "BCH":
            req_price["pair"] = update.message.text.upper() + config["base_currency"]
        else:
            for asset, data in assets.items():
                if data["altname"] == update.message.text.upper():
                    req_price["pair"] = asset + base_currency
                    break

        # Send request to Kraken to get current trading price for currency-pair
        res_price = kraken_api("Ticker", data=req_price, private=False)

        # If Kraken replied with an error, show it
        if res_price["error"]:
            error = btfy(res_price["error"][0])
            update.message.reply_text(error)
            log(logging.ERROR, error)
            return

        # Get last trade price
        pair = list(res_price["result"].keys())[0]
        last_price = res_price["result"][pair]["c"][0]

        value_euro = float(0)

        for asset, data in assets.items():
            if data["altname"] == update.message.text.upper():
                # Calculate value by multiplying balance with last trade price
                value_euro = float(res_balance["result"][asset]) * float(last_price)
                break

        # Show only 2 digits after decimal place
        value_euro = "{0:.2f}".format(value_euro)

        msg = update.message.text.upper() + ": " + value_euro + " " + config["base_currency"]

        # Add last trade price to msg
        last_trade_price = "{0:.2f}".format(float(last_price))
        msg += "\n(Ticker: " + last_trade_price + " " + config["base_currency"] + ")"

        update.message.reply_text(bold(msg), reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# Reloads the custom keyboard
@restrict_access
def reload_cmd(bot, update):
    msg = emo_w + " Reloading keyboard..."
    update.message.reply_text(msg, reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Get current state of Kraken API
# Is it under maintenance or functional?
@restrict_access
def state_cmd(bot, update):
    update.message.reply_text(emo_w + " Retrieving API state...")
    msg = bold("Kraken API Status: " + api_state()) + "\n" + "https://status.kraken.com"
    update.message.reply_text(msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# Returns a string representation of a trade. Looks like this:
# sell 0.03752345 ETH-EUR @ limit 267.5 on 2017-08-22 22:18:22
def get_trade_str(trade):
    # Format pair-string from 'XXBTZEUR' to 'XXBT-ZEUR'
    for asset, data in assets.items():
        # Default pair
        if trade["pair"].endswith(asset):
            first_asset = trade["pair"][:len(asset)]
            second_asset = trade["pair"][len(trade["pair"])-len(asset):]
            pair_str = first_asset + "-" + second_asset
            break
        # Pair with short asset name (BCH pairs are like that)
        elif trade["pair"].endswith(data["altname"]):
            first_asset = trade["pair"][:len(data["altname"])]
            second_asset = trade["pair"][len(trade["pair"]) - len(data["altname"]):]
            pair_str = first_asset + "-" + second_asset
            break

    # Replace asset names with clean asset names
    pair_list = pair_str.split("-")

    pair_str = pair_str.replace(pair_list[0], assets[pair_list[0]]["altname"])
    pair_str = pair_str.replace(pair_list[1], assets[pair_list[1]]["altname"])

    trade_str = (trade["type"] + " " +
                 trim_zeros(trade["vol"]) + " " +
                 pair_str + " @ limit " +
                 trim_zeros(trade["price"]) + " on " +
                 datetime_from_timestamp(trade["time"]))

    return trade_str


# Shows executed trades with volume and price
@restrict_access
def history_cmd(bot, update):
    update.message.reply_text(emo_w + " Retrieving finalized trades...")

    # Send request to Kraken to get trades history
    res_trades = kraken_api("TradesHistory", private=True)

    # If Kraken replied with an error, show it
    if res_trades["error"]:
        error = btfy(res_trades["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
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
            KeyboardButton(KeyboardEnum.NEXT.clean()),
            KeyboardButton(KeyboardEnum.CANCEL.clean())
        ]

        # Get number of first items in list (latest trades)
        for items in range(config["history_items"]):
            newest_trade = next(iter(trades), None)

            total_value = "{0:.2f}".format(float(newest_trade["price"]) * float(newest_trade["vol"]))
            msg = get_trade_str(newest_trade) + " (Value: " + total_value + " EUR)"

            reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))
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
        msg = bold("Trade history is empty")
        update.message.reply_text(emo_f + " " + msg, reply_markup=keyboard_cmds(), parse_mode=ParseMode.MARKDOWN)

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
        KeyboardButton(KeyboardEnum.API_STATE.clean()),
        KeyboardButton(KeyboardEnum.CANCEL.clean())
    ]

    reply_mrk = ReplyKeyboardMarkup(build_menu(buttons, n_cols=2))
    update.message.reply_text(reply_msg, reply_markup=reply_mrk)

    return WorkflowEnum.BOT_SUB_CMD


# Execute chosen sub-cmd of 'bot' cmd
def bot_sub_cmd(bot, update):
    # Update check
    if update.message.text.upper() == KeyboardEnum.UPDATE_CHECK.clean():
        status_code, msg = get_update_state()
        update.message.reply_text(msg)
        return

    # Update
    elif update.message.text.upper() == KeyboardEnum.UPDATE.clean():
        return update_cmd(bot, update)

    # Restart
    elif update.message.text.upper() == KeyboardEnum.RESTART.clean():
        restart_cmd(bot, update)

    # Shutdown
    elif update.message.text.upper() == KeyboardEnum.SHUTDOWN.clean():
        shutdown_cmd(bot, update)

    # API State
    elif update.message.text.upper() == KeyboardEnum.API_STATE.clean():
        state_cmd(bot, update)

    # Cancel
    elif update.message.text.upper() == KeyboardEnum.CANCEL.clean():
        return cancel(bot, update)


# Show links to Kraken currency charts
@restrict_access
def chart_cmd(bot, update):
    # Send only one message with all configured charts
    if config["single_chart"]:
        msg = str()

        for coin, url in config["coin_charts"].items():
            msg += coin + ": " + url + "\n"

        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard_cmds())

        return ConversationHandler.END

    # Choose currency and display chart for it
    else:
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
        if currency.upper() == coin.upper():
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
    update.message.reply_text(emo_w + " Retrieving wallets to deposit...")

    req_data = dict()
    req_data["asset"] = chat_data["currency"]

    # Send request to Kraken to get trades history
    res_dep_meth = kraken_api("DepositMethods", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_dep_meth["error"]:
        error = btfy(res_dep_meth["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
        return

    req_data["method"] = res_dep_meth["result"][0]["method"]

    # Send request to Kraken to get trades history
    res_dep_addr = kraken_api("DepositAddresses", data=req_data, private=True)

    # If Kraken replied with an error, show it
    if res_dep_addr["error"]:
        error = btfy(res_dep_addr["error"][0])
        update.message.reply_text(error)
        log(logging.ERROR, error)
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
    reply_msg = " Withdraw " + volume + " " + currency + " to wallet " + wallet + "?"

    update.message.reply_text(emo_q + reply_msg, reply_markup=keyboard_confirm())

    return WorkflowEnum.WITHDRAW_CONFIRM


# Withdraw funds from wallet
def funding_withdraw_confirm(bot, update, chat_data):
    if update.message.text.upper() == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    update.message.reply_text(emo_w + " Withdrawal initiated...")

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
        log(logging.ERROR, error)
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
        log(logging.ERROR, error)
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
        msg = emo_e + " Update not executed. Unexpected status code: " + github_script.status_code
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
    update.message.reply_text(emo_g + " Shutting down...", reply_markup=ReplyKeyboardRemove())

    # See comments on the 'shutdown' function
    threading.Thread(target=shutdown).start()


# Restart this python script
@restrict_access
def restart_cmd(bot, update):
    update.message.reply_text(emo_w + " Bot is restarting...", reply_markup=ReplyKeyboardRemove())

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
    if update.message.text.upper() == "USER_ID":
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

    msg = " Save new value and restart bot?"
    update.message.reply_text(emo_q + msg, reply_markup=keyboard_confirm())

    return WorkflowEnum.SETTINGS_CONFIRM


# Confirm saving new setting and restart bot
def settings_confirm(bot, update, chat_data):
    if update.message.text.upper() == KeyboardEnum.NO.clean():
        return cancel(bot, update)

    # Set new value in config dictionary
    config[chat_data["setting"]] = chat_data["value"]

    # Save changed config as new one
    with open("config.json", "w") as cfg:
        json.dump(config, cfg, indent=4)

    update.message.reply_text(emo_f + " New value saved")

    # Restart bot to activate new setting
    restart_cmd(bot, update)


# Will show a cancel message, end the conversation and show the default keyboard
def cancel(bot, update):
    update.message.reply_text(emo_c + " Canceled...", reply_markup=keyboard_cmds())
    return ConversationHandler.END


# Check if GitHub hosts a different script then the currently running one
def get_update_state():
    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified (remote file has same hash, is the same version)
    if github_file.status_code == 304:
        msg = emo_t + " Bot is up to date"
    # Status code 200 = OK (remote file has different hash, is not the same version)
    elif github_file.status_code == 200:
        msg = emo_n + " New version available. Get it with /update"
    # Every other status code
    else:
        msg = emo_e + " Update check not possible. Unexpected status code: " + github_file.status_code

    return github_file.status_code, msg


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
        error = btfy(res_data["error"][0])
        log(logging.ERROR, error)
        if config["send_error"]:
            src = "Order state check:\n"
            updater.bot.send_message(chat_id=config["user_id"], text=src + emo_e + " " + error)
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
        msg = " Trade executed:\n" + job.context["order_txid"] + "\n" + trim_zeros(order_info["descr"]["order"])
        bot.send_message(chat_id=config["user_id"], text=bold(emo_n + msg), parse_mode=ParseMode.MARKDOWN)
        # Stop this job
        job.schedule_removal()


# Start periodical job to check if new bot version is available
def monitor_updates():
    if config["update_check"]:
        # Save time in seconds from config
        update_time = config["update_time"]

        # Check if current bot version is the latest
        def version_check(bot, job):
            status_code, msg = get_update_state()

            # Status code 200 means that the remote file is not the same
            if status_code == 200:
                msg = emo_n + " New version available. Get it with /update"
                bot.send_message(chat_id=config["user_id"], text=msg)

        # Add Job to JobQueue to run periodically
        job_queue.run_repeating(version_check, update_time, first=0)


# Monitor status changes of previously created open orders
def monitor_orders():
    if config["check_trade"]:
        # Send request for open orders to Kraken
        res_data = kraken_api("OpenOrders", private=True)

        # If Kraken replied with an error, show it
        if res_data["error"]:
            error = btfy(res_data["error"][0])
            log(logging.ERROR, error)
            if config["send_error"]:
                src = "Monitoring orders:\n"
                updater.bot.send_message(chat_id=config["user_id"], text=src + emo_e + " " + error)
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


# TODO: Complete sanity check
# Check sanity of settings in config file
def is_conf_sane():
    for setting, value in config.items():
        if "USER_ID" == setting.upper():
            if not value.isdigit():
                msg = " USER_ID has to be a number"
                updater.bot.send_message(config["user_id"], emo_e + msg)
                return False
        if "BASE_CURRENCY" == setting.upper():
            if not len(value) == 3:
                msg = " BASE_CURRENCY has to have a length of 3"
                updater.bot.send_message(config["user_id"], emo_e + msg)
                return False

    return True


# Show welcome message and custom keyboard for commands
def initialize():
    msg = " Preparing bot..."
    message = updater.bot.send_message(config["user_id"], emo_w + msg)

    res_assets = kraken_api("Assets")

    # If Kraken replied with an error, show it
    if res_assets["error"]:
        # TODO: If no reply, show button to restart.
        # TODO: If error, there can't be any possibility to show command keyboard! '/reload' must not be possible
        error = btfy(res_assets["error"][0])
        message.edit_text(config["user_id"], emo_e + " Preparing bot... FAILED")
        updater.bot.send_message(config["user_id"], error)
        log(logging.ERROR, error)
        return

    # Save assets in global variable
    global assets
    assets = res_assets["result"]

    # Find base currency name
    for asset, data in assets.items():
        if config["base_currency"] == data["altname"]:
            global base_currency
            base_currency = asset
            break

    # Edit last message
    message.edit_text(emo_d + " Preparing bot... DONE")

    msg = " Checking sanity..."
    message = updater.bot.send_message(config["user_id"], emo_w + msg)

    # Check sanity of configuration file
    if not is_conf_sane():
        message.edit_text(emo_d + " Checking sanity... FAILED")
        msg = "Config is not sane. Shut the bot down with /shutdown and adjust configuration"
        updater.bot.send_message(config["user_id"], msg, reply_markup=ReplyKeyboardRemove())
    # Sanity check finished successfully
    else:
        message.edit_text(emo_d + " Checking sanity... DONE")
        updater.bot.send_message(config["user_id"], emo_b + " Kraken-Bot is ready!", reply_markup=keyboard_cmds())


# Converts a Unix timestamp to a data-time object with format 'Y-m-d H:M:S'
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


# TODO: Test it
# Beautifies Kraken error messages
def btfy(text):
    # Remove whitespaces
    text = text.strip()

    new_text = str()

    for x in range(0, len(list(text))):
        new_text += list(text)[x]

        if list(text)[x] == ":":
            new_text += " "

    return emo_e + " " + new_text


# Return state of Kraken API
# State will be extracted from Kraken Status website
def api_state():
    response = requests.get("https://status.kraken.com")

    # If response code is not 200, return state 'UNKNOWN'
    if response.status_code != 200:
        return "UNKNOWN"

    soup = BeautifulSoup(response.content, "html.parser")

    for data in soup.find_all(class_="component-inner-container"):
        for data2 in data.find_all(class_="name"):
            if "API" in data2.get_text():
                return data.find(class_="component-status").get_text().strip()


# Returns a pre compiled Regex pattern to ignore case
def comp(pattern):
    return re.compile(pattern, re.IGNORECASE)


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


# Handle all telegram and telegram.ext related errors
def handle_telegram_error(bot, update, error):
    error_str = "Update '%s' caused error '%s'" % (update, error)
    log(logging.ERROR, error_str)

    if config["send_error"]:
        updater.bot.send_message(chat_id=config["user_id"], text=error_str)


# Log all errors
dispatcher.add_error_handler(handle_telegram_error)

# Add command handlers to dispatcher
dispatcher.add_handler(CommandHandler("update", update_cmd))
dispatcher.add_handler(CommandHandler("restart", restart_cmd))
dispatcher.add_handler(CommandHandler("shutdown", shutdown_cmd))
dispatcher.add_handler(CommandHandler("balance", balance_cmd))
dispatcher.add_handler(CommandHandler("reload", reload_cmd))
dispatcher.add_handler(CommandHandler("state", state_cmd))


# FUNDING conversation handler
funding_handler = ConversationHandler(
    entry_points=[CommandHandler('funding', funding_cmd)],
    states={
        WorkflowEnum.FUNDING_CURRENCY:
            [RegexHandler(comp("^(" + regex_coin_or() + ")$"), funding_currency, pass_chat_data=True),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        WorkflowEnum.FUNDING_CHOOSE:
            [RegexHandler(comp("^(DEPOSIT)$"), funding_deposit, pass_chat_data=True),
             RegexHandler(comp("^(WITHDRAW)$"), funding_withdraw),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        WorkflowEnum.WITHDRAW_WALLET:
            [MessageHandler(Filters.text, funding_withdraw_wallet, pass_chat_data=True)],
        WorkflowEnum.WITHDRAW_VOLUME:
            [MessageHandler(Filters.text, funding_withdraw_volume, pass_chat_data=True)],
        WorkflowEnum.WITHDRAW_CONFIRM:
            [RegexHandler(comp("^(YES|NO)$"), funding_withdraw_confirm, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(funding_handler)


# HISTORY conversation handler
history_handler = ConversationHandler(
    entry_points=[CommandHandler('history', history_cmd)],
    states={
        WorkflowEnum.HISTORY_NEXT:
            [RegexHandler(comp("^(NEXT)$"), history_next),
             RegexHandler(comp("^(CANCEL)$"), cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(history_handler)


# CHART conversation handler
chart_handler = ConversationHandler(
    entry_points=[CommandHandler('chart', chart_cmd)],
    states={
        WorkflowEnum.CHART_CURRENCY:
            [RegexHandler(comp("^(" + regex_coin_or() + ")$"), chart_currency),
             RegexHandler(comp("^(CANCEL)$"), cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(chart_handler)


# ORDERS conversation handler
orders_handler = ConversationHandler(
    entry_points=[CommandHandler('orders', orders_cmd)],
    states={
        WorkflowEnum.ORDERS_CLOSE:
            [RegexHandler(comp("^(CLOSE ORDER)$"), orders_choose_order),
             RegexHandler(comp("^(CLOSE ALL)$"), orders_close_all),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        WorkflowEnum.ORDERS_CLOSE_ORDER:
            [RegexHandler(comp("^(CANCEL)$"), cancel),
             RegexHandler(comp("^[A-Z0-9]{6}-[A-Z0-9]{5}-[A-Z0-9]{6}$"), orders_close_order)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(orders_handler)


# TRADE conversation handler
trade_handler = ConversationHandler(
    entry_points=[CommandHandler('trade', trade_cmd)],
    states={
        WorkflowEnum.TRADE_BUY_SELL:
            [RegexHandler(comp("^(BUY|SELL)$"), trade_buy_sell, pass_chat_data=True),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        WorkflowEnum.TRADE_CURRENCY:
            [RegexHandler(comp("^(" + regex_coin_or() + ")$"), trade_currency, pass_chat_data=True),
             RegexHandler(comp("^(CANCEL)$"), cancel),
             RegexHandler(comp("^(ALL)$"), trade_sell_all)],
        WorkflowEnum.TRADE_SELL_ALL_CONFIRM:
            [RegexHandler(comp("^(YES|NO)$"), trade_sell_all_confirm)],
        WorkflowEnum.TRADE_PRICE:
            [RegexHandler(comp("^((?=.*?\d)\d*[.]?\d*)$"), trade_price, pass_chat_data=True)],
        WorkflowEnum.TRADE_VOL_TYPE:
            [RegexHandler(comp("^(EUR|USD|CAD|GBP|JPY|KRW|VOLUME)$"), trade_vol_type, pass_chat_data=True),
             RegexHandler(comp("^(ALL)$"), trade_vol_type_all, pass_chat_data=True),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        WorkflowEnum.TRADE_VOLUME:
            [RegexHandler(comp("^((?=.*?\d)\d*[.]?\d*)$"), trade_volume, pass_chat_data=True)],
        WorkflowEnum.TRADE_CONFIRM:
            [RegexHandler(comp("^(YES|NO)$"), trade_confirm, pass_chat_data=True)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(trade_handler)


# PRICE conversation handler
price_handler = ConversationHandler(
    entry_points=[CommandHandler('price', price_cmd)],
    states={
        WorkflowEnum.PRICE_CURRENCY:
            [RegexHandler(comp("^(" + regex_coin_or() + ")$"), price_currency),
             RegexHandler(comp("^(CANCEL)$"), cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(price_handler)


# VALUE conversation handler
value_handler = ConversationHandler(
    entry_points=[CommandHandler('value', value_cmd)],
    states={
        WorkflowEnum.VALUE_CURRENCY:
            [RegexHandler(comp("^(" + regex_coin_or() + "|ALL)$"), value_currency),
             RegexHandler(comp("^(CANCEL)$"), cancel)]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(value_handler)


# Will return the SETTINGS_CHANGE state for a conversation handler
# This way the state is reusable
def settings_change_state():
    return [WorkflowEnum.SETTINGS_CHANGE,
            [RegexHandler(comp("^(" + regex_settings_or() + ")$"), settings_change, pass_chat_data=True),
             RegexHandler(comp("^(CANCEL)$"), cancel)]]


# Will return the SETTINGS_SAVE state for a conversation handler
# This way the state is reusable
def settings_save_state():
    return [WorkflowEnum.SETTINGS_SAVE,
            [MessageHandler(Filters.text, settings_save, pass_chat_data=True)]]


# Will return the SETTINGS_CONFIRM state for a conversation handler
# This way the state is reusable
def settings_confirm_state():
    return [WorkflowEnum.SETTINGS_CONFIRM,
            [RegexHandler(comp("^(YES|NO)$"), settings_confirm, pass_chat_data=True)]]


# BOT conversation handler
bot_handler = ConversationHandler(
    entry_points=[CommandHandler('bot', bot_cmd)],
    states={
        WorkflowEnum.BOT_SUB_CMD:
            [RegexHandler(comp("^(UPDATE CHECK|UPDATE|RESTART|SHUTDOWN)$"), bot_sub_cmd),
             RegexHandler(comp("^(API STATE)$"), state_cmd),
             RegexHandler(comp("^(SETTINGS)$"), settings_cmd),
             RegexHandler(comp("^(CANCEL)$"), cancel)],
        settings_change_state()[0]: settings_change_state()[1],
        settings_save_state()[0]: settings_save_state()[1],
        settings_confirm_state()[0]: settings_confirm_state()[1]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(bot_handler)


# SETTINGS conversation handler
settings_handler = ConversationHandler(
    entry_points=[CommandHandler('settings', settings_cmd)],
    states={
        settings_change_state()[0]: settings_change_state()[1],
        settings_save_state()[0]: settings_save_state()[1],
        settings_confirm_state()[0]: settings_confirm_state()[1]
    },
    fallbacks=[CommandHandler('cancel', cancel)]
)
dispatcher.add_handler(settings_handler)


# If webhook is enabled, don't use polling
# https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks
if config["webhook_enabled"]:
    updater.start_webhook(listen=config["webhook_listen"],
                          port=config["webhook_port"],
                          url_path=config["bot_token"],
                          key=config["webhook_key"],
                          cert=config["webhook_cert"],
                          webhook_url=config["webhook_url"])
else:
    # Start polling to handle all user input
    # Dismiss all in the meantime send commands
    updater.start_polling(clean=True)

# Show welcome-message, update-state and commands-keyboard
initialize()

# Check for new bot version periodically
monitor_updates()

# Monitor status changes of open orders
monitor_orders()

# Run the bot until you press Ctrl-C or the process receives SIGINT,
# SIGTERM or SIGABRT. This should be used most of the time, since
# start_polling() is non-blocking and will stop the bot gracefully.
updater.idle()
