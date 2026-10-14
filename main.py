import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter

WALLETS_FILE = "wallets.json"
GROUPS_FILE = "groups.json"
LABELS_FILE = "labels.json"
SEEN_FILE = "seen.json"
RECENT_FILE = "recent.json"

SOL_WALLETS = []
GROUPS = {}
LABELS = {}
seen_sigs = set()
recent_children = defaultdict(list)
recent_token_buys = defaultdict(list)
sniper_marked = {}
tg_queue = deque()
funder_cache = {}
last_tx_time = datetime.now()
last_bundled = None
last_activity = {}
last_dist_alert = {}
best_alert_cache = {}

SOLANA_PUB_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://solana.api.onchainkit.io"
]
EVM_RPCS = {
    "base": ["https://rpc.ankr.com/base", "https://base.meowrpc.com"],
    "bsc": ["https://rpc.ankr.com/bsc", "https://bsc.meowrpc.com"],
}
rpc_idx = 0
last_rpc_call = 0

def get_public_rpc():
    global rpc_idx, last_rpc_call
    now = time.time()
    if now - last_rpc_call < 0.35:
        time.sleep(0.35 - (now - last_rpc_call))
    last_rpc_call = time.time()
    url = SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]
    rpc_idx += 1
    return url

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def auto_set_commands():
    try:
        if not BOT_TOKEN: return
        cmds = [
            {"command":"bundle","description":"Bundle SOL or 0x EVM auto"},
            {"command":"labelgroup","description":"Label bundle to FOMOFARMER"},
            {"command":"listgroups","description":"List all groups"},
            {"command":"listwallets","description":"List wallets in group"},
            {"command":"remove","description":"Remove wallet or group"},
            {"command":"status","description":"Bot status V8 NEW"},
            {"command":"start","description":"Start bot"},
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
        print("✅ Commands auto-set V8 NEW", flush=True)
    except Exception as e:
        print(f"setCommands err {e}", flush=True)

app = Flask(__name__)
@app.route('/')
def home(): return "Shoks V8 NEW ONLY", 200
@app.route('/health')
def health(): return "OK", 200
def run_flask(): app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))

def send_tg_worker():
    while True:
        try:
            if tg_queue:
                text = tg_queue.popleft()
                if not BOT_TOKEN or not CHAT_ID: print(text[:800], flush=True)
                else:
                    try:
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15)
                    except: pass
                time.sleep(0.6)
            else: time.sleep(0.2)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker, daemon=True).start()

def send_tg(text):
    if len(tg_queue) > 200: tg_queue.popleft()
    tg_queue.append(text)

def short(a): return f"{a[:4]}..{a[-4:]}" if len(a)>8 else a
def is_evm(addr): return addr.startswith("0x") and len(addr)==42
def detect_chain(addr): return "base" if is_evm(addr) else "sol"
def wallet_exists(addr): return any((w["addr"] if isinstance(w, dict) else w)==addr for w in SOL_WALLETS)
def get_wallet_chain(addr):
    for w in SOL_WALLETS:
        if isinstance(w, dict) and w["addr"]==addr: return w.get("chain","sol")
    return "base" if is_evm(addr) else "sol"

def display_group(wallet_or_group):
    if not wallet_or_group: return "?"
    gid = GROUPS.get(wallet_or_group, wallet_or_group)
    return LABELS.get(gid, LABELS.get(wallet_or_group, short(gid)))

def load_all():
    global SOL_WALLETS, GROUPS, LABELS, seen_sigs, recent_children
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE)); raw=d.get("sol",[])
            if raw and len(raw)>0 and isinstance(raw[0], str):
                SOL_WALLETS = [{"addr":x,"chain":"sol","group":d.get("groups",{}).get(x,x)} for x in raw]
            else:
                SOL_WALLETS = raw
            if "groups" in d and d["groups"]: GROUPS.update(d["groups"])
    except: pass
    try:
        if os.path.exists(GROUPS_FILE): GROUPS.update(json.load(open(GROUPS_FILE)))
    except: pass
    try:
        if os.path.exists(LABELS_FILE): LABELS.update(json.load(open(LABELS_FILE)))
    except: pass
    try:
        if os.path.exists(SEEN_FILE): seen_sigs=set(json.load(open(SEEN_FILE)))
    except: pass
    try:
        if os.path.exists(RECENT_FILE):
            d=json.load(open(RECENT_FILE))
            for k,v in d.items():
                lst=[]
                for item in v:
                    try:
                        a,b,c_str,d_flag,e = item
                        c = datetime.fromisoformat(c_str)
                        if (datetime.now()-c).total_seconds() < 3600:
                            lst.append((a,b,c,d_flag,e))
                    except: continue
                recent_children[k]=lst
    except: pass

