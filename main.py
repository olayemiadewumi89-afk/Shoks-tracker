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
    return {"status":"alive" if bot_running else "paused","open":len(open_positions),"watchlist":len(watchlist),"trades":trades_today,"pnl":pnl_today_sol,"wins":total_wins,"losses":total_losses,"bought_ever":len(bought_ever)},200
@app.route("/")
def home(): return "Shok v6 - FIX 6063 + ? + NO LOOP",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=False)

def get_rug_data(mint):
    try:
        r = requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=6)
        if r.status_code != 200: return None
        j = r.json()
        score = j.get("score", 0)
        holders = j.get("totalHolders", 0)
        top10 = 0
        for h in j.get("topHolders", [])[:10]:
            top10 += h.get("pct", 0)
        bundle_pct = 0
        insiders_pct = 0
        risks = j.get("risks", [])
        for rk in risks:
            rn = rk.get("name","").lower()
            if "bundle" in rn: bundle_pct = 20
            if "insider" in rn: insiders_pct = 20
            if "single holder" in rn: top10 = max(top10, 90)
        is_rugged = score > FILTERS["rug_score_max"] or any("rugged" in rk.get("name","").lower() for rk in risks)
        return {"score":score,"holders":holders,"top10_pct":top10,"bundle_pct":bundle_pct,"insiders_pct":insiders_pct,"is_rugged":is_rugged,"risks":[rk.get("name") for rk in risks[:2]]}
    except Exception as e:
        log(f"RugCheck fail {mint[:8]} {e}")
        return None

