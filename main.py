from flask import Flask
import threading, os, time, requests
from datetime import datetime

app = Flask(__name__)
@app.route('/')
def home():
    return "Shoks Tracker LIVE - 34 wallets"

def run_web():
    app.run(host='0.0.0.0', port=10000)

threading.Thread(target=run_web, daemon=True).start()

BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID=os.getenv("TELEGRAM_CHAT_ID")
WALLETS_RAW=os.getenv("WATCH_WALLETS","")

def send_tg(t):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML"}, timeout=15)
    except: pass

print(f"Loaded wallets: {len(WALLETS_RAW.split(','))}")
send_tg(f"🚀 <b>SHOKS TRACKER LIVE on RENDER</b>\n{len(WALLETS_RAW.split(','))} wallets\n{datetime.now().strftime('%H:%M')}")

while True:
    time.sleep(30)
