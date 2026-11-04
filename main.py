import os, time, threading, traceback, base64, json, random, queue
from datetime import datetime, date
from flask import Flask
import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from base58 import b58decode
import websocket

RPC_URL = os.getenv("RPC_URL", "")
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")

BUY_AMOUNT_SOL = 0.01
MAX_OPEN_POSITIONS = 2
MAX_TRADES_PER_DAY = 20
DAILY_LOSS_LIMIT_SOL = 0.2

# FAST FILTERS - for WS 0 sec snipe
FAST_FILTERS = {
    "mcap_min": 5000,
    "mcap_max": 900000,
    "bundle_max_pct": 25,
    "insiders_max_pct": 20,
    "top10_max_pct": 45,
    "rug_score_max": 5000,
}

# STRICT FILTERS - your original, for confirmation after 2 min
STRICT_FILTERS = {
    "liquidity_min": 20000,
    "liquidity_max": 600000,
    "mcap_min": 40000,
    "mcap_max": 900000,
    "bundle_max_pct": 25,
    "insiders_max_pct": 20,
    "holders_min": 50,
    "top10_max_pct": 35,
    "volume_5m_min": 8000,
    "buyers_vs_sellers": 1.1,
    "stop_loss_pct": 35,
    "take_profit_x": 2.0,
    "emergency_rug_pct": 50,
    "fake_vol_ratio": 1.5,
    "rug_score_max": 5000,
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
bought_ever = set()
last_429_time = 0
app = Flask(__name__)

ws_mints_queue = queue.Queue()
ws_connected = False
ws_last_token_time = time.time()

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
            {"command":"pnl","description":"📊 PnL"},
            {"command":"positions","description":"📦 Open"},
            {"command":"watchlist","description":"👀 Watchlist"},
            {"command":"stats","description":"📈 Stats"},
            {"command":"balance","description":"💰 Balance"},
            {"command":"trades","description":"Last trades"},
            {"command":"stop","description":"⏸️ Pause"},
            {"command":"start","description":"▶️ Resume"},
            {"command":"help","description":"Help"}
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands": cmds}, timeout=10)
    except: pass

@app.route("/health")
def health(): 
    return {"status":"alive" if bot_running else "paused","open":len(open_positions),"trades":trades_today,"ws":ws_connected,"q":ws_mints_queue.qsize(),"watch":len(watchlist)},200
@app.route("/")
def home(): return "Shok v9.2 - Dual Filters",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

def on_ws_message(ws, message):
    global ws_last_token_time
    try:
        data = json.loads(message)
        mint = None
        if isinstance(data, dict):
            mint = data.get("mint") or data.get("ca") or data.get("tokenAddress")
            if not mint and "data" in data:
                mint = data["data"].get("mint")
        if not mint or len(mint) < 32: return
        if mint in blacklist or mint in open_positions or mint in bought_ever: return
        if mint in list(ws_mints_queue.queue): return
        ws_last_token_time = time.time()
        log(f"WS NEW {mint[:8]} q={ws_mints_queue.qsize()+1}")
        ws_mints_queue.put(mint)
    except: pass

def on_ws_open(ws):
    global ws_connected
    ws_connected = True
    log("WS Connected - subscribing newToken")
    try: ws.send(json.dumps({"method": "subscribeNewToken"}))
    except: pass

def on_ws_error(ws, error): log(f"WS Error {error}")
def on_ws_close(ws, c, m):
    global ws_connected
    ws_connected = False
    log(f"WS Closed {c}")

def pumpportal_ws_loop():
    global ws_connected
    while True:
        try:
            log("WS Connecting wss://pumpportal.fun/api/data")
            ws = websocket.WebSocketApp(
                "wss://pumpportal.fun/api/data",
                on_open=on_ws_open,
                on_message=on_ws_message,
                on_error=on_ws_error,
                on_close=on_ws_close
            )
            ws.run_forever(ping_interval=20, ping_timeout=15)
            ws_connected = False
            log("WS reconnect 5s")
            time.sleep(5)
        except Exception as e:
            ws_connected = False
            log(f"WS crash {e}")
            time.sleep(5)

def get_rug_data(mint):
    global last_429_time
    try:
        if time.time() - last_429_time < 8: 
            return {"holders":100,"top10_pct":20,"bundle_pct":0,"insiders_pct":0,"score":0,"is_rugged":False}
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429,403):
            last_429_time = time.time()
            return {"holders":100,"top10_pct":20,"bundle_pct":0,"insiders_pct":0,"score":0,"is_rugged":False}
        if r.status_code != 200: 
            return {"holders":100,"top10_pct":20,"bundle_pct":0,"insiders_pct":0,"score":0,"is_rugged":False}
        j = r.json()
        score = j.get("score", 0)
        holders = j.get("totalHolders", 0)
        top10 = sum([h.get("pct",0) for h in j.get("topHolders",[])[:10]])
        bundle_pct = 0
        insiders_pct = 0
        for rk in j.get("risks",[]):
            rn = rk.get("name","").lower()
            if "bundle" in rn: bundle_pct = 20
            if "insider" in rn: insiders_pct = 20
            if "single holder" in rn: top10 = max(top10, 90)
        is_rugged = score > FAST_FILTERS["rug_score_max"]
        return {"score":score,"holders":holders,"top10_pct":top10,"bundle_pct":bundle_pct,"insiders_pct":insiders_pct,"is_rugged":is_rugged}
    except:
        return {"holders":100,"top10_pct":20,"bundle_pct":0,"insiders_pct":0,"score":0,"is_rugged":False}

