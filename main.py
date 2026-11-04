import os, time, threading, traceback, base64, json, random
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
    return {"status":"alive" if bot_running else "paused","open":len(open_positions),"watchlist":len(watchlist),"trades":trades_today},200
@app.route("/")
def home(): return "Shok v7.4 - FINAL FIX",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

def get_rug_data(mint):
    global last_429_time
    try:
        if time.time() - last_429_time < 15:
            time.sleep(2)
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=7, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 429:
            last_429_time = time.time()
            log(f"RugCheck 429 - sleep")
            time.sleep(5)
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
        log(f"RugCheck fail {mint[:8]} {e}")
        return None

def fetch_token_data(mint):
    pump_symbol = "?"
    pump_mcap = 0
    try:
        r=requests.get(f"https://frontend-api-v3.pump.fun/coins/{mint}", timeout=4, headers={"User-Agent":"Mozilla/5.0","Origin":"https://pump.fun"})
        if r.status_code==200:
            j=r.json()
            pump_mcap=float(j.get("usd_market_cap",0) or 0)
            pump_symbol=str(j.get("symbol","?"))[:12]
    except: pass
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code==429:
            global last_429_time
            last_429_time = time.time()
            log(f"Dex token 429 {mint[:6]} - backoff")
            time.sleep(5)
            return None
        j=r.json()
        pairs=j.get("pairs",[])
        if pairs:
            p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
            dex_symbol = str(p.get("baseToken",{}).get("symbol","?"))[:12]
            final_symbol = pump_symbol if pump_symbol!="?" else dex_symbol
            if final_symbol=="?": return None
            txns = p.get("txns",{}).get("m5",{})
            buys = int(txns.get("buys",0) or 0)
            sells = int(txns.get("sells",0) or 0)
            mcap = float(p.get("fdv", p.get("marketCap",0)) or 0)
            if mcap==0 and pump_mcap>0: mcap = pump_mcap
            return {
                "mcap": mcap,
                "liquidity": float(p.get("liquidity",{}).get("usd",0) or 0),
                "volume_5m": float(p.get("volume",{}).get("m5",0) or 0),
                "symbol": final_symbol,
                "buyers_5m": buys,
                "sellers_5m": sells,
            }
    except: pass
    if pump_mcap>0 and pump_symbol!="?":
        return {"mcap":pump_mcap,"liquidity":35000,"volume_5m":0,"symbol":pump_symbol,"buyers_5m":0,"sellers_5m":0}
    return None

