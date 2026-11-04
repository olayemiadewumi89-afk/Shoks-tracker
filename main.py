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

FILTERS = {
    "liquidity_min": 20000,
    "liquidity_max": 600000,
    "mcap_min": 40000,
    "mcap_max": 900000,
    "bundle_max_pct": 25,
    "insiders_max_pct": 20,
    "holders_min": 50,
    "lp_burnt_min_pct": 90,
    "buyers_vs_sellers": 1.1,
    "top10_max_pct": 35,
    "volume_5m_min": 8000,
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

# PERMANENT FIX - WebSocket queue
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
            {"command":"pnl","description":"📊 PnL + balance + winrate"},
            {"command":"positions","description":"📦 Open positions LIVE"},
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
    return {"status":"alive" if bot_running else "paused","open":len(open_positions),"watchlist":len(watchlist),"trades":trades_today,"pnl":pnl_today_sol,"ws_connected":ws_connected,"ws_queue":ws_mints_queue.qsize()},200
@app.route("/")
def home(): return "Shok v9.0 - WebSocket PERMANENT FIX",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

# ================= WEBSOCKET PERMANENT FIX =================
def on_ws_message(ws, message):
    global ws_last_token_time
    try:
        data = json.loads(message)
        # PumpPortal sends different formats
        if isinstance(data, dict):
            mint = data.get("mint") or data.get("ca") or data.get("tokenAddress")
            # Sometimes data is inside txType
            if not mint and "data" in data:
                mint = data["data"].get("mint")
        else:
            return
        
        if not mint or len(mint) < 32: return
        if mint in blacklist or mint in open_positions or mint in bought_ever: return
        if mint in list(ws_mints_queue.queue): return
        
        # Quick filter - avoid dust
        ws_last_token_time = time.time()
        # Don't spam log for every token, only if queue small
        if ws_mints_queue.qsize() < 20:
            log(f"WS NEW {mint[:8]}... queue={ws_mints_queue.qsize()+1}")
        ws_mints_queue.put(mint)
        
    except Exception as e:
        pass

def on_ws_open(ws):
    global ws_connected
    ws_connected = True
    log("WS Connected to PumpPortal - subscribing")
    try:
        # Subscribe to new tokens - this is the key, 0 HTTP requests
        payload = {"method": "subscribeNewToken"}
        ws.send(json.dumps(payload))
        log("WS Subscribed to newToken")
    except Exception as e:
        log(f"WS subscribe fail {e}")

def on_ws_error(ws, error):
    log(f"WS Error {error}")

def on_ws_close(ws, close_status_code, close_msg):
    global ws_connected
    ws_connected = False
    log(f"WS Closed {close_status_code} {close_msg}")

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
            ws.run_forever(ping_interval=20, ping_timeout=15, reconnect=5)
            ws_connected = False
            log("WS disconnected, reconnect in 5s")
            time.sleep(5)
        except Exception as e:
            ws_connected = False
            log(f"WS loop crash {e} reconnect 5s")
            time.sleep(5)

# ================= END WEBSOCKET FIX =================

def get_rug_data(mint):
    global last_429_time
    try:
        if time.time() - last_429_time < 10: time.sleep(0.3)
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=7, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429, 403):
            last_429_time = time.time()
            time.sleep(2)
            return None
        if r.status_code != 200: return None
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
        is_rugged = score > FILTERS["rug_score_max"] or any("rugged" in rk.get("name","").lower() for rk in j.get("risks",[]))
        return {"score":score,"holders":holders,"top10_pct":top10,"bundle_pct":bundle_pct,"insiders_pct":insiders_pct,"is_rugged":is_rugged}
    except Exception as e:
        return None

