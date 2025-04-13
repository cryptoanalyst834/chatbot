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
            btc = {s['baseAsset'] for s in symbols if s['quoteAsset'] == 'BTC' and s['status'] == 'TRADING'}
            usdt = {s['baseAsset'] for s in symbols if s['quoteAsset'] == 'USDT' and s['status'] == 'TRADING'}
            self.btc_pairs = list(btc & usdt)
        except Exception as e:
            logging.error(f"Ошибка получения пар: {e}")

    def calculate_arbitrage(self, prices):
        opps = []
        btc_usdt = prices.get('BTCUSDT', 1)
        for coin in self.btc_pairs:
            usdt_pair, btc_pair = f"{coin}USDT", f"{coin}BTC"
            if usdt_pair in prices and btc_pair in prices:
                try:
                    coins = (self.initial_deposit / prices[usdt_pair]) * (1 - self.fee)
                    btc = coins * prices[btc_pair] * (1 - self.fee)
                    final = btc * btc_usdt * (1 - self.fee)
                    spread = (final / self.initial_deposit - 1) * 100
                    if spread > self.min_spread:
                        opps.append({
                            "coin": coin,
                            "spread": round(spread, 2),
                            "profit": round(final - self.initial_deposit, 2)
                        })
                except Exception as e:
                    logging.warning(f"{coin}: {e}")
        return sorted(opps, key=lambda x: x["spread"], reverse=True)

    def log_opportunities(self, opportunities):
        if not opportunities:
            return
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        json_path = os.path.join(self.log_folder, f"{date}.json")

        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(["Время", "Монета", "Спред, %", "Прибыль, USDT"])
            for o in opportunities:
                writer.writerow([datetime.now(UTC).isoformat(), o["coin"], o["spread"], o["profit"]])

        with open(json_path, "a") as f:
            for o in opportunities:
                f.write(json.dumps(o) + "\n")

    def run_analysis(self):
        while self.running:
            prices = self.get_prices()
            if prices:
                opps = self.calculate_arbitrage(prices)
                self.log_opportunities(opps)
                for chat_id in self.user_chat_ids:
                    if opps:
                        text = "\n".join([f"{o['coin']}: {o['spread']}% ({o['profit']} USDT)" for o in opps])
                        bot.send_message(chat_id, f"📈 *Найдены связки:*\n{text}", parse_mode="Markdown")
            time.sleep(60)

    def start_analysis(self, message):
        cid = message.chat.id
        self.user_chat_ids.add(cid)
        bot.send_message(cid, "🔍 Анализ запущен")
        if not self.running:
            self.running = True
            self.get_trade_pairs()
            self.thread = threading.Thread(target=self.run_analysis, daemon=True)
            self.thread.start()

    def stop_analysis(self, message):
        cid = message.chat.id
        self.user_chat_ids.discard(cid)
        bot.send_message(cid, "⛔️ Вы отписались от анализа")
        if not self.user_chat_ids:
            self.running = False

    def send_status(self, message):
        status = "✅ Активен" if self.running else "❌ Остановлен"
        bot.send_message(message.chat.id, f"*📊 Статус:*\n{status}", parse_mode="Markdown")

    def show_settings(self, message):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("/download_report", "/stop")
        bot.send_message(message.chat.id, "⚙️ *Меню настроек*", reply_markup=markup, parse_mode="Markdown")

    def download_report(self, message):
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        path = os.path.join(self.log_folder, f"{date}.csv")
        if os.path.exists(path):
            with open(path, "rb") as f:
                bot.send_document(message.chat.id, f)
        else:
            bot.send_message(message.chat.id, "📊 Отчет пока не сформирован.")

# Создание экземпляра
arbitrage_bot = BinanceArbitrageBot()

# Webhook endpoint
@app.route(f"/{SECRET_PATH}", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

# Установка Webhook при первом запросе
@app.before_first_request
def setup_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/{SECRET_PATH}")
    logging.info("✅ Webhook установлен")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
