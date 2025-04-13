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

# Настройки логирования
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename='logs/arbitrage.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

class BinanceArbitrageBot:
    def __init__(self, token, min_spread=3.0, fee=0.001, initial_deposit=1000.0):
        self.token = token
        self.min_spread = min_spread
        self.fee = fee
        self.initial_deposit = initial_deposit
        self.bot = telebot.TeleBot(token)
        self.running = False
        self.btc_pairs = []
        self.user_chat_ids = set()
        self.log_folder = "logs"
        self.logger = self.setup_logger()

        self.bot.message_handler(commands=['start'])(self.start_analysis)
        self.bot.message_handler(commands=['stop'])(self.stop_analysis)
        self.bot.message_handler(commands=['status'])(self.send_status)
        self.bot.message_handler(commands=['settings'])(self.show_settings)
        self.bot.message_handler(commands=['download_report'])(self.download_report)

    def setup_logger(self):
        logger = logging.getLogger("ArbitrageBotLogger")
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(os.path.join(self.log_folder, "arbitrage.log"))
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        return logger

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
            self.logger.info(f"Загружено {len(self.btc_pairs)} пар для анализа")
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
        json_path = os.path.join(self.log_folder, f"{date}.json")
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
        with open(json_path, "a") as f:
            for opp in opportunities:
                f.write(json.dumps(opp) + "\n")

    def send_message(self, chat_id, text):
        try:
            self.bot.send_message(chat_id, text, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка отправки сообщения {chat_id}: {e}")

    def send_csv_report(self, chat_id):
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "rb") as f:
                    self.bot.send_document(chat_id, f, caption="📎 Отчет по прибыльным связкам")
            except Exception as e:
                self.send_message(chat_id, f"Ошибка при отправке отчета: {e}")
        else:
            self.send_message(chat_id, "📊 Отчет пока не сформирован")

    def start_analysis(self, message):
        chat_id = message.chat.id
        self.user_chat_ids.add(chat_id)
        self.send_message(chat_id, "🔍 Анализ запущен.")
        if not self.running:
            self.running = True
            self.get_trade_pairs()
            thread = threading.Thread(target=self.run_analysis)
            thread.daemon = True
            thread.start()

    def stop_analysis(self, message):
        chat_id = message.chat.id
        if chat_id in self.user_chat_ids:
            self.user_chat_ids.remove(chat_id)
            self.send_message(chat_id, "⛔️ Вы отписаны от анализа.")
        if not self.user_chat_ids:
            self.running = False
            self.logger.info("Остановлен: нет активных пользователей")

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
                    text = "\n".join([
                        f"{opp['coin']}: {opp['spread']}% ({opp['profit']} USDT)"
                        for opp in opportunities
                    ])
                    for chat_id in self.user_chat_ids:
                        self.send_message(chat_id, f"📈 *Найдены связки с прибылью:*\n{text}")
                else:
                    self.logger.info("Связки не найдены")
            except Exception as e:
                self.logger.error(f"Ошибка в цикле анализа: {e}")
            time.sleep(60)

    def send_status(self, message):
        status = "✅ Активен" if self.running else "❌ Остановлен"
        self.send_message(
            message.chat.id,
            f"*📊 Статус:*\nАнализ: {status}\nМинимальный спред: `{self.min_spread}%`\n"
            f"Комиссия: `{self.fee*100}%`\nДепозит: `{self.initial_deposit} USDT`"
        )

    def show_settings(self, message):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("/download_report", "/stop")
        self.bot.send_message(
            message.chat.id,
            "⚙️ *Настройки*\nНажмите кнопку ниже, чтобы скачать отчет:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def download_report(self, message):
        self.send_csv_report(message.chat.id)

    def run(self):
        self.bot.polling(none_stop=True)

if __name__ == "__main__":
    bot = BinanceArbitrageBot(TELEGRAM_TOKEN)
    bot.run()
