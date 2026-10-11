import os, asyncio, threading, time, requests, json, re
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter

# ========= CONFIG =========
WALLETS_FILE = "wallets.json"
GROUPS_FILE = "groups.json"
LABELS_FILE = "labels.json"
SEEN_FILE = "seen.json"

SOL_WALLETS = []
GROUPS = {}
LABELS = {}
seen_sigs = set()
recent_children = defaultdict(list)
sniper_marked = {}
tg_queue = deque()
funder_cache = {}
last_tx_time = datetime.now()
last_bundled = None # for /labelgroup

SOLANA_PUB_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://solana.api.onchainkit.io"
]
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

app = Flask(__name__)
@app.route('/')
def home(): return "Shoks V5.1c FINAL - SHORT + LABELS", 200
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
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True},timeout=15)
                    except: pass
                time.sleep(0.6)
            else: time.sleep(0.2)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker, daemon=True).start()

def send_tg(text):
    if len(tg_queue) > 60: tg_queue.clear()
    tg_queue.append(text)

def short(a): return f"{a[:4]}..{a[-4:]}" if len(a)>8 else a

def display_group(wallet_or_group):
    if not wallet_or_group: return "?"
    gid = GROUPS.get(wallet_or_group, wallet_or_group)
    return LABELS.get(gid, LABELS.get(wallet_or_group, short(gid)))

# ========= STORAGE =========
def load_all():
    global SOL_WALLETS, GROUPS, LABELS, seen_sigs, last_bundled
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE)); SOL_WALLETS=d.get("sol",[])
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

def save_all():
    try:
        json.dump({"sol":SOL_WALLETS,"groups":GROUPS}, open(WALLETS_FILE,"w"))
        json.dump(GROUPS, open(GROUPS_FILE,"w"))
        json.dump(LABELS, open(LABELS_FILE,"w"))
    except: pass

def save_seen():
    try: json.dump(list(seen_sigs)[-1200:], open(SEEN_FILE,"w"))
    except: pass

load_all()

BLACKLIST_MINTS=["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"]

def get_token_info(token_address):
    if token_address in BLACKLIST_MINTS: return None
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=7).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=50]
        if not valid: valid=pairs
        best=sorted(valid, key=lambda x: x.get("liquidity",{}).get("usd",0) or 0, reverse=True)[0]
        fdv=best.get("fdv") or best.get("marketCap") or 0
        if fdv>=1_000_000: mcap_str=f"${fdv/1_000_000:.2f}M"
        elif fdv>=1000: mcap_str=f"${fdv/1000:.0f}k"
        else: mcap_str=f"${fdv:.0f}"
        price=float(best.get("priceUsd",0) or 0)
        if price==0: return None
        age_min=0
        try:
            pc=best.get("pairCreatedAt")
            if pc: age_min=int((time.time()*1000-pc)/60000)
        except: pass
        return {"name":best.get("baseToken",{}).get("symbol",token_address[:6]),"mcap_str":mcap_str,"mcap_val":fdv,"price":price,"dex_link":f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}","photon":f"https://photon-sol.tinyastro.io/en/lp/{token_address}","pair":best.get("pairAddress",token_address),"chain":best.get("chainId","solana"),"age_min":age_min}
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
            if diff>0.000001 and diff>0: token_transfers.append(("unknown",owner,mint,abs(diff),True))
    except: pass
    funder_cache[sig]=(datetime.now(),(sol_transfers,token_transfers))
    if len(funder_cache)>700: funder_cache.clear()
    return sol_transfers, token_transfers

def handle_bundle(wallet):
    global last_bundled
    wallet=wallet.strip()
    if len(wallet)<30: send_tg("❌ Invalid wallet"); return
    send_tg(f"🔍 Scanning `{short(wallet)}` for parent...")
    try:
        sigs=get_signatures(wallet,15); funders=[]
        for item in sigs[:12]:
            sig=item.get("signature")
            if not sig: continue
            sols,_=get_tx_details(sig)
            for frm,to,amt in sols:
                if to==wallet and amt>=0.03 and frm not in [f[0] for f in funders]:
                    funders.append((frm,amt,sig))
        if not funders:
            if wallet not in SOL_WALLETS: SOL_WALLETS.append(wallet)
            GROUPS[wallet]=wallet; last_bundled=wallet; save_all()
            send_tg(f"⚠️ No parent — ROOT `{short(wallet)}`\nAdded solo as new group\nNow `/labelgroup FOMOFARMER`"); return
        main_funder=funders[0][0]; last_bundled=main_funder
        GROUPS[wallet]=main_funder; GROUPS[main_funder]=main_funder
        if main_funder not in SOL_WALLETS: SOL_WALLETS.append(main_funder)
        added=0
        sib_sigs=get_signatures(main_funder,20)
        for item in sib_sigs[:15]:
            sig=item.get("signature")
            if not sig: continue
            sols,_=get_tx_details(sig)
            for frm,to,amt in sols:
                if frm==main_funder and to!=wallet and amt>=0.03 and to not in SOL_WALLETS and added<7:
                    SOL_WALLETS.append(to); GROUPS[to]=main_funder; added+=1
                    recent_children[main_funder].append((to,amt,datetime.now(),False,None))
        save_all()
        send_tg(f"📦 Bundled `{short(wallet)}`\nParent `{short(main_funder)}` G:{display_group(main_funder)}\n✅ +{added} sibs | Total {len(SOL_WALLETS)}\n`/labelgroup FOMOFARMER` to label")
    except Exception as e: send_tg(f"❌ Bundle error: {e}")