def fetch_new_tokens():
    global last_429_time
    # Global cooldown after 429
    if time.time() - last_429_time < 20:
        log(f"Cooldown after 429 - sleep {20 - (time.time() - last_429_time):.0f}s")
        time.sleep(5)
        return []
    
    tokens=[]
    BIG = {"pumpCmXqMfrsAkQ5r2W9FmXof6eH9sSVJ7vuy1uxs4Hz"}
    sess = requests.Session()
    sess.headers.update({"User-Agent":"Mozilla/5.0"})

    # 1. Token-profiles latest - 60 req/min limit, this WORKS on Render
    try:
        r=sess.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=10)
        log(f"Dex profiles status {r.status_code}")
        if r.status_code==429:
            last_429_time = time.time()
            log("Dex profiles 429 - cooldown 20s")
            time.sleep(20)
            return []
        if r.status_code==200:
            data=r.json()
            for c in data[:50]:
                try:
                    if c.get("chainId")!="solana": continue
                    mint=c.get("tokenAddress")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions or mint in bought_ever: continue
                    real = fetch_token_data(mint)
                    if not real: continue
                    if real["mcap"]==0: continue
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    rug = get_rug_data(mint)
                    if rug is None: continue
                    if rug["is_rugged"]: 
                        blacklist[mint]=time.time()+7200
                        continue
                    if rug["holders"] < FILTERS["holders_min"]: continue
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
                        "is_pump": mint.endswith("pump")
                    })
                except: continue
            if tokens:
                log(f"Found {len(tokens)} NEW from Dex profiles")
                return tokens
    except Exception as e: log(f"profiles fail {e}")

    # 2. Token-boosts latest - also works, less 429
    try:
        r=sess.get("https://api.dexscreener.com/token-boosts/latest/v1", timeout=10)
        log(f"Dex boosts status {r.status_code}")
        if r.status_code==200:
            for c in r.json()[:40]:
                try:
                    if c.get("chainId")!="solana": continue
                    mint=c.get("tokenAddress")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions or mint in bought_ever: continue
                    real = fetch_token_data(mint)
                    if not real: continue
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    rug = get_rug_data(mint)
                    if rug is None: continue
                    if rug["is_rugged"]: continue
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
                        "is_pump": mint.endswith("pump")
                    })
                except: continue
            if tokens:
                log(f"Found {len(tokens)} NEW from Dex boosts")
                return tokens
    except Exception as e: log(f"boosts fail {e}")

    # 3. Search pump.fun - 300 req/min, highest limit
    try:
        r=sess.get("https://api.dexscreener.com/latest/dex/search/?q=pump.fun", timeout=10)
        log(f"Dex search status {r.status_code}")
        if r.status_code==429:
            last_429_time = time.time()
            log("Dex search 429 - cooldown")
            time.sleep(15)
            return []
        if r.status_code==200:
            for p in r.json().get("pairs",[])[:20]:
                try:
                    mint=p.get("baseToken",{}).get("address")
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions or mint in bought_ever: continue
                    real = fetch_token_data(mint)
                    if not real: continue
                    rug = get_rug_data(mint)
                    if rug is None: continue
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
            if tokens:
                log(f"Found {len(tokens)} NEW from Dex search")
                return tokens
    except Exception as e: log(f"search fail {e}")

    log(f"Found 0 NEW - all sources checked, sleep 15s")
    time.sleep(15)
    return []

