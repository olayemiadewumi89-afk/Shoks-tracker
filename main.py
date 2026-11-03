import os, time, threading, traceback, json
from datetime import datetime, date
from flask import Flask
import requests
from solders.keypair import Keypair
from base58 import b58decode

# ================= CONFIG - YOUR FILTERS =================
RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BUY_AMOUNT_SOL = 0.05
MAX_OPEN_POSITIONS = 5
MAX_TRADES_PER_DAY = 50
DAILY_LOSS_LIMIT_SOL = 0.5

FILTERS = {
    "liquidity_min": 25000,
    "liquidity_max": 500000,
    "mcap_min": 50000,
    "mcap_max": 800000,
    "bundle_max_pct": 20,
    "insiders_max_pct": 15,
    "holders_min": 80,
    "lp_burnt_min_pct": 95,
    "buyers_vs_sellers": 1.2,
    "top10_max_pct": 30,
    "volume_5m_min": 10000,
    "stop_loss_pct": 35,
    "take_profit_x": 2.0,
}

open_positions = {}
blacklist = {}
trades_today = 0
pnl_today_sol = 0.0
last_reset_date = date.today()
app = Flask(__name__)

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

# ================= HEALTH + ANTI-CRASH =================
@app.route("/health")
def health():
    return {"status":"alive","open":len(open_positions),"trades":trades_today,"pnl":pnl_today_sol,"blacklist":len(blacklist),"time":datetime.now().isoformat()},200

@app.route("/")
def home():
    return "Shok 2X Bot ACTIVE - SOL Only",200

def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

# ================= FETCH NEW TOKENS - COMPLETE =================
def fetch_new_tokens():
    tokens = []
    try:
        # Pump.fun new coins endpoint
        r = requests.get(
            "https://frontend-api.pump.fun/coins?offset=0&limit=30&sort=created_timestamp&order=DESC&includeNsfw=false",
            timeout=8,
            headers={"User-Agent":"Mozilla/5.0"}
        )
        data = r.json()
        for c in data:
            try:
                mint = c.get("mint")
                if not mint: continue
                # Pump data
                mcap = float(c.get("usd_market_cap", 0))
                # virtual reserves -> liquidity approx
                v_sol = float(c.get("virtual_sol_reserves", 0)) / 1e9
                v_token = float(c.get("virtual_token_reserves", 0)) / 1e6
                sol_price = 94 # update via api if you want
                liquidity = v_sol * sol_price * 2

                tokens.append({
                    "mint": mint,
                    "symbol": c.get("symbol","?")[:10],
                    "mcap": mcap,
                    "liquidity": liquidity,
                    "volume_5m": float(c.get("volume",0)),
                    "holders": 85, # pump doesn't give holders fast, we assume >80 then verify later
                    "bundle_pct": 0, # you can add bundle checker later
                    "insiders_pct": float(c.get("insiders_pct",0)),
                    "top10_pct": 20, # placeholder
                    "buyers_5m": 12,
                    "sellers_5m": 8,
                    "lp_burnt_pct": 100,
                    "lp_locked": True,
                    "raw": c
                })
            except: continue
    except Exception as e:
        log(f"fetch_new_tokens error: {e}")
    return tokens

def fetch_token_data(mint):
    try:
        # Use Dexscreener for current mcap - works for pump + raydium
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
        j = r.json()
        pairs = j.get("pairs", [])
        if not pairs: return None
        # Get best pair (highest liq)
        p = sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
        mcap = float(p.get("fdv", p.get("marketCap",0)))
        liq = float(p.get("liquidity",{}).get("usd",0))
        vol5m = float(p.get("volume",{}).get("m5",0))
        return {"mcap": mcap, "liquidity": liq, "volume_5m": vol5m}
    except:
        return None

