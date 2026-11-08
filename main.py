import os, time, threading, json, queue, traceback, re
from datetime import date, datetime
from flask import Flask
import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from base58 import b58decode
import websocket, base64

RPC_URL = os.getenv("RPC_URL","")
PRIVATE_KEY_B58 = os.getenv("PRIVATE_KEY","")
BOT_TOKEN = os.getenv("BOT_TOKEN","")
CHAT_ID = os.getenv("CHAT_ID","")
DRY_RUN = os.getenv("DRY_RUN","true").lower() == "true"

BUY_AMOUNT = 0.005
MAX_OPEN = 1
MAX_TRADES_DAY = 5
DAILY_LOSS_LIMIT = 0.05
STOP_LOSS = 35
EMERGENCY_RUG = 45
TAKE_PROFIT_X = 2.0

# FIXED: Looser at birth to catch HEREWEGO early
FILTERS = {
    "mcap_min": 20000,
    "mcap_max": 900000,
    "liquidity_min": 15000,
    "volume_5m_min": 3000,
    "holders_min": 25,
    "bundle_max": 25,
    "insiders_max": 20,
    "top10_max": 35,
    "rug_score_max": 4000
}

# FIXED: Tight for buy but allows 800K runners
FILTERS_MOMENTUM = {
    "mcap_min": 80000,
    "mcap_max": 800000,
    "liquidity_min": 18000,
    "holders_min": 120,
    "top10_max": 25,
    "bundle_max": 15,
    "insiders_max": 15,
    "volume_5m_min": 3000,
    "buys_m5_min": 30,
    "buy_sell_ratio_min": 2.5,
    "price_m5_min": 10,
    "price_m15_min": 20,
    "holders_growth_min": 10,
    "dip_min_pct": 50
}

open_positions = {}
blacklist = {}
watchlist = {}
momentum_watch = {}
trades_today = 0
pnl_today = 0.0
last_reset = date.today()
ws_q = queue.Queue()
ws_ok = False
bot_running = True
threads_started = False
seen_mints = set()

app = Flask(__name__)

def log(m): 
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)

def tg(t):
    if not BOT_TOKEN or not CHAT_ID: 
        return
    try: 
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":t}, timeout=8)
    except Exception as e:
        log(f"TG fail {e}")

@app.route("/health")
def health(): 
    return {
        "dry_run":DRY_RUN,
        "ws":ws_ok,
        "watch":len(watchlist),
        "momentum_watch":len(momentum_watch),
        "open":len(open_positions),
        "trades":trades_today,
        "queue":ws_q.qsize()
    },200

@app.route("/")
def home(): 
    return f"v11.2 FIXED DRY={DRY_RUN} Watch={len(watchlist)} Mom={len(momentum_watch)} Open={len(open_positions)}",200

def ws_on_message(ws, message):
    try:
        d=json.loads(message)
        mint=d.get("mint") or d.get("ca") or (d.get("data",{}).get("mint") if isinstance(d.get("data"),dict) else None)
        if not mint or len(mint)<32: 
            return
        # FIX 1: Don't block on seen_mints forever
        if mint in blacklist or mint in open_positions or mint in watchlist: 
            return
        ws_q.put(mint)
        seen_mints.add(mint)
    except:
        pass

def ws_on_open(ws):
    global ws_ok
    ws_ok=True
    log("WS Connected")
    tg(f"WS Connected v11.2 DRY={DRY_RUN}")
    try: 
        ws.send(json.dumps({"method":"subscribeNewToken"}))
    except Exception as e:
        log(f"WS sub fail {e}")

def ws_on_close(ws,c,m):
    global ws_ok
    ws_ok=False

def ws_on_error(ws,e): 
    log(f"WS Err {e}")

def ws_loop():
    global ws_ok
    while True:
        try:
            w=websocket.WebSocketApp("wss://pumpportal.fun/api/data", on_open=ws_on_open, on_message=ws_on_message, on_error=ws_on_error, on_close=ws_on_close)
            w.run_forever(ping_interval=20, ping_timeout=15)
            ws_ok=False
            time.sleep(5)
        except Exception as e:
            ws_ok=False
            log(f"WS crash {e}")
            time.sleep(5)