def save_all():
    try:
        json.dump({"sol":SOL_WALLETS,"groups":GROUPS}, open(WALLETS_FILE,"w"))
        json.dump(GROUPS, open(GROUPS_FILE,"w"))
        json.dump(LABELS, open(LABELS_FILE,"w"))
    except: pass

def save_seen():
    try: json.dump(list(seen_sigs)[-2000:], open(SEEN_FILE,"w"))
    except: pass

def save_recent():
    try:
        out={k: [(a,b,c.isoformat(),d,e) for a,b,c,d,e in v[-15:]] for k,v in recent_children.items()}
        json.dump(out, open(RECENT_FILE,"w"))
    except: pass

load_all()

BLACKLIST_MINTS=["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"]

def get_token_info(token_address):
    if token_address in BLACKLIST_MINTS: return None
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=7).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=20]
        if not valid: valid=pairs
        best=sorted(valid, key=lambda x: x.get("liquidity",{}).get("usd",0) or 0, reverse=True)[0]
        fdv=best.get("fdv") or best.get("marketCap") or 0
        if fdv>=1_000_000: mcap_str=f"${fdv/1_000_000:.2f}M"
        elif fdv>=1000: mcap_str=f"${fdv/1000:.0f}k"
        else: mcap_str=f"${fdv:.0f}"
        price=float(best.get("priceUsd",0) or 0)
        # FIX: DON'T RETURN NONE WHEN PRICE 0 - early Pump coin
        is_early = price == 0
        if price==0: price = 0.00000001
        age_min=0
        try:
            pc=best.get("pairCreatedAt")
            if pc: age_min=int((time.time()*1000-pc)/60000)
        except: pass
        vol24 = float(best.get("volume",{}).get("h24",0) or 0)
        vol5 = float(best.get("volume",{}).get("m5",0) or 0)
        liq = float(best.get("liquidity",{}).get("usd",0) or 0)
        chg5 = float(best.get("priceChange",{}).get("m5",0) or 0)
        return {"name":best.get("baseToken",{}).get("symbol",token_address[:6]),"mcap_str":mcap_str,"mcap_val":fdv,"price":price,"dex_link":f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}","photon":f"https://photon-sol.tinyastro.io/en/lp/{token_address}" if best.get("chainId")=="solana" else f"https://photon.tinyastro.io/en/lp/{token_address}","pair":best.get("pairAddress",token_address),"chain":best.get("chainId","solana"),"age_min":age_min,"vol24":vol24,"vol5":vol5,"liq":liq,"chg5":chg5,"is_early":is_early}
    except: return None

def get_signatures(wallet, limit=10):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":limit}]}
        return requests.post(get_public_rpc(), json=payload, timeout=12).json().get("result",[]) or []
    except: return []

def get_tx_details(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<600:
        return funder_cache[sig][1]
    sol_transfers=[]; token_transfers=[]
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        res=requests.post(get_public_rpc(), json=payload, timeout=14).json().get("result")
        if not res: funder_cache[sig]=(datetime.now(),([],[])); return [],[]
        for instr in res.get("transaction",{}).get("message",{}).get("instructions",[]):
            try:
                if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                    info=instr["parsed"]["info"]; sol_transfers.append((info["source"],info["destination"],info.get("lamports",0)/1e9))
            except: continue
        for inner in res.get("meta",{}).get("innerInstructions",[]) or []:
            for instr in inner.get("instructions",[]):
                try:
                    if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                        info=instr["parsed"]["info"]; sol_transfers.append((info["source"],info["destination"],info.get("lamports",0)/1e9))
                except: continue
        pre={(b.get("owner",""),b.get("mint","")): float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0) for b in res.get("meta",{}).get("preTokenBalances",[]) or []}
        for b in res.get("meta",{}).get("postTokenBalances",[]) or []:
            owner=b.get("owner",""); mint=b.get("mint","")
            if mint in BLACKLIST_MINTS: continue
            post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            diff=post_amt-pre.get((owner,mint),0)
            if abs(diff)>0.000001:
                token_transfers.append(("unknown",owner,mint,abs(diff), diff>0))
    except: pass
    funder_cache[sig]=(datetime.now(),(sol_transfers,token_transfers))
    if len(funder_cache)>700: funder_cache.clear()
    return sol_transfers, token_transfers