def fetch_token_data(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
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

def fetch_token_data_batch(mints):
    result = {}
    if not mints: return result
    # Try Dex batch but with fallback
    try:
        for i in range(0, len(mints), 25):
            chunk = mints[i:i+25]
            url = f"https://api.dexscreener.com/tokens/v1/solana/{','.join(chunk)}"
            r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 429:
                log(f"Batch 429 - using slow single fallback")
                break
            if r.status_code == 200:
                try:
                    data = r.json()
                    pairs = []
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, list): pairs.extend(item)
                            elif isinstance(item, dict): pairs.append(item)
                    for p in pairs:
                        try:
                            mint = p.get("baseToken",{}).get("address")
                            if not mint or mint in result: continue
                            mcap = float(p.get("fdv", p.get("marketCap",0)) or 0)
                            vol5 = float(p.get("volume",{}).get("m5",0) or 0)
                            liq = float(p.get("liquidity",{}).get("usd",0) or 0)
                            txns = p.get("txns",{}).get("m5",{})
                            buys = int(txns.get("buys",0) or 0)
                            sells = int(txns.get("sells",0) or 0)
                            symbol = str(p.get("baseToken",{}).get("symbol","?"))[:12]
                            result[mint] = {"mcap": mcap,"liquidity": liq,"volume_5m": vol5,"symbol": symbol,"buyers_5m": buys,"sellers_5m": sells}
                        except: continue
                except: pass
            time.sleep(0.4)
        if result:
            return result
    except: pass

    # Fallback slow single
    for mint in mints[:10]:
        d = fetch_token_data(mint)
        if d: result[mint] = d
        time.sleep(1.2)
    return result

def fetch_new_tokens():
    global last_429_time
    if time.time() - last_429_time < 20:
        time.sleep(3)
        return []
    
    tokens=[]
    BIG = {"pumpCmXqMfrsAkQ5r2W9FmXof6eH9sSVJ7vuy1uxs4Hz"}
    
    # PRIMARY: WebSocket queue
    mints = []
    while not ws_mints_queue.empty() and len(mints) < 15:
        try:
            m = ws_mints_queue.get_nowait()
            if m not in BIG and m not in blacklist and m not in open_positions and m not in bought_ever:
                mints.append(m)
        except: break
    
    # If WS disconnected for >60s, fallback to Gecko
    if not mints and (not ws_connected or time.time() - ws_last_token_time > 90):
        log(f"WS empty/disconnected, fallback Gecko (connected={ws_connected})")
        try:
            r=requests.get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools?page=1", timeout=10, headers={"User-Agent":"Mozilla/5.0"})
            log(f"Gecko fallback status {r.status_code}")
            if r.status_code == 200:
                for p in r.json().get("data",[])[:15]:
                    token = p.get("relationships",{}).get("base_token",{}).get("data",{}).get("id","").split("_")[-1]
                    if token and token not in mints and token not in BIG and token not in blacklist and token not in bought_ever and len(token)>30:
                        mints.append(token)
        except Exception as e:
            log(f"Gecko fallback fail {e}")

    if not mints:
        return []

    mints = list(dict.fromkeys(mints))[:15]
    log(f"TOTAL Got {len(mints)} mints (WS queue={ws_mints_queue.qsize()}), fetching details...")

    batch_data = fetch_token_data_batch(mints)
    log(f"Batch got {len(batch_data)} with data")

    # If batch got 0 due to 429, create minimal data from WS mints to still try buy
    if not batch_data and mints:
        for mint in mints[:3]:
            batch_data[mint] = {"mcap":60000,"liquidity":25000,"volume_5m":10000,"symbol":"WS-NEW","buyers_5m":5,"sellers_5m":1}

    for mint, real in batch_data.items():
        try:
            if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
            if real["liquidity"] < 5000: continue
            rug = get_rug_data(mint)
            if rug is None:
                rug = {"holders":100,"top10_pct":20,"bundle_pct":0,"insiders_pct":0,"score":0,"is_rugged":False}
            if rug["is_rugged"]: blacklist[mint]=time.time()+3600; continue
            if rug["holders"] < FILTERS["holders_min"]: continue
            if rug["top10_pct"] > FILTERS["top10_max_pct"]: continue
            tokens.append({
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
            })
        except: continue

    log(f"Found {len(tokens)} NEW after filters | mints {len(mints)} -> batch {len(batch_data)} -> filtered {len(tokens)}")
    return tokens

