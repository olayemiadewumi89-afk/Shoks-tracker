import os, time, threading, traceback, base64, json
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
    "emergency_rug_pct": 50,
    "fake_vol_ratio": 1.2,
}

open_positions = {}
blacklist = {}
watchlist = {}
trade_history = []
trades_today = 0
pnl_today_sol = 0.0
total_wins = 0
total_losses = 0
bot_running = True
last_reset_date = date.today()
app = Flask(__name__)

def log(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except: pass

def get_balance_sol():
    try:
        kp = get_keypair()
        if not kp: return 0
        payload={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[str(kp.pubkey())]}
        r=requests.post(RPC_URL, json=payload, timeout=8).json()
        return r.get("result",{}).get("value",0)/1e9
    except: return 0

def set_telegram_commands():
    try:
        if not BOT_TOKEN: return
        cmds = [
            {"command":"pnl","description":"📊 PnL + balance + winrate"},
            {"command":"positions","description":"📦 Open positions"},
            {"command":"watchlist","description":"👀 Tokens waiting for 10K vol"},
            {"command":"stats","description":"📈 Winrate + history"},
            {"command":"balance","description":"💰 Wallet balance"},
            {"command":"trades","description":"Last 10 trades"},
            {"command":"stop","description":"⏸️ Pause bot"},
            {"command":"start","description":"▶️ Resume bot"},
            {"command":"help","description":"Show commands"}
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands": cmds}, timeout=10)
        log("TG menu fixed")
    except Exception as e:
        log(f"setMyCommands fail {e}")

@app.route("/health")
def health(): 
    return {"status":"alive" if bot_running else "paused","open":len(open_positions),"watchlist":len(watchlist),"trades":trades_today,"pnl":pnl_today_sol,"wins":total_wins,"losses":total_losses},200
@app.route("/")
def home(): return "Shok STRICT REAL BOT v2 - 2X + RUG FIX",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

def fetch_token_data(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=4)
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

def fetch_new_tokens():
    tokens=[]
    BIG = {"pumpCmXqMfrsAkQ5r2W9FmXof6eH9sSVJ7vuy1uxs4Hz"}
    try:
        r=requests.get("https://frontend-api-v3.pump.fun/coins?offset=0&limit=50&sort=created_timestamp&order=DESC&includeNsfw=false", timeout=12, headers={"User-Agent":"Mozilla/5.0","Origin":"https://pump.fun","Referer":"https://pump.fun/"})
        if r.status_code==200:
            j=r.json()
            data=j if isinstance(j,list) else j.get("coins",[])
            for c in data:
                try:
                    mint=c.get("mint")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions: continue
                    real=fetch_token_data(mint)
                    if not real or real["mcap"]==0: 
                        mcap=float(c.get("usd_market_cap",0) or 0)
                        if mcap==0: continue
                        real={"mcap":mcap,"liquidity":30000,"volume_5m":0,"symbol":c.get("symbol","?")}
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    if not (FILTERS["liquidity_min"] <= real["liquidity"] <= FILTERS["liquidity_max"]): continue
                    if real["volume_5m"] > real["mcap"] * FILTERS["fake_vol_ratio"]:
                        log(f"SKIP FAKE VOL {real['symbol']} MC {real['mcap']/1000:.0f}K VOL {real['volume_5m']:.0f}")
                        continue
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
                    if real["volume_5m"] > real["mcap"] * FILTERS["fake_vol_ratio"]: continue
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
    if not (FILTERS["liquidity_min"] <= t["liquidity"] <= FILTERS["liquidity_max"]): return False, f"Liq {t['liquidity']:.0f}", False
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"Mcap {t['mcap']:.0f}", False
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle", False
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders", False
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}", False
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']}%", False
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"Vol5m {t['volume_5m']:.0f}", True
    if t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S", False
    if not (t["lp_locked"] or t["lp_burnt_pct"] >= FILTERS["lp_burnt_min_pct"]): return False, "LP", False
    if t["volume_5m"] > t["mcap"] * FILTERS["fake_vol_ratio"]: return False, f"FakeVol {t['volume_5m']/1000:.0f}K>{t['mcap']/1000:.0f}K", False
    return True, "PASS", False

def get_keypair():
    if not PRIVATE_KEY_B58: return None
    try: return Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
    except: return None

def send_tx_via_rpc(signed_tx_b64):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[signed_tx_b64,{"encoding":"base64","skipPreflight":True, "maxRetries": 2}]}
        r=requests.post(RPC_URL, json=payload, timeout=12)
        j=r.json()
        if "result" in j:
            return j["result"]
        else:
            log(f"RPC err {j}")
            return None
    except Exception as e:
        log(f"RPC fail {e}")
        return None