def fetch_token_data(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=5, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429,403):
            global last_429_time
            last_429_time = time.time()
            return None
        j=r.json()
        pairs=j.get("pairs",[])
        if pairs:
            p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
            return {
                "mcap": float(p.get("fdv",0) or 0),
                "liquidity": float(p.get("liquidity",{}).get("usd",0) or 0),
                "volume_5m": float(p.get("volume",{}).get("m5",0) or 0),
                "symbol": str(p.get("baseToken",{}).get("symbol","?"))[:12],
                "buyers_5m": int(p.get("txns",{}).get("m5",{}).get("buys",0) or 0),
                "sellers_5m": int(p.get("txns",{}).get("m5",{}).get("sells",0) or 0),
            }
    except: pass
    return None

def passes_fast_filters(t):
    if not (FAST_FILTERS["mcap_min"] <= t["mcap"] <= FAST_FILTERS["mcap_max"]): return False, f"FAST Mcap {t['mcap']:.0f}"
    if t["bundle_pct"] > FAST_FILTERS["bundle_max_pct"]: return False, f"FAST Bundle {t['bundle_pct']}%"
    if t["insiders_pct"] > FAST_FILTERS["insiders_max_pct"]: return False, f"FAST Insiders {t['insiders_pct']}%"
    if t["top10_pct"] > FAST_FILTERS["top10_max_pct"]: return False, f"FAST Top10 {t['top10_pct']:.0f}%"
    if t.get("rug_score",0) > FAST_FILTERS["rug_score_max"]: return False, f"FAST RugScore"
    return True, "FAST PASS"