def get_sol_balance(wallet):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getBalance","params":[wallet]}
        r=requests.post(get_public_rpc(), json=payload, timeout=8).json()
        lamports=r.get("result",{}).get("value",0) if isinstance(r.get("result"), dict) else r.get("result",0)
        return lamports/1e9
    except: return 999

def get_evm_txs(address, chain="base", limit=10):
    try:
        for rpc in EVM_RPCS[chain]:
            payload={"jsonrpc":"2.0","id":1,"method":"ankr_getTransactionsByAddress","params":{"address":address,"pageSize":limit,"blockchain":chain}}
            r=requests.post(rpc, json=payload, timeout=12).json()
            txs=r.get("result",{}).get("transactions",[]) or r.get("result",[]) or []
            if txs: return txs[:limit]
        return []
    except: return []

def parse_evm_transfers(txs):
    out=[]
    for tx in txs:
        try:
            frm=tx.get("from","").lower()
            to=(tx.get("to") or "").lower()
            v=tx.get("value","0")
            eth=float(int(v,16)/1e18) if isinstance(v,str) and v.startswith("0x") else float(v or 0)
            out.append((frm,to,eth,tx.get("hash","")))
        except: continue
    return out

def handle_bundle(wallet):
    global last_bundled
    wallet=wallet.strip()
    chain=detect_chain(wallet)
    evm=is_evm(wallet)
    if not evm and len(wallet)<30: send_tg("❌ Invalid wallet"); return
    send_tg(f"🔍 FORWARD {short(wallet)} ⛓️ {chain.upper()}...")
    try:
        funders=[]
        if not evm:
            sigs=get_signatures(wallet,15)
            for item in sigs[:12]:
                sig=item.get("signature")
                if not sig: continue
                sols,_=get_tx_details(sig)
                for frm,to,amt in sols:
                    if to==wallet and amt>=0.001 and frm not in [f[0] for f in funders]:
                        funders.append((frm,amt,sig))
        else:
            for ch in ["base","bsc"]:
                txs=get_evm_txs(wallet, ch, 15)
                for frm,to,amt,hash_ in parse_evm_transfers(txs):
                    if to==wallet.lower() and amt>=0.0005 and frm not in [f[0] for f in funders]:
                        funders.append((frm,amt,hash_))
                        chain=ch
                if funders: break

        if not funders:
            if not wallet_exists(wallet):
                SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":wallet})
            GROUPS[wallet]=wallet
            LABELS[wallet]=f"CEX_{short(wallet).upper()}"
            last_bundled=wallet; save_all()
            last_activity[wallet]=datetime.now()
            send_tg(f"⚠️ No parent — CEX/BINANCE\n{short(wallet)} ⛓️ {chain.upper()}\nCEX - {short(wallet)}\nFORWARD tracking ON\n/labelgroup FOMOFARMER"); return

        main_funder=funders[0][0]; last_bundled=main_funder
        GROUPS[wallet]=main_funder; GROUPS[main_funder]=main_funder
        if not wallet_exists(main_funder):
            SOL_WALLETS.append({"addr":main_funder,"chain":chain,"group":main_funder})
        if not wallet_exists(wallet):
            SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":main_funder})
        last_activity[wallet]=datetime.now()
        last_activity[main_funder]=datetime.now()
        added=0
        if not evm:
            sib_sigs=get_signatures(main_funder,15)
            now_ts = time.time()
            for item in sib_sigs[:12]:
                bt = item.get("blockTime",0)
                if bt and (now_ts - bt) > 900: # 15m
                    continue
                sig=item.get("signature")
                if not sig: continue
                sols,_=get_tx_details(sig)
                for frm,to,amt in sols:
                    if frm==main_funder and to!=wallet and not wallet_exists(to) and amt>=0.001 and added<15:
                        SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                        recent_children[main_funder].append((to,amt,datetime.now(),False,None))
                        last_activity[to]=datetime.now()
        else:
            txs=get_evm_txs(main_funder, chain, 20)
            for frm,to,amt,hash_ in parse_evm_transfers(txs):
                if frm==main_funder.lower() and to!=wallet.lower() and not wallet_exists(to) and amt>=0.0005 and added<15:
                    SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                    recent_children[main_funder].append((to,amt,datetime.now(),False,None))
                    last_activity[to]=datetime.now()
        save_all(); save_recent()
        send_tg(f"📦 Bundled {short(wallet)} ⛓️ {chain.upper()}\nParent {short(main_funder)} G:{display_group(main_funder)}\n✅ +{added} NEW sibs (15m) | Total {len(SOL_WALLETS)}\nFORWARD ON\n/labelgroup FOMOFARMER")
    except Exception as e: send_tg(f"❌ Bundle error: {e}")

