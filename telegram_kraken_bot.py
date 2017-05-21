# TODO: Add logging
# TODO: Add exception handling

import json

import krakenex
from telegram.ext import Updater, CommandHandler

# Read configuration
with open("config.json") as configFile:
    data = json.load(configFile)

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# Set bot token
updater = Updater(token=data["bot_token"])
dispatcher = updater.dispatcher


# Check if Telegram user is valid
def valid_user(update):
    user_name = update.message.from_user.username
    if user_name == data["allowed_user"]:
        return True
    else:
        return False


# Get balance of all currencies
def balance(bot, update):
    chat_id = update.message.chat_id

    # Check if user if valid for this action
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    res_data = kraken.query_private("Balance")

    msg = ""
    for primary_key, primary_value in res_data.items():
        if (primary_key == "error") and (len(primary_value)):
            for list_value in primary_value:
                msg += list_value + "\n"
            break

        if primary_key == "result":
            for currency_key, currency_value in primary_value.items():
                msg += currency_key + ": " + currency_value + "\n"
            break

    bot.send_message(chat_id, text=msg)


# Create orders to buy or sell currencies with price limit
def trade(bot, update):
    chat_id = update.message.chat_id

    # Check if user if valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    if len(msg_params) == 1:
        syntax = "Syntax: /trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'€'])"
        bot.send_message(chat_id, text=syntax)
        return

    # Send request to Kraken to get euro balance to calculate volume
    res_data = kraken.query_private("Balance")
    euros = res_data["result"]["ZEUR"]

    # Calculate volume depending on full euro balance and round it to 8 digits
    volume = "{0:.8f}".format(float(euros) / float(msg_params[3]))

    # TODO: Implement using entered volume / amount

    req_data = dict()
    req_data["type"] = msg_params[1]
    req_data["pair"] = msg_params[2] + "ZEUR"
    req_data["price"] = msg_params[3]
    req_data["ordertype"] = "limit"
    req_data["volume"] = volume

    # Send request to create order to Kraken
    res_data = kraken.query_private("AddOrder", req_data)

    # If Kraken replied with an error, show it
    if res_data["error"]:
        bot.send_message(chat_id, text=res_data["error"][0])
        return

    # If there is a transaction id, order was placed successfully
    if res_data["result"]["txid"]:
        bot.send_message(chat_id, text="Order placed")
        return

    bot.send_message(chat_id, text="Undefined state: no error and no txid")


def orders(bot, update):
    chat_id = update.message.chat_id

    # Check if user if valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    # Save message parameters in list
    msg_params = update.message.text.split(" ")

    # If there are no parameters, show all orders
    if len(msg_params) == 1:
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
        res_data = kraken.query_private("OpenOrders")

        # If Kraken replied with an error, show it
        if res_data["error"]:
            bot.send_message(chat_id, text=res_data["error"][0])
            return

        if res_data["result"]["open"]:
            for order in res_data["result"]["open"]:
                req_data = dict()
                req_data["txid"] = order

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

    # If parameter is 'close' and txid is provided, close order with txid
    if msg_params[1] == "close":
        if msg_params[2]:
            req_data = dict()
            req_data["txid"] = msg_params[2]

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


def help(bot, update):
    chat_id = update.message.chat_id

    # Check if user if valid
    if not valid_user(update):
        bot.send_message(chat_id, text="Wrong user!")
        return

    syntax_msg = "/balance\n\n"
    syntax_msg += "/trade ['buy' / 'sell'] [currency] [price per unit] ([volume] / [amount'€'])\n\n"
    syntax_msg += "/orders\n\n"
    syntax_msg += "/orders ['close'] [txid]\n\n"
    syntax_msg += "/orders ['close-all']"

    bot.send_message(chat_id, text=syntax_msg)

# Create message and command handlers
helpHandler = CommandHandler("help", help)
balanceHandler = CommandHandler("balance", balance)
tradeHandler = CommandHandler("trade", trade)
ordersHandler = CommandHandler("orders", orders)

# Add handlers to dispatcher
dispatcher.add_handler(helpHandler)
dispatcher.add_handler(balanceHandler)
dispatcher.add_handler(tradeHandler)
dispatcher.add_handler(ordersHandler)

updater.start_polling()