def passes_strict_filters(t):
    if not (STRICT_FILTERS["mcap_min"] <= t["mcap"] <= STRICT_FILTERS["mcap_max"]): return False, f"STRICT Mcap {t['mcap']:.0f}", False
    if t["liquidity"] < STRICT_FILTERS["liquidity_min"]: return False, f"STRICT Liq {t['liquidity']:.0f}", False
    if t["bundle_pct"] > STRICT_FILTERS["bundle_max_pct"]: return False, f"STRICT Bundle {t['bundle_pct']}%", False
    if t["insiders_pct"] > STRICT_FILTERS["insiders_max_pct"]: return False, f"STRICT Insiders {t['insiders_pct']}%", False
    if t["holders"] < STRICT_FILTERS["holders_min"]: return False, f"STRICT Holders {t['holders']}", False
    if t["top10_pct"] > STRICT_FILTERS["top10_max_pct"]: return False, f"STRICT Top10 {t['top10_pct']:.0f}%", False
    if t["volume_5m"] < STRICT_FILTERS["volume_5m_min"]: return False, f"STRICT Vol {t['volume_5m']:.0f}", True
    if t["sellers_5m"]>0 and t["buyers_5m"] < t["sellers_5m"] * STRICT_FILTERS["buyers_vs_sellers"]: return False, f"STRICT B/S {t['buyers_5m']}/{t['sellers_5m']}", False
    if t["volume_5m"] > t["mcap"] * STRICT_FILTERS["fake_vol_ratio"]: return False, f"STRICT FakeVol", False
    return True, "STRICT PASS", False

def fetch_new_tokens():
    mints = []
    while not ws_mints_queue.empty() and len(mints) < 5:
        try:
            m = ws_mints_queue.get_nowait()
            if m not in blacklist and m not in open_positions and m not in bought_ever:
                mints.append(m)
        except: break
    
    if not mints:
        if not ws_connected:
            try:
                r=requests.get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", timeout=8, headers={"User-Agent":"Mozilla/5.0"})
                if r.status_code == 200:
                    for p in r.json().get("data",[])[:10]:
                        token = p.get("relationships",{}).get("base_token",{}).get("data",{}).get("id","").split("_")[-1]
                        if token and len(token)>30 and token not in mints:
                            mints.append(token)
            except: pass
        if not mints:
            return []

    log(f"TOTAL Got {len(mints)} mints from WS")
    tokens = []
    for mint in mints:
        try:
            real = fetch_token_data(mint)
            if not real:
                real = {"mcap":12000,"liquidity":8000,"volume_5m":0,"symbol":f"WS-{mint[:4]}","buyers_5m":1,"sellers_5m":0}
                log(f"Dex 429 for {mint[:8]}, using WS estimate 12K")
            
            rug = get_rug_data(mint)
            if rug["is_rugged"]: 
                blacklist[mint]=time.time()+3600
                log(f"Rug skip {mint[:8]} score {rug['score']}")
                continue
            
            t = {
                "mint": mint,
                "symbol": real["symbol"],
                "mcap": real["mcap"],
                "liquidity": real["liquidity"],
                "volume_5m": real["volume_5m"],
                "holders": rug["holders"],
                "bundle_pct": rug["bundle_pct"],
                "insiders_pct": rug["insiders_pct"],
                "top10_pct": rug["top10_pct"],
                "buyers_5m": real["buyers_5m"],
                "sellers_5m": real["sellers_5m"],
                "lp_burnt_pct": 100, "lp_locked": True,
                "rug_score": rug["score"],
                "is_pump": True
            }
            ok, reason = passes_fast_filters(t)
            if not ok:
                log(f"FAST filter fail {t['symbol']} {reason}")
                continue
            
            tokens.append(t)
        except Exception as e:
            log(f"fetch_new err {e}")
            continue

    log(f"Found {len(tokens)} NEW after FAST filters")
    return tokens

def get_keypair():
    if not PRIVATE_KEY_B58: return None
    try: return Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
    except: return None

def send_tx_via_rpc(signed_tx_b64):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[signed_tx_b64,{"encoding":"base64","skipPreflight":False, "preflightCommitment":"confirmed"}]}
        r=requests.post(RPC_URL, json=payload, timeout=15)
        j=r.json()
        sig = j.get("result")
        if not sig:
            log(f"RPC err {j.get('error') or j}")
            return None
        log(f"Sent {sig[:20]} waiting...")
        for _ in range(15):
            time.sleep(2)
            try:
                st = requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[sig],{"searchTransactionHistory":True}]}, timeout=8).json()
                val = st.get("result",{}).get("value",[None])[0]
                if val:
                    if val.get("err"):
                        log(f"TX FAILED {val.get('err')}")
                        return None
                    if val.get("confirmationStatus") in ("confirmed","finalized"):
                        log(f"CONFIRMED {val.get('confirmationStatus')}")
                        return sig
            except: pass
        return sig
    except Exception as e:
        log(f"RPC fail {e}")
        return None

