import os
import time
import logging
from datetime import datetime, UTC
import telebot
import requests
import csv
import json
import threading
from dotenv import load_dotenv
from telebot import types

# Загрузка .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # новый параметр

bot = telebot.TeleBot(TELEGRAM_TOKEN)

class BinanceArbitrageBot:
    def __init__(self, min_spread=3.0, fee=0.001, initial_deposit=1000.0):
        self.min_spread = min_spread
        self.fee = fee
        self.initial_deposit = initial_deposit
        self.running = False
        self.btc_pairs = []
        self.user_chat_ids = set()
        self.log_folder = "logs"
        os.makedirs(self.log_folder, exist_ok=True)

        logging.basicConfig(
            filename='logs/arbitrage.log',
            level=logging.INFO,
            format='%(asctime)s - %(message)s'
        )
        self.logger = logging.getLogger("ArbitrageBotLogger")

        # Команды
        bot.message_handler(commands=['start'])(self.start_analysis)
        bot.message_handler(commands=['stop'])(self.stop_analysis)
        bot.message_handler(commands=['status'])(self.send_status)
        bot.message_handler(commands=['settings'])(self.show_settings)
        bot.message_handler(commands=['download_report'])(self.download_report)

    # методы... (оставь все как в твоем коде выше, без polling, только send_csv_report и другие)

    def run(self):
        bot.remove_webhook()
        time.sleep(1)
        bot.set_webhook(url=WEBHOOK_URL)
        print(f"Webhook установлен: {WEBHOOK_URL}")

if __name__ == "__main__":
    app = BinanceArbitrageBot()
    app.run()