def alert_prep(funder):
    now=datetime.now()
    recent=[x for x in recent_children[funder] if (now-x[2]).total_seconds()<=600]
    recent_children[funder]=recent
    save_recent()
    if not recent: return
    sol_only=[x for x in recent if not x[3]]
    token_only=[x for x in recent if x[3]]
    gl=display_group(funder)
    chain=get_wallet_chain(funder).upper()
    if token_only:
        mint=token_only[-1][4] if len(token_only[-1])>4 else "token"
        count=len(token_only)
        key=(funder, mint)
        last = last_dist_alert.get(key)
        if last and (now-last[0]).total_seconds() < 120 and count <= last[1]+2:
            return
        last_dist_alert[key]=(now, count)
        info=get_token_info(mint) if mint and mint!="token" else None
        name=info["name"] if info else short(mint) if mint else "token"
        mcap=info["mcap_str"] if info else ""
        child_list="\n".join([f"• {short(c[0])} {c[1]:.2f} {name} +0.01 gas" if c[1]>=0.01 else f"• {short(c[0])} {c[1]:.0f} {name}" for c in token_only[-5:]])
        total=sum(c[1] for c in token_only)
        total_str = f"\nTotal {total:.0f}" if total>=1 else ""
        send_tg(f"🔴🔴🔴 DIST {count}w ABOUT TO SELL 🔴🔴🔴\nG:{gl} {short(funder)} ⛓️ {chain}\nToken {name} {mcap}\n{child_list}{total_str}\nSC: https://solscan.io/account/{funder}")
        return
    if len(sol_only)>=1:
        count=len(sol_only)
        key=(funder, "SOL_PREP")
        last = last_dist_alert.get(key)
        if last and (now-last[0]).total_seconds() < 90 and count <= last[1]+1:
            return
        last_dist_alert[key]=(now, count)
        biggest=max(sol_only, key=lambda x: x[1])
        biggest_amt=biggest[1]; biggest_child=biggest[0]
        if biggest_amt>=0.08:
            sniper_marked[biggest_child]={"funder":funder,"amt":biggest_amt,"time":now,"group":gl}
        childs_str=" ".join([f"{short(c[0])} {c[1]:.3f}S" for c in sol_only[:5]])
        sniper_str=f"\n🎯 MAIN SNIPER {short(biggest_child)} {biggest_amt:.3f} 👑" if biggest_amt>=0.15 else ""
        cluster=f"\nCluster {len(sol_only)}w bundled same coin" if len(sol_only)>=2 else ""
        send_tg(f"⚠️ PREP {len(sol_only)}w 5m ⛓️ {chain}{cluster}\nG:{gl}\n{childs_str}{sniper_str}\nSC: https://solscan.io/account/{funder}")

