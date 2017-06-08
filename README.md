# Telegram Kraken Bot
Python script to trade on Kraken via Telegram

## Usage
Install python packages

`pip install python-telegram-bot --upgrade`  
`pip install krakenex --upgrade`  

Then simply run the script

## TODO
- Add password protections for actions:
    - Possibility 1 - Login, do whatever you like as often as you like, logout
    - Possibility 2 - execute command, bot shows "Enter password", user enters password, command is executed
- Add confirmation for order creation / cancel: Ask if data is correct, if user enters 'y', command is executed
