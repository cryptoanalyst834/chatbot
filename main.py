import os
import time
import logging
from datetime import datetime, UTC
import requests
import csv
import json
import threading
from dotenv import load_dotenv
from telebot import types, TeleBot
from fastapi import FastAPI, Request
import telebot

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Например: https://chatbot-production-cc5d.up.railway.app

# Настройки логирования
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename='logs/arbitrage.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

bot = TeleBot(TELEGRAM_TOKEN)
app = FastAPI()

# Класс бота
class BinanceArbitrageBot:
    def __init__(self):
        self.min_spread = 3.0
        self.fee = 0.001
        self.initial_deposit = 1000.0
        self.running = False
        self.btc_pairs = []
        self.user_chat_ids = set()
        self.log_folder = "logs"
        self.logger = self.setup_logger()
        self.register_handlers()

    def setup_logger(self):
        logger = logging.getLogger("ArbitrageBotLogger")
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(os.path.join(self.log_folder, "arbitrage.log"))
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        return logger

    def register_handlers(self):
        @bot.message_handler(commands=['start'])
        def start(message):
            chat_id = message.chat.id
            self.user_chat_ids.add(chat_id)
            bot.send_message(chat_id, "🔍 Анализ запущен.")
            if not self.running:
                self.running = True
                self.get_trade_pairs()
                thread = threading.Thread(target=self.run_analysis)
                thread.daemon = True
                thread.start()

        @bot.message_handler(commands=['stop'])
        def stop(message):
            chat_id = message.chat.id
            if chat_id in self.user_chat_ids:
                self.user_chat_ids.remove(chat_id)
                bot.send_message(chat_id, "⛔️ Вы отписаны от анализа.")
            if not self.user_chat_ids:
                self.running = False

        @bot.message_handler(commands=['status'])
        def status(message):
            status_text = "✅ Активен" if self.running else "❌ Остановлен"
            bot.send_message(
                message.chat.id,
                f"*📊 Статус:*\nАнализ: {status_text}\nМинимальный спред: `{self.min_spread}%`\n"
                f"Комиссия: `{self.fee*100}%`\nДепозит: `{self.initial_deposit} USDT`",
                parse_mode="Markdown"
            )

        @bot.message_handler(commands=['settings'])
        def settings(message):
            markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add("/download_report", "/stop")
            bot.send_message(
                message.chat.id,
                "⚙️ *Настройки*\nНажмите кнопку ниже, чтобы скачать отчет:",
                reply_markup=markup,
                parse_mode="Markdown"
            )

        @bot.message_handler(commands=['download_report'])
        def download(message):
            self.send_csv_report(message.chat.id)

    def get_prices(self):
        try:
            response = requests.get("https://api.binance.com/api/v3/ticker/price")
            response.raise_for_status()
            return {item['symbol']: float(item['price']) for item in response.json()}
        except Exception as e:
            self.logger.error(f"Ошибка получения цен: {e}")
            return {}

    def get_trade_pairs(self):
        try:
            response = requests.get("https://api.binance.com/api/v3/exchangeInfo")
            symbols = response.json()['symbols']
            btc_coins = set()
            usdt_coins = set()
            for s in symbols:
                if s['status'] != 'TRADING':
                    continue
                if s['quoteAsset'] == 'BTC':
                    btc_coins.add(s['baseAsset'])
                if s['quoteAsset'] == 'USDT':
                    usdt_coins.add(s['baseAsset'])
            self.btc_pairs = list(btc_coins & usdt_coins)
        except Exception as e:
            self.logger.error(f"Ошибка получения пар: {e}")

    def calculate_arbitrage(self, prices):
        opportunities = []
        btc_usdt = prices.get('BTCUSDT', 1)
        for coin in self.btc_pairs:
            usdt_pair = f"{coin}USDT"
            btc_pair = f"{coin}BTC"
            if usdt_pair not in prices or btc_pair not in prices:
                continue
            try:
                coins_bought = (self.initial_deposit / prices[usdt_pair]) * (1 - self.fee)
                btc_earned = coins_bought * prices[btc_pair] * (1 - self.fee)
                final_usdt = btc_earned * btc_usdt * (1 - self.fee)
                spread = (final_usdt / self.initial_deposit - 1) * 100
                if spread > self.min_spread:
                    opportunities.append({
                        "coin": coin,
                        "spread": round(spread, 2),
                        "profit": round(final_usdt - self.initial_deposit, 2)
                    })
            except Exception as e:
                self.logger.error(f"Ошибка для {coin}: {e}")
        return sorted(opportunities, key=lambda x: x["spread"], reverse=True)

    def log_opportunities(self, opportunities):
        if not opportunities:
            return
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(["Время", "Монета", "Спред, %", "Прибыль, USDT"])
            for opp in opportunities:
                writer.writerow([
                    datetime.now(UTC).isoformat(),
                    opp["coin"],
                    opp["spread"],
                    opp["profit"]
                ])

    def send_csv_report(self, chat_id):
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        if os.path.exists(csv_path):
            with open(csv_path, "rb") as f:
                bot.send_document(chat_id, f)
        else:
            bot.send_message(chat_id, "📊 Отчет пока не сформирован")

    def run_analysis(self):
        while self.running:
            try:
                prices = self.get_prices()
                if not prices:
                    time.sleep(60)
                    continue
                opportunities = self.calculate_arbitrage(prices)
                self.log_opportunities(opportunities)
                if opportunities:
                    text = "\n".join([f"{opp['coin']}: {opp['spread']}% ({opp['profit']} USDT)" for opp in opportunities])
                    for chat_id in self.user_chat_ids:
                        bot.send_message(chat_id, f"📈 *Найдены связки с прибылью:*\n{text}", parse_mode="Markdown")
            except Exception as e:
                self.logger.error(f"Ошибка в цикле анализа: {e}")
            time.sleep(60)

# Endpoint Webhook для Telegram
@app.post(f"/{TELEGRAM_TOKEN}")
async def webhook(request: Request):
    data = await request.json()
    update = telebot.types.Update.de_json(data)
    bot.process_new_updates([update])
    return {"ok": True}

# Инициализация и запуск
arbitrage_bot = BinanceArbitrageBot()

# Установка webhook (разово, или в startup)
import requests as r
set_hook_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={WEBHOOK_URL}/{TELEGRAM_TOKEN}"
r.get(set_hook_url)