def should_alert(mint, usd, info, wallet_group):
    now = datetime.now()
    recent = [x for x in recent_token_buys[mint] if (now - x[2]).total_seconds() <= 600]
    recent_token_buys[mint] = recent
    recent.append((wallet_group, usd, now))
    recent_token_buys[mint] = recent
    total_5m = sum(x[1] for x in recent)
    wallets_5m = len(set(x[0] for x in recent))
    age_min = info["age_min"]
    mcap = info["mcap_val"]
    vol5 = info["vol5"]
    vol24 = info["vol24"]
    liq = info["liq"]
    chg5 = info["chg5"]
    # FIX: allow early coins with low liq
    if liq < 1000 and age_min > 15:
        return None
    is_old = age_min > 14400
    is_dead_volume = vol24 < 2000 and vol5 < 500
    if is_old and is_dead_volume:
        if not (vol5 > 5000 and chg5 > 15 and liq > 15000):
            return None
    if 5 <= usd <= 300: # FIX was 20-200, now 5-300 to catch $80 buys
        if (mcap < 300000 and age_min < 180) or info.get("is_early"):
            return f"🟢 PROBE: 1w test BUY ${usd:.0f} at {info['mcap_str']} MC | {age_min}m old | vol5m ${vol5:.0f} | {info['name']}"
        else: return None
    if 80 <= total_5m <= 15000: # FIX was 500-8000, now 80-15000
        if mcap < 800000 and wallets_5m >= 2 or info.get("is_early"):
            if is_old:
                if vol5 > 8000 and chg5 > 20:
                    return f"♻️🟡 REVIVAL SWARM: {wallets_5m}w total ${total_5m:.0f} in 5m at {info['mcap_str']} | age {age_min//1440}d | vol5m ${vol5:.0f} +{chg5:.0f}% | {info['name']}"
                else: return None
            else:
                return f"🟡 SWARM: {wallets_5m}w total ${total_5m:.0f} in 5m at {info['mcap_str']} | {age_min}m | vol24 ${vol24:.0f} | {info['name']}"
    if usd >= 500: # FIX was 1000
        if is_old and is_dead_volume:
            if vol5 > 5000 and liq > 10000:
                return f"♻️🔵 REVIVAL WHALE: 1w BUY ${usd:.0f} at {info['mcap_str']} | {age_min//1440}d old but vol spike ${vol5:.0f} | {info['name']}"
            else: return None
        return f"🔵 WHALE: 1w BUY ${usd:.0f} at {info['mcap_str']} | {age_min}m | liq ${liq:.0f} | {info['name']}"
    return None

def get_best_bet():
    now=datetime.now()
    best_mint=None
    best_score=0
    best_data=None
    for mint, buys in list(recent_token_buys.items()):
        recent=[b for b in buys if (now-b[2]).total_seconds()<=600]
        if len(recent)<1: continue
        total=sum(b[1] for b in recent)
        wallets=len(set(b[0] for b in recent))
        biggest=max(b[1] for b in recent)
        if total < 50: continue # ignore dust
        score = total + (wallets*200) + biggest
        if score > best_score:
            best_score=score
            best_mint=mint
            best_data=(total,wallets,biggest,recent)
    if not best_mint: return None
    if best_mint in best_alert_cache and (now-best_alert_cache[best_mint]).total_seconds()<300:
        return None
    best_alert_cache[best_mint]=now
    total,wallets,biggest,recent = best_data
    info=get_token_info(best_mint)
    name=info["name"] if info else f"{best_mint[:6]}.."
    mcap=info["mcap_str"] if info else "early"
    return f"🔥🔥🔥 BEST BET 🔥🔥🔥\n{name} {mcap}\nTotal ${total:.0f} across {wallets}w | Biggest ${biggest:.0f}\nCA: {best_mint}\nDS: {info['dex_link'] if info else f'https://dexscreener.com/solana/{best_mint}'}\nPH: https://photon-sol.tinyastro.io/en/lp/{best_mint}"

