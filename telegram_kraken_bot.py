#!/usr/bin/python3

import json
import logging
import os
import sys
import time

import krakenex
import requests
# from enum import Enum
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, Job, CallbackQueryHandler, ConversationHandler

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
# TODO: logger.debug("CHAT_ID: " + str(chat_id))

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

# Define Enum for callback hierarchy
ONE, TWO, THREE, FOUR = range(4)


# Add a custom keyboard with all available commands
def show_cmds():
    chat_id = get_chat_id()

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        updater.bot.send_message(chat_id, text="Access denied")
        return

    kraken_btn = [
        KeyboardButton("/trade", callback_data="trade"),
        KeyboardButton("/orders", callback_data="orders"),
        KeyboardButton("/balance", callback_data="balance"),
        KeyboardButton("/price", callback_data="price"),
        KeyboardButton("/value", callback_data="value"),
        KeyboardButton("/status", callback_data="status")
    ]

    markup = ReplyKeyboardMarkup(build_menu(kraken_btn, n_cols=3, header_buttons=None, footer_buttons=None))
    updater.bot.send_message(chat_id, "Enter a command", reply_markup=markup)


# Create a button menu to show in Telegram messages
def build_menu(buttons, n_cols=1, header_buttons=None, footer_buttons=None):
    menu = [buttons[i:i + n_cols] for i in range(0, len(buttons), n_cols)]

    if header_buttons:
        menu.insert(0, header_buttons)
    if footer_buttons:
        menu.append(footer_buttons)

    return menu


# Check order status and send message if changed
def monitor_order(bot, job):
    req_data = dict()
    req_data["txid"] = job.context["order_txid"]

    # Send request to get info on specific order
    res_data = kraken.query_private("QueryOrders", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(job.context["chat_id"], text=res_data["error"][0])
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
            updater.bot.send_message(chat_id=config["user_id"], text=res_data["error"][0])
            return

        # Get time in seconds from config
        check_trade_time = config["check_trade_time"]

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                order_txid = str(order)

                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=config["user_id"], order_txid=order_txid)

                # Create job to check status of order
                job_check_order = Job(monitor_order, check_trade_time, context=context_data)
                job_queue.put(job_check_order, next_t=0.0)


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
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # Command without arguments
    if len(msg_params) == 1:
        # Send request to Kraken to get current balance of all currencies
        res_data = kraken.query_private("Balance")

    # Command with argument 'available'
    elif len(msg_params) == 2 and msg_params[1] == "available":
        req_data = dict()
        req_data["asset"] = "Z" + config["trade_to_currency"]

        # Send request to Kraken to get current trade balance of all currencies
        res_data = kraken.query_private("TradeBalance", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    msg = ""

    # Check for '/trade available'
    # FIXME: Why does this show me the value of my XXBT coins?
    if "tb" in res_data["result"]:
        # tb = trade balance (combined balance of all equity currencies)
        msg = config["trade_to_currency"] + ": " + trim_zeros(res_data["result"]["tb"])
    else:
        for currency_key, currency_value in res_data["result"].items():
            msg += currency_key + ": " + trim_zeros(currency_value) + "\n"

    bot.send_message(chat_id, text=msg)


# Create orders to buy or sell currencies with price limit - choose 'buy' or 'sell'
def trade_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    buttons = [
        InlineKeyboardButton("buy", callback_data="buy"),
        InlineKeyboardButton("sell", callback_data="sell"),
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=footer))
    bot.send_message(chat_id, "BUY or SELL?", reply_markup=reply_markup)

    return ONE


# Callback for the 'trade' command - choose the currency
def trade_one(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    if data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return

    buttons = [
        InlineKeyboardButton("XXBT", callback_data="xxbt"),
        InlineKeyboardButton("XETH", callback_data="xeth"),
        InlineKeyboardButton("XXMR", callback_data="xxmr")
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=footer))
    bot.edit_message_text(data + "\nEnter currency to " + data, chat_id, message_id, reply_markup=reply_markup)

    return TWO


# Callback for the 'status' command - enter price per unit
def trade_two(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    if data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return

    buttons = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    line_brk_index = update.callback_query.message.index("\n")
    cmd = update.callback_query.message[0:line_brk_index]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons))
    bot.edit_message_text(
        cmd + " " + data + "\nEnter price per unit",
        chat_id,
        message_id,
        reply_markup=reply_markup)

    return THREE


# Callback for the 'status' command - enter volume
def trade_three(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    if data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return

    buttons = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons))
    bot.edit_message_text(data + "\Enter volume", chat_id, message_id, reply_markup=reply_markup)

    return THREE


