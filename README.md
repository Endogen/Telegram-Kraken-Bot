# Telegram Kraken Bot
Python script to trade on Kraken via Telegram

## Installation
Install python packages

`pip install python-telegram-bot --upgrade`  
`pip install krakenex --upgrade`  
`pip install requests --upgrade`

## Configuration
Before executing the script, it's necessary to configure the bot. Open the file `config.json` and edit the settings

#### user_id
Your user ID. The bot will only reply to messages from this user. If you don't know your user ID, send a message to `userinfobot` and he will reply your ID

#### bot_token
The token of your bot. You will get this from 'BotFather' when you create your bot

#### password_needed
If you want to use the bot with a password, set this to `true`, otherwise to `false`

#### password_hash
Will be set automatically once you enable the password protection and set a new password. **Please don't change this**

#### confirm_action
If `true` the 'trade' command will ask if the entered data is correct and if you confirm it the new order will be created

#### trade_to_currency
The 'real-life' currency you are using (for example 'EUR')

#### check_trade
If `true` then every order (already existing or newly created) will be monitored by a job and if the status changes to `closed` (which means that the trade was successfully executed) then a message will be send

#### check_trade_time
Time in seconds to check for order status change (see also setting `check_trade`)

#### update_url
URL to the newest version of the bot itself. This is needed for the update functionality. Per default this points to my repository and if you don't have your own repo with some changes then you can use the default value

#### update_hash
Hash of the current version of the bot. **Please don't change this**. Will be set automatically when updating

## Usage
Run the script with

`python telegram_kraken_bot.py`