def get_balance():
    try:
        kp=Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
        r=requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[str(kp.pubkey())]}, timeout=8).json()
        return r.get("result",{}).get("value",0)/1e9
    except:
        return 0

def get_keypair():
    try: return Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
    except: return None

def fetch_dex(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429,403): 
            time.sleep(0.5)
            return None
        pairs=r.json().get("pairs",[])
        if not pairs: return None
        p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
        vol = p.get("volume",{})
        txns = p.get("txns",{})
        pc = p.get("priceChange",{})
        return {
            "mcap":float(p.get("fdv",0)or 0), 
            "liquidity":float(p.get("liquidity",{}).get("usd",0)or 0), 
            "volume_5m":float(vol.get("m5",0)or 0),
            "volume_1h":float(vol.get("h1",0)or 0),
            "volume_6h":float(vol.get("h6",0)or 0),
            "buys_m5": int(txns.get("m5",{}).get("buys",0) or 0),
            "sells_m5": int(txns.get("m5",{}).get("sells",0) or 0),
            "price_m5": float(pc.get("m5",0) or 0),
            "price_m15": float(pc.get("m15",0) or 0),
            "price_h1": float(pc.get("h1",0) or 0),
            "symbol":str(p.get("baseToken",{}).get("symbol","?"))[:12]
        }
    except:
        return None

def extract_number_pct(text):
    try:
        m = re.search(r'(\d+(?:\.\d+)?)\s*%', str(text))
        if m: return float(m.group(1))
        return 0
    except:
        return 0

def fetch_rug(mint):
    try:
        r=requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code!=200: return None
        j=r.json()
        holders=j.get("totalHolders",0)
        top10=sum([h.get("pct",0) for h in j.get("topHolders",[])[:10]])
        bundle=0; insiders=0
        for rk in j.get("risks",[]):
            n=str(rk.get("name","")).lower()
            val = str(rk.get("value","")) + " " + str(rk.get("description",""))
            if "bundle" in n:
                num = extract_number_pct(val)
                bundle = max(bundle, num*2 if num<10 and num>0 else num if num>0 else 15)
            if "insider" in n:
                num = extract_number_pct(val)
                insiders = max(insiders, num if num>0 else 15)
            if "single holder" in n: top10=max(top10,90)
        return {"score":j.get("score",0),"holders":holders,"top10":top10,"bundle":bundle,"insiders":insiders}
    except:
        return None

def strict_pass(t):
    if not (FILTERS["mcap_min"] <= t["mcap"] <= FILTERS["mcap_max"]): return False, f"mcap {t['mcap']:.0f}"
    if t["liquidity"] < FILTERS["liquidity_min"]: return False, f"liq {t['liquidity']:.0f}"
    if t["volume_5m"] < FILTERS["volume_5m_min"]: return False, f"vol {t['volume_5m']:.0f}"
    if t["holders"] < FILTERS["holders_min"]: return False, f"holders {t['holders']}"
    if t["bundle"] > FILTERS["bundle_max"]: return False, f"bundle {t['bundle']}%"
    if t["insiders"] > FILTERS["insiders_max"]: return False, f"insiders {t['insiders']}%"
    if t["top10"] > FILTERS["top10_max"]: return False, f"top10 {t['top10']:.0f}%"
    if t["rug_score"] > FILTERS["rug_score_max"]: return False, f"score {t['rug_score']}"
    return True, "PASS"