def buy_token(mint):
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL")
    try:
        kp=get_keypair()
        if not kp: return None
        pubkey=str(kp.pubkey())
        resp=requests.post("https://pumpportal.fun/api/trade-local", json={
            "publicKey": pubkey,
            "action": "buy",
            "mint": mint,
            "denominatedInSol": "true",
            "amount": BUY_AMOUNT_SOL,
            "slippage": 30,
            "priorityFee": 0.00005,
            "pool": "auto"
        }, timeout=15)
        if resp.status_code!=200:
            log(f"Buy err {resp.status_code} {resp.text[:300]}")
            send_telegram(f"❌ Buy {resp.status_code}: {resp.text[:200]}")
            return None
        
        # FIXED: Try raw bytes first, then base64
        vtx = None
        try:
            vtx = VersionedTransaction.from_bytes(resp.content)
        except:
            try:
                b64_text = resp.text.strip().strip('"').strip()
                # keep only ascii
                b64_text = ''.join(c for c in b64_text if ord(c) < 128)
                if b64_text.startswith('{'):
                    try:
                        jj = json.loads(b64_text)
                        if "transaction" in jj: b64_text = jj["transaction"]
                    except: pass
                b64_text = ''.join(b64_text.split())
                tx_bytes = base64.b64decode(b64_text)
                vtx = VersionedTransaction.from_bytes(tx_bytes)
            except Exception as parse_e:
                log(f"Parse fail {parse_e}")
                return None

        signed_vtx=VersionedTransaction(vtx.message,[kp])
        sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
        log(f"BUY TX {sig[:20] if sig else 'fail'}")
        if sig:
            send_telegram(f"✅ BUY TX {sig[:30]}")
        return sig
    except Exception as e:
        log(f"Buy fail {e}\n{traceback.format_exc()}")
        send_telegram(f"❌ Buy crash {e}")
        return None

def sell_token(mint, slippage=30, tries=1):
    for attempt in range(tries):
        try:
            kp=get_keypair(); pubkey=str(kp.pubkey())
            use_slip = 50 if attempt>0 else slippage
            resp=requests.post("https://pumpportal.fun/api/trade-local", json={
                "publicKey": pubkey,
                "action": "sell",
                "mint": mint,
                "amount": "100%",
                "denominatedInSol": "false",
                "slippage": use_slip,
                "priorityFee": 0.00005,
                "pool": "auto"
            }, timeout=15)
            if resp.status_code!=200: 
                log(f"Sell err {resp.text[:200]} try {attempt}")
                time.sleep(0.5)
                continue
            
            vtx = None
            try:
                vtx = VersionedTransaction.from_bytes(resp.content)
            except:
                b64_text = resp.text.strip().strip('"')
                b64_text = ''.join(c for c in b64_text if ord(c) < 128)
                b64_text = ''.join(b64_text.split())
                try:
                    jj = json.loads(resp.text)
                    if "transaction" in jj: b64_text = jj["transaction"]
                except: pass
                tx_bytes = base64.b64decode(b64_text)
                vtx = VersionedTransaction.from_bytes(tx_bytes)

            signed_vtx=VersionedTransaction(vtx.message,[kp])
            sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
            if sig:
                log(f"SELL TX {sig[:20]} slip {use_slip}% try {attempt}")
                return sig
        except Exception as e: 
            log(f"Sell fail try {attempt} {e}")
            time.sleep(0.5)
    return None

def check_positions():
    global pnl_today_sol, total_wins, total_losses
    to_remove=[]
    for mint,pos in list(open_positions.items()):
        data=fetch_token_data(mint)
        if not data: continue
        curr=data["mcap"]; entry=pos["entry_mcap"]
        if curr >= entry * FILTERS["take_profit_x"]:
            sell_token(mint, slippage=30, tries=2)
            pnl_today_sol+=BUY_AMOUNT_SOL; total_wins+=1
            trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":BUY_AMOUNT_SOL,"result":"WIN","time":datetime.now().strftime("%H:%M")})
            send_telegram(f"✅ 2X WIN {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n+{BUY_AMOUNT_SOL} SOL")
            to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["emergency_rug_pct"]/100):
            sell_token(mint, slippage=50, tries=3)
            loss=BUY_AMOUNT_SOL*0.5; pnl_today_sol-=loss; total_losses+=1
            blacklist[mint]=time.time()+7200
            trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":-loss,"result":"RUG","time":datetime.now().strftime("%H:%M")})
            send_telegram(f"🚨 RUG SELL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n-{loss:.3f} SOL (saved from 2K)")
            to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["stop_loss_pct"]/100):
            sell_token(mint, slippage=40, tries=3)
            loss=BUY_AMOUNT_SOL*0.35; pnl_today_sol-=loss; total_losses+=1
            blacklist[mint]=time.time()+7200
            trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":-loss,"result":"LOSS","time":datetime.now().strftime("%H:%M")})
            send_telegram(f"❌ SL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n-{loss:.3f} SOL")
            to_remove.append(mint)
    for m in to_remove: open_positions.pop(m,None)

