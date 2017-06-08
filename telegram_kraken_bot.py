# TODO: Add logging
# TODO: Add exception handling
# TODO: Change value in config from 'ZEUR' to 'EUR'?
# TODO: Always the same behaviour for commands without params?
# TODO: Remove 'help' command, instead check for every command if argument 'help' is present, if yes show syntax
# TODO: Implement password protection
# TODO: Show 'XBT' to user instead of 'XXBT'
# TODO: 'calc' to calculate possible win if sold for INPUT - or just integrate this into the confirmation of 'trade'

import json
import krakenex
import logging
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

# FIXME: Do we need this variable or do we read it from config 'trade_to_currency'?
euro_str = "EUR"


# Check for newly closed orders and send message if trade happened
def check_order(bot, job):
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

    # Check if trade is executed
    if order_info["status"] == "closed":
        msg = "Trade executed:\n" + job.context["order_txid"] + "\n" + order_info["descr"]["order"]
        bot.send_message(chat_id=job.context["chat_id"], text=msg)
        # Stop this job
        job.schedule_removal()
    elif order_info["status"] == "canceled":
        # Stop this job
        job.schedule_removal()


# Check if Telegram user is valid
def valid_user(update):
    user_name = update.message.from_user.username
    if user_name == config["allowed_user"]:
        return True
    else:
        return False


# Remove trailing zeros to get clean values
def trim_value(value_to_trim):
    if isinstance(value_to_trim, float):
        return ('%.8f' % value_to_trim).rstrip('0').rstrip('.')
    elif isinstance(value_to_trim, str):
        return ('%.8f' % float(value_to_trim)).rstrip('0').rstrip('.')


