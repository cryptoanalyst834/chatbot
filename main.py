import os
import csv
import json
import time
import logging
import requests
import threading
from datetime import datetime, UTC
from dotenv import load_dotenv
from flask import Flask, request
import telebot
from telebot import types

# Загрузка .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например: https://chatbot-production-xxx.up.railway.app
SECRET_PATH = TELEGRAM_TOKEN.split(":")[0]

# Flask
app = Flask(__name__)
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# Логирование
os.makedirs("logs", exist_ok=True)
logging.basicConfig(filename='logs/arbitrage.log', level=logging.INFO)

class BinanceArbitrageBot:
    def __init__(self, min_spread=3.0, fee=0.001, initial_deposit=1000.0):
        self.min_spread = min_spread
        self.fee = fee
        self.initial_deposit = initial_deposit
        self.running = False
        self.btc_pairs = []
        self.user_chat_ids = set()
        self.log_folder = "logs"
        self.thread = None

        # Регистрация команд
        bot.register_message_handler(self.start_analysis, commands=['start'])
        bot.register_message_handler(self.stop_analysis, commands=['stop'])
        bot.register_message_handler(self.send_status, commands=['status'])
        bot.register_message_handler(self.show_settings, commands=['settings'])
        bot.register_message_handler(self.download_report, commands=['download_report'])

    def get_prices(self):
        try:
            r = requests.get("https://api.binance.com/api/v3/ticker/price")
            r.raise_for_status()
            return {item['symbol']: float(item['price']) for item in r.json()}
        except Exception as e:
            logging.error(f"Ошибка получения цен: {e}")
            return {}

    def get_trade_pairs(self):
        try:
            r = requests.get("https://api.binance.com/api/v3/exchangeInfo")
            symbols = r.json()['symbols']
            btc = {s['baseAsset'] for s in symbols if s['quoteAsset'] == 'BTC' and
