# Telegram Kraken Bot
Python bot to trade on Kraken via Telegram

## Overview
This script is a polling (not [webhook](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Webhooks)) based telegram bot. It can trade crypto-currencies on the [Kraken](http://kraken.com) marketplace and has a user friendly interface (custom keyboards with buttons).

### Features
- Bound to a specific user (only this user can use it)
- Integrated update mechanism (to latest version on GitHub)
- Sends message if order is closed (successfully sold / buyed)

## Configuration
Before starting up the bot you have to take care of some settings. You need to edit two files:

### config.json
This file holds the configuration for your bot. You have to at least edit the values for __user_id__ and __bot_token__.

- __user_id__ Your user ID. The bot will only reply to messages from this user. If you don't know your user ID, send a message to `userinfobot` and he will reply your ID

- __bot_token__ The token that identifies your bot. You will get this from 'BotFather' when you create your bot

- __password_needed__ (_currently not used_) If you want to use the bot with a password, set this to `true`, otherwise to `false`

- __password_hash__ (_currently not used_) Will be set automatically once you enable the password protection and set a new password. __Please don't change this__

- __trade\_to\_currency__ The 'real-life' currency you are using (for example `EUR`)

- __check_trade__ If `true` then every order (already existing or newly created) will be monitored by a job and if the status changes to `closed` (which means that the trade was successfully executed) then a message will be send

- __check\_trade\_time__ Time in seconds to check for order status change (see also setting `check_trade`)

- __update_url__ URL to the newest version of the bot itself. This is needed for the update functionality. Per default this points to my repository and if you don't have your own repo with some changes then you can use the default value

- __update_hash__ Hash of the current version of the bot. __Please don't change this__. Will be set automatically when updating

### kraken.key
This file holds two keys that are necessary in order to communicate with Kraken. Both keys have to be considered secret and you should be the only one that knows them. If you don't know where to get / how to gererate the keys:

1. Login to Kraken
2. Click on `Settings`
3. Click on `API`
4. Click on `Generate New Key`
5. Enter `Telegram-Kraken-Bot` in `Key Description`
6. Enter `4` in `Nonce Window`
7. Select all available permissions at `Key Permissions`
8. Click on `Generate Key`

When you have your Kraken API keys, open the file `kraken.key` and replace `some_api_key` (first line) with the value of `API Key` and `some_private_key` (second line) with the value of `Private Key`.

## Installation
In order to run the bot you need to execute the script `telegram_kraken_bot.py`. If you don't have any idea where to host it, take a look at [Where to host Telegram Bots](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Where-to-host-Telegram-Bots). I'm going to assume that you are running it on a Linux root server but you can also run the script locally on your computer for testing purposes.

### Prerequisites
You have to use __Python 3__ to execute the script and you need to install the following Python modules first:

`pip3 install python-telegram-bot --upgrade`  
`pip3 install krakenex --upgrade`  
`pip3 install requests --upgrade`

### Starting up
To start the script, execute `python3 telegram_kraken_bot.py &`. If you host your script on a remote server and you are accessing it via SSH, close the connection with `exit`.

## Usage
If you configured the bot correctly and execute the script, you should get a welcome message from the bot along with the information if you are using the latest version. There should also be a custom keyboard that shows you all the available commands. Click on a button to execute the command of type the command in directly.

### Available commands
##### Related to Kraken
- `/trade`: Starts a workflow that leads to the creation of a new order to buy or sell crypto-currencies
- `/orders`: Shows all open orders (buy or sell) and gives the possibility to close a specific order or close all orders
- `/balance`: Shows all your assets and also the volume available to trade - if there are open orders that block some of your assets
- `/price`: Returns the last trade price for the selected crypto-currency
- `/value`: Shows the current market value of the whole volume for the chosen crypto-currency (or for all assets) based on the last trade price
- `/bot`: Shows options to check for an update, update, restart and shutdown the bot

##### Related to the bot
- `/update`: Updates the bot to the latest version available on GitHub
- `/restart`: Restarts the bot
- `/shutdown`: Shuts the bot down

## Development
I know that it is unconventional to have the whole source code in just one file. At some point i should have been switching to object orientation but i kind of like the idea to have it all in just one file. This also makes the `/update` command much simpler :)

### Todo
[ ] Add password protection  
[ ] Add command `/stats` that shows statistics  
[ ] Add command `/history` that shows executed trades  
[ ] Add command `/chart` to show TradingView Chart Widget website  
[ ] Don't hardcode available crypto-currencies (after Kraken fixed it's API)  
[ ] Add option to auto-update (with custom update-check-time)  

### Known bugs
- Background jobs that check order state do not send messages if `updater.idle()` is present
- Command `/shutdown` doesn't fully shutdown the bot