def momentum_pass(dex, rug, track):
    f = FILTERS_MOMENTUM
    if not (f["mcap_min"] <= dex["mcap"] <= f["mcap_max"]): return False, f"mom mcap {dex['mcap']/1000:.0f}K"
    if dex["liquidity"] < f["liquidity_min"]: return False, f"mom liq {dex['liquidity']:.0f}"
    if rug["holders"] < f["holders_min"]: return False, f"mom holders {rug['holders']}"
    if rug["top10"] > f["top10_max"]: return False, f"mom top10 {rug['top10']:.0f}%"
    if rug["bundle"] > f["bundle_max"]: return False, f"mom bundle {rug['bundle']}%"
    if rug["insiders"] > f["insiders_max"]: return False, f"mom insiders {rug['insiders']}%"
    if dex["volume_5m"] < f["volume_5m_min"]: return False, f"mom vol {dex['volume_5m']:.0f}"
    if dex["buys_m5"] < f["buys_m5_min"]: return False, f"mom buys {dex['buys_m5']}"
    ratio = dex["buys_m5"] / max(1, dex["sells_m5"])
    if ratio < f["buy_sell_ratio_min"]: return False, f"mom ratio {ratio:.1f}"
    if dex["price_m5"] < f["price_m5_min"]: return False, f"mom p m5 {dex['price_m5']:.0f}%"
    if dex["price_m15"] < f["price_m15_min"]: return False, f"mom p m15 {dex['price_m15']:.0f}%"
    if track:
        growth = rug["holders"] - track.get("holders_prev", rug["holders"])
        if growth < f["holders_growth_min"]: return False, f"mom growth {growth}"
        if dex["liquidity"] <= track.get("liq_prev", dex["liquidity"]): return False, f"mom liq not growing"
    return True, f"PASS ratio {ratio:.1f}"

def send_tx(b64):
    try:
        pl={"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[b64,{"encoding":"base64","skipPreflight":False}]}
        r=requests.post(RPC_URL, json=pl, timeout=15).json()
        sig=r.get("result")
        if not sig: return None
        for _ in range(12):
            time.sleep(2)
            st=requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[sig],{"searchTransactionHistory":True}]}, timeout=8).json()
            v=st.get("result",{}).get("value",[None])[0]
            if v and v.get("confirmationStatus") in ("confirmed","finalized") and not v.get("err"):
                return sig
            if v and v.get("err"): return None
        return sig
    except:
        return None

def buy(mint):
    if DRY_RUN:
        log(f"DRY BUY {mint[:8]}")
        return "DRY_RUN_SIG"
    bal=get_balance()
    if bal < BUY_AMOUNT+0.008: return None
    kp=get_keypair()
    if not kp: return None
    try:
        resp=requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":str(kp.pubkey()),"action":"buy","mint":mint,"denominatedInSol":"true","amount":BUY_AMOUNT,"slippage":50,"priorityFee":0.005,"pool":"auto"}, timeout=20)
        if resp.status_code!=200: return None
        try: vtx=VersionedTransaction.from_bytes(resp.content)
        except:
            tx_bytes=base64.b64decode(''.join(resp.text.strip().split()))
            vtx=VersionedTransaction.from_bytes(tx_bytes)
        signed=VersionedTransaction(vtx.message,[kp])
        return send_tx(base64.b64encode(bytes(signed)).decode())
    except:
        return None

def sell(mint):
    kp=get_keypair()
    if not kp: return None
    for slip in [40,70,100]:
        try:
            resp=requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":str(kp.pubkey()),"action":"sell","mint":mint,"amount":"100%","denominatedInSol":"false","slippage":slip,"priorityFee":0.005,"pool":"auto"}, timeout=20)
            if resp.status_code!=200: continue
            try: vtx=VersionedTransaction.from_bytes(resp.content)
            except:
                tx_bytes=base64.b64decode(''.join(resp.text.strip().split()))
                vtx=VersionedTransaction.from_bytes(tx_bytes)
            signed=VersionedTransaction(vtx.message,[kp])
            sig=send_tx(base64.b64encode(bytes(signed)).decode())
            if sig: return sig
        except:
            continue
    return None