def alert_prep(funder):
    now=datetime.now()
    recent=[x for x in recent_children[funder] if (now-x[2]).total_seconds()<=300]
    recent_children[funder]=recent
    if not recent: return
    sol_only=[x for x in recent if not x[3]]
    token_only=[x for x in recent if x[3]]
    gl=display_group(funder)
    if token_only:
        mint=token_only[0][4] if len(token_only[0])>4 else "token"
        info=get_token_info(mint) if mint!="token" and mint else None
        name=info["name"] if info else short(mint)
        mcap=info["mcap_str"] if info else ""
        child_list="\n".join([f"• `{short(c[0])}` +{c[1]:.1f}" for c in token_only[:4]])
        send_tg(f"🚨 DIST {len(token_only)}w 5m\nG:{gl} `{short(funder)}`\nToken {name} {mcap}\n{child_list}\n[SC](https://solscan.io/account/{funder})"); return
    if len(sol_only)>=1:
        biggest=max(sol_only, key=lambda x: x[1])
        biggest_amt=biggest[1]; biggest_child=biggest[0]
        if biggest_amt>=0.35 or (len(sol_only)<=3 and biggest_amt>=0.08):
            sniper_marked[biggest_child]={"funder":funder,"amt":biggest_amt,"time":now,"group":gl}
        # Check if child is now splitter
        is_splitter=False
        try:
            if biggest_child in recent_children: is_splitter=True
        except: pass
        childs_str=" ".join([f"`{short(c[0])}` {c[1]:.2f}S" for c in sol_only[:4]])
        sniper_str=f"\n🎯 Sniper `{short(biggest_child)}` {biggest_amt:.2f} SOL" if biggest_amt>=0.3 else ""
        # splitter detection
        split_tag=""
        if len(sol_only)>=2 and funder in SOL_WALLETS:
            split_tag=f"\nSplitter: `{short(funder)}` -> {len(sol_only)} childs"
        send_tg(f"⚠️ PREP {len(sol_only)}w 5m\nG:{gl}{split_tag}\n{childs_str}{sniper_str}\n[SC](https://solscan.io/account/{funder})")

async def track_sol_polling():
    while True:
        try:
            for w in SOL_WALLETS[-90:]:
                sigs=get_signatures(w,3)
                for item in sigs[:2]:
                    sig=item.get("signature")
                    if not sig or sig in seen_sigs: continue
                    sols,tokens=get_tx_details(sig)
                    for _,owner,mint,amt,is_buy in tokens:
                        if owner==w and is_buy and amt>0:
                            info=get_token_info(mint)
                            if not info: continue
                            if amt*info["price"]<8: continue
                            seen_sigs.add(sig); save_seen()
                            global last_tx_time; last_tx_time=datetime.now()
                            is_sniper=w in sniper_marked
                            is_low=info["mcap_val"]<300_000; is_new=info["age_min"]<180
                            gl=display_group(w)
                            # SHORT LINKS ONLY
                            ds=info["dex_link"]; ph=info["photon"]; txl=f"https://solscan.io/tx/{sig}"
                            if is_low and is_new:
                                tag="SNIPER" if is_sniper else "EARLY"
                                send_tg(f"🎯 EARLY @{info['mcap_str']}\n🪙 {info['name']} | {info['mcap_str']} | {info['age_min']}m\n👤 `{short(w)}` G:{gl} {tag}\nCA: `{mint}`\n[DS]({ds}) [PH]({ph}) [TX]({txl})")
                            else:
                                tag=" SNIPER" if is_sniper else ""
                                send_tg(f"💰 BUY {info['mcap_str']}\n{info['name']} | `{short(w)}` G:{gl}{tag} | ${amt*info['price']:.0f}\nCA: `{mint[:6]}..{mint[-4:]}`\n[DS]({ds}) [PH]({ph}) [TX]({txl})")
        except Exception as e: print(f"poll err {e}", flush=True)
        await asyncio.sleep(6)