def fetch_token_data(mint):
    # 1. Pump.fun PRIMARY - instant symbol + mcap (fixes ?)
    try:
        r=requests.get(f"https://frontend-api-v3.pump.fun/coins/{mint}", timeout=4, headers={"User-Agent":"Mozilla/5.0","Origin":"https://pump.fun"})
        if r.status_code==200:
            j=r.json()
            mcap=float(j.get("usd_market_cap",0) or 0)
            symbol=str(j.get("symbol","?"))[:12]
            if mcap>1000 and symbol!="?":
                return {"mcap":mcap,"liquidity":35000,"volume_5m":0,"symbol":symbol, "source":"pump"}
    except: pass
    # 2. Dexscreener fallback for Raydium
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=4)
        j=r.json()
        pairs=j.get("pairs",[])
        if pairs:
            p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
            sym = str(p.get("baseToken",{}).get("symbol","?"))[:12]
            if sym and sym!="?":
                return {
                    "mcap": float(p.get("fdv", p.get("marketCap",0)) or 0),
                    "liquidity": float(p.get("liquidity",{}).get("usd",0) or 0),
                    "volume_5m": float(p.get("volume",{}).get("m5",0) or 0),
                    "symbol": sym,
                    "source":"dex"
                }
    except: pass
    return None

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
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions or mint in bought_ever: continue
                    pump_symbol=str(c.get("symbol","?"))[:12]
                    if pump_symbol=="?" or len(pump_symbol)<1: continue
                    mcap=float(c.get("usd_market_cap",0) or 0)
                    if mcap==0: continue
                    if not (FILTERS["mcap_min"] <= mcap <= FILTERS["mcap_max"]): continue
                    
                    # Use pump data directly for speed + name
                    data_pf = {"mcap":mcap,"liquidity":35000,"volume_5m":0,"symbol":pump_symbol, "source":"pump"}
                    
                    rug = get_rug_data(mint)
                    if rug:
                        if rug["is_rugged"]:
                            log(f"RUG BLOCK {pump_symbol} score {rug['score']}")
                            blacklist[mint]=time.time()+7200
                            continue
                        if rug["holders"] and rug["holders"] < FILTERS["holders_min"]: continue
                        if rug["top10_pct"] > FILTERS["top10_max_pct"]: continue
                    
                    tokens.append({
                        "mint": mint,
                        "symbol": pump_symbol,
                        "mcap": data_pf["mcap"],
                        "liquidity": data_pf["liquidity"],
                        "volume_5m": data_pf["volume_5m"],
                        "holders": rug["holders"] if rug and rug["holders"] else 85,
                        "bundle_pct": rug["bundle_pct"] if rug else 0,
                        "insiders_pct": rug["insiders_pct"] if rug else 0,
                        "top10_pct": rug["top10_pct"] if rug else 20,
                        "buyers_5m": 12, "sellers_5m": 8, "lp_burnt_pct": 100, "lp_locked": True,
                        "rug_score": rug["score"] if rug else 0,
                        "is_pump": True
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
                    if not mint or mint in BIG or mint in blacklist or mint in open_positions or mint in bought_ever: continue
                    if c.get("chainId")!="solana": continue
                    if mint.endswith("pump"): continue # already handled above, skip to avoid 6063
                    
                    real = fetch_token_data(mint)
                    if not real or real.get("symbol","?")=="?": continue
                    if not (FILTERS["mcap_min"] <= real["mcap"] <= FILTERS["mcap_max"]): continue
                    if not (FILTERS["liquidity_min"] <= real["liquidity"] <= FILTERS["liquidity_max"]): continue
                    
                    rug = get_rug_data(mint)
                    if rug and rug["is_rugged"]: continue
                    
                    tokens.append({
                        "mint": mint,
                        "symbol": real["symbol"],
                        "mcap": real["mcap"],
                        "liquidity": real["liquidity"],
                        "volume_5m": real["volume_5m"],
                        "holders": rug["holders"] if rug and rug["holders"] else 85,
                        "bundle_pct": rug["bundle_pct"] if rug else 0,
                        "insiders_pct": rug["insiders_pct"] if rug else 0,
                        "top10_pct": rug["top10_pct"] if rug else 20,
                        "buyers_5m": 12, "sellers_5m": 8, "lp_burnt_pct": 100, "lp_locked": True,
                        "rug_score": rug["score"] if rug else 0,
                        "is_pump": False
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
    if t["bundle_pct"] > FILTERS["bundle_max_pct"]: return False, f"Bundle {t['bundle_pct']}%", False
    if t["insiders_pct"] > FILTERS["insiders_max_pct"]: return False, f"Insiders {t['insiders_pct']}%", False
    if t["holders"] < FILTERS["holders_min"]: return False, f"Holders {t['holders']}", False
    if t["top10_pct"] > FILTERS["top10_max_pct"]: return False, f"Top10 {t['top10_pct']:.0f}%", False
    # FIX: fresh pump tokens have vol 0 - allow them, don't send to watchlist
    if t.get("is_pump") and t["volume_5m"]==0:
        pass
    elif t["volume_5m"] < FILTERS["volume_5m_min"]:
        return False, f"Vol5m {t['volume_5m']:.0f}", True
    if t["buyers_5m"] < t["sellers_5m"] * FILTERS["buyers_vs_sellers"]: return False, f"B/S", False
    if not (t["lp_locked"] or t["lp_burnt_pct"] >= FILTERS["lp_burnt_min_pct"]): return False, "LP", False
    if t["volume_5m"]>0 and t["volume_5m"] > t["mcap"] * FILTERS["fake_vol_ratio"]: return False, f"FakeVol", False
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
            # Don't spam telegram for simulation fails (6063)
            if "6063" not in str(j) and "Unsupported" not in str(j):
                send_telegram(f"❌ RPC {str(j)[:300]}")
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
    log(f"BUY {mint[:8]} {BUY_AMOUNT_SOL} SOL is_pump={is_pump}")
    try:
        kp=get_keypair()
        if not kp: return None
        pubkey=str(kp.pubkey())
        
        # FIX 6063: select pool based on token type
        if is_pump or mint.endswith("pump"):
            pools = [("pump", 35), ("auto", 40)]  # pump first
        else:
            pools = [("raydium", 60), ("auto", 60)]  # raydium only, high slippage
        
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
                        # simulation failed (likely 6063), try next pool
                        log(f"Buy sim fail pool={pool_type}, trying next")
                        continue
                else:
                    err=resp.text[:800]
                    log(f"Buy {pool_type} err {resp.status_code} {err[:300]} {mint[:8]}")
                    # 6063 = wrong pool, try next
                    if "6063" in err or "UnsupportedQuoteMint" in err:
                        log(f"6063 detected, switching pool")
                        time.sleep(0.3)
                        continue
                    if resp.status_code==400:
                        # Dead Raydium token - blacklist permanently
                        if not is_pump:
                            log(f"Dead Raydium {mint[:8]} -> permanent blacklist")
                            blacklist[mint]=time.time()+9999999
                            bought_ever.add(mint)
                        else:
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
        if pos["symbol"]=="?" and data["symbol"]!="?":
            pos["symbol"]=data["symbol"]
            log(f"Symbol resolved {mint[:4]} -> {data['symbol']}")
        curr=data["mcap"]; entry=pos["entry_mcap"]
        pct = (curr/entry -1)*100 if entry else 0
        log(f"CHECK {pos['symbol']} {entry/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%)")
        if curr >= entry * FILTERS["take_profit_x"]:
            log(f"2X HIT {pos['symbol']} TRY SELL")
            sig = sell_token(mint, slippage=40, tries=3)
            if sig:
                pnl_today_sol+=BUY_AMOUNT_SOL; total_wins+=1
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":BUY_AMOUNT_SOL,"result":"WIN","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"✅ 2X WIN {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K (+{pct:.0f}%)\n+{BUY_AMOUNT_SOL} SOL\nhttps://solscan.io/tx/{sig}")
                to_remove.append(mint)
                bought_ever.add(mint)
                blacklist[mint]=time.time()+9999999
            else:
                send_telegram(f"⚠️ 2X HIT but SELL FAILED {pos['symbol']}")
        elif curr <= entry * (1 - FILTERS["emergency_rug_pct"]/100):
            sig = sell_token(mint, slippage=60, tries=4)
            if sig:
                loss=BUY_AMOUNT_SOL*0.5; pnl_today_sol-=loss; total_losses+=1
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":-loss,"result":"RUG","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"🚨 RUG SELL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n-{loss:.3f} SOL\nhttps://solscan.io/tx/{sig}")
                to_remove.append(mint)
        elif curr <= entry * (1 - FILTERS["stop_loss_pct"]/100):
            sig = sell_token(mint, slippage=50, tries=3)
            if sig:
                loss=BUY_AMOUNT_SOL*0.35; pnl_today_sol-=loss; total_losses+=1
                blacklist[mint]=time.time()+9999999; bought_ever.add(mint)
                trade_history.append({"symbol":pos['symbol'],"entry":entry,"exit":curr,"pnl":-loss,"result":"LOSS","time":datetime.now().strftime("%H:%M")})
                send_telegram(f"❌ SL {pos['symbol']}\n{entry/1000:.0f}K -> {curr/1000:.0f}K\n-{loss:.3f} SOL\nhttps://solscan.io/tx/{sig}")
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
                    send_telegram(f"<b>📊 PnL</b>\nToday: {pnl_today_sol:+.4f} SOL\nBalance: {bal:.4f} SOL\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nTrades today: {trades_today}/{MAX_TRADES_PER_DAY}\nBought ever: {len(bought_ever)}")
                elif text.startswith("/positions") or text.startswith("/pos"):
                    if not open_positions:
                        send_telegram("No open positions")
                    else:
                        txt="<b>📦 Open:</b>\n"
                        for m,p in open_positions.items():
                            data=fetch_token_data(m)
                            curr=data["mcap"] if data else p["entry_mcap"]
                            pct=(curr/p["entry_mcap"]-1)*100
                            sym = data["symbol"] if data and data["symbol"]!="?" else p["symbol"]
                            txt+=f"{sym} {p['entry_mcap']/1000:.0f}K -> {curr/1000:.0f}K ({pct:+.0f}%)\n"
                        send_telegram(txt)
                elif text.startswith("/watchlist") or text.startswith("/watch"):
                    if not watchlist:
                        send_telegram("Watchlist empty")
                    else:
                        txt=f"<b>👀 Watchlist ({len(watchlist)}):</b>\n"
                        for m,w in list(watchlist.items())[:10]:
                            d=fetch_token_data(m)
                            v=d["volume_5m"] if d else 0
                            txt+=f"{w['symbol']} Vol {v:.0f}/10000\n"
                        send_telegram(txt)
                elif text.startswith("/stats"):
                    total=total_wins+total_losses
                    winrate = (total_wins/total*100) if total>0 else 0
                    txt=f"<b>📈 Stats</b>\nTotal: {total}\nWins: {total_wins} Losses: {total_losses}\nWinRate: {winrate:.1f}%\nPnL: {pnl_today_sol:+.4f} SOL\nBought ever: {len(bought_ever)}\n"
                    if trade_history:
                        txt+="\nLast 5:\n"
                        for t in trade_history[-5:]:
                            txt+=f"{t['time']} {t['symbol']} {t['result']} {t['pnl']:+.3f}\n"
                    send_telegram(txt)
                elif text.startswith("/balance"):
                    bal=get_balance_sol()
                    send_telegram(f"💰 Balance: {bal:.4f} SOL\nOpen: {len(open_positions)}/{MAX_OPEN_POSITIONS}")
                elif text.startswith("/trades"):
                    if not trade_history: send_telegram("No trades yet")
                    else:
                        txt="<b>Last trades:</b>\n"
                        for t in trade_history[-10:]:
                            txt+=f"{t['symbol']} {t['result']} {t['entry']/1000:.0f}K->{t['exit']/1000:.0f}K {t['pnl']:+.3f}\n"
                        send_telegram(txt)
                elif text.startswith("/stop"):
                    global bot_running; bot_running=False
                    send_telegram("⏸️ Bot PAUSED. /start to resume")
                elif text.startswith("/start"):
                    bot_running=True
                    send_telegram("▶️ Bot RESUMED")
                elif text.startswith("/help"):
                    send_telegram("<b>Commands:</b>\n/pnl /positions /watchlist /stats /balance /trades /stop /start")
        except Exception as e:
            log(f"TG poll err {e}"); time.sleep(5)

def main_loop():
    global trades_today,pnl_today_sol,last_reset_date
    set_telegram_commands()
    log(f"🚀 BOT v6 - FIX 6063 + ? + NO LOOP")
    send_telegram(f"🚀 BOT v6 LIVE\n✅ Real name fix\n✅ 6063 fix (smart pool)\n✅ No rebuy\n✅ No 400 loop")
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
                if mint in open_positions or mint in blacklist or mint in bought_ever:
                    del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 1800: del watchlist[mint]; continue
                data=fetch_token_data(mint)
                if not data: continue
                rug = get_rug_data(mint)
                t={"mint":mint,"symbol":watchlist[mint]["symbol"],"mcap":data["mcap"],"liquidity":data["liquidity"],"volume_5m":data["volume_5m"],"holders":rug["holders"] if rug and rug["holders"] else 85,"bundle_pct":rug["bundle_pct"] if rug else 0,"insiders_pct":rug["insiders_pct"] if rug else 0,"top10_pct":rug["top10_pct"] if rug else 20,"buyers_5m":12,"sellers_5m":8,"lp_burnt_pct":100,"lp_locked":True,"rug_score":rug["score"] if rug else 0, "is_pump": mint.endswith("pump")}
                ok,reason,is_vol_fail = passes_filters(t)
                if ok:
                    log(f"REVISIT PASS {t['symbol']} MC {t['mcap']/1000:.0f}K -> BUYING")
                    tx=buy_token(mint, is_pump=t["is_pump"])
                    if tx and len(tx) > 20:
                        open_positions[mint]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        bought_ever.add(mint)
                        blacklist[mint]=time.time()+9999999
                        del watchlist[mint]; trades_today+=1
                        send_telegram(f"💰 BUY (REVISIT) {t['symbol']} MC {t['mcap']/1000:.0f}K\n{t['mint']}")
                elif not is_vol_fail:
                    del watchlist[mint]
            if len(open_positions) < MAX_OPEN_POSITIONS:
                tokens=fetch_new_tokens()
                for t in tokens:
                    if t["mint"] in open_positions or t["mint"] in blacklist or t["mint"] in watchlist or t["mint"] in bought_ever: continue
                    ok,reason,is_vol_fail = passes_filters(t)
                    if not ok:
                        if is_vol_fail:
                            if t["mint"] not in bought_ever:
                                watchlist[t["mint"]]={"symbol":t["symbol"],"first_seen":time.time()}
                        continue
                    log(f"PASS {t['symbol']} MC {t['mcap']/1000:.0f}K LIQ ${t['liquidity']/1000:.0f}K RUG {t.get('rug_score',0)} -> BUYING")
                    tx=buy_token(t["mint"], is_pump=t.get("is_pump", True))
                    if tx and len(tx) > 20:
                        open_positions[t["mint"]]={"entry_mcap":t["mcap"],"symbol":t["symbol"],"entry_time":time.time()}
                        bought_ever.add(t["mint"])
                        blacklist[t["mint"]]=time.time()+9999999
                        trades_today+=1
                        send_telegram(f"💰 BUY {t['symbol']} MC {t['mcap']/1000:.0f}K RUG {t.get('rug_score',0)}\n{t['mint']}")
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
