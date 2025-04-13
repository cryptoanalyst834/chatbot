import os
import time
import logging
import sys
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

    def send_csv_report(self, chat_id):
        date = datetime.utcnow().strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "rb") as f:
                    self.bot.send_document(chat_id, f, caption="📎 Отчет по прибыльным связкам")
            except Exception as e:
                self.send_message(chat_id, f"Ошибка отправки CSV: {str(e)}")
        else:
            self.send_message(chat_id, "📄 CSV отчет не найден.")

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
            self.send_message(chat_id, "⛔ Вы отписаны от уведомлений")
        if not self.user_chat_ids:
            self.running = False
            self.logger.info("Анализ остановлен: нет подписчиков")

    def run_analysis(self):
        while self.running:
            try:
                prices = self.get_prices()
                if not prices:
                    self.logger.warning("Не удалось получить цены. Повтор через минуту.")
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
                        self.send_message(chat_id, f"📈 *Актуальные связки:*\n{formatted}")
                else:
                    self.logger.info("Связки не найдены")
            except Exception as e:
                self.logger.error(f"Ошибка в анализе: {str(e)}")
            time.sleep(60)

    def send_status(self, message):
        chat_id = message.chat.id
        status = "Активен ✅" if self.running else "Остановлен ❌"
        self.send_message(chat_id, (
            f"📊 *Статус бота*\n"
            f"Анализ: {status}\n"
            f"Минимальный спред: `{self.min_spread}%`\n"
            f"Комиссия: `{self.fee * 100}%`\n"
            f"Депозит: `{self.initial_deposit} USDT`"
        ))

    def show_settings(self, message):
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("/download_report")
        self.bot.send_message(
            message.chat.id,
            (
                "⚙️ *Настройки*\n"
                "Изменить параметры:\n"
                "/set_min_spread <число>\n"
                "/set_fee <0-1>\n"
                "/set_deposit <число>\n\n"
                "📥 Скачать CSV: /download_report"
            ),
            reply_markup=markup,
            parse_mode='Markdown'
        )

    def download_report(self, message):
        self.send_csv_report(message.chat.id)

    def run(self):
        self.bot.message_handler(commands=['set_min_spread'])(self.set_min_spread)
        self.bot.message_handler(commands=['set_fee'])(self.set_fee)
        self.bot.message_handler(commands=['set_deposit'])(self.set_deposit)
        self.bot.polling(none_stop=True)

    def set_min_spread(self, message):
        try:
            value = float(message.text.split()[1])
            self.min_spread = value
            self.send_message(message.chat.id, f"Минимальный спред установлен: {value}%")
        except:
            self.send_message(message.chat.id, "Ошибка: /set_min_spread <число>")

    def set_fee(self, message):
        try:
            value = float(message.text.split()[1])
            if not 0 <= value <= 1:
                raise ValueError
            self.fee = value
            self.send_message(message.chat.id, f"Комиссия установлена: {value*100:.1f}%")
        except:
            self.send_message(message.chat.id, "Ошибка: /set_fee <от 0 до 1>")

    def set_deposit(self, message):
        try:
            value = float(message.text.split()[1])
            if value <= 0:
                raise ValueError
            self.initial_deposit = value
            self.send_message(message.chat.id, f"Депозит установлен: {value} USDT")
        except:
            self.send_message(message.chat.id, "Ошибка: /set_deposit <число>")

# === MAIN ===
LOCKFILE = "/tmp/bot.lock"

def acquire_lock():
    if os.path.exists(LOCKFILE):
        print("❗ Бот уже запущен.")
        sys.exit(1)
    with open(LOCKFILE, "w") as f:
        f.write(str(os.getpid()))

def release_lock():
    if os.path.exists(LOCKFILE):
        os.remove(LOCKFILE)

if __name__ == "__main__":
    acquire_lock()
    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN)
        bot.delete_webhook()
        print("✅ Webhook удален.")
        arbitrage_bot = BinanceArbitrageBot(TELEGRAM_TOKEN)
        arbitrage_bot.run()
    finally:
        release_lock()