def track_funders_15s():
    while True:
        time.sleep(15)
        try:
            funders=set(GROUPS.values())
            for k in recent_children.keys(): funders.add(k)
            if not funders: funders=set(SOL_WALLETS[:25])
            for funder in list(funders)[:40]:
                try:
                    sigs=get_signatures(funder,8)
                    for item in sigs[:5]:
                        sig=item.get("signature")
                        if not sig or sig in seen_sigs: continue
                        sols,tokens=get_tx_details(sig)
                        for frm,to,amt in sols:
                            if frm==funder and amt>=0.02 and to not in SOL_WALLETS:
                                SOL_WALLETS.append(to); GROUPS[to]=GROUPS.get(funder,funder); save_all()
                                recent_children[funder].append((to,amt,datetime.now(),False,None)); seen_sigs.add(sig); save_seen(); alert_prep(funder)
                        for _,to2,mint,amt2,_ in tokens:
                            if to2 not in SOL_WALLETS and amt2>0 and len(mint)>20:
                                SOL_WALLETS.append(to2); GROUPS[to2]=GROUPS.get(funder,funder); save_all()
                                recent_children[funder].append((to2,amt2,datetime.now(),True,mint)); seen_sigs.add(sig); save_seen(); alert_prep(funder)
                except: continue
        except Exception as e: print(f"funder err {e}", flush=True)

def handle_command(text):
    global last_bundled
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        uniq=len(set(GROUPS.values()))
        send_tg(f"🚀 V5.1c FINAL SHORT\n{len(SOL_WALLETS)} wallets | {uniq} groups | {len(LABELS)} labels\nLast {mins}m\nBuy>$8 | Prep 1-5w 15s | Early<300k\n/bundle <addr>\n/labelgroup <NAME>\n/listgroups\n/list\n/remove\n/status\n/test")
    elif low.startswith("/bundle"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /bundle <addr>")
        else: threading.Thread(target=handle_bundle, args=(p[1],), daemon=True).start()
    elif low.startswith("/labelgroup"):
        p=t.split(maxsplit=1)
        if len(p)<2: send_tg("Usage: /labelgroup FOMOFARMER")
        else:
            nl=p[1].strip().upper().replace(" ","_")[:20]
            target=last_bundled or (SOL_WALLETS[-1] if SOL_WALLETS else None)
            if not target: send_tg("Bundle first"); return
            gk=GROUPS.get(target,target)
            LABELS[gk]=nl; save_all()
            send_tg(f"🏷️ `{short(gk)}` → **{nl}**\nNow all G:{nl}")
    elif low.startswith("/deletelabels"):
        LABELS.clear(); save_all(); send_tg("🗑️ Labels cleared")
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
            send_tg("📦 Groups:\n"+"\n".join(lines))
    elif low.startswith("/list"):
        if not SOL_WALLETS: send_tg("Empty")
        else:
            msg=f"📋 {len(SOL_WALLETS)}w:\n"
            for w in SOL_WALLETS[-45:]:
                g=display_group(w); tag=" SNIPER" if w in sniper_marked else ""
                # show if wallet itself is splitter
                is_splitter = len(recent_children.get(w,[]))>0
                tag2=" [SPLITTER]" if is_splitter else ""
                msg+=f"`{short(w)}` G:{g}{tag}{tag2}\n"
            send_tg(msg)
    elif low.startswith("/remove"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /remove <addr>")
        else:
            a=p[1].strip()
            if a in SOL_WALLETS: SOL_WALLETS.remove(a); GROUPS.pop(a,None); save_all(); send_tg(f"🗑️ `{short(a)}` removed")
            else: send_tg("Not found")
    elif low.startswith("/clear"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); recent_children.clear(); sniper_marked.clear(); save_all(); send_tg("🧹 Cleared")
    elif low.startswith("/status"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        send_tg(f"📊 V5.1c FINAL\nWallets {len(SOL_WALLETS)}\nGroups {len(set(GROUPS.values()))}\nLabels {len(LABELS)}\nSniper {len(sniper_marked)}\nLast {mins}m")
    elif low.startswith("/test"):
        gl=display_group(last_bundled) if last_bundled else (list(LABELS.values())[0] if LABELS else "FOMOFARMER")
        send_tg(f"✅ V5.1c SHORT OK G:{gl}\n🎯 EARLY @$104k\n🪙 TEST | $104k | 1m\n👤 `8xK2..pL9` G:{gl} SNIPER\nCA: `7vQ1..pump`\n[DS](https://dexscreener.com/solana/test) [PH](https://photon-sol.tinyastro.io/en/lp/test) [TX](https://solscan.io/tx/test)")

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
    send_tg("🚀 V5.1c FINAL DEPLOYED\n15s | PREP 1-5w | Labels + Short [DS][PH][TX]")
    await track_sol_polling()

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e: print(f"CRASH {e}", flush=True); time.sleep(5); start_bot()

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=poll_commands,daemon=True).start()
    time.sleep(1); start_bot()
