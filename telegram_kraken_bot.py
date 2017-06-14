# TODO: Add logging
# TODO: Remove 'help' command, instead check for every command if argument 'help' is present, if yes show syntax
# TODO: Show 'XBT' to user instead of 'XXBT'
# TODO: 'calc' to calculate possible win if sold for INPUT - or integrate into confirmation of 'trade'
# TODO: Change 'msg_params[]' into dictionaries
# TODO: When using chat after long time, first message doesn't work - maybe because i ues a different client?
# TODO: Add possibility to start job for every command so that command gets executed periodically
# TODO: Add dynamic buttons: https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets#usage-1
# TODO: Take a look at code snippets: https://github.com/python-telegram-bot/python-telegram-bot/wiki/Code-snippets
# TODO: - Add password protections for actions:
# TODO:     - Possibility 1 - Login, do whatever you like as often as you like, logout
# TODO:     - Possibility 2 - execute command, bot shows "Enter password", user enters password, command is executed
# TODO: - Add confirmation for order creation / cancel: Ask if data is correct, if user enters 'y', command is executed
# TODO: Add interactive mode? Add button for every command and the guide user through entering value (one after one)

import json
import krakenex
import logging
import os
import time
import sys
import requests
from telegram.ext import Updater, CommandHandler, Job

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger()
# TODO: Usage: logger.debug("CHAT_ID: " + str(chat_id))

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
def balance(bot, update):
    chat_id = update.message.chat_id

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


