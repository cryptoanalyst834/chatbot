import os
import time
import logging
from datetime import datetime
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

        # Команды Telegram
        self.bot.message_handler(commands=['start'])(self.start_analysis)
        self.bot.message_handler(commands=['stop'])(self.stop_analysis)
        self.bot.message_handler(commands=['status'])(self.send_status)
        self.bot.message_handler(commands=['settings'])(self.show_settings)
        self.bot.message_handler(commands=['download_report'])(self.download_report)

    def setup_logger(self):
        logger = logging.getLogger("ArbitrageBotLogger")
        logger.setLevel(logging.INFO)
        log_file = os.path.join(self.log_folder, "arbitrage.log")
        handler = logging.FileHandler(log_file)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def get_prices(self):
        try:
            response = requests.get("https://api.binance.com/api/v3/ticker/price")
            if response.status_code != 200:
                self.logger.error(f"Ошибка получения цен: {response.status_code}, {response.text}")
                return {}
            return {item['symbol']: float(item['price']) for item in response.json()}
        except Exception as e:
            self.logger.error(f"Ошибка получения цен: {str(e)}")
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
            self.logger.error(f"Ошибка получения пар: {str(e)}")

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
                self.logger.error(f"Ошибка для {coin}: {str(e)}")
        return sorted(opportunities, key=lambda x: x["spread"], reverse=True)

    def log_opportunities(self, opportunities):
        if not opportunities:
            return
        date = datetime.utcnow().strftime("%Y-%m-%d")
        csv_file = os.path.join(self.log_folder, f"{date}.csv")
        with open(csv_file, "a", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(["Время", "Монета", "Спред, %", "Прибыль, USDT"])
            for opp in opportunities:
                writer.writerow([
                    datetime.utcnow().isoformat(),
                    opp["coin"],
                    opp["spread"],
                    opp["profit"]
                ])

    def send_csv_report(self, chat_id, csv_path):
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "rb") as f:
                    self.bot.send_document(chat_id, f, caption="📎 Отчет по связкам")
            except Exception as e:
                self.send_message(chat_id, f"Ошибка отправки отчета: {str(e)}")
        else:
            self.send_message(chat_id, "📊 Нет данных для выгрузки")

    def send_message(self, chat_id, text):
        try:
            self.bot.send_message(chat_id, text, parse_mode='Markdown')
        except Exception as e:
            self.logger.error(f"Ошибка отправки сообщения {chat_id}: {str(e)}")

    def start_analysis(self, message):
        chat_id = message.chat.id
        if chat_id not in self.user_chat_ids:
            self.user_chat_ids.add(chat_id)
            self.send_message(chat_id, "🔍 Анализ запущен для вашего аккаунта")

        if not self.running:
            self.running = True
            self.get_trade_pairs()
            self.analysis_thread = threading.Thread(target=self.run_analysis)
            self.analysis_thread.daemon = True
            self.analysis_thread.start()
            self.send_message(chat_id, "✅ Глобальный анализ запущен")

    def stop_analysis(self, message):
        chat_id = message.chat.id
        if chat_id in self.user_chat_ids:
            self.user_chat_ids.remove(chat_id)
            self.send_message(chat_id, "❌ Вы отписались от анализа")
        if not self.user_chat_ids:
            self.running = False
            self.logger.info("Анализ остановлен: нет подписчиков")

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
                    formatted = "\n".join([
                        f"{opp['coin']}: {opp['spread']}% ({opp['profit']} USDT)"
                        for opp in opportunities
                    ])
                    for chat_id in self.user_chat_ids:
                        self.send_message(chat_id, f"📈 *Профитные связки:*\n\n{formatted}")
                else:
                    self.logger.info("Связки не найдены")
            except Exception as e:
                self.logger.error(f"Ошибка анализа: {str(e)}")
            time.sleep(60)

    def send_status(self, message):
        chat_id = message.chat.id
        status = "Активен ✅" if self.running else "Остановлен ❌"
        response = (
            f"📊 *Статус бота*\n"
            f"Анализ: {status}\n"
            f"Спред: `{self.min_spread}%`\n"
            f"Комиссия: `{self.fee*100}%`\n"
            f"Депозит: `{self.initial_deposit} USDT`"
        )
        self.send_message(chat_id, response)

    def show_settings(self, message):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("/download_report", "/stop")
        self.bot.send_message(
            message.chat.id,
            "⚙️ *Настройки*\n"
            "• /download_report — Скачать отчет\n"
            "• /stop — Остановить анализ\n\n"
            "Другие команды: /start /status /settings",
            reply_markup=markup,
            parse_mode='Markdown'
        )

    def download_report(self, message):
        chat_id = message.chat.id
        date = datetime.utcnow().strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        self.send_csv_report(chat_id, csv_path)

    def run(self):
        self.bot.polling(none_stop=True)

if __name__ == "__main__":
    bot = BinanceArbitrageBot(TELEGRAM_TOKEN)
    bot.run()
