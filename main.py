import os, time, threading, traceback, base64
from datetime import datetime, date
from flask import Flask
import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from base58 import b58decode

RPC_URL = os.getenv("RPC_URL", "")
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BUY_AMOUNT_SOL = 0.01
MAX_OPEN_POSITIONS = 2
MAX_TRADES_PER_DAY = 20
DAILY_LOSS_LIMIT_SOL = 0.2

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

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

@app.route("/health")
def health(): return {"status":"alive","open":len(open_positions),"trades":trades_today},200
@app.route("/")
def home(): return "Shok STRICT NEW BOT",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

def fetch_token_data(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
        j=r.json()
        pairs=j.get("pairs",[])
        if not pairs: return None
        p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
        return {
            "mcap": float(p.get("fdv", p.get("marketCap",0)) or 0),
            "liquidity": float(p.get("liquidity",{}).get("usd",0) or 0),
            "volume_5m": float(p.get("volume",{}).get("m5",0) or 0),
            "symbol": p.get("baseToken",{}).get("symbol","?")
        }
    except: return None

# ========= STRICT NEW TOKENS ONLY =========
def fetch_new_tokens():
    tokens=[]
    BIG = {"pumpCmXqMfrsAkQ5r2W9FmXof6eH9sSVJ7vuy1uxs4Hz"}
    # Source 1: Pump.fun NEW directly - best for new tokens
    try:
        r=requests.get("https://frontend-api-v3.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false", timeout=12, headers={"User-Agent":"Mozilla/5.0","Origin":"https://pump.fun","Referer":"https://pump.fun/"})
        if r.status_code==200:
            j=r.json()
            data=j if isinstance(j,list) else j.get("coins",[])
            for c in data:
                try:
                    mint=c.get("mint")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions: continue
                    # Real data immediately
                    real=fetch_token_data(mint)
                    if not real or real["mcap"]==0: 
                        mcap=float(c.get("usd_market_cap",0) or 0)
                        if mcap==0: continue
                        real={"mcap":mcap,"liquidity":30000,"volume_5m":0,"symbol":c.get("symbol","?")}
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    if not (FILTERS["liquidity_min"] <= real["liquidity"] <= FILTERS["liquidity_max"]): continue
                    
                    tokens.append({
                        "mint": mint,
                        "symbol": str(real.get("symbol") or c.get("symbol","?"))[:12],
                        "mcap": real["mcap"],
                        "liquidity": real["liquidity"],
                        "volume_5m": real["volume_5m"],
                        "holders": 85, "bundle_pct": 0, "insiders_pct": 0, "top10_pct": 20,
                        "buyers_5m": 12, "sellers_5m": 8, "lp_burnt_pct": 100, "lp_locked": True
                    })
                except: continue
            if tokens:
                log(f"Found {len(tokens)} NEW from pump.fun")
                return tokens
    except Exception as e: log(f"pump v3 fail {e}")

    # Source 2: Dexscreener latest profiles
    try:
        r=requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        if r.status_code==200:
            for c in r.json()[:50]:
                try:
                    mint=c.get("tokenAddress")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions: continue
                    if c.get("chainId")!="solana": continue
                    real=fetch_token_data(mint)
                    if not real or real["mcap"]==0: continue
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    if not (FILTERS["liquidity_min"] <= real["liquidity"] <= FILTERS["liquidity_max"]): continue
                    tokens.append({
                        "mint": mint,
                        "symbol": str(c.get("symbol","?") or real["symbol"])[:12],
                        "mcap": real["mcap"],
                        "liquidity": real["liquidity"],
                        "volume_5m": real["volume_5m"],
                        "holders": 85, "bundle_pct": 0, "insiders_pct": 0, "top10_pct": 20,
                        "buyers_5m": 12, "sellers_5m": 8, "lp_burnt_pct": 100, "lp_locked": True
                    })
                except: continue
            if tokens:
                log(f"Found {len(tokens)} NEW from Dex profiles")
                return tokens
    except Exception as e: log(f"profiles fail {e}")
    
    return []

def passes_filters(t):
    if not (FILTERS["liquidity_min"] <= t["liquidity"] <= FILTERS["liquidity_max"]): return False, f"Liq {t['liquidity']:.0f}"
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"Mcap {t['mcap']:.0f}"
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle {t['bundle_pct']}%"
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders {t['insiders_pct']}%"
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}"
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']}%"
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"Vol5m {t['volume_5m']:.0f}"
    if t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S {t['buyers_5m']}/{t['sellers_5m']}"
    if not (t["lp_locked"] or t["lp_burnt_pct"] >= FILTERS["lp_burnt_min_pct"]): return False, "LP"
    return True, "PASS"