# Get balance of all currencies
# TODO: Should have the option to print EUR that is available to trade (if already order placed): '/balance available'?
def balance(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid for this action
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # Send request to Kraken to get current balance of all currencies
    res_data = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    msg = ""
    for currency_key, currency_value in res_data["result"].items():
        msg += currency_key + ": " + currency_value + "\n"

    bot.send_message(chat_id, text=msg)


# Create orders to buy or sell currencies with price limit
def trade(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # No arguments entered, just the '/trade' command
    if len(msg_params) == 1:
        syntax = "Syntax: /trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'€'])"
        bot.send_message(chat_id, text=syntax)
        return

    # Volume is specified
    if len(msg_params) == 5:
        if msg_params[4].upper().endswith(euro_str):
            volume = "{0:.8f}".format(float(msg_params[4][:-len(euro_str)]) / float(msg_params[3]))
        else:
            volume = msg_params[4]
    # Volume is NOT specified
    else:
        # Send request to Kraken to get euro balance to calculate volume
        res_data = kraken.query_private("Balance")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return

        # Logic for 'buy'
        if msg_params[1] == "buy":
            # FIXME: Only get euros that are not blocked by open orders!
            euros = res_data["result"][config["trade_to_currency"]]
            # Calculate volume depending on full euro balance and round it to 8 digits
            volume = "{0:.8f}".format(float(euros) / float(msg_params[3]))
        # Logic for 'sell'
        elif msg_params[1] == "sell":
            current_volume = res_data["result"][msg_params[2].upper()]
            # Get volume from balance and round it to 8 digits
            volume = "{0:.8f}".format(float(current_volume))
        else:
            bot.send_message(chat_id, text="Argument should be 'buy' or 'sell' but is '" + msg_params[1] + "'")
            return

    req_data = dict()
    req_data["type"] = msg_params[1]
    req_data["pair"] = msg_params[2] + config["trade_to_currency"]
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
    if res_data_add_order["result"]["txid"]:
        add_order_txid = res_data_add_order["result"]["txid"][0]

        req_data = dict()
        req_data["txid"] = add_order_txid

        # Send request to get info on specific order
        res_data_query_order = kraken.query_private("QueryOrders", req_data)

        # If Kraken replied with an error, show it
        if res_data_query_order["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return

        if res_data_query_order["result"][add_order_txid]:
            order_desc = res_data_query_order["result"][add_order_txid]["descr"]["order"]
            bot.send_message(chat_id, text="Order placed:\n" + add_order_txid + "\n" + order_desc)

            if config["check_trade"].lower() == "true":
                # Get time in seconds from config
                check_trade_time = config["check_trade_time"]
                # Create context object with chat ID and order TXID
                context_data = dict(chat_id=update.message.chat_id, order_txid=add_order_txid)

                # Create job to check status of newly created order
                job_check_order = Job(check_order, check_trade_time, context=context_data)
                job_queue.put(job_check_order, next_t=0.0)
            return
        else:
            bot.send_message(chat_id, text="No open orders")
            return
    else:
        bot.send_message(chat_id, text="Undefined state: no error and no txid")


# Show and manage orders
def orders(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
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
                order_desc = res_data["result"]["open"][order]["descr"]["order"]
                bot.send_message(chat_id, text=order + "\n" + order_desc)
            return
        else:
            bot.send_message(chat_id, text="No open orders")
            return

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

    # If parameter is 'close' and TXID is provided, close order with specific TXID
    if msg_params[1] == "close":
        if msg_params[2]:
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
            bot.send_message(chat_id, text="Syntax: /orders ['close'] [txid]")
            return


# Show syntax for all available commands
def help(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    syntax_msg = "/balance\n\n"
    syntax_msg += "/trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'€'])\n\n"
    syntax_msg += "/orders\n\n"
    syntax_msg += "/orders ['close'] [txid]\n\n"
    syntax_msg += "/orders ['close-all']\n\n"
    syntax_msg += "/price [currency] ([currency] ...)"

    bot.send_message(chat_id, text=syntax_msg)


# Show last trade price for given currency
def price(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # FIXME: Check if there are additional params. If not, show syntax help

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    req_data = dict()
    req_data["pair"] = ""

    # Loop over all parameters (except first) and add them as currencies to request
    first = True
    for param in msg_params:
        if first:
            first = False
        else:
            req_data["pair"] += param + config["trade_to_currency"] + ","

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
        currency = currency_key[:-len(config["trade_to_currency"])]
        # Read last trade price
        last_trade_price = currency_value["c"][0]

        # Remove zeros at the end
        last_trade_price = trim_value(last_trade_price)

        #  Add currency to price
        last_trade_price += " " + config["trade_to_currency"][1:]

        # Create message
        msg += currency + ": " + last_trade_price + "\n"

    bot.send_message(chat_id, text=msg)


# Show the current real money value for all assets combined
def value(bot, update):
    chat_id = update.message.chat_id

    # Check if user is valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # Send request to Kraken to get current balance of all currencies
    res_data_balance = kraken.query_private("Balance")

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        bot.send_message(chat_id, text=res_data_balance["error"][0])
        return

    req_data_price = dict()
    req_data_price["pair"] = ""

    for currency_name, currency_amount in res_data_balance["result"].items():
        if currency_name == config["trade_to_currency"]:
            continue

        req_data_price["pair"] += currency_name + config["trade_to_currency"] + ","

    # Remove last comma from 'pair' string
    req_data_price["pair"] = req_data_price["pair"][:-1]

    # Send request to Kraken to get current trading price for currency-pair
    res_data_price = kraken.query_public("Ticker", req_data_price)

    # If Kraken replied with an error, show it
    if res_data_balance["error"]:
        bot.send_message(chat_id, text=res_data_balance["error"][0])
        return

    total_value_euro = float(0)

    for currency_pair_name, currency_price in res_data_price["result"].items():
        # Remove trade-to-currency from currency pair to get the pure currency
        currency_without_pair = currency_pair_name[:-len(config["trade_to_currency"])]
        currency_balance = res_data_balance["result"][currency_without_pair]

        # Calculate total value by multiplying currency asset with last trade price
        total_value_euro += float(currency_balance) * float(currency_price["c"][0])

    # Show only 2 digits after decimal place
    total_value_euro = "{0:.2f}".format(total_value_euro)

    bot.send_message(chat_id, text=total_value_euro + " " + euro_str)

# Create message and command handlers
helpHandler = CommandHandler("help", help)
balanceHandler = CommandHandler("balance", balance)
tradeHandler = CommandHandler("trade", trade)
ordersHandler = CommandHandler("orders", orders)
priceHandler = CommandHandler("price", price)
valueHandler = CommandHandler("value", value)

# Add handlers to dispatcher
dispatcher.add_handler(helpHandler)
dispatcher.add_handler(balanceHandler)
dispatcher.add_handler(tradeHandler)
dispatcher.add_handler(ordersHandler)
dispatcher.add_handler(priceHandler)
dispatcher.add_handler(valueHandler)

updater.start_polling()
