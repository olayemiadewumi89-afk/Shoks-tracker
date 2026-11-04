import os, time, threading, json, queue, traceback
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
STOP_LOSS = 35          # -35% close as you wanted
EMERGENCY_RUG = 45      # -45% emergency rug cut
TAKE_PROFIT_X = 2.0     # 2x profit

FILTERS = {
    "mcap_min": 40000,
    "mcap_max": 900000,
    "liquidity_min": 20000,
    "volume_5m_min": 8000,
    "holders_min": 50,
    "bundle_max": 25,
    "insiders_max": 20,
    "top10_max": 35,
    "rug_score_max": 4000
}

open_positions = {}
blacklist = {}
watchlist = {}
trades_today = 0
pnl_today = 0.0
last_reset = date.today()
ws_q = queue.Queue()
ws_ok = False
bot_running = True
app = Flask(__name__)

def log(m): print(f"[{datetime.now().strftime('%H:%M:%S')}] {m}", flush=True)
def tg(t):
    if not BOT_TOKEN or not CHAT_ID: return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":t}, timeout=8)
    except: pass

@app.route("/health")
def health(): return {"dry_run":DRY_RUN,"ws":ws_ok,"watch":len(watchlist),"open":len(open_positions),"trades":trades_today,"stop_loss":STOP_LOSS,"take_profit":TAKE_PROFIT_X},200
@app.route("/")
def home(): return f"v10.4 LIVE-READY DRY_RUN={DRY_RUN} STOP={STOP_LOSS}% TP={TAKE_PROFIT_X}x",200
def run_flask(): app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)), debug=False)

def ws_on_message(ws, message):
    try:
        d=json.loads(message)
        mint=d.get("mint") or d.get("ca") or (d.get("data",{}).get("mint") if isinstance(d.get("data"),dict) else None)
        if not mint or len(mint)<32: return
        if mint in blacklist or mint in open_positions or mint in watchlist: return
        if mint in list(ws_q.queue): return
        log(f"WS NEW {mint[:8]}")
        ws_q.put(mint)
    except: pass

def ws_on_open(ws):
    global ws_ok
    ws_ok=True
    log("WS Connected")
    tg(f"WS Connected v10.4 DRY_RUN={DRY_RUN} STOP={STOP_LOSS}% TP={TAKE_PROFIT_X}x")

def ws_on_close(ws,c,m):
    global ws_ok
    ws_ok=False
    log(f"WS Closed {c}")
def ws_on_error(ws,e): log(f"WS Err {e}")

def ws_loop():
    global ws_ok
    while True:
        try:
            w=websocket.WebSocketApp("wss://pumpportal.fun/api/data", on_open=ws_on_open, on_message=ws_on_message, on_error=ws_on_error, on_close=ws_on_close)
            w.run_forever(ping_interval=20, ping_timeout=15)
            ws_ok=False
            time.sleep(5)
        except:
            ws_ok=False
            time.sleep(5)

def get_balance():
    try:
        kp=Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
        r=requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[str(kp.pubkey())]}, timeout=8).json()
        return r.get("result",{}).get("value",0)/1e9
    except: return 0

def get_keypair():
    try: return Keypair.from_bytes(b58decode(PRIVATE_KEY_B58.strip()))
    except: return None

def fetch_dex(mint):
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429,403): return None
        pairs=r.json().get("pairs",[])
        if not pairs: return None
        p=sorted(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)), reverse=True)[0]
        return {"mcap":float(p.get("fdv",0)or 0), "liquidity":float(p.get("liquidity",{}).get("usd",0)or 0), "volume_5m":float(p.get("volume",{}).get("m5",0)or 0), "symbol":str(p.get("baseToken",{}).get("symbol","?"))[:12]}
    except: return None