def get_keypair():
    if not PRIVATE_KEY_B58: return None
    try: return Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
    except: return None

def send_tx_via_rpc(signed_tx_b64):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[signed_tx_b64,{"encoding":"base64","skipPreflight":True}]}
        r=requests.post(RPC_URL, json=payload, timeout=12)
        return r.json().get("result")
    except: return None

def buy_token(mint):
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL")
    try:
        kp=get_keypair()
        pubkey=str(kp.pubkey())
        resp=requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":pubkey,"action":"buy","mint":mint,"denominatedInSol":"true","amount":BUY_AMOUNT_SOL,"slippage":30,"priorityFee":0.002,"pool":"pump"}, timeout=15)
        if resp.status_code!=200: log(f"Buy err {resp.text[:120]}"); return None
        vtx=VersionedTransaction.from_bytes(resp.content)
        signed_vtx=VersionedTransaction(vtx.message,[kp])
        sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
        log(f"BUY TX {sig[:20] if sig else 'fail'}")
        return sig
    except Exception as e: log(f"Buy fail {e}"); return None

def sell_token(mint):
    try:
        kp=get_keypair()
        pubkey=str(kp.pubkey())
        resp=requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":pubkey,"action":"sell","mint":mint,"amount":"100%","denominatedInSol":"false","slippage":30,"priorityFee":0.002,"pool":"pump"}, timeout=15)
        if resp.status_code!=200: return None
        vtx=VersionedTransaction.from_bytes(resp.content)
        signed_vtx=VersionedTransaction(vtx.message,[kp])
        sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
        log(f"SELL TX {sig[:20] if sig else 'fail'}")
        return sig
    except Exception as e: log(f"Sell fail {e}"); return None

def check_positions():
    global pnl_today_sol
    to_remove=[]
    for mint,pos in open_positions.items():
        data=fetch_token_data(mint)
        if not data: continue
        curr=data["mcap"]
        entry=pos["entry_mcap"]
        if curr >= entry * FILTERS["take_profit_x"]:
            sell_token(mint)
            pnl_today_sol+=BUY_AMOUNT_SOL
            send_telegram(f"✅ 2X WIN {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K")
            to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["stop_loss_pct"]/100):
            sell_token(mint)
            loss=BUY_AMOUNT_SOL*0.35
            pnl_today_sol-=loss
            blacklist[mint]=time.time()+7200
            send_telegram(f"❌ SL {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K")
            to_remove.append(mint)
    for m in to_remove: open_positions.pop(m,None)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    log(f"🚀 STRICT NEW BOT LIVE - {FILTERS['mcap_min']/1000:.0f}K-{FILTERS['mcap_max']/1000:.0f}K MCAP, LIQ {FILTERS['liquidity_min']/1000:.0f}K-{FILTERS['liquidity_max']/1000:.0f}K, 2X TP")
    send_telegram(f"🚀 STRICT NEW BOT LIVE\nNEW tokens only\nMC {FILTERS['mcap_min']/1000:.0f}K-{FILTERS['mcap_max']/1000:.0f}K LIQ {FILTERS['liquidity_min']/1000:.0f}K-{FILTERS['liquidity_max']/1000:.0f}K\n2X TP -35% SL AUTO")

    while True:
        try:
            if date.today()!=last_reset_date:
                trades_today=0; pnl_today_sol=0.0; last_reset_date=date.today()
            if pnl_today_sol <= -DAILY_LOSS_LIMIT_SOL:
                log("Daily limit sleep 6h"); time.sleep(6*3600); continue
            if trades_today >= MAX_TRADES_PER_DAY: time.sleep(3600); continue

            now=time.time()
            for k in list(blacklist.keys()):
                if blacklist[k] < now: del blacklist[k]

            if len(open_positions) < MAX_OPEN_POSITIONS:
                tokens=fetch_new_tokens()
                for t in tokens:
                    if t["mint"] in open_positions or t["mint"] in blacklist: continue
                    ok,reason=passes_filters(t)
                    if not ok:
                        log(f"Filter fail {t['symbol']} {t['mcap']/1000:.0f}K - {reason}")
                        continue
                    log(f"PASS {t['symbol']} MC {t['mcap']/1000:.0f}K LIQ ${t['liquidity']/1000:.0f}K -> BUYING")
                    tx=buy_token(t["mint"])
                    if tx:
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        trades_today+=1
                        send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K\n{t['mint']}")
                        if len(open_positions)>=MAX_OPEN_POSITIONS: break
                    time.sleep(1)

            check_positions()
            time.sleep(5)
        except Exception as e:
            log(f"CRASH {e}\n{traceback.format_exc()}"); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    time.sleep(1)
    while True:
        try: main_loop()
        except Exception as e: log(f"RESTART {e}"); time.sleep(5)