# Callback for the 'status' command - enter volume
def trade_four(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    # TODO: Add logic to execute trade command on kraken

    bot.edit_message_text(data + "COMMAND COMPLETE", chat_id, message_id)

    return FOUR


# Show and manage orders
def orders_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # If there are no parameters, show all orders
    if len(msg_params) == 1:
        # Send request for open orders to Kraken
        res_data = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                order_desc = trim_zeros(res_data["result"]["open"][order]["descr"]["order"])
                bot.send_message(chat_id, text=order + "\n" + order_desc)
            return
        else:
            bot.send_message(chat_id, text="No open orders")
            return
    elif len(msg_params) == 2:
        # If parameter is 'close-all' then close all orders
        if msg_params[1] == "close-all":
            # Send request for open orders to Kraken
            res_data = kraken.query_private("OpenOrders")

            # If Kraken replied with an error, show it
            if res_data["error"]:
                bot.send_message(chat_id, text=res_data["error"][0])
                return

            if res_data["result"]["open"]:
                for order in res_data["result"]["open"]:
                    req_data = dict()
                    req_data["txid"] = order

                    # Send request to Kraken to cancel orders
                    res_data = kraken.query_private("CancelOrder", req_data)

                    # If Kraken replied with an error, show it
                    if res_data["error"]:
                        bot.send_message(chat_id, text=res_data["error"][0])
                        return

                    bot.send_message(chat_id, text="Order closed:\n" + order)
                return
            else:
                bot.send_message(chat_id, text="No open orders")
                return
        else:
            bot.send_message(chat_id, text="Syntax: /orders (['close'] [txid] / ['close-all'])")
            return
    elif len(msg_params) == 3:
        # If parameter is 'close' and TXID is provided, close order with specific TXID
        if msg_params[1] == "close":
            if len(msg_params) == 3:
                req_data = dict()
                req_data["txid"] = msg_params[2]

                # Send request to Kraken to cancel orders
                res_data = kraken.query_private("CancelOrder", req_data)

                # If Kraken replied with an error, show it
                if res_data["error"]:
                    bot.send_message(chat_id, text=res_data["error"][0])
                    return

                bot.send_message(chat_id, text="Order closed:\n" + msg_params[2])
                return
        else:
            bot.send_message(chat_id, text="Syntax: /orders (['close'] [txid] / ['close-all'])")
            return


# Show syntax for all available commands
def syntax_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    syntax_msg = "/balance (['available'])\n"
    syntax_msg += "/trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'eur'])\n"
    syntax_msg += "/orders (['close'] [txid] / 'close-all'])\n"
    syntax_msg += "/price [currency] ([currency] ...)\n"
    syntax_msg += "/value ([currency])\n"
    syntax_msg += "/update\n"
    syntax_msg += "/restart\n"
    syntax_msg += "/status"

    bot.send_message(chat_id, text=syntax_msg)


# TODO: Remove ReplyKeyboard as long as we are in the execution of a command. After complete execution, add it again
# Show the current price for the chosen currency
def price_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    buttons = [
        InlineKeyboardButton("XBT", callback_data="xbt"),
        InlineKeyboardButton("ETH", callback_data="eth"),
        InlineKeyboardButton("XMR", callback_data="xmr")
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=3, footer_buttons=footer))
    bot.send_message(chat_id, "Price for which currency?", reply_markup=reply_markup)

    return ONE