# Create orders to buy or sell currencies with price limit
def trade(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # No arguments entered, just the '/trade' command
    if len(msg_params) == 1:
        syntax = "Syntax: /trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'eur'])"
        bot.send_message(chat_id, text=syntax)
        return

    # Volume is specified
    if len(msg_params) == 5:
        if msg_params[4].upper().endswith(config["trade_to_currency"]):
            amount = float(msg_params[4][:-len(config["trade_to_currency"])])
            price_per_unit = float(msg_params[3])
            volume = "{0:.8f}".format(amount / price_per_unit)
        else:
            volume = msg_params[4]
    # Volume is NOT specified
    elif len(msg_params) == 4:
        buy = "buy"
        sell = "sell"

        # Logic for 'buy'
        if msg_params[1] == buy:
            req_data = dict()
            req_data["asset"] = "Z" + config["trade_to_currency"]

            # Send request to Kraken to get current trade balance of all currencies
            res_data = kraken.query_private("TradeBalance", req_data)

            # If Kraken replied with an error, show it
            if res_data["error"]:
                bot.send_message(chat_id, text=res_data["error"][0])
                return

            euros = res_data["result"]["tb"]
            # Calculate volume depending on full euro balance and round it to 8 digits
            volume = "{0:.8f}".format(float(euros) / float(msg_params[3]))
        # Logic for 'sell'
        elif msg_params[1] == "sell":

            # Send request to Kraken to get euro balance to calculate volume
            res_data = kraken.query_private("Balance")

            # If Kraken replied with an error, show it
            if res_data["error"]:
                bot.send_message(chat_id, text=res_data["error"][0])
                return

            current_volume = res_data["result"][msg_params[2].upper()]
            # Get volume from balance and round it to 8 digits
            volume = "{0:.8f}".format(float(current_volume))
        else:
            msg = "Argument should be '" + buy + "' or '" + sell + "' but is '" + msg_params[1] + "'"
            bot.send_message(chat_id, text=msg)
            return
    else:
        syntax = "Syntax: /trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'eur'])"
        bot.send_message(chat_id, text=syntax)
        return

    req_data = dict()
    req_data["type"] = msg_params[1]
    req_data["pair"] = msg_params[2] + "Z" + config["trade_to_currency"]
    req_data["price"] = msg_params[3]
    req_data["ordertype"] = "limit"
    req_data["volume"] = volume

    # Send request to create order to Kraken
    res_data_add_order = kraken.query_private("AddOrder", req_data)

    # If Kraken replied with an error, show it
    if res_data_add_order["error"]:
        bot.send_message(chat_id, text=res_data_add_order["error"][0])
        return

    # If there is a transaction id then the order was placed successfully
    # FIXME: Am i sure that this check is working? Try 'if es_data_add_order["result"]["txid"] is not None:'
    if res_data_add_order["result"]["txid"]:
        add_order_txid = res_data_add_order["result"]["txid"][0]

        req_data = dict()
        req_data["txid"] = add_order_txid

        # Send request to get info on specific order
        res_data_query_order = kraken.query_private("QueryOrders", req_data)

        # If Kraken replied with an error, show it
        # FIXME: Am i sure that this check is working?
        if res_data_query_order["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return
        # FIXME: Am i sure that this check is working?
        if res_data_query_order["result"][add_order_txid]:
            order_desc = res_data_query_order["result"][add_order_txid]["descr"]["order"]
            bot.send_message(chat_id, text="Order placed: " + add_order_txid + "\n" + trim_zeros(order_desc))

            if config["check_trade"].lower() == "true":
                # Get time in seconds from config
                check_trade_time = config["check_trade_time"]
                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=update.message.chat_id, order_txid=add_order_txid)

                # Create job to check status of newly created order
                job_check_order = Job(monitor_order, check_trade_time, context=context_data)
                job_queue.put(job_check_order, next_t=0.0)
            return
        else:
            bot.send_message(chat_id, text="No order with TXID " + add_order_txid)
            return
    else:
        bot.send_message(chat_id, text="Undefined state: no error and no TXID")


# Show and manage orders
def orders(bot, update):
    chat_id = update.message.chat_id

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
def syntax(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    syntax_msg = "/balance (['available'])\n\n"
    syntax_msg += "/trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'eur'])\n\n"
    syntax_msg += "/orders (['close'] [txid] / 'close-all'])\n\n"
    syntax_msg += "/price [currency] ([currency] ...)\n\n"
    syntax_msg += "/value ([currency])\n\n"
    syntax_msg += "/update"

    bot.send_message(chat_id, text=syntax_msg)


# Show last trade price for given currency
def price(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    if len(msg_params) == 1:
        bot.send_message(chat_id, text="Syntax: /price [currency] ([currency] ...)")
        return

    req_data = dict()
    req_data["pair"] = ""

    # Loop over all parameters (except first) and add them as currencies to request
    first = True
    for param in msg_params:
        if first:
            first = False
        else:
            req_data["pair"] += param + "Z" + config["trade_to_currency"] + ","

    # Remove last comma from 'pair' string
    req_data["pair"] = req_data["pair"][:-1]

    # Send request to Kraken to get current trading price for currency-pair
    res_data = kraken.query_public("Ticker", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    msg = ""
    for currency_key, currency_value in res_data["result"].items():
        # Set currency without 'trade to currency' value (for example 'ZEUR')
        currency = currency_key[:-len("Z" + config["trade_to_currency"])]
        # Read last trade price
        last_trade_price = currency_value["c"][0]

        # Remove zeros at the end
        last_trade_price = trim_zeros(last_trade_price)

        #  Add currency to price
        last_trade_price += " " + config["trade_to_currency"]

        # Create message
        msg += currency + ": " + last_trade_price + "\n"

    bot.send_message(chat_id, text=msg)


# Show the current real money value for all assets combined
def value(bot, update):
    chat_id = update.message.chat_id

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

    # Status code 304 = Not Modified
    if github_file.status_code != 304:
        # Send message that new version is available
        msg = "New version available. Get it with /update"
        updater.bot.send_message(chat_id=config["user_id"], text=msg)


# Download newest script and update the currently running script with it and restart
def update_bot(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    # Get newest version of this script from GitHub
    headers = {"If-None-Match": config["update_hash"]}
    github_file = requests.get(config["update_url"], headers=headers)

    # Status code 304 = Not Modified
    if github_file.status_code == 304:
        msg = "You are running the latest version of the bot"
        updater.bot.send_message(chat_id=chat_id, text=msg)
    # Status code 200 = OK
    elif github_file.status_code == 200:
        # Save current ETag (hash) in configuration file
        with open("config.json", "w") as cfg:
            e_tag = github_file.headers.get("ETag")
            config["update_hash"] = e_tag
            json.dump(config, cfg)

        # Save the content of the remote file
        with open(os.path.basename(__file__), "w") as file:
            file.write(github_file.text)

        # Restart the bot
        restart_bot(bot, update)
        update_bot(bot, update)
    # Every other status code
    else:
        msg = "Update not executed. Request delivered unexpected status code: " + github_file.status_code
        updater.bot.send_message(chat_id=chat_id, text=msg)


# Restart this python script
def restart_bot(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if str(chat_id) != config["user_id"]:
        bot.send_message(chat_id, text="Access denied")
        return

    bot.send_message(chat_id, "Bot is restarting...")
    time.sleep(0.2)
    os.execl(sys.executable, sys.executable, *sys.argv)

# Create message and command handlers
helpHandler = CommandHandler("help", syntax)
balanceHandler = CommandHandler("balance", balance)
tradeHandler = CommandHandler("trade", trade)
ordersHandler = CommandHandler("orders", orders)
priceHandler = CommandHandler("price", price)
valueHandler = CommandHandler("value", value)
updateHandler = CommandHandler("update", update_bot)
restartHandler = CommandHandler("restart", restart_bot)

# Add handlers to dispatcher
dispatcher.add_handler(helpHandler)
dispatcher.add_handler(balanceHandler)
dispatcher.add_handler(tradeHandler)
dispatcher.add_handler(ordersHandler)
dispatcher.add_handler(priceHandler)
dispatcher.add_handler(valueHandler)
dispatcher.add_handler(updateHandler)
dispatcher.add_handler(restartHandler)

# Start the bot
updater.start_polling()

# Check if script is the newest version
check_for_update()

# Monitor status changes of open orders
monitor_open_orders()
