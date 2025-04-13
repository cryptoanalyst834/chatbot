import os
import logging
from datetime import datetime, UTC
from dotenv import load_dotenv
import telebot
import requests
import csv
import json
from flask import Flask, request

# Загрузка .env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Указать в Railway переменных окружения

# Логирование
os.makedirs("logs", exist_ok=True)
logging.basicConfig(filename='logs/arbitrage.log', level=logging.INFO)

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Пары, пользователи, настройки
user_chat_ids = set()
btc_pairs = []
running = True
min_spread = 3.0
fee = 0.001
deposit = 1000.0
log_folder = "logs"

# Получение актуальных цен
def get_prices():
    try:
        res = requests.get("https://api.binance.com/api/v3/ticker/price")
        res.raise_for_status()
        return {item['symbol']: float(item['price']) for item in res.json()}
    except Exception as e:
        logging.error(f"Цены ошибка: {e}")
        return {}

# Получение монет с парами BTC и USDT
def get_trade_pairs():
    try:
        res = requests.get("https://api.binance.com/api/v3/exchangeInfo")
        symbols = res.json()['symbols']
        btc = set()
        usdt = set()
        for s in symbols:
            if s['status'] != 'TRADING': continue
            if s['quoteAsset'] == 'BTC': btc.add(s['baseAsset'])
            if s['quoteAsset'] == 'USDT': usdt.add(s['baseAsset'])
        return list(btc & usdt)
    except Exception as e:
        logging.error(f"Пары ошибка: {e}")
        return []

# Расчет связок
def calculate(prices, pairs):
    btc_usdt = prices.get('BTCUSDT', 1)
    result = []
    for coin in pairs:
        usdt = f"{coin}USDT"
        btc = f"{coin}BTC"
        if usdt not in prices or btc not in prices:
            continue
        try:
            step1 = (deposit / prices[usdt]) * (1 - fee)
            step2 = step1 * prices[btc] * (1 - fee)
            step3 = step2 * btc_usdt * (1 - fee)
            spread = (step3 / deposit - 1) * 100
            if spread > min_spread:
                result.append({
                    "coin": coin,
                    "spread": round(spread, 2),
                    "profit": round(step3 - deposit, 2)
                })
        except Exception as e:
            logging.error(f"{coin} расчет ошибка: {e}")
    return sorted(result, key=lambda x: x['spread'], reverse=True)

# Лог в CSV
def log_to_file(data):
    if not data:
        return
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = os.path.join(log_folder, f"{date}.csv")
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if f.tell() == 0:
            writer.writerow(["Время", "Монета", "Спред", "Прибыль"])
        for d in data:
            writer.writerow([datetime.now(UTC).isoformat(), d["coin"], d["spread"], d["profit"]])

# Обработка Telegram-команд
@bot.message_handler(commands=['start'])
def start(msg):
    cid = msg.chat.id
    user_chat_ids.add(cid)
    bot.send_message(cid, "🔍 Бот запущен. Используйте /download_report для получения отчета.")

@bot.message_handler(commands=['stop'])
def stop(msg):
    cid = msg.chat.id
    if cid in user_chat_ids:
        user_chat_ids.remove(cid)
        bot.send_message(cid, "⛔️ Вы отписались от анализа.")

@bot.message_handler(commands=['download_report'])
def download(msg):
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    path = os.path.join(log_folder, f"{date}.csv")
    if os.path.exists(path):
        with open(path, "rb") as f:
            bot.send_document(msg.chat.id, f)
    else:
        bot.send_message(msg.chat.id, "📄 Отчет еще не сформирован.")

# Webhook маршрут
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Unsupported Media Type', 415

# Фоновый поток анализа
def analysis_loop():
    global btc_pairs
    btc_pairs = get_trade_pairs()
    while True:
        prices = get_prices()
        if prices:
            deals = calculate(prices, btc_pairs)
            log_to_file(deals)
            if deals:
                msg = "\n".join([f"{d['coin']}: {d['spread']}% ({d['profit']} USDT)" for d in deals])
                for cid in user_chat_ids:
                    bot.send_message(cid, f"📈 Найдены связки:\n{msg}")
        time.sleep(60)

# Установка webhook при запуске
def setup_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info("Webhook установлен")

if __name__ == '__main__':
    setup_webhook()
    threading.Thread(target=analysis_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