async def track_sol_polling():
    while True:
        try:
            if last_activity:
                sorted_w = sorted(SOL_WALLETS, key=lambda wdict: last_activity.get(wdict["addr"] if isinstance(wdict, dict) else wdict, datetime.min), reverse=True)
                # FIX: include all last_bundled group wallets
                bundled_extra = [w for w in SOL_WALLETS if GROUPS.get(w["addr"] if isinstance(w,dict) else w,"")==last_bundled] if last_bundled else []
                active_list = (sorted_w[:30] + bundled_extra)[:60]
            else:
                active_list = SOL_WALLETS[-50:]
            for wdict in active_list:
                w=wdict["addr"] if isinstance(wdict,dict) else wdict
                chain=wdict.get("chain","sol") if isinstance(wdict,dict) else "sol"
                if chain!="sol": continue
                sigs=get_signatures(w,6)
                for item in sigs[:4]:
                    sig=item.get("signature")
                    if not sig or sig in seen_sigs: continue
                    sols,tokens=get_tx_details(sig)
                    for _,owner,mint,amt,is_buy in tokens:
                        if owner==w and amt>0 and is_buy:
                            info=get_token_info(mint)
                            if not info:
                                # FIX: Pump early coin with no Dexscreener price - still alert!
                                if amt>1000:
                                    send_tg(f"🟢 EARLY PUMP BUY (no price yet)\n👤 {short(w)} G:{display_group(w)}\nCA: {mint}\nAmount: {amt:.0f} tokens\nTX: https://solscan.io/tx/{sig}\nSC: https://solscan.io/account/{w}")
                                    seen_sigs.add(sig); save_seen()
                                    last_activity[w]=datetime.now()
                                    # Track for BEST BET even if no info
                                    recent_token_buys[mint].append((w, 20, datetime.now()))
                                continue
                            usd=amt*info["price"]
                            if info.get("is_early"): usd = max(usd, 15) # force at least $15 for early
                            if usd<5: continue
                            seen_sigs.add(sig); save_seen()
                            last_tx_time=datetime.now()
                            last_activity[w]=datetime.now()
                            is_sniper=w in sniper_marked
                            gl=display_group(w)
                            ds=info["dex_link"]; ph=info["photon"]; txl=f"https://solscan.io/tx/{sig}"
                            alert_text = should_alert(mint, usd, info, w)
                            if alert_text:
                                sniper_tag = " 👑 SNIPER" if is_sniper else ""
                                send_tg(f"{alert_text}\n👤 {short(w)} G:{gl}{sniper_tag}\nCA: {mint}\nVol24 ${info['vol24']:.0f} Vol5m ${info['vol5']:.0f} Liq ${info['liq']:.0f} {info['chg5']:.0f}% 5m\nDS: {ds}\nPH: {ph}\nTX: {txl}")
                        elif owner==w and amt>0 and not is_buy:
                            info=get_token_info(mint)
                            if not info: continue
                            usd=amt*info["price"]
                            if usd<100: continue
                            seen_sigs.add(sig); save_seen()
                            last_activity[w]=datetime.now()
                            gl=display_group(w)
                            ds=info["dex_link"]; ph=info["photon"]; txl=f"https://solscan.io/tx/{sig}"
                            send_tg(f"🔻 SELL ${usd:.0f} {info['mcap_str']}\n{info['name']} | {short(w)} G:{gl}\nCA: {mint[:6]}..{mint[-4:]}\nDS: {ds} PH: {ph} TX: {txl}")
            # BEST BET check every loop
            best = get_best_bet()
            if best: send_tg(best)
        except Exception as e: print(f"poll err {e}", flush=True)
        await asyncio.sleep(5)