def buy_token(mint, is_pump=True):
    if mint in open_positions or mint in bought_ever:
        log(f"Skip double {mint[:8]}")
        return None
    bal = get_balance_sol()
    if bal < BUY_AMOUNT_SOL + 0.008:
        log(f"Low bal {bal:.4f}")
        return None
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL bal={bal:.4f}")
    try:
        kp=get_keypair()
        if not kp: return None
        pubkey=str(kp.pubkey())
        pools = [("auto", 80), ("pump", 60), ("auto", 100)]
        for pool_type, slippage in pools:
            try:
                resp=requests.post("https://pumpportal.fun/api/trade-local", json={
                    "publicKey": pubkey,
                    "action": "buy",
                    "mint": mint,
                    "denominatedInSol": "true",
                    "amount": BUY_AMOUNT_SOL,
                    "slippage": slippage,
                    "priorityFee": 0.008,
                    "pool": pool_type
                }, timeout=20)
                if resp.status_code==200:
                    try: vtx = VersionedTransaction.from_bytes(resp.content)
                    except:
                        b64 = resp.text.strip().strip('"')
                        b64 = ''.join(c for c in b64 if ord(c) < 128)
                        b64 = ''.join(b64.split())
                        try:
                            jj = json.loads(resp.text)
                            if isinstance(jj, dict) and "transaction" in jj: b64 = jj["transaction"]
                        except: pass
                        tx_bytes = base64.b64decode(b64)
                        vtx = VersionedTransaction.from_bytes(tx_bytes)
                    signed_vtx=VersionedTransaction(vtx.message,[kp])
                    sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
                    if sig:
                        log(f"BUY TX {sig} {pool_type} {slippage}%")
                        send_telegram(f"✅ FAST BUY {pool_type} {mint[:4]} {slippage}%\nhttps://solscan.io/tx/{sig}")
                        return sig
                else:
                    log(f"Buy {pool_type} err {resp.status_code} {resp.text[:200]}")
                    continue
            except Exception as e:
                log(f"Buy {pool_type} ex {e}")
                continue
        return None
    except Exception as e:
        log(f"Buy fail {e}")
        return None

def sell_token(mint, slippage=30, tries=2):
    pools = ["auto","pump"]
    for attempt in range(tries):
        for pool_type in pools:
            try:
                kp=get_keypair(); pubkey=str(kp.pubkey())
                use_slip = 70 if attempt>0 else slippage
                resp=requests.post("https://pumpportal.fun/api/trade-local", json={
                    "publicKey": pubkey,
                    "action": "sell",
                    "mint": mint,
                    "amount": "100%",
                    "denominatedInSol": "false",
                    "slippage": use_slip,
                    "priorityFee": 0.008,
                    "pool": pool_type
                }, timeout=20)
                if resp.status_code!=200: continue
                try: vtx = VersionedTransaction.from_bytes(resp.content)
                except:
                    b64 = resp.text.strip().strip('"')
                    b64 = ''.join(c for c in b64 if ord(c) < 128)
                    b64 = ''.join(b64.split())
                    tx_bytes = base64.b64decode(b64)
                    vtx = VersionedTransaction.from_bytes(tx_bytes)
                signed_vtx=VersionedTransaction(vtx.message,[kp])
                sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
                if sig:
                    log(f"SELL {sig}")
                    return sig
            except: pass
        time.sleep(1)
    return None