def check_positions():
    global trades_today, pnl_today
    remove=[]
    for mint,pos in list(open_positions.items()):
        data=fetch_dex(mint)
        if not data: continue
        entry=pos["entry"]; curr=data["mcap"]
        pct=(curr/entry-1)*100 if entry else 0
        log(f"CHECK {pos['symbol']} {entry/1000:.0f}K->{curr/1000:.0f}K {pct:+.0f}%")
        should_sell=None
        if curr >= entry*TAKE_PROFIT_X: should_sell="WIN"
        elif curr <= entry*(1-EMERGENCY_RUG/100): should_sell="RUG"
        elif curr <= entry*(1-STOP_LOSS/100): should_sell="STOP"
        if should_sell:
            profit_sol = (curr/entry - 1) * BUY_AMOUNT if entry else 0
            if DRY_RUN or pos.get("is_paper"):
                tg(f"DRY CLOSE {should_sell} {pos['symbol']} {pct:+.0f}% PnL {profit_sol:+.4f}")
                pnl_today += profit_sol
                trades_today += 1
                remove.append(mint)
                continue
            s=None
            for _ in range(3):
                s=sell(mint)
                if s: break
                time.sleep(2)
            if s:
                tg(f"{should_sell} {pos['symbol']} {pct:+.0f}% https://solscan.io/tx/{s}")
                pnl_today += profit_sol
                trades_today += 1
                remove.append(mint)
    for m in remove: 
        open_positions.pop(m,None)

def main_loop():
    global trades_today, pnl_today, last_reset, bot_running
    log(f"v11.2 NEW LOOP START")
    last_ws_log = time.time()
    while True:
        try:
            if date.today()!=last_reset:
                trades_today=0; pnl_today=0; last_reset=date.today()
            if not bot_running:
                time.sleep(5); continue
            now=time.time()
            if now - last_ws_log > 120:
                log(f"WS ok={ws_ok} q={ws_q.qsize()} watch={len(watchlist)} mom={len(momentum_watch)} open={len(open_positions)}")
                last_ws_log = now
            if pnl_today <= -DAILY_LOSS_LIMIT:
                time.sleep(300); continue
            while not ws_q.empty() and len(watchlist)<50:
                try:
                    m=ws_q.get_nowait()
                    if m not in blacklist and m not in open_positions and m not in watchlist:
                        watchlist[m]={"first_seen":now,"symbol":f"WS-{m[:4]}"}
                except: break
            for mint in list(watchlist.keys()):
                if mint in open_positions or mint in blacklist:
                    del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 21600:
                    del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] < 30: continue # FIX: was 90 -> 30 sec faster
                dex=fetch_dex(mint)
                if not dex: continue
                rug=fetch_rug(mint)
                if not rug: continue
                t={"mcap":dex["mcap"],"liquidity":dex["liquidity"],"volume_5m":dex["volume_5m"],"holders":rug["holders"],"bundle":rug["bundle"],"insiders":rug["insiders"],"top10":rug["top10"],"rug_score":rug["score"]}
                ok,reason=strict_pass(t)
                if not ok:
                    # FIX 2: holders/vol/liq -> move to MOM, not trash
                    if "vol" in reason or "holders" in reason or "liq" in reason:
                        momentum_watch[mint] = {
                            "first_seen": now,
                            "holders_prev": rug["holders"],
                            "liq_prev": dex["liquidity"],
                            "mcap_low": dex["mcap"],
                            "ath": dex["mcap"],
                            "last_check": now
                        }
                        del watchlist[mint]
                        log(f"NEW early -> MOM tracker {dex['symbol']} {reason} MC {dex['mcap']/1000:.0f}K")
                        continue
                    del watchlist[mint]
                    log(f"FILTER FAIL NEW {mint[:8]} {reason}")
                    continue
                if len(open_positions)>=MAX_OPEN: continue
                if trades_today>=MAX_TRADES_DAY: time.sleep(60); continue
                sig=buy(mint)
                if sig:
                    if DRY_RUN:
                        open_positions[mint]={"entry":dex["mcap"],"symbol":dex["symbol"]+"-NEW","time":now,"is_paper":True}
                        blacklist[mint]=now+86400
                        del watchlist[mint]
                        tg(f"📝 PAPER BUY NEW {dex['symbol']} MC {dex['mcap']/1000:.0f}K\nhttps://dexscreener.com/solana/{mint}")
                    else:
                        open_positions[mint]={"entry":dex["mcap"],"symbol":dex["symbol"],"time":now,"is_paper":False}
                        blacklist[mint]=now+9999999; del watchlist[mint]; trades_today+=1
                        tg(f"🚀 BUY NEW {dex['symbol']} https://solscan.io/tx/{sig}")
            time.sleep(3)
        except Exception as e:
            log(f"MAIN CRASH {e}\n{traceback.format_exc()}"); time.sleep(10)