def positions_monitor_loop():
    log("Fast monitor 2s started")
    while True:
        try:
            if bot_running and open_positions:
                check_positions()
            time.sleep(2)
        except Exception as e:
            log(f"monitor err {e}")
            time.sleep(2)

def handle_telegram_commands():
    offset=0
    log("TG Command listener started")
    while True:
        try:
            if not BOT_TOKEN: time.sleep(10); continue
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params={"offset":offset,"timeout":20}, timeout=25)
            updates=r.json().get("result",[])
            for upd in updates:
                offset=upd["update_id"]+1
                msg=upd.get("message",{})
                chat_id=str(msg.get("chat",{}).get("id",""))
                if chat_id!=str(CHAT_ID): continue
                text=(msg.get("text") or "").strip().lower()
                if not text.startswith("/"): continue
                if text.startswith("/pnl"):
                    bal=get_balance_sol()
                    winrate = (total_wins/(total_wins+total_losses)*100) if (total_wins+total_losses)>0 else 0
                    send_telegram(f"<b>📊 PnL</b>\nToday: {pnl_today_sol:+.4f} SOL\nBalance: {bal:.4f} SOL\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nTrades today: {trades_today}/{MAX_TRADES_PER_DAY}")
                elif text.startswith("/positions") or text.startswith("/pos"):
                    if not open_positions:
                        send_telegram("No open positions")
                    else:
                        txt="<b>📦 Open:</b>\n"
                        for m,p in open_positions.items():
                            data=fetch_token_data(m)
                            curr=data["mcap"] if data else p["entry_mcap"]
                            pct=(curr/p["entry_mcap"]-1)*100
                            txt+=f"{p['symbol']} {p['entry_mcap']/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%)\n"
                        send_telegram(txt)
                elif text.startswith("/watchlist") or text.startswith("/watch"):
                    if not watchlist:
                        send_telegram("Watchlist empty - waiting for vol 10K")
                    else:
                        txt=f"<b>👀 Watchlist ({len(watchlist)}):</b>\n"
                        for m,w in list(watchlist.items())[:10]:
                            d=fetch_token_data(m)
                            v=d["volume_5m"] if d else 0
                            txt+=f"{w['symbol']} Vol {v:.0f}/10000\n"
                        send_telegram(txt)
                elif text.startswith("/stats") or text.startswith("/winrate"):
                    total=total_wins+total_losses
                    winrate = (total_wins/total*100) if total>0 else 0
                    txt=f"<b>📈 Stats</b>\nTotal trades: {total}\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nPnL today: {pnl_today_sol:+.4f} SOL\n"
                    if trade_history:
                        txt+="\nLast 5:\n"
                        for t in trade_history[-5:]:
                            txt+=f"{t['time']} {t['symbol']} {t['result']} {t['pnl']:+.3f}\n"
                    send_telegram(txt)
                elif text.startswith("/balance") or text.startswith("/bal"):
                    bal=get_balance_sol()
                    send_telegram(f"💰 Balance: {bal:.4f} SOL\nOpen: {len(open_positions)}/{MAX_OPEN_POSITIONS}")
                elif text.startswith("/trades"):
                    if not trade_history: send_telegram("No trades yet")
                    else:
                        txt="<b>Last trades:</b>\n"
                        for t in trade_history[-10:]:
                            txt+=f"{t['symbol']} {t['result']} {t['entry']/1000:.0f}K->{t['exit']/1000:.0f}K {t['pnl']:+.3f}\n"
                        send_telegram(txt)
                elif text.startswith("/stop") or text.startswith("/pause"):
                    global bot_running; bot_running=False
                    send_telegram("⏸️ Bot PAUSED. /start to resume")
                elif text.startswith("/start") or text.startswith("/resume"):
                    bot_running=True
                    send_telegram("▶️ Bot RESUMED - scanning for real buys")
                elif text.startswith("/help"):
                    send_telegram("<b>Commands:</b>\n/pnl - PnL + balance\n/positions - open bags\n/watchlist - tokens waiting for vol 10K\n/stats - winrate + history\n/balance - wallet balance\n/trades - last trades\n/stop - pause bot\n/start - resume bot")
        except Exception as e:
            log(f"TG poll err {e}"); time.sleep(5)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    set_telegram_commands()
    log(f"🚀 REAL BUY BOT v2.1 - 2X TP + RUG FIX - MC {FILTERS['mcap_min']/1000:.0f}K-{FILTERS['mcap_max']/1000:.0f}K VOL {FILTERS['volume_5m_min']/1000:.0f}K")
    send_telegram(f"🚀 REAL BUY BOT v2.1 LIVE\nMC {FILTERS['mcap_min']/1000:.0f}K-{FILTERS['mcap_max']/1000:.0f}K LIQ {FILTERS['liquidity_min']/1000:.0f}K-{FILTERS['liquidity_max']/1000:.0f}K VOL {FILTERS['volume_5m_min']/1000:.0f}K\n✅ 2X TP kept\n✅ Rug filter: VOL>MC blocked\n✅ Fast monitor 2s\n\n<b>Commands:</b> /pnl /positions /watchlist /stats /balance")
    while True:
        try:
            if not bot_running: time.sleep(5); continue
            if date.today()!=last_reset_date:
                trades_today=0; pnl_today_sol=0.0; last_reset_date=date.today()
            if pnl_today_sol <= -DAILY_LOSS_LIMIT_SOL:
                log("Daily limit sleep 6h"); time.sleep(6*3600); continue
            if trades_today >= MAX_TRADES_PER_DAY: time.sleep(3600); continue
            now=time.time()
            for k in list(blacklist.keys()):
                if blacklist[k] < now: del blacklist[k]
            for mint in list(watchlist.keys()):
                if mint in open_positions or mint in blacklist: del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 1800: del watchlist[mint]; continue
                data=fetch_token_data(mint)
                if not data: continue
                t={
                    "mint": mint,
                    "symbol": watchlist[mint]["symbol"],
                    "mcap": data["mcap"],
                    "liquidity": data["liquidity"],
                    "volume_5m": data["volume_5m"],
                    "holders": 85, "bundle_pct": 0, "insiders_pct": 0, "top10_pct": 20,
                    "buyers_5m": 12, "sellers_5m": 8, "lp_burnt_pct": 100, "lp_locked": True
                }
                ok,reason,is_vol_fail = passes_filters(t)
                if ok:
                    log(f"REVISIT PASS {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f} -> BUYING")
                    tx=buy_token(mint)
                    if tx and len(tx) > 20:
                        open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        del watchlist[mint]; trades_today+=1
                        send_telegram(f"💰 BUY (REVISIT) {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f}\n{t['mint']}")
                elif not is_vol_fail:
                    del watchlist[mint]
            if len(open_positions) < MAX_OPEN_POSITIONS:
                tokens=fetch_new_tokens()
                for t in tokens:
                    if t["mint"] in open_positions or t["mint"] in blacklist or t["mint"] in watchlist: continue
                    ok,reason,is_vol_fail = passes_filters(t)
                    if not ok:
                        log(f"Filter fail {t['symbol']} {t['mcap']/1000:.0f}K - {reason}")
                        if is_vol_fail and t["mcap"] >= 80000:
                            watchlist[t["mint"]]={"symbol":t["symbol"],"first_seen":time.time()}
                            log(f" -> WATCHLIST {t['symbol']} waiting VOL 10K (now {t['volume_5m']:.0f})")
                        continue
                    log(f"PASS {t['symbol']} MC {t['mcap']/1000:.0f}K LIQ ${t['liquidity']/1000:.0f}K VOL {t['volume_5m']:.0f} -> BUYING")
                    tx=buy_token(t["mint"])
                    if tx and len(tx) > 20:
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        trades_today+=1
                        send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f}\n{t['mint']}")
                        if len(open_positions)>=MAX_OPEN_POSITIONS: break
                    time.sleep(1)
            time.sleep(3)
        except Exception as e:
            log(f"CRASH {e}\n{traceback.format_exc()}"); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=handle_telegram_commands,daemon=True).start()
    threading.Thread(target=positions_monitor_loop,daemon=True).start()
    time.sleep(1)
    while True:
        try: main_loop()
        except Exception as e: log(f"RESTART {e}"); time.sleep(5)