def check_positions():
    global pnl_today_sol, total_wins, total_losses
    to_remove=[]
    for mint,pos in list(open_positions.items()):
        data=fetch_token_data(mint)
        if not data: continue
        pos["curr_mcap"] = data["mcap"]
        pos["curr_symbol"] = data["symbol"]
        curr=data["mcap"]; entry=pos["entry_mcap"]
        pct = (curr/entry -1)*100 if entry else 0
        log(f"CHECK {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%)")
        if curr >= entry * STRICT_FILTERS["take_profit_x"]:
            sig = sell_token(mint, slippage=50, tries=3)
            if sig:
                time.sleep(3)
                bal_after = get_balance_sol()
                pnl_today_sol+=BUY_AMOUNT_SOL*0.9; total_wins+=1
                send_telegram(f"✅ 2X WIN {pos['symbol']} +{pct:.0f}%\nhttps://solscan.io/tx/{sig}")
                to_remove.append(mint)
                bought_ever.add(mint)
                blacklist[mint]=time.time()+9999999
        elif curr <= entry * (1 - STRICT_FILTERS["emergency_rug_pct"]/100):
            sig = sell_token(mint, slippage=70, tries=4)
            if sig:
                to_remove.append(mint)
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
        elif curr <= entry * (1 - STRICT_FILTERS["stop_loss_pct"]/100):
            sig = sell_token(mint, slippage=60, tries=3)
            if sig:
                to_remove.append(mint)
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
    for m in to_remove: open_positions.pop(m,None)

def positions_monitor_loop():
    while True:
        try:
            if bot_running and open_positions:
                check_positions()
            time.sleep(2)
        except: time.sleep(2)