def fetch_dex_trending_mints():
    mints = []
    try:
        # FIX 3: lastTrade not market_cap - catches pumping NOW coins like HEREWEGO
        r = requests.get("https://frontend-api-v2.pump.fun/coins?offset=0&limit=100&sort=lastTrade&order=DESC&includeNsfw=false", timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code == 200:
            data = r.json()
            coins = data if isinstance(data, list) else data.get("coins", [])
            for c in coins:
                mc = float(c.get("usd_market_cap",0) or 0)
                if 30000 <= mc <= 1600000:
                    mint = c.get("mint")
                    if mint: mints.append(mint)
    except Exception as e:
        log(f"pump trending fail {e}")
    return mints[:50]

def momentum_loop():
    global trades_today
    log("MOMENTUM DEX DIRECT START - independent")
    while True:
        try:
            if not bot_running:
                time.sleep(10); continue
            now=time.time()
            candidates = fetch_dex_trending_mints()
            for m in list(momentum_watch.keys()):
                if m not in candidates:
                    candidates.append(m)

            log(f"MOM DEX scan {len(candidates)} candidates")
            for mint in candidates[:40]:
                if mint in open_positions: continue
                if mint in blacklist and now < blacklist[mint] - 1000:
                    continue
                dex = fetch_dex(mint)
                if not dex: continue
                if not (FILTERS_MOMENTUM["mcap_min"] <= dex["mcap"] <= FILTERS_MOMENTUM["mcap_max"]):
                    track = momentum_watch.get(mint)
                    if track:
                        track["ath"] = max(track.get("ath",0), dex["mcap"])
                        track["mcap_low"] = min(track.get("mcap_low", dex["mcap"]), dex["mcap"])
                    continue
                rug = fetch_rug(mint)
                if not rug: continue
                
                track = momentum_watch.get(mint)
                if not track:
                    momentum_watch[mint] = {
                        "first_seen": now,
                        "holders_prev": rug["holders"],
                        "liq_prev": dex["liquidity"],
                        "mcap_low": dex["mcap"],
                        "ath": dex["mcap"],
                        "last_check": now - 60
                    }
                    continue
                if now - track.get("last_check",0) < 45: continue
                track["last_check"] = now
                track["ath"] = max(track.get("ath",0), dex["mcap"])
                track["mcap_low"] = min(track.get("mcap_low", dex["mcap"]), dex["mcap"])

                ok, reason = momentum_pass(dex, rug, track)
                if not ok:
                    if now - track["first_seen"] > 180:
                        track["holders_prev"] = rug["holders"]
                        track["liq_prev"] = dex["liquidity"]
                    continue

                if len(open_positions)>=MAX_OPEN: continue
                if trades_today>=MAX_TRADES_DAY: continue

                log(f"🚀 MOM DEX PASS {dex['symbol']} {dex['mcap']/1000:.0f}K buys {dex['buys_m5']}/{dex['sells_m5']} m5 +{dex['price_m5']:.0f}%")
                sig = buy(mint)
                if sig:
                    if DRY_RUN:
                        open_positions[mint]={"entry":dex["mcap"],"symbol":dex["symbol"]+"-MOM","time":now,"is_paper":True}
                        blacklist[mint]=now+86400
                        momentum_watch.pop(mint,None)
                        tg(f"📈 PAPER BUY MOM {dex['symbol']} MC {dex['mcap']/1000:.0f}K Buys {dex['buys_m5']}/{dex['sells_m5']} +{dex['price_m5']:.0f}% Vol {dex['volume_5m']:.0f}\nhttps://dexscreener.com/solana/{mint}")
                    else:
                        open_positions[mint]={"entry":dex["mcap"],"symbol":dex["symbol"]+"-MOM","time":now,"is_paper":False}
                        blacklist[mint]=now+9999999; momentum_watch.pop(mint,None); trades_today+=1
                        tg(f"📈 BUY MOM {dex['symbol']} MC {dex['mcap']/1000:.0f}K https://solscan.io/tx/{sig}")
            time.sleep(15)
        except Exception as e:
            log(f"MOM DEX CRASH {e}\n{traceback.format_exc()}")
            time.sleep(10)

def pos_loop():
    while True:
        try:
            if open_positions: check_positions()
            time.sleep(3)
        except Exception as e:
            log(f"pos err {e}")
            time.sleep(3)

def tg_loop():
    global DRY_RUN, bot_running
    off=0
    while True:
        try:
            if not BOT_TOKEN:
                time.sleep(10); continue
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params={"offset":off,"timeout":20}, timeout=25).json()
            for upd in r.get("result",[]):
                off=upd["update_id"]+1
                msg=upd.get("message",{})
                if CHAT_ID and str(msg.get("chat",{}).get("id",""))!=str(CHAT_ID): continue
                txt=(msg.get("text") or ""); low=txt.lower()
                if not low.startswith("/"): continue
                if "/balance" in low:
                    tg(f"Bal {get_balance():.4f} SOL DRY={DRY_RUN} Watch {len(watchlist)} Mom {len(momentum_watch)} Open {len(open_positions)}")
                if "/positions" in low or "/pos" in low:
                    if not open_positions: tg("No open")
                    else:
                        lines=[]
                        for m,p in open_positions.items():
                            d=fetch_dex(m)
                            if d:
                                pct=(d['mcap']/p['entry']-1)*100 if p['entry'] else 0
                                lines.append(f"{p['symbol']} {p['entry']/1000:.0f}K->{d['mcap']/1000:.0f}K {pct:+.0f}%")
                        tg("\n".join(lines))
                if "/watch" in low:
                    tg(f"Watch {len(watchlist)} Mom {len(momentum_watch)}")
                if "/mom" in low:
                    tg(f"Mom {len(momentum_watch)} tracked")
                if "/pnl" in low:
                    tg(f"PnL {pnl_today:+.4f} Trades {trades_today}")
                if "/dry" in low and "off" in low:
                    DRY_RUN=False; tg("DRY OFF LIVE")
                if "/dry" in low and "on" in low:
                    DRY_RUN=True; tg("DRY ON")
                if "/stop" in low:
                    bot_running=False; tg("PAUSED")
                if "/start" in low or "/resume" in low:
                    bot_running=True; tg("RESUMED")
                if "/sellall" in low:
                    for m in list(open_positions.keys()): sell(m)
                    open_positions.clear(); tg("Closed all")
        except Exception as e:
            log(f"TG err {e}"); time.sleep(5)

def clear_seen():
    while True:
        time.sleep(7200)
        seen_mints.clear()
        log("seen_mints cleared - allow re-entry")

def start_background_threads():
    global threads_started
    if threads_started: return
    threads_started = True
    threading.Thread(target=ws_loop,daemon=True).start()
    threading.Thread(target=pos_loop,daemon=True).start()
    threading.Thread(target=tg_loop,daemon=True).start()
    threading.Thread(target=main_loop,daemon=True).start()
    threading.Thread(target=momentum_loop,daemon=True).start()
    threading.Thread(target=clear_seen,daemon=True).start()
    log("All threads v11.2 FIXED started")

start_background_threads()

if __name__=="__main__":
    port=int(os.getenv("PORT",10000))
    app.run(host="0.0.0.0", port=port, debug=False)