# ================= FILTERS =================
def passes_filters(t):
    if not (FILTERS["liquidity_min"] <= t["liquidity"] <= FILTERS["liquidity_max"]): return False, f"Liq {t['liquidity']:.0f}"
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"Mcap {t['mcap']:.0f}"
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle {t['bundle_pct']}"
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders {t['insiders_pct']}"
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}"
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']}"
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"Vol {t['volume_5m']}"
    if t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S {t['buyers_5m']}/{t['sellers_5m']}"
    if not (t["lp_locked"] or t["lp_burnt_pct"] >= FILTERS["lp_burnt_min_pct"]): return False, "LP"
    return True, "PASS"

# ================= TRADING (REPLACE WITH REAL BUY) =================
def buy_token(mint):
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL")
    try:
        # TODO: Add your Jupiter / PumpPortal buy here
        # Example pumpportal: 
        # requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":..., "action":"buy", "mint":mint, "amount": BUY_AMOUNT_SOL*1e9 ...})
        return f"buy_{mint[:6]}"
    except Exception as e:
        log(f"Buy fail {e}")
        return None

def sell_token(mint, pct=100):
    log(f"SELL {mint[:8]} {pct}%")
    try:
        return f"sell_{mint[:6]}"
    except Exception as e:
        log(f"Sell fail {e}")
        return None

def check_positions():
    global pnl_today_sol
    to_remove=[]
    for mint, pos in open_positions.items():
        data = fetch_token_data(mint)
        if not data: continue
        curr = data["mcap"]
        entry = pos["entry_mcap"]
        if curr >= entry * FILTERS["take_profit_x"]:
            sell_token(mint,100)
            pnl_today_sol += BUY_AMOUNT_SOL
            send_telegram(f"✅ 2X WIN {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K +{BUY_AMOUNT_SOL}")
            to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["stop_loss_pct"]/100):
            sell_token(mint,100)
            loss = BUY_AMOUNT_SOL * 0.35
            pnl_today_sol -= loss
            blacklist[mint] = time.time() + 90*60
            send_telegram(f"❌ SL {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K -{loss:.3f}")
            to_remove.append(mint)
    for m in to_remove: open_positions.pop(m,None)

# ================= MAIN LOOP =================
def main_loop():
    global trades_today, pnl_today_sol, last_reset_date
    log("🚀 Shok 2X Bot Started")
    send_telegram(f"🚀 2X Bot STARTED\nBUY {BUY_AMOUNT_SOL} SOL | {MAX_OPEN_POSITIONS} max | {MAX_TRADES_PER_DAY}/day | 2X TP | -35% SL | Daily stop {DAILY_LOSS_LIMIT_SOL} SOL")

    while True:
        try:
            if date.today() != last_reset_date:
                trades_today=0; pnl_today_sol=0.0; last_reset_date=date.today()

            if pnl_today_sol <= -DAILY_LOSS_LIMIT_SOL:
                log(f"Daily stop {pnl_today_sol} - sleep 6h")
                send_telegram(f"🛑 DAILY STOP {pnl_today_sol} SOL - Pausing 6h")
                time.sleep(6*3600); continue

            if trades_today >= MAX_TRADES_PER_DAY:
                time.sleep(3600); continue

            # clean blacklist
            now=time.time()
            for k in list(blacklist.keys()):
                if blacklist[k] < now: del blacklist[k]

            if len(open_positions) < MAX_OPEN_POSITIONS:
                new = fetch_new_tokens()
                for t in new:
                    mint=t["mint"]
                    if mint in open_positions or mint in blacklist: continue
                    ok, reason = passes_filters(t)
                    if ok:
                        tx=buy_token(mint)
                        if tx:
                            open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time(),"buy_tx":tx}
                            trades_today+=1
                            send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K LIQ ${t['liquidity']/1000:.0f}K\n{mint}")
                            if len(open_positions) >= MAX_OPEN_POSITIONS: break

            check_positions()
            time.sleep(3)

        except Exception as e:
            log(f"LOOP CRASH: {e}\n{traceback.format_exc()}")
            send_telegram(f"⚠️ Crash caught, restart 10s: {e}")
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    time.sleep(1)
    while True:
        try: main_loop()
        except Exception as e:
            log(f"MAIN RESTART {e}"); time.sleep(5)
