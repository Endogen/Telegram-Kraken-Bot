# TODO: How does sending the password and the secret work? Is it secure right now?
# TODO: Add logging

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
import krakenex
import json

# Connect to Kraken
kraken = krakenex.API()
kraken.load_key("kraken.key")

# Read configuration
with open("config.json") as configFile:
    data = json.load(configFile)


# Check if Telegram user is valid
def valid_user(update):
    user_name = update.message.from_user.username
    if user_name == data["allowed_user"]:
        return True
    else:
        return False

updater = Updater(token=data["bot_token"])
dispatcher = updater.dispatcher


def echo(bot, update):
    bot.send_message(chat_id=update.message.chat_id, text=update.message.text)


def balance(bot, update):
    chat_id = update.message.chat_id

    if valid_user(update):
        balance_data = kraken.query_private("Balance")

        msg = ""
        for primary_key, primary_value in balance_data.items():
            if (primary_key == "error") and (len(primary_value)):
                msg = "ERROR: "
                for list_value in primary_value:
                    msg += list_value + ", "
                break

            if primary_key == "result":
                for currency_key, currency_value in primary_value.items():
                    msg += currency_key + ": " + currency_value + "\n"
                break

        bot.send_message(chat_id, text=msg)

    else:
        bot.send_message(chat_id, text="Wrong user!")

echoHandler = MessageHandler(Filters.text, echo)
balanceHandler = CommandHandler("balance", balance)

dispatcher.add_handler(echoHandler)
dispatcher.add_handler(balanceHandler)

updater.start_polling()