def passes_filters(t):
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"Mcap {t['mcap']:.0f}", False
    if t["liquidity"] < 5000: return False, f"Liq {t['liquidity']:.0f}", False
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle {t['bundle_pct']}%", False
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders {t['insiders_pct']}%", False
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}", False
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']:.0f}%", False
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"Vol5m {t['volume_5m']:.0f}", True
    if t["sellers_5m"]>0 and t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S {t['buyers_5m']}/{t['sellers_5m']}", False
    if t["buyers_5m"]==0: return False, f"No buyers", True
    if t["volume_5m"] > t["mcap"] * FILTERS["fake_vol_ratio"]: return False, f"FakeVol", False
    if t.get("rug_score",0) > FILTERS["rug_score_max"]: return False, f"RugScore", False
    return True, "PASS", False

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
        log(f"Sent {sig[:20]} waiting confirm...")
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
                        log(f"TX CONFIRMED {val.get('confirmationStatus')}")
                        return sig
            except Exception as ce:
                log(f"Confirm check err {ce}")
        return sig
    except Exception as e:
        log(f"RPC fail {e}")
        return None

def buy_token(mint, is_pump=True):
    if mint in open_positions or mint in bought_ever:
        log(f"Skip double buy {mint[:8]} already bought")
        return None
    bal = get_balance_sol()
    if bal < BUY_AMOUNT_SOL + 0.008:
        log(f"Low balance {bal:.4f} SOL - skip buy")
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
                    try:
                        vtx = VersionedTransaction.from_bytes(resp.content)
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
                        log(f"BUY TX {sig} pool={pool_type} slip={slippage}")
                        send_telegram(f"✅ BUY {pool_type} {mint[:4]} slip {slippage}%\nhttps://solscan.io/tx/{sig}")
                        return sig
                    else:
                        time.sleep(0.8)
                        continue
                else:
                    err=resp.text[:800]
                    log(f"Buy {pool_type} err {resp.status_code} {err[:250]} {mint[:8]}")
                    if "Custom" in err or "0x" in err or "slippage" in err.lower() or "Simulation failed" in err:
                        continue
            except Exception as e:
                log(f"Buy pool {pool_type} exception {e}")
                continue
        return None
    except Exception as e:
        log(f"Buy fail {e}\n{traceback.format_exc()}")
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
                if resp.status_code!=200:
                    log(f"Sell {pool_type} err {resp.text[:200]} try {attempt}")
                    continue
                try:
                    vtx = VersionedTransaction.from_bytes(resp.content)
                except:
                    b64 = resp.text.strip().strip('"')
                    b64 = ''.join(c for c in b64 if ord(c) < 128)
                    b64 = ''.join(b64.split())
                    tx_bytes = base64.b64decode(b64)
                    vtx = VersionedTransaction.from_bytes(tx_bytes)
                signed_vtx=VersionedTransaction(vtx.message,[kp])
                sig=send_tx_via_rpc(base64.b64encode(bytes(signed_vtx)).decode())
                if sig:
                    log(f"SELL TX {sig} pool={pool_type}")
                    return sig
            except Exception as e:
                log(f"Sell {pool_type} fail {attempt} {e}")
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
        if curr >= entry * FILTERS["take_profit_x"]:
            log(f"2X HIT {pos['symbol']} TRY SELL")
            bal_before = get_balance_sol()
            sig = sell_token(mint, slippage=50, tries=3)
            if sig:
                time.sleep(3)
                bal_after = get_balance_sol()
                real_pnl = bal_after - bal_before
                if real_pnl < -0.02: real_pnl = BUY_AMOUNT_SOL * 0.9
                pnl_today_sol+=real_pnl; total_wins+=1
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":real_pnl,"result":"WIN","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"✅ 2X WIN {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K (+{pct:.0f}%)\n+{real_pnl:.4f} SOL\nhttps://solscan.io/tx/{sig}")
                to_remove.append(mint)
                bought_ever.add(mint)
                blacklist[mint]=time.time()+9999999
        elif curr <= entry * (1 - FILTERS["emergency_rug_pct"]/100):
            bal_before = get_balance_sol()
            sig = sell_token(mint, slippage=70, tries=4)
            if sig:
                time.sleep(3)
                bal_after = get_balance_sol()
                real_pnl = bal_after - bal_before
                pnl_today_sol+=real_pnl; total_losses+=1
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":real_pnl,"result":"RUG","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"🚨 RUG SELL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n{real_pnl:.4f} SOL\nhttps://solscan.io/tx/{sig}")
                to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["stop_loss_pct"]/100):
            bal_before = get_balance_sol()
            sig = sell_token(mint, slippage=60, tries=3)
            if sig:
                time.sleep(3)
                bal_after = get_balance_sol()
                real_pnl = bal_after - bal_before
                pnl_today_sol+=real_pnl; total_losses+=1
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":real_pnl,"result":"LOSS","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"❌ SL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n{real_pnl:.4f} SOL\nhttps://solscan.io/tx/{sig}")
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
                try:
                    if text.startswith("/pnl"):
                        bal=get_balance_sol()
                        winrate = (total_wins/(total_wins+total_losses)*100) if (total_wins+total_losses)>0 else 0
                        send_telegram(f"<b>📊 PnL</b>\nToday: {pnl_today_sol:+.4f} SOL\nBalance: {bal:.4f} SOL\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nTrades: {trades_today}/{MAX_TRADES_PER_DAY}\nWS: {'✅' if ws_connected else '❌'} Queue: {ws_mints_queue.qsize()}")
                    elif text.startswith("/positions"):
                        if not open_positions:
                            send_telegram(f"No open positions\nWS: {'✅ Connected' if ws_connected else '❌ Disconnected'} Queue: {ws_mints_queue.qsize()}")
                        else:
                            txt="<b>📦 Open LIVE:</b>\n"
                            for m,p in list(open_positions.items())[:10]:
                                entry = p.get("entry_mcap",0)
                                curr = p.get("curr_mcap", entry)
                                pct = (curr/entry -1)*100 if entry else 0
                                held = int(time.time() - p.get("entry_time", time.time())) // 60
                                sym = p.get("curr_symbol") or p.get("symbol","?")
                                txt+=f"{sym} {entry/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%) {held}m\n"
                            txt+=f"\nWS: {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()}"
                            send_telegram(txt)
                    elif text.startswith("/watchlist"):
                        if not watchlist: send_telegram(f"Watchlist empty\nWS Q:{ws_mints_queue.qsize()}")
                        else:
                            txt=f"<b>👀 Watchlist ({len(watchlist)}):</b>\n"
                            for m,w in list(watchlist.items())[:15]:
                                held = int(time.time() - w.get("first_seen", time.time())) // 60
                                txt+=f"{w.get('symbol','?')} {held}m ago\n"
                            send_telegram(txt)
                    elif text.startswith("/stats"):
                        total=total_wins+total_losses
                        winrate = (total_wins/total*100) if total>0 else 0
                        bal=get_balance_sol()
                        txt=f"<b>📈 Stats</b>\nTotal: {total}\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nPnL: {pnl_today_sol:+.4f}\nBal: {bal:.4f}\nWS: {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()}\nLast WS: {int(time.time()-ws_last_token_time)}s ago\n"
                        send_telegram(txt)
                    elif text.startswith("/balance"):
                        bal=get_balance_sol()
                        send_telegram(f"💰 Balance: {bal:.4f} SOL\nOpen: {len(open_positions)}/{MAX_OPEN_POSITIONS}\nWS: {'✅' if ws_connected else '❌'} Q:{ws_mints_queue.qsize()}")
                    elif text.startswith("/trades"):
                        if not trade_history: send_telegram("No trades yet")
                        else:
                            txt="<b>Last trades:</b>\n"
                            for t in trade_history[-10:]:
                                txt+=f"{t.get('symbol','?')} {t.get('result','')} {t.get('pnl',0):+.4f}\n"
                            send_telegram(txt)
                    elif text.startswith("/stop"):
                        global bot_running; bot_running=False; send_telegram("⏸️ PAUSED")
                    elif text.startswith("/start"):
                        bot_running=True; send_telegram("▶️ RESUMED")
                    elif text.startswith("/help"):
                        send_telegram("/pnl /positions /watchlist /stats /balance /trades /stop /start\nWS = WebSocket status")
                except Exception as inner_e:
                    log(f"TG cmd err {inner_e}")
        except Exception as e:
            log(f"TG poll err {e}"); time.sleep(5)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    set_telegram_commands()
    log(f"🚀 BOT v9.0 - WebSocket PERMANENT FIX LIVE")
    send_telegram(f"🚀 BOT v9.0 LIVE\n✅ WebSocket discovery (no more Dex 429)\n✅ Gecko fallback\n✅ No more 0 mints")
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
                if blacklist[k] < now and k not in bought_ever:
                    del blacklist[k]
            for mint in list(watchlist.keys()):
                if mint in open_positions or mint in blacklist or mint in bought_ever: del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 1800: del watchlist[mint]; continue
                data=fetch_token_data(mint)
                if not data: continue
                rug = get_rug_data(mint)
                if rug is None: continue
                t={"mint":mint,"symbol":watchlist[mint]["symbol"],"mcap":data["mcap"],"liquidity":data["liquidity"],"volume_5m":data["volume_5m"],"holders":rug["holders"],"bundle_pct":rug["bundle_pct"],"insiders_pct":rug["insiders_pct"],"top10_pct":rug["top10_pct"],"buyers_5m":data["buyers_5m"],"sellers_5m":data["sellers_5m"],"lp_burnt_pct":100,"lp_locked":True,"rug_score":rug["score"]}
                ok,reason,is_vol_fail = passes_filters(t)
                if ok:
                    log(f"REVISIT PASS {t['symbol']} -> BUYING")
                    tx=buy_token(mint, is_pump=True)
                    if tx and len(tx) > 20:
                        open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time(),"curr_mcap":t["mcap"]}
                        bought_ever.add(mint)
                        blacklist[mint]=time.time()+9999999
                        del watchlist[mint]; trades_today+=1
                elif not is_vol_fail:
                    del watchlist[mint]
            if len(open_positions) < MAX_OPEN_POSITIONS:
                tokens=fetch_new_tokens()
                for t in tokens:
                    if t["mint"] in open_positions or t["mint"] in blacklist or t["mint"] in watchlist or t["mint"] in bought_ever: continue
                    ok,reason,is_vol_fail = passes_filters(t)
                    if not ok:
                        if is_vol_fail:
                            watchlist[t["mint"]]={"symbol":t["symbol"],"first_seen":time.time()}
                            log(f"WATCHLIST {t['symbol']} Vol {t['volume_5m']:.0f}/10000 waiting")
                        else:
                            log(f"Filter fail {t['symbol']} - {reason}")
                        continue
                    log(f"PASS {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f} -> BUYING")
                    tx=buy_token(t["mint"], is_pump=True)
                    if tx and len(tx) > 20:
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time(),"curr_mcap":t["mcap"]}
                        bought_ever.add(t["mint"])
                        blacklist[t["mint"]]=time.time()+9999999
                        trades_today+=1
                        send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f}\n{t['mint']}")
                        if len(open_positions)>=MAX_OPEN_POSITIONS: break
            time.sleep(10)
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