def track_funders_15s():
    while True:
        time.sleep(10)
        try:
            funders=set(GROUPS.values())
            for k in recent_children.keys(): funders.add(k)
            if not funders: funders=set([(w["addr"] if isinstance(w,dict) else w) for w in SOL_WALLETS[:25]])
            print(f"Checking {len(funders)} funders...", flush=True)
            for funder in list(funders)[:50]:
                try:
                    chain=get_wallet_chain(funder)
                    if chain=="sol":
                        sigs=get_signatures(funder,12)
                        now_ts = time.time()
                        for item in sigs[:8]:
                            bt = item.get("blockTime",0)
                            if bt and (now_ts - bt) > 900: # FIX 15m not 5m
                                continue
                            sig=item.get("signature")
                            if not sig or sig in seen_sigs: continue
                            sols,tokens=get_tx_details(sig)
                            found=False
                            for frm,to,amt in sols:
                                if frm==funder and amt>=0.001:
                                    if not wallet_exists(to):
                                        SOL_WALLETS.append({"addr":to,"chain":"sol","group":GROUPS.get(funder,funder)}); GROUPS[to]=GROUPS.get(funder,funder)
                                    # FIX: always add to recent even if wallet exists
                                    recent_children[funder].append((to,amt,datetime.now(),False,None))
                                    seen_sigs.add(sig); save_seen(); save_recent(); save_all()
                                    last_activity[to]=datetime.now(); last_activity[funder]=datetime.now()
                                    found=True
                            for _,to2,mint,amt2,_ in tokens:
                                if amt2>0 and len(mint)>20:
                                    if not wallet_exists(to2):
                                        SOL_WALLETS.append({"addr":to2,"chain":"sol","group":GROUPS.get(funder,funder)}); GROUPS[to2]=GROUPS.get(funder,funder)
                                    recent_children[funder].append((to2,amt2,datetime.now(),True,mint))
                                    seen_sigs.add(sig); save_seen(); save_recent(); save_all()
                                    last_activity[to2]=datetime.now()
                                    found=True
                            if found:
                                alert_prep(funder)
                                print(f"PREP alert for {short(funder)}", flush=True)
                    else:
                        txs=get_evm_txs(funder, chain, 8)
                        for frm,to,amt,hash_ in parse_evm_transfers(txs):
                            if frm==funder.lower() and amt>=0.0005 and hash_ not in seen_sigs:
                                if not wallet_exists(to):
                                    SOL_WALLETS.append({"addr":to,"chain":chain,"group":GROUPS.get(funder,funder)}); GROUPS[to]=GROUPS.get(funder,funder)
                                recent_children[funder].append((to,amt,datetime.now(),False,None)); seen_sigs.add(hash_); save_seen(); save_recent(); save_all()
                                last_activity[to]=datetime.now(); alert_prep(funder)
                except Exception as e:
                    print(f"funder loop err {e}", flush=True)
                    continue
        except Exception as e: print(f"funder err {e}", flush=True)

def auto_clean_dead_wallets():
    while True:
        time.sleep(3600)
        try:
            if len(SOL_WALLETS) < 50: continue
            now=datetime.now()
            to_remove=[]
            checked=0
            for wdict in SOL_WALLETS[:]:
                if checked >= 20: break
                w=wdict["addr"] if isinstance(wdict,dict) else wdict
                chain=wdict.get("chain","sol") if isinstance(wdict,dict) else "sol"
                if chain!="sol": continue
                if w in GROUPS.values(): continue
                if w in sniper_marked: continue
                last_act=last_activity.get(w)
                if last_act and (now-last_act).total_seconds() < 86400: continue
                bal=get_sol_balance(w)
                checked+=1
                time.sleep(0.5)
                if bal < 0.005:
                    to_remove.append(wdict)
            if to_remove:
                for wdict in to_remove:
                    w=wdict["addr"] if isinstance(wdict,dict) else wdict
                    SOL_WALLETS.remove(wdict)
                    GROUPS.pop(w, None)
                    last_activity.pop(w, None)
                    sniper_marked.pop(w, None)
                save_all()
                send_tg(f"🧹 Auto-cleaned {len(to_remove)} dead wallets (<0.005 SOL, no activity 24h)\nNow {len(SOL_WALLETS)} wallets")
        except Exception as e:
            print(f"clean err {e}", flush=True)