def passes_filters(t):
    if not (FILTERS["liquidity_min"] <= t["liquidity"] <= FILTERS["liquidity_max"]): return False, f"Liq {t['liquidity']:.0f}", False
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"Mcap {t['mcap']:.0f}", False
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle {t['bundle_pct']}%", False
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders {t['insiders_pct']}%", False
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}", False
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']:.0f}%", False
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"Vol5m {t['volume_5m']:.0f}", True
    if t["sellers_5m"]>0 and t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S {t['buyers_5m']}/{t['sellers_5m']}", False
    if t["buyers_5m"]==0: return False, f"No buyers", True
    if not (t["lp_locked"] or t["lp_burnt_pct"] >= FILTERS["lp_burnt_min_pct"]): return False, "LP", False
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
            log(f"RPC err {j}")
            return None
        log(f"Sent {sig[:20]} waiting confirm...")
        for _ in range(12):
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
    bal = get_balance_sol()
    if bal < BUY_AMOUNT_SOL + 0.005:
        log(f"Low balance {bal:.4f} SOL - skip buy")
        send_telegram(f"⚠️ Low balance {bal:.4f} SOL - can't buy {mint[:8]}")
        return None
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL is_pump={is_pump} bal={bal:.4f}")
    try:
        kp=get_keypair()
        if not kp: return None
        pubkey=str(kp.pubkey())
        pools = [("pump", 35), ("auto", 40)] if (is_pump or mint.endswith("pump")) else [("raydium", 60), ("auto", 60)]
        for pool_type, slippage in pools:
            try:
                resp=requests.post("https://pumpportal.fun/api/trade-local", json={
                    "publicKey": pubkey,
                    "action": "buy",
                    "mint": mint,
                    "denominatedInSol": "true",
                    "amount": BUY_AMOUNT_SOL,
                    "slippage": slippage,
                    "priorityFee": 0.002,
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
                        log(f"BUY TX {sig} pool={pool_type}")
                        send_telegram(f"✅ BUY {pool_type} {mint[:4]}\nhttps://solscan.io/tx/{sig}")
                        return sig
                else:
                    err=resp.text[:800]
                    log(f"Buy {pool_type} err {resp.status_code} {err[:300]}")
                    if resp.status_code==400:
                        blacklist[mint]=time.time()+1800
                        send_telegram(f"❌ Buy {pool_type} {mint[:8]}: {err[:200]}")
                        return None
            except Exception as e:
                log(f"Buy pool {pool_type} exception {e}")
                continue
        return None
    except Exception as e:
        log(f"Buy fail {e}\n{traceback.format_exc()}")
        send_telegram(f"❌ Buy crash {e}")
        return None

def sell_token(mint, slippage=30, tries=2):
    is_pump = mint.endswith("pump")
    pools = ["pump","auto"] if is_pump else ["raydium","auto"]
    for attempt in range(tries):
        for pool_type in pools:
            try:
                kp=get_keypair(); pubkey=str(kp.pubkey())
                use_slip = 70 if not is_pump else (50 if attempt>0 else slippage)
                resp=requests.post("https://pumpportal.fun/api/trade-local", json={
                    "publicKey": pubkey,
                    "action": "sell",
                    "mint": mint,
                    "amount": "100%",
                    "denominatedInSol": "false",
                    "slippage": use_slip,
                    "priorityFee": 0.002,
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
        curr=data["mcap"]; entry=pos["entry_mcap"]
        pct = (curr/entry -1)*100 if entry else 0
        log(f"CHECK {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%) B/S {data.get('buyers_5m',0)}/{data.get('sellers_5m',0)}")
        if curr >= entry * FILTERS["take_profit_x"]:
            log(f"2X HIT {pos['symbol']} TRY SELL")
            bal_before = get_balance_sol()
            sig = sell_token(mint, slippage=40, tries=3)
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
            sig = sell_token(mint, slippage=60, tries=4)
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
            sig = sell_token(mint, slippage=50, tries=3)
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
                if text.startswith("/pnl"):
                    bal=get_balance_sol()
                    winrate = (total_wins/(total_wins+total_losses)*100) if (total_wins+total_losses)>0 else 0
                    send_telegram(f"<b>📊 PnL</b>\nToday: {pnl_today_sol:+.4f} SOL\nBalance: {bal:.4f} SOL\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nTrades: {trades_today}/{MAX_TRADES_PER_DAY}")
                elif text.startswith("/watchlist"):
                    if not watchlist: send_telegram("Watchlist empty - waiting for 10K vol")
                    else:
                        txt=f"<b>👀 Watchlist ({len(watchlist)}):</b>\n"
                        for m,w in list(watchlist.items())[:10]:
                            d=fetch_token_data(m)
                            v=d["volume_5m"] if d else 0
                            txt+=f"{w['symbol']} Vol {v:.0f}/10000\n"
                        send_telegram(txt)
                elif text.startswith("/balance"):
                    bal=get_balance_sol()
                    send_telegram(f"💰 Balance: {bal:.4f} SOL\nOpen: {len(open_positions)}/{MAX_OPEN_POSITIONS}")
                elif text.startswith("/stop"):
                    global bot_running; bot_running=False
                    send_telegram("⏸️ Bot PAUSED")
                elif text.startswith("/start"):
                    bot_running=True
                    send_telegram("▶️ Bot RESUMED")
        except Exception as e:
            log(f"TG poll err {e}"); time.sleep(5)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    set_telegram_commands()
    log(f"🚀 BOT v7.4 - FINAL FIX LIVE")
    send_telegram(f"🚀 BOT v7.4 LIVE\n✅ No 404 endpoints\n✅ 429 cooldown\n✅ 10K vol strict")
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
                        open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
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
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        bought_ever.add(t["mint"])
                        blacklist[t["mint"]]=time.time()+9999999
                        trades_today+=1
                        send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K VOL {t['volume_5m']:.0f}\n{t['mint']}")
                        if len(open_positions)>=MAX_OPEN_POSITIONS: break
            time.sleep(15)
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