# Callback for the 'price' command - choose currency
def price_one(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    req_data = dict()

    # TODO: Create an Enum for this values
    if data == "xbt":
        req_data["pair"] = "XXBT" + "Z" + config["trade_to_currency"]
    elif data == "eth":
        req_data["pair"] = "XETH" + "Z" + config["trade_to_currency"]
    elif data == "xmr":
        req_data["pair"] = "XXMR" + "Z" + config["trade_to_currency"]
    elif data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return
    else:
        bot.edit_message_text("Unknown callback command...", chat_id, message_id)
        return

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken.query_public("Ticker", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    # TODO: It is not necessary to iterate over items - just get the one thing we need
    msg = ""
    for currency_key, currency_value in res_data["result"].items():
        # Get currency without 'trade to currency' value (for example 'ZEUR')
        currency = currency_key[1:-len("Z" + config["trade_to_currency"])]

        # Read last trade price and remove zeros at the end
        last_trade_price = trim_zeros(currency_value["c"][0])

        # Create message
        msg = currency + ": " + last_trade_price
        break

    bot.edit_message_text(message_id=message_id, chat_id=chat_id, text=msg)


# Show the current real money value for all assets combined
def value_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        bot.send_message(chat_id, text=res_data_balance["error"][0])
        return

    curr_str = "Overall: "

    req_data_price = dict()
    req_data_price["pair"] = ""

    for currency_name, currency_amount in res_data_balance["result"].items():
        if currency_name.endswith(config["trade_to_currency"]):
            continue

        if (len(msg_params) == 2) and (currency_name == msg_params[1].upper()):
            req_data_price["pair"] = currency_name + "Z" + config["trade_to_currency"] + ","
            curr_str = msg_params[1].upper() + ": "
            break

        req_data_price["pair"] += currency_name + "Z" + config["trade_to_currency"] + ","

    # Remove last comma from 'pair' string
    req_data_price["pair"] = req_data_price["pair"][:-1]

    # Send request to Kraken to get current trading price for currency-pair
    res_data_price = kraken.query_public("Ticker", req_data_price)

    # If Kraken replied with an error, show it
    if res_data_price["error"]:
        bot.send_message(chat_id, text=res_data_price["error"][0])
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

    bot.send_message(chat_id, text=curr_str + total_value_euro + " " + config["trade_to_currency"])


# Check if GitHub hosts a different script then the current one
def check_for_update():
    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified (remote file has same hash, is the same version)
    if github_file.status_code == 304:
        # Send message that bot is up to date
        msg = "Bot is up to date"
        updater.bot.send_message(chat_id=config["user_id"], text=msg)
    # Status code 200 = OK (remote file has different hash, is not the same version)
    elif github_file.status_code == 200:
        # Send message that new version is available
        msg = "New version available. Get it with /update"
        updater.bot.send_message(chat_id=config["user_id"], text=msg)
    # Every other status code
    else:
        msg = "Update check not possible. Unexpected status code: " + github_file.status_code
        updater.bot.send_message(chat_id=config["user_id"], text=msg)


# This command will give the user the possibility to check for an update, update or restart the bot
def status_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    buttons = [
        InlineKeyboardButton("Update Check", callback_data="update_check"),
        InlineKeyboardButton("Update", callback_data="update"),
        InlineKeyboardButton("Restart", callback_data="restart"),
        InlineKeyboardButton("Shutdown", callback_data="shutdown")
    ]

    footer = [
        InlineKeyboardButton("Cancel", callback_data="cancel")
    ]

    reply_markup = InlineKeyboardMarkup(build_menu(buttons, n_cols=2, footer_buttons=footer))
    bot.send_message(chat_id, "Choose an option", reply_markup=reply_markup)

    return ONE


# Callback for the 'status' command - choose a sub-command
def status_one(bot, update):
    chat_id = get_chat_id(update)
    message_id = update.callback_query.message.message_id

    data = update.callback_query.data

    # FIXME: This will not set a new message (on update). Exclude the message from the method and add a return value
    # then call this method and check return value. Show message dependant on return value.
    # TODO: Change in whole app: Only send msg to user from config, not user that send msg
    if data == "update_check":
        check_for_update()
    elif data == "update":
        update(bot, update)
    elif data == "restart":
        restart_cmd(bot, update)
    elif data == "shutdown":
        bot.edit_message_text("Shutting down...", chat_id, message_id)
        exit()  # TODO: Test this
    elif data == "cancel":
        bot.edit_message_text("Canceled...", chat_id, message_id)
        return
    else:
        bot.edit_message_text("Unknown callback command...", chat_id, message_id)
        return


# Download newest script, update the currently running script and restart
def update_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified
    if github_file.status_code == 304:
        msg = "You are running the latest version"
        updater.bot.send_message(chat_id=chat_id, text=msg)
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

        # Restart the bot
        restart_cmd(bot, update)
    # Every other status code
    else:
        msg = "Update not executed. Unexpected status code: " + github_file.status_code
        updater.bot.send_message(chat_id=chat_id, text=msg)


# Terminate this script
def shutdown_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    bot.send_message(chat_id, "Shutting down...")

    # Terminate bot
    exit()


# Restart this python script
def restart_cmd(bot, update):
    chat_id = get_chat_id(update)

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    bot.send_message(chat_id, "Bot is restarting...")
    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)


# Return chat ID for an Update object
def get_chat_id(update=None):
    if update:
        if update.message:
            return update.message.chat_id
        elif update.callback_query:
            return update.callback_query.from_user["id"]
    else:
        return config["user_id"]


# Add handlers to dispatcher
dispatcher.add_handler(CommandHandler("help", syntax_cmd))
dispatcher.add_handler(CommandHandler("balance", balance_cmd))
dispatcher.add_handler(CommandHandler("orders", orders_cmd))
dispatcher.add_handler(CommandHandler("value", value_cmd))
dispatcher.add_handler(CommandHandler("update", update_cmd))
dispatcher.add_handler(CommandHandler("restart", restart_cmd))
dispatcher.add_handler(CommandHandler("shutdown", shutdown_cmd))

# TRADE command handler
trade_handler = ConversationHandler(
    entry_points=[CommandHandler('trade', trade_cmd)],
    states={
        ONE: [CallbackQueryHandler(trade_one)],
        TWO: [CallbackQueryHandler(trade_two)],
        THREE: [CallbackQueryHandler(trade_three)]
    },
    fallbacks=[CommandHandler('trade', trade_cmd)]
)
dispatcher.add_handler(trade_handler)

# PRICE command handler
price_handler = ConversationHandler(
    entry_points=[CommandHandler('price', price_cmd)],
    states={
        ONE: [CallbackQueryHandler(price_one)]
    },
    fallbacks=[CommandHandler('price', price_cmd)]
)
dispatcher.add_handler(price_handler)

# STATUS command handler
status_handler = ConversationHandler(
    entry_points=[CommandHandler('status', status_cmd)],
    states={
        ONE: [CallbackQueryHandler(status_one)]
    },
    fallbacks=[CommandHandler('status', status_cmd)]
)
dispatcher.add_handler(status_handler)


# Start the bot
updater.start_polling()

# TODO: Sure that i don't need that?
#updater.idle()

# Check if script is the newest version
check_for_update()

# Monitor status changes of open orders
monitor_open_orders()

# Show all possible commands
show_cmds()
