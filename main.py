import os
import time
import logging
from datetime import datetime, timezone
import telebot
import requests
import csv
import json
from dotenv import load_dotenv
from telebot import types
from flask import Flask, request

# === Конфигурация ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Указать ваш URL (например, от Railway или Render)

# === Настройка логирования ===
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename='logs/arbitrage.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

class BinanceArbitrageBot:
    def __init__(self, min_spread=3.0, fee=0.001, initial_deposit=1000.0):
        self.min_spread = min_spread
        self.fee = fee
        self.initial_deposit = initial_deposit
        self.running = False
        self.btc_pairs = []
        self.user_chat_ids = set()
        self.log_folder = "logs"
        self.logger = self.setup_logger()

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
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_file = os.path.join(self.log_folder, f"{date}.csv")
        with open(csv_file, "a", newline="") as f:
            writer = csv.writer(f)
            if f.tell() == 0:
                writer.writerow(["Время", "Монета", "Спред, %", "Прибыль, USDT"])
            for opp in opportunities:
                writer.writerow([
                    datetime.now(timezone.utc).isoformat(),
                    opp["coin"],
                    opp["spread"],
                    opp["profit"]
                ])

    def send_csv_report(self, chat_id):
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_path = os.path.join(self.log_folder, f"{date}.csv")
        if os.path.exists(csv_path):
            try:
                with open(csv_path, "rb") as f:
                    bot.send_document(chat_id, f, caption="📎 Отчет по связкам")
            except Exception as e:
                bot.send_message(chat_id, f"Ошибка отправки CSV: {str(e)}")
        else:
            bot.send_message(chat_id, "📄 CSV отчет не найден.")

# === Экземпляр бота ===
arb_bot = BinanceArbitrageBot()

# === Telegram handlers ===
@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    if chat_id not in arb_bot.user_chat_ids:
        arb_bot.user_chat_ids.add(chat_id)
        bot.send_message(chat_id, "🔍 Анализ запущен для вашего аккаунта")
    if not arb_bot.running:
        arb_bot.running = True
        arb_bot.get_trade_pairs()
        threading.Thread(target=run_analysis, daemon=True).start()
        bot.send_message(chat_id, "Анализ запущен")

@bot.message_handler(commands=['stop'])
def handle_stop(message):
    chat_id = message.chat.id
    arb_bot.user_chat_ids.discard(chat_id)
    bot.send_message(chat_id, "⚠️ Вы отписаны от уведомлений")
    if not arb_bot.user_chat_ids:
        arb_bot.running = False

@bot.message_handler(commands=['status'])
def handle_status(message):
    status = "✅ Активен" if arb_bot.running else "❌ Остановлен"
    bot.send_message(
        message.chat.id,
        f"📊 *Статус:*\nАнализ: {status}\nСпред: `{arb_bot.min_spread}%`\nКомиссия: `{arb_bot.fee*100}%`\nДепозит: `{arb_bot.initial_deposit} USDT`",
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['settings'])
def handle_settings(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("/download_report")
    bot.send_message(
        message.chat.id,
        "⚙️ *Настройки*\n📥 Нажмите кнопку ниже, чтобы скачать отчет",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['download_report'])
def handle_download(message):
    arb_bot.send_csv_report(message.chat.id)

def run_analysis():
    while arb_bot.running:
        try:
            prices = arb_bot.get_prices()
            if not prices:
                time.sleep(60)
                continue
            opportunities = arb_bot.calculate_arbitrage(prices)
            arb_bot.log_opportunities(opportunities)
            if opportunities:
                formatted = "\n".join([f"{o['coin']}: {o['spread']}% ({o['profit']} USDT)" for o in opportunities])
                for chat_id in arb_bot.user_chat_ids:
                    bot.send_message(chat_id, f"📈 *Найдены связки:*\n{formatted}", parse_mode='Markdown')
        except Exception as e:
            arb_bot.logger.error(f"Ошибка анализа: {str(e)}")
        time.sleep(60)

# === Flask route for Telegram Webhook ===
@app.route(f"/{TELEGRAM_TOKEN}", methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=['GET'])
def index():
    return "Bot is alive!"

if __name__ == "__main__":
    bot.remove_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