def handle_command(text):
    global last_bundled
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if isinstance(w,dict) and w.get("chain")=="sol")
        evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"🚀 V8 FIXED - BEST BET\n{len(SOL_WALLETS)}w (SOL:{sol_c} EVM:{evm_c}) | {uniq} groups\nTracking 60 active\n/bundle <addr>")
    elif low.startswith("/bundle"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /bundle <addr>")
        else: threading.Thread(target=handle_bundle, args=(p[1],), daemon=True).start()
    elif low.startswith("/labelgroup"):
        p=t.split(maxsplit=1)
        if len(p)<2: send_tg("Usage: /labelgroup FOMOFARMER")
        else:
            nl=p[1].strip().upper().replace(" ","_")[:20]
            target=last_bundled or (SOL_WALLETS[-1]["addr"] if SOL_WALLETS and isinstance(SOL_WALLETS[-1],dict) else (SOL_WALLETS[-1] if SOL_WALLETS else None))
            if not target: send_tg("Bundle first"); return
            gk=GROUPS.get(target,target)
            LABELS[gk]=nl; save_all()
            send_tg(f"Label {short(gk)} -> {nl}")
    elif low.startswith("/listgroups"):
        c=Counter(GROUPS.values())
        if not c: send_tg("No groups")
        else:
            lines=[]
            for g,cnt in c.most_common(25):
                lbl=LABELS.get(g,"")
                name=lbl if lbl else short(g)
                extra=f" ({short(g)})" if lbl else ""
                lines.append(f"{name}: {cnt}w{extra}")
            send_tg("Groups:\n"+"\n".join(lines))
    elif low.startswith("/listwallets") or (low.startswith("/list") and not low.startswith("/listgroups")):
        parts=t.split(maxsplit=1)
        filter_gid=None
        if len(parts)>1:
            fg=parts[1].strip().upper()
            for gid,lbl in LABELS.items():
                if lbl==fg: filter_gid=gid; break
            if not filter_gid: filter_gid=parts[1].strip()
        wallets=SOL_WALLETS
        if filter_gid:
            wallets=[w for w in SOL_WALLETS if (w.get("group") if isinstance(w,dict) else GROUPS.get(w,w))==filter_gid or GROUPS.get(w.get("addr","") if isinstance(w,dict) else w,"")==filter_gid]
        if not wallets: send_tg("Empty")
        else:
            msg=f"{len(wallets)}w:\n"
            for wdict in wallets[-50:]:
                w=wdict["addr"] if isinstance(wdict,dict) else wdict
                ch=wdict.get("chain","sol").upper() if isinstance(wdict,dict) else "SOL"
                g=display_group(w); tag=" SNIPER" if w in sniper_marked else ""
                msg+=f"{short(w)} {ch} G:{g}{tag}\n"
            send_tg(msg)
    elif low.startswith("/remove"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /remove <addr>")
        else:
            a=p[1].strip()
            to_del=[w for w in SOL_WALLETS if (w["addr"] if isinstance(w,dict) else w)==a]
            if to_del:
                for w in to_del: SOL_WALLETS.remove(w); GROUPS.pop(w["addr"] if isinstance(w,dict) else w,None)
                save_all(); send_tg(f"Removed {short(a)}")
            else:
                gid=None
                for g,l in LABELS.items():
                    if l==a.upper(): gid=g; break
                if gid:
                    SOL_WALLETS[:]=[w for w in SOL_WALLETS if (w.get("group") if isinstance(w,dict) else GROUPS.get(w,w))!=gid and (w.get("addr") if isinstance(w,dict) else w)!=gid]
                    GROUPS.pop(gid,None); LABELS.pop(gid,None); save_all(); send_tg(f"Group {a} removed")
                else: send_tg("Not found")
    elif low.startswith("/clear"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); recent_children.clear(); sniper_marked.clear(); recent_token_buys.clear(); last_dist_alert.clear(); save_all(); send_tg("Cleared")
    elif low.startswith("/status"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        sol_c=sum(1 for w in SOL_WALLETS if isinstance(w,dict) and w.get("chain")=="sol")
        evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"V8 FIXED BEST BET\nWallets {len(SOL_WALLETS)} (SOL:{sol_c} EVM:{evm_c})\nGroups {len(set(GROUPS.values()))}\nTracking 60 active\nLast {mins}m")

def poll_commands():
    off=0
    while True:
        try:
            if not BOT_TOKEN: time.sleep(5); continue
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]+1
                txt=u.get("message",{}).get("text","")
                if txt.startswith("/"): handle_command(txt)
        except: time.sleep(3)

async def main_loop():
    threading.Thread(target=track_funders_15s,daemon=True).start()
    threading.Thread(target=auto_clean_dead_wallets,daemon=True).start()
    send_tg("🚀 V8 FIXED DEPLOYED\n✅ Catches 0.0015 fundings (15m)\n✅ Picks coins with no price (Pump early)\n✅ BEST BET for 10 coins")
    await track_sol_polling()

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e: print(f"CRASH {e}", flush=True); time.sleep(5); start_bot()

if __name__=="__main__":
    auto_set_commands()
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=poll_commands,daemon=True).start()
    time.sleep(1); start_bot()