def fetch_rug(mint):
    try:
        r=requests.get(f"https://api.rugcheck.xyz/v1/tokens/{mint}/report", timeout=6, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code in (429,403): return None
        if r.status_code!=200: return None
        j=r.json()
        score=j.get("score",0)
        holders=j.get("totalHolders",0)
        top10=sum([h.get("pct",0) for h in j.get("topHolders",[])[:10]])
        bundle=0; insiders=0
        for rk in j.get("risks",[]):
            n=rk.get("name","").lower()
            if "bundle" in n: bundle=25
            if "insider" in n: insiders=25
            if "single holder" in n: top10=max(top10,90)
        return {"score":score,"holders":holders,"top10":top10,"bundle":bundle,"insiders":insiders}
    except: return None

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

def send_tx(b64):
    try:
        pl={"jsonrpc":"2.0","id":1,"method":"sendTransaction","params":[b64,{"encoding":"base64","skipPreflight":False}]}
        r=requests.post(RPC_URL, json=pl, timeout=15).json()
        sig=r.get("result")
        if not sig:
            log(f"send err {r}")
            return None
        for _ in range(12):
            time.sleep(2)
            st=requests.post(RPC_URL, json={"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses","params":[[sig],{"searchTransactionHistory":True}]}, timeout=8).json()
            v=st.get("result",{}).get("value",[None])[0]
            if v and v.get("confirmationStatus") in ("confirmed","finalized") and not v.get("err"):
                return sig
            if v and v.get("err"):
                log(f"tx failed {v.get('err')}")
                return None
        return sig
    except Exception as e:
        log(f"send_tx {e}")
        return None

def buy(mint):
    if DRY_RUN:
        log(f"DRY RUN WOULD BUY {mint[:8]} - no tx sent")
        return "DRY_RUN_SIG"
    bal=get_balance()
    if bal < BUY_AMOUNT+0.008:
        log(f"low bal {bal:.4f} skip {mint[:8]}")
        tg(f"Low bal {bal:.4f} SOL - cannot buy {mint[:6]}")
        return None
    kp=get_keypair()
    try:
        resp=requests.post("https://pumpportal.fun/api/trade-local", json={"publicKey":str(kp.pubkey()),"action":"buy","mint":mint,"denominatedInSol":"true","amount":BUY_AMOUNT,"slippage":50,"priorityFee":0.005,"pool":"auto"}, timeout=20)
        if resp.status_code!=200:
            log(f"buy portal {resp.status_code} {resp.text[:150]}")
            return None
        try: vtx=VersionedTransaction.from_bytes(resp.content)
        except:
            b64=''.join(resp.text.strip().split())
            tx_bytes=base64.b64decode(b64)
            vtx=VersionedTransaction.from_bytes(tx_bytes)
        signed=VersionedTransaction(vtx.message,[kp])
        sig=send_tx(base64.b64encode(bytes(signed)).decode())
        return sig
    except Exception as e:
        log(f"buy ex {e}")
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
        except: continue
    return None

def check_positions():
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
            if DRY_RUN:
                tg(f"DRY CLOSE {should_sell} {pos['symbol']} {pct:+.0f}%")
                remove.append(mint)
                continue
            s=None
            for attempt in range(3):
                s=sell(mint)
                if s: break
                time.sleep(2)
            if s:
                tg(f"{should_sell} {pos['symbol']} {pct:+.0f}% https://solscan.io/tx/{s}")
                remove.append(mint)
            else:
                log(f"SELL FAIL {pos['symbol']} - retry next loop")
                if pct <= -60:
                    tg(f"FORCE CLOSE {pos['symbol']} {pct:.0f}%")
                    remove.append(mint)
    for m in remove: open_positions.pop(m,None)

def main_loop():
    global trades_today, pnl_today, last_reset, bot_running
    log(f"v10.4 START DRY_RUN={DRY_RUN} STOP={STOP_LOSS}%")
    tg(f"v10.4 START DRY={DRY_RUN} STOP {STOP_LOSS}% TP {TAKE_PROFIT_X}x")
    while True:
        try:
            if date.today()!=last_reset:
                trades_today=0; pnl_today=0; last_reset=date.today()
            if not bot_running:
                time.sleep(5); continue
            now=time.time()
            if pnl_today <= -DAILY_LOSS_LIMIT:
                log("Daily loss limit"); time.sleep(300); continue
            while not ws_q.empty() and len(watchlist)<50:
                try:
                    m=ws_q.get_nowait()
                    if m not in blacklist and m not in open_positions and m not in watchlist:
                        watchlist[m]={"first_seen":now,"symbol":f"WS-{m[:4]}"}
                        log(f"WATCHLIST ADD {m[:8]} total {len(watchlist)}")
                except: break
            for mint in list(watchlist.keys()):
                if mint in open_positions or mint in blacklist:
                    del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] > 1800:
                    del watchlist[mint]; continue
                if now - watchlist[mint]["first_seen"] < 90: continue
                dex=fetch_dex(mint)
                if not dex: continue
                rug=fetch_rug(mint)
                if not rug: continue
                t={"mcap":dex["mcap"],"liquidity":dex["liquidity"],"volume_5m":dex["volume_5m"],"holders":rug["holders"],"bundle":rug["bundle"],"insiders":rug["insiders"],"top10":rug["top10"],"rug_score":rug["score"]}
                ok,reason=strict_pass(t)
                if not ok:
                    if "vol" in reason: continue
                    del watchlist[mint]
                    log(f"FILTER FAIL {mint[:8]} {reason}")
                    continue
                if len(open_positions)>=MAX_OPEN: continue
                if trades_today>=MAX_TRADES_DAY: time.sleep(60); continue
                log(f"STRICT PASS {dex['symbol']} MC {dex['mcap']/1000:.0f}K -> BUY")
                sig=buy(mint)
                if sig:
                    if DRY_RUN:
                        tg(f"📝 PAPER BUY {dex['symbol']} MC {dex['mcap']/1000:.0f}K https://dexscreener.com/solana/{mint}")
                        blacklist[mint]=now+3600; del watchlist[mint]
                    else:
                        open_positions[mint]={"entry":dex["mcap"],"symbol":dex["symbol"],"time":now}
                        blacklist[mint]=now+9999999; del watchlist[mint]; trades_today+=1
                        tg(f"🚀 BUY {dex['symbol']} MC {dex['mcap']/1000:.0f}K https://solscan.io/tx/{sig}")
            time.sleep(5)
        except Exception as e:
            log(f"MAIN CRASH {e}\n{traceback.format_exc()}"); time.sleep(10)