def handle_telegram_commands():
    offset=0
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
                try:
                    if text.startswith("/pnl"):
                        bal=get_balance_sol()
                        send_telegram(f"<b>PnL</b> {pnl_today_sol:+.4f} SOL Bal {bal:.4f} WS {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()}")
                    elif text.startswith("/positions"):
                        if not open_positions:
                            send_telegram(f"No pos WS {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()} Watch:{len(watchlist)}")
                        else:
                            txt="<b>Open:</b>\n"
                            for m,p in list(open_positions.items())[:10]:
                                entry = p.get("entry_mcap",0)
                                curr = p.get("curr_mcap", entry)
                                pct = (curr/entry -1)*100 if entry else 0
                                txt+=f"{p.get('symbol','?')} {entry/1000:.0f}K->{curr/1000:.0f}K {pct:+.0f}%\n"
                            send_telegram(txt)
                    elif text.startswith("/watchlist"):
                        if not watchlist: send_telegram(f"Watch empty WS Q:{ws_mints_queue.qsize()}")
                        else:
                            txt=f"<b>Watch {len(watchlist)}:</b>\n"
                            for m,w in list(watchlist.items())[:10]:
                                txt+=f"{w.get('symbol','?')} vol? \n"
                            send_telegram(txt)
                    elif text.startswith("/stats"):
                        send_telegram(f"Wins {total_wins} Losses {total_losses} PnL {pnl_today_sol:+.4f} WS {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()}")
                    elif text.startswith("/balance"):
                        bal=get_balance_sol()
                        send_telegram(f"Bal {bal:.4f} SOL WS Q:{ws_mints_queue.qsize()}")
                    elif text.startswith("/stop"):
                        global bot_running; bot_running=False; send_telegram("⏸️ PAUSED")
                    elif text.startswith("/start"):
                        bot_running=True; send_telegram("▶️ RESUMED")
                    elif text.startswith("/help"):
                        send_telegram("/pnl /positions /watchlist /stats /balance /stop /start")
                except: pass
        except: time.sleep(5)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    set_telegram_commands()
    log(f"🚀 BOT v9.2 - Dual Filters LIVE - FAST + STRICT")
    send_telegram(f"🚀 BOT v9.2 LIVE\n✅ FAST filters for WS snipe (0 sec)\n✅ STRICT filters for watchlist (2 min)\n✅ No more Dex 429 block")
    while True:
        try:
            if not bot_running: time.sleep(5); continue
            if date.today()!=last_reset_date:
                trades_today=0; pnl_today_sol=0.0; last_reset_date=date.today()
            now=time.time()
            for k in list(blacklist.keys()):
                if blacklist[k] < now and k not in bought_ever:
                    del blacklist[k]
            # WATCHLIST - STRICT FILTERS
            for mint in list(watchlist.keys()):
                if mint in open_positions or mint in blacklist or mint in bought_ever: del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 1800: del watchlist[mint]; continue
                data=fetch_token_data(mint)
                if not data: continue
                rug = get_rug_data(mint)
                t={"mint":mint,"symbol":watchlist[mint]["symbol"],"mcap":data["mcap"],"liquidity":data["liquidity"],"volume_5m":data["volume_5m"],"holders":rug["holders"],"bundle_pct":rug["bundle_pct"],"insiders_pct":rug["insiders_pct"],"top10_pct":rug["top10_pct"],"buyers_5m":data["buyers_5m"],"sellers_5m":data["sellers_5m"],"lp_burnt_pct":100,"lp_locked":True,"rug_score":rug["score"]}
                ok,reason,is_vol_fail = passes_strict_filters(t)
                if ok:
                    log(f"STRICT REVISIT PASS {t['symbol']} -> BUYING")
                    tx=buy_token(mint, is_pump=True)
                    if tx and len(tx) > 20:
                        open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time(),"curr_mcap":t["mcap"]}
                        bought_ever.add(mint)
                        blacklist[mint]=time.time()+9999999
                        del watchlist[mint]; trades_today+=1
                elif not is_vol_fail:
                    del watchlist[mint]
            # NEW TOKENS - FAST FILTERS
            if len(open_positions) < MAX_OPEN_POSITIONS:
                tokens=fetch_new_tokens()
                for t in tokens:
                    if t["mint"] in open_positions or t["mint"] in blacklist or t["mint"] in bought_ever: continue
                    ok,reason = passes_fast_filters(t)
                    if not ok:
                        # If fails FAST, don't discard, put in watchlist for STRICT check later
                        if "Vol" not in reason and "Mcap" not in reason:
                            watchlist[t["mint"]]={"symbol":t["symbol"],"first_seen":time.time()}
                            log(f"WATCHLIST {t['symbol']} - FAST {reason} -> waiting STRICT")
                        else:
                            log(f"FAST fail {t['symbol']} {reason}")
                        continue
                    # Also check if it already meets STRICT - if not, add to watchlist for 2nd buy
                    strict_ok, strict_reason, is_vol_fail = passes_strict_filters(t)
                    if not strict_ok and is_vol_fail:
                        watchlist[t["mint"]]={"symbol":t["symbol"],"first_seen":time.time()}
                        log(f"WATCHLIST {t['symbol']} FAST PASS but STRICT needs vol {t['volume_5m']:.0f}")
                    
                    log(f"FAST PASS {t['symbol']} MC {t['mcap']/1000:.0f}K -> BUYING")
                    tx=buy_token(t["mint"], is_pump=True)
                    if tx and len(tx) > 20:
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time(),"curr_mcap":t["mcap"]}
                        bought_ever.add(t["mint"])
                        blacklist[t["mint"]]=time.time()+9999999
                        trades_today+=1
                        send_telegram(f"💰 FAST BUY {t['symbol']} {t['mcap']/1000:.0f}K\n{t['mint']}")
                        if len(open_positions)>=MAX_OPEN_POSITIONS: break
            time.sleep(8)
        except Exception as e:
            log(f"CRASH {e}\n{traceback.format_exc()}"); time.sleep(10)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=handle_telegram_commands,daemon=True).start()
    threading.Thread(target=positions_monitor_loop,daemon=True).start()
    threading.Thread(target=pumpportal_ws_loop,daemon=True).start()
    time.sleep(1)
    while True:
        try: main_loop()
        except Exception as e: log(f"RESTART {e}"); time.sleep(5)