def pos_loop():
    while True:
        try:
            if open_positions: check_positions()
            time.sleep(3)
        except: time.sleep(3)

def tg_loop():
    global DRY_RUN, bot_running
    off=0
    while True:
        try:
            if not BOT_TOKEN: time.sleep(10); continue
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params={"offset":off,"timeout":20}, timeout=25).json()
            for upd in r.get("result",[]):
                off=upd["update_id"]+1
                msg=upd.get("message",{})
                if CHAT_ID and str(msg.get("chat",{}).get("id",""))!=str(CHAT_ID): continue
                txt=(msg.get("text") or ""); low=txt.lower()
                if not low.startswith("/"): continue
                log(f"TG cmd {low}")
                if "/balance" in low:
                    tg(f"Bal {get_balance():.4f} SOL DRY={DRY_RUN} WS={ws_ok} Watch {len(watchlist)} Open {len(open_positions)}")
                if "/positions" in low or "/pos" in low:
                    if not open_positions: tg("No open positions")
                    else:
                        lines=[]
                        for m,p in open_positions.items():
                            d=fetch_dex(m)
                            if d:
                                pct=(d['mcap']/p['entry']-1)*100 if p['entry'] else 0
                                lines.append(f"{p['symbol']} {p['entry']/1000:.0f}K->{d['mcap']/1000:.0f}K {pct:+.0f}%")
                        tg("\n".join(lines))
                if "/watch" in low:
                    tg(f"Watch {len(watchlist)} coins")
                if "/pnl" in low:
                    tg(f"PnL {pnl_today:+.4f} Trades {trades_today} STOP {STOP_LOSS}% TP {TAKE_PROFIT_X}x")
                if "/dry" in low and "off" in low:
                    DRY_RUN=False; tg("DRY OFF - LIVE ⚠️")
                if "/dry" in low and "on" in low:
                    DRY_RUN=True; tg("DRY ON - safe ✅")
                if "/stop" in low:
                    bot_running=False; tg("⏸️ PAUSED buying, selling still active")
                if "/start" in low or "/resume" in low:
                    bot_running=True; tg("▶️ RESUMED")
                if "/sellall" in low:
                    for m in list(open_positions.keys()): sell(m)
                    open_positions.clear(); tg("Closed all")
        except Exception as e:
            log(f"TG err {e}"); time.sleep(5)

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=ws_loop,daemon=True).start()
    threading.Thread(target=pos_loop,daemon=True).start()
    threading.Thread(target=tg_loop,daemon=True).start()
    main_loop()
