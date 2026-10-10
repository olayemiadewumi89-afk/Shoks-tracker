import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque
from web3 import Web3

# === PERSISTENT PATH FIX ===
DATA_DIR = "/data" if os.path.exists("/data") else "."
def p(f): return os.path.join(DATA_DIR, f)

SOL_WALLETS = [
"Beqv6dzTcjV2eodo8RRXCiCcnSYrS1vkQKhfqwHXqeit","5t8FA8z7SFiEpjau2nTBYKN8jd5CGo8Ttcgt1m73C7d",
"6xmMW5JPSEfeRuNdkxixHWsm4Sf57SdXybA3BdwZzrM1","2Ysos2B2S6rb9FBNYUB7Yrk54Xv2nNbr8eBHs1sX8M6m",
"Agt534WQKGXuVt9UFamcgqH9djySF9PmxqNS5caiMCAV","Ab25e8CyZ6119HSY6r4wdKpBPxY3UBLWfrZ7PEBvHD4F",
"4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo","9BMzTpSo4URse1oN666pmexhdjpU1vA5p7LtroCFQdLU",
"498g1rVnFcnjBjpfw1xyqA1WvgQXUU8RWuELjxkjAayQ","8f39XhhZoRD8sYb6K5K9N7iSHXkL7BDmFFQNDF3TtsEr",
"83b2LMf12aLec92kur3duRA8VvUkLaXUtML31aCcZNCM","7aoGoRSexZu1DE4vC24CVo4SqWrBDeoJ34qLRGUsL9ha",
"2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V","7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9",
"H2QSGECp13sFLJgdTsDtayX3dk18Dm6sQMSQKcew7Xzk","DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39",
"6My97BBVoJz3j7mQWFVz5pykFxMsCs44gjhaqmczcAND","D9tPQeij7vSTZwkxzxZibso4GFuRW8aBMpCg5QhCSfVL",
"59mWSDjx5VFQz15FGJio8BJzupKorK5SVHSbaJaZGAxC",
"3zs5nZyqTZmjvvaNTRnfNZyhNzuu7WW9BrJL31gGyPVY",
"C2UdM2JiKg9xFETQpXBxynACovJuFYer9SKVm2",
]
EVM_WALLETS = [
"0xfd87eda88be6c372453b721da63d58ad1a5b2d94","0x2a17e1e796dd7bd3b27efe2cda72d4b909baaacf",
"0x4bc1782fafb967834e0e75947ba15113e48fc70e","0x5a08728a6704d99468d8919ba0adc2e1d2733083",
"0xae97104fde93a82797afec819ac0e09b8d544907","0xabf122e41ce873a3c6a71fbf1c6419ca5df07f17",
"0xd1c77a04b87393e98a1220532e72e8f7d0a31c5a","0x0c175c6a0065ee05f871a68783d2de432a1e6cbe",
"0x696d1265c8fc4f14797abebfae3c43ebfa9d8e28","0x0121525f755c9e7bbc525bba6672716ab46ced57",
"0x27d6a5a1d9a5e89bfce0239f08d9c95cf2256ebb","0xb8f305f27ccc406373de0082cc06cb1d065504ea",
"0xb02208d1b27811480cf6171bccc2b7af9513cddb","0x06de9c48b1e639ed5c13ec8fbd4080a38e39f2d1",
"0x1890e719822bc704c4f117aa4109401c2bab6f79","0xd4cf04bc9d7c80b49c6c30a633f7b9bd5370b4d6",
"0x7f6e4e1e59be05dc717c8e5429154a7408609ee2",
"0x0819955F989A720B76E41Da601e38CD4556eF8ed",
]

WALLETS_FILE=p("wallets.json"); MEMORY_FILE=p("cluster_memory.json"); FUNDERS_FILE=p("funders.json"); NEW_CHILDREN_FILE=p("new_children.json"); LAST_ACTIVE_FILE=p("last_active.json"); LABELS_FILE=p("labels.json"); GROUPS_FILE=p("groups.json")
cluster_memory=defaultdict(list)
seen_sigs=set(); cluster_alerted={}; funder_cache={}; tg_queue=deque()
STABLES={"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}
last_tx_time=datetime.now(); known_splitters=set(); known_funders=set(); new_children=set(); last_active={}; last_rpc_call=0
splitter_child_count=defaultdict(set)
wallet_labels={}; wallet_groups={}
last_bundle={"addresses":[],"funder":"","root":"","time":None}
pending_label=False
bundle_in_progress=False

def load_wallets():
    global SOL_WALLETS, EVM_WALLETS, known_splitters, known_funders, new_children, last_active, wallet_labels, wallet_groups
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE));
            saved_sol=d.get("sol",[]); saved_evm=d.get("evm",[])
            for w in saved_sol:
                if w not in SOL_WALLETS: SOL_WALLETS.append(w)
            for w in saved_evm:
                if w.lower() not in [x.lower() for x in EVM_WALLETS]: EVM_WALLETS.append(w)
    except: pass
    try:
        if os.path.exists(FUNDERS_FILE):
            d=json.load(open(FUNDERS_FILE)); known_splitters=set(d.get("splitters",[])); known_funders=set(d.get("funders",[]))
    except: pass
    try:
        if os.path.exists(NEW_CHILDREN_FILE): new_children=set([x.lower() for x in json.load(open(NEW_CHILDREN_FILE))])
    except: pass
    try:
        if os.path.exists(LAST_ACTIVE_FILE): last_active=json.load(open(LAST_ACTIVE_FILE))
    except: pass
    try:
        if os.path.exists(LABELS_FILE): wallet_labels=json.load(open(LABELS_FILE))
    except: pass
    try:
        if os.path.exists(GROUPS_FILE): wallet_groups=json.load(open(GROUPS_FILE))
    except: pass

def save_wallets():
    try: json.dump({"sol":SOL_WALLETS,"evm":EVM_WALLETS}, open(WALLETS_FILE,"w"))
    except: pass
def save_funders():
    try: json.dump({"splitters":list(known_splitters)[-100:],"funders":list(known_funders)[-100:]}, open(FUNDERS_FILE,"w"))
    except: pass
def save_new_children():
    try: json.dump(list(new_children)[-150:], open(NEW_CHILDREN_FILE,"w"))
    except: pass
def save_last_active():
    try: json.dump(last_active, open(LAST_ACTIVE_FILE,"w"))
    except: pass
def save_labels():
    try: json.dump(wallet_labels, open(LABELS_FILE,"w"))
    except: pass
def save_groups():
    try: json.dump(wallet_groups, open(GROUPS_FILE,"w"))
    except: pass
def save_memory():
    try:
        data={k:[(w,ts.isoformat(),ch) for w,ts,ch in v[-150:]] for k,v in cluster_memory.items()}
        json.dump(data, open(MEMORY_FILE,"w"))
    except: pass
def load_memory():
    try:
        if os.path.exists(MEMORY_FILE):
            for k,v in json.load(open(MEMORY_FILE)).items():
                for w,ts_str,ch in v:
                    try: ts=datetime.fromisoformat(ts_str)
                    except: ts=datetime.now()
                    cluster_memory[k].append((w,ts,ch))
    except: pass
load_wallets(); load_memory()

def mark_active(wallet):
    try:
        last_active[wallet.lower()]=datetime.now().isoformat()
        if len(last_active)%30==0: save_last_active()
    except: pass
def get_group(addr): return wallet_groups.get(addr.lower(), "")
def get_label(addr):
    lbl = wallet_labels.get(addr.lower(), wallet_labels.get(addr, ""))
    if lbl: return lbl
    return wallet_groups.get(addr.lower(), "")
def short(a):
    try: return f"{a[:4]}..{a[-4:]}" if len(a)>8 else a
    except: return "?"
def format_wallet(addr, is_new_child=False, parent_addr=None):
    lbl = wallet_labels.get(addr.lower(), wallet_groups.get(addr.lower(),""))
    sh = short(addr)
    if is_new_child: return f"<code>{sh}</code>"
    if lbl: return f"{lbl} <code>{sh}</code>"
    return f"<code>{sh}</code>"

# === NEW: DELETE LABEL + ALL ADDRESSES ===
def delete_label_and_addresses(label_name):
    global SOL_WALLETS, EVM_WALLETS
    label_name = label_name.strip()
    to_delete = []
    for addr, grp in list(wallet_groups.items()):
        if grp == label_name:
            to_delete.append(addr)
    for addr, lbl in list(wallet_labels.items()):
        if lbl == label_name and addr not in to_delete:
            to_delete.append(addr)

    deleted = 0
    for addr in to_delete:
        low = addr.lower()
        # remove from wallets
        before_sol = len(SOL_WALLETS)
        SOL_WALLETS = [w for w in SOL_WALLETS if w.lower()!= low]
        if len(SOL_WALLETS) < before_sol: deleted+=1
        before_evm = len(EVM_WALLETS)
        EVM_WALLETS = [w for w in EVM_WALLETS if w.lower()!= low]
        if len(EVM_WALLETS) < before_evm: deleted+=1
        # clean other maps
        wallet_labels.pop(low, None)
        wallet_labels.pop(addr, None)
        wallet_groups.pop(low, None)
        wallet_groups.pop(addr, None)
        new_children.discard(low)
        last_active.pop(low, None)

    # If label exists but no addresses matched (empty group), still clean
    if not to_delete:
        # also delete any keys where value == label but address not in list anymore
        pass

    save_wallets(); save_labels(); save_groups(); save_new_children(); save_last_active()
    return deleted, len(to_delete)

def get_all_labels_with_counts():
    groups=defaultdict(list)
    for addr, grp in wallet_groups.items():
        if grp: groups[grp].append(addr)
    for addr, lbl in wallet_labels.items():
        if lbl and lbl not in groups: groups[lbl].append(addr)
        elif lbl and addr not in groups[lbl]: groups[lbl].append(addr)
    return groups

def prune_inactive():
    while True:
        time.sleep(3600*6)
        try:
            now=datetime.now(); to_remove_sol=[]; to_remove_evm=[]
            for w in SOL_WALLETS[:]:
                la=last_active.get(w.lower())
                if not la: continue
                try:
                    if (now-datetime.fromisoformat(la)).days>30:
                        if w not in known_splitters and w not in known_funders: to_remove_sol.append(w)
                except: pass
            for w in EVM_WALLETS[:]:
                la=last_active.get(w.lower())
                if not la: continue
                try:
                    if (now-datetime.fromisoformat(la)).days>30:
                        if w.lower() not in [x.lower() for x in known_splitters]: to_remove_evm.append(w)
                except: pass
            removed=0
            for w in to_remove_sol:
                if w in SOL_WALLETS: SOL_WALLETS.remove(w); removed+=1
                new_children.discard(w.lower()); last_active.pop(w.lower(),None)
            for w in to_remove_evm:
                EVM_WALLETS=[x for x in EVM_WALLETS if x.lower()!=w.lower()]; removed+=1
                new_children.discard(w.lower()); last_active.pop(w.lower(),None)
            if removed>0:
                save_wallets(); save_new_children(); save_last_active()
                send_tg(f"🧹 Auto-cleaned {removed}")
        except Exception as e: print(f"prune err {e}", flush=True)

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com"]
rpc_idx=0
def get_public_rpc():
    global rpc_idx, last_rpc_call
    now=time.time()
    if now-last_rpc_call<0.4: time.sleep(0.4-(now-last_rpc_call))
    last_rpc_call=time.time()
    url=SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]; rpc_idx+=1
    return url

RPCS_FALLBACK={
    "BSC":["https://bsc-rpc.publicnode.com","https://bsc.meowrpc.com","https://bsc.llamarpc.com"],
    "BASE":["https://base-rpc.publicnode.com","https://base.meowrpc.com","https://base.llamarpc.com","https://mainnet.base.org"]
}
def get_w3_with_fallback(chain):
    for url in RPCS_FALLBACK.get(chain,[]):
        try:
            w3=Web3(Web3.HTTPProvider(url,request_kwargs={'timeout':5}))
            if w3.is_connected(): return w3
        except: continue
    return None

BOT_TOKEN=os.getenv("BOT_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")
try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True",timeout=10)
except: pass

app=Flask(__name__)
@app.route('/')
def home(): return "Shok V4.9.4 FULL CLICKABLE + DELETE LABELS",200
@app.route('/health')
def health(): return "OK",200
@app.route('/debug')
def debug():
    try:
        groups_len = len(set(wallet_groups.values())) if wallet_groups else 0
        return f"V4.9.4 SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} Groups:{groups_len} Last:{int((datetime.now()-last_tx_time).total_seconds()/60)}m DIR:{DATA_DIR}",200
    except Exception as e:
        return f"DEBUG ERR {e}",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))

def send_tg_worker():
    while True:
        try:
            if tg_queue:
                item=tg_queue.popleft()
                text, markup = item if isinstance(item, tuple) else (item, None)
                if not BOT_TOKEN or not CHAT_ID: print(text[:500], flush=True)
                else:
                    try:
                        payload={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True,"parse_mode":"HTML"}
                        if markup: payload["reply_markup"]=markup
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json=payload,timeout=15)
                    except: pass
                time.sleep(1.0)
            else: time.sleep(0.4)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker, daemon=True).start()
def send_tg(text):
    if len(tg_queue)>20: tg_queue.clear()
    tg_queue.append(text)
def send_tg_markup(text, markup):
    if len(tg_queue)>20: tg_queue.clear()
    tg_queue.append((text, markup))

def get_token_info_quick(token_address, chain_hint="SOL"):
    if token_address in STABLES: return None
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=5).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=500]
        if not valid: return None
        best=sorted(valid, key=lambda x: x.get("liquidity",{}).get("usd",0), reverse=True)[0]
        liq=best.get("liquidity",{}).get("usd",0); name=best.get("baseToken",{}).get("symbol", token_address[:6])
        fdv=best.get("fdv") or best.get("marketCap") or 0
        mcap=f"${fdv/1_000_000:.2f}M" if fdv>=1_000_000 else f"${fdv/1000:.1f}k" if fdv>=1000 else "New"
        price=float(best.get("priceUsd",0) or 0)
        if price==0: return None
        dex_link=f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}"
        return name, mcap, dex_link, price, best.get('chainId','SOL').upper(), liq, best.get('pairAddress',token_address)
    except: return None

def get_sol_transfers_cached(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<600:
        return funder_cache[sig][1]
    transfers=[]
    try:
        rpc_url=get_public_rpc()
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        r=requests.post(rpc_url, json=payload, timeout=12)
        res=r.json().get("result")
        if not res:
            funder_cache[sig]=(datetime.now(),[]); return []
        for instr in res.get("transaction",{}).get("message",{}).get("instructions",[]) or []:
            try:
                if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                    info=instr["parsed"]["info"]
                    transfers.append((info["source"], info["destination"], float(info.get("lamports",0))/1e9, sig))
            except: continue
        for inner in res.get("meta",{}).get("innerInstructions",[]) or []:
            for instr in inner.get("instructions",[]) or []:
                try:
                    if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                        info=instr["parsed"]["info"]
                        transfers.append((info["source"], info["destination"], float(info.get("lamports",0))/1e9, sig))
                except: continue
    except: pass
    funder_cache[sig]=(datetime.now(),transfers)
    if len(funder_cache)>800: funder_cache.clear()
    return transfers

def analyze_wallet_bundle(wallet):
    global last_bundle, pending_label, bundle_in_progress
    if bundle_in_progress:
        send_tg("⏳ Bundle busy..."); return
    bundle_in_progress=True
    wallet=wallet.strip()
    send_tg(f"🔍 Scanning <code>{short(wallet)}</code> (deep 100)...")
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet, {"limit":100}]}
        r=requests.post(get_public_rpc(), json=payload, timeout=15).json()
        sigs=r.get("result",[]); funders=[]; children=[]
        for item in sigs[:80]:
            sig=item.get("signature")
            if not sig: continue
            for frm,to,amt,_ in get_sol_transfers_cached(sig):
                if to==wallet and amt>=0.005:
                    if frm not in [f[0] for f in funders]: funders.append((frm, amt, sig))
                if frm==wallet and amt>=0.005 and to!=wallet:
                    if to not in [c[0] for c in children]: children.append((to, amt))
        if funders:
            main_funder, amt, _ = funders[0]; siblings=[]
            payload2={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[main_funder, {"limit":60}]}
            r2=requests.post(get_public_rpc(), json=payload2, timeout=15).json()
            for item in r2.get("result",[])[:40]:
                s=item.get("signature")
                if not s: continue
                for frm,to,amt2,_ in get_sol_transfers_cached(s):
                    if frm==main_funder and amt2>=0.005 and to!=wallet:
                        if to not in [x[0] for x in siblings]: siblings.append((to, amt2))
            all_group=[wallet, main_funder] + [s for s,_ in siblings[:10]]
            added=0
            for addr in [a for a in all_group if a!=wallet]:
                if addr not in SOL_WALLETS and len(SOL_WALLETS)<150:
                    SOL_WALLETS.append(addr); added+=1; new_children.add(addr.lower()); mark_active(addr)
            if added>0: save_wallets(); save_new_children(); save_last_active()
            known_splitters.add(main_funder); save_funders()
            last_bundle={"addresses":all_group,"funder":main_funder,"root":wallet,"time":datetime.now().isoformat()}
            pending_label=True
            msg=f"<b>BUNDLE</b> <code>{short(wallet)}</code>\nSplitter <code>{short(main_funder)}</code> {amt:.3f} SOL\n<b>{len(siblings)} sibs:</b>\n" + "\n".join([f"• <code>{short(s)}</code> {a:.3f}" for s,a in siblings[:8]])
            msg+=f"\n\n✅ +{added} added → /labelgroup &lt;name&gt; or /skip"
            send_tg(msg)
        elif children:
            all_group=[wallet] + [c for c,_ in children[:15]]
            added=0
            for c,_ in children[:15]:
                if c not in SOL_WALLETS and len(SOL_WALLETS)<150:
                    SOL_WALLETS.append(c); added+=1; new_children.add(c.lower()); mark_active(c)
            save_wallets(); save_new_children(); known_splitters.add(wallet); known_funders.add(wallet); save_funders()
            last_bundle={"addresses":all_group,"funder":wallet,"root":wallet,"time":datetime.now().isoformat()}
            pending_label=True
            msg=f"<b>ROOT</b> <code>{short(wallet)}</code>\nFunded {len(children)} children:\n" + "\n".join([f"• <code>{short(c)}</code> {a:.3f}" for c,a in children[:10]])
            msg+=f"\n\n✅ +{added} added → /labelgroup &lt;name&gt; or /skip"
            send_tg(msg)
        else:
            send_tg(f"💤 Dormant <code>{short(wallet)}</code> - ROOT vault\n<a href=\"https://solscan.io/account/{wallet}\">Solscan</a>")
            if wallet not in SOL_WALLETS:
                SOL_WALLETS.append(wallet); save_wallets()
                last_bundle={"addresses":[wallet],"funder":wallet,"root":wallet,"time":datetime.now().isoformat()}
                pending_label=True
                send_tg(f"Added ROOT: <code>{short(wallet)}</code>\n/labelgroup &lt;name&gt; or /skip")
    except Exception as e: send_tg(f"Bundle err {e}")
    finally: bundle_in_progress=False

def analyze_evm_bundle(chain, wallet):
    global last_bundle, pending_label, bundle_in_progress
    if bundle_in_progress:
        send_tg("⏳ Bundle busy..."); return
    bundle_in_progress=True
    send_tg(f"🔍 Scanning <code>{short(wallet)}</code> {chain} (full)...")
    try:
        wallet_chk=Web3.to_checksum_address(wallet)
        funder=None; siblings=[]
        try:
            if chain=="BASE":
                r=requests.get(f"https://base.blockscout.com/api?module=account&action=txlist&address={wallet_chk}", timeout=12).json()
                txs=r.get("result",[]) if isinstance(r.get("result"), list) else []
                for tx in txs:
                    if tx.get("to","").lower()==wallet_chk.lower():
                        try:
                            v=int(tx.get("value","0"))
                            if v>0:
                                val=v/1e18
                                if val>=0.001:
                                    funder=(tx.get("from"), val); break
                        except: continue
            else:
                r=requests.get(f"https://api-bsc.blockscout.com/api?module=account&action=txlist&address={wallet_chk}", timeout=12).json()
                txs=r.get("result",[]) if isinstance(r.get("result"), list) else []
                for tx in txs:
                    if tx.get("to","").lower()==wallet_chk.lower():
                        try:
                            v=int(tx.get("value","0"))
                            if v>0:
                                val=v/1e18
                                if val>=0.001:
                                    funder=(tx.get("from"), val); break
                        except: continue
        except: pass
        if not funder:
            w3=get_w3_with_fallback(chain)
            if w3:
                latest=w3.eth.block_number; start=time.time()
                for bn in range(latest, max(latest-8000,0), -1):
                    if time.time()-start>20: break
                    try:
                        block=w3.eth.get_block(bn, full_transactions=True)
                        for tx in block.transactions:
                            if tx.get('to','') and tx.get('to','').lower()==wallet_chk.lower() and int(tx.get('value',0))>0:
                                val=float(w3.from_wei(tx['value'],'ether'))
                                if val>=0.001:
                                    funder=(tx.get('from'), val); break
                        if funder: break
                    except: continue
        if not funder:
            send_tg(f"💤 <b>ROOT EVM</b> {chain} <code>{short(wallet)}</code>\nNo funder - CEX")
            if wallet.lower() not in [x.lower() for x in EVM_WALLETS]:
                EVM_WALLETS.append(wallet_chk); save_wallets()
                last_bundle={"addresses":[wallet],"funder":wallet,"root":wallet,"time":datetime.now().isoformat()}
                pending_label=True
                send_tg(f"Added ROOT: <code>{short(wallet)}</code>\n/labelgroup &lt;name&gt; or /skip")
            bundle_in_progress=False; return
        funder_addr, amt = funder
        try:
            if chain=="BASE":
                r=requests.get(f"https://base.blockscout.com/api?module=account&action=txlist&address={Web3.to_checksum_address(funder_addr)}", timeout=12).json()
                txs=r.get("result",[]) if isinstance(r.get("result"), list) else []
                for tx in txs[:150]:
                    if tx.get("from","").lower()==funder_addr.lower() and tx.get("to","").lower()!=wallet_chk.lower():
                        try:
                            v=int(tx.get("value","0"))
                            if v>0:
                                val=v/1e18
                                to_addr=tx.get("to","")
                                if val>=0.001 and to_addr.lower() not in [s[0].lower() for s in siblings]:
                                    siblings.append((to_addr, val))
                        except: continue
        except: pass
        msg=f"<b>BUNDLE</b> {chain} <code>{short(wallet)}</code>\nFunder <code>{short(funder_addr)}</code> {amt:.4f}\n"
        if siblings: msg+=f"<b>{len(siblings)} sibs:</b>\n" + "\n".join([f"• <code>{short(s)}</code> {a:.4f}" for s,a in siblings[:8]])
        added=0; all_group=[wallet, funder_addr]
        if funder_addr.lower() not in [x.lower() for x in EVM_WALLETS]:
            try: EVM_WALLETS.append(Web3.to_checksum_address(funder_addr))
            except: EVM_WALLETS.append(funder_addr)
            added+=1; known_splitters.add(funder_addr)
        for sib,_ in siblings[:10]:
            if sib.lower() not in [x.lower() for x in EVM_WALLETS]:
                try: EVM_WALLETS.append(Web3.to_checksum_address(sib))
                except: EVM_WALLETS.append(sib)
                added+=1; new_children.add(sib.lower()); all_group.append(sib)
        if added>0: save_wallets(); save_funders(); save_new_children()
        last_bundle={"addresses":all_group,"funder":funder_addr,"root":wallet,"time":datetime.now().isoformat()}
        pending_label=True
        msg+=f"\n\n✅ +{added} added → /labelgroup &lt;name&gt; or /skip"
        send_tg(msg)
    except Exception as e:
        send_tg(f"EVM err {e}")
    finally:
        bundle_in_progress=False

def check_cluster_1d(mint, name, mcap, dex_link, chain):
    now=datetime.now(); events=cluster_memory.get(mint,[]); recent=[e for e in events if (now-e[1])<=timedelta(days=1)]
    uniq={}; [uniq.__setitem__(w.lower(),w) for w,_,_ in recent]
    uniq_wallets=list(uniq.values())
    if len(uniq_wallets)<2: return
    last=cluster_alerted.get(mint.lower())
    if last and (now-last)<=timedelta(hours=12): return
    cluster_alerted[mint.lower()]=now
    wallets_str="\n".join([f"• {format_wallet(w)}" for w in uniq_wallets[:6]])
    dex_short=f'<a href="{dex_link}">Chart</a>'
    send_tg(f"👥 <b>CLUSTER 1D</b>\n<b>{name}</b> {mcap} {chain}\n{len(uniq_wallets)} wallets:\n{wallets_str}\n{dex_short}")

def get_tx_parsed_public(sig):
    for _ in range(2):
        rpc_url=get_public_rpc()
        try:
            payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
            r=requests.post(rpc_url, json=payload, timeout=10)
            if r.status_code!=200: continue
            res=r.json().get("result")
            if not res: continue
            meta=res.get("meta",{}); pre=meta.get("preTokenBalances",[]); post=meta.get("postTokenBalances",[])
            bal_map={}
            for b in pre: bal_map[(b.get("owner",""),b.get("mint",""))]=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            transfers=[]
            for b in post:
                owner=b.get("owner",""); mint=b.get("mint","")
                if mint in STABLES: continue
                post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0); pre_amt=bal_map.get((owner,mint),0); diff=post_amt-pre_amt
                if abs(diff)<0.000001: continue
                transfers.append({"mint":mint,"owner":owner,"amount":abs(diff),"is_buy":diff>0})
            if transfers: return {"transfers":transfers}
        except: continue
    return None

def process_sol_tx_public(wallet_list, parsed, sig):
    global last_tx_time
    if sig in seen_sigs: return
    if not parsed or not parsed.get("transfers"): return
    transfers=sorted(parsed["transfers"], key=lambda x: x["amount"], reverse=True)
    best=None
    for tr in transfers:
        if tr["owner"] in wallet_list and tr["amount"]>0.0001: best=tr; break
    if not best: return
    mint=best["mint"]; amt=best["amount"]; owner=best["owner"]; is_buy=best["is_buy"]
    if mint in STABLES: return
    info=get_token_info_quick(mint,"SOL")
    if not info: return
    name,mcap,dex_link,price,_,liq,pair = info
    usd=amt*price if price>0 else 0
    if usd<10: return
    seen_sigs.add(sig)
    if len(seen_sigs)>800: seen_sigs.clear()
    last_tx_time=datetime.now(); mark_active(owner)
    is_new = owner.lower() in new_children
    cluster_memory[mint].append((owner,datetime.now(),"SOL")); save_memory()
    label_str = format_wallet(owner)
    dex_short=f'<a href="{dex_link}">📊 Chart</a>'
    tx_short=f'<a href="https://solscan.io/tx/{sig}">🔍 Tx</a>'
    if is_buy:
        if is_new:
            grp = get_group(owner) or "Unknown"
            send_tg(f"🟢 <b>FIRST BUY</b> [{grp}]\n<b>{name}</b> {mcap}\n💰 ${usd:,.2f} | {amt:,.0f}\n👤 {label_str}\n{dex_short} | {tx_short} | Liq ${liq:,.0f}")
        else:
            send_tg(f"🟢 <b>BUY</b> <b>{name}</b> {mcap}\n💰 ${usd:,.2f}\n👤 {label_str}\n{dex_short} | {tx_short}")
        threading.Thread(target=lambda: check_cluster_1d(mint,name,mcap,dex_link,"SOL"),daemon=True).start()
    else:
        send_tg(f"🔴 <b>SELL</b> <b>{name}</b> {mcap}\n💰 ${usd:,.2f}\n👤 {label_str}\n{dex_short} | {tx_short}")

async def track_sol_polling():
    print(f"SOL V4.9.4 FULL $10+ DIR:{DATA_DIR}", flush=True)
    while True:
        for w in SOL_WALLETS[-50:]:
            try:
                rpc_url=get_public_rpc()
                payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[w, {"limit":2}]}
                r=requests.post(rpc_url, json=payload, timeout=10)
                if r.status_code!=200: continue
                result=r.json().get("result",[])
                if not isinstance(result,list): continue
                for item in result:
                    sig=item.get("signature") if isinstance(item,dict) else None
                    if not sig or sig in seen_sigs: continue
                    parsed=get_tx_parsed_public(sig)
                    if not parsed: continue
                    process_sol_tx_public([w], parsed, sig)
            except Exception as e: print(f"sol err {e}", flush=True)
        await asyncio.sleep(5)

def get_token_decimals(w3, token_addr):
    try:
        abi='[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]'
        contract=w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=json.loads(abi))
        return contract.functions.decimals().call()
    except: return 18

async def track_chain(chain):
    global last_tx_time
    seen=set()
    while True:
        try:
            w3=get_w3_with_fallback(chain)
            if not w3: await asyncio.sleep(10); continue
            bn=w3.eth.block_number
            for b in range(max(0,bn-2), bn+1):
                if b in seen: continue
                seen.add(b)
                if len(seen)>200: seen=set(list(seen)[-100:])
                try:
                    block=w3.eth.get_block(b,full_transactions=True)
                    for tx in block.transactions:
                        frm=tx.get('from')
                        if not frm or frm.lower() not in [x.lower() for x in EVM_WALLETS]: continue
                        h=tx.hash.hex() if hasattr(tx.hash,'hex') else tx['hash'].hex()
                        try:
                            receipt=w3.eth.get_transaction_receipt(h)
                            for log in receipt.get('logs',[]):
                                if len(log.get('topics',[]))!=3: continue
                                if log['topics'][0].hex()!='ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef': continue
                                from_addr='0x'+log['topics'][1].hex()[-40:]; to_addr='0x'+log['topics'][2].hex()[-40:]
                                token_contract=log['address']; amount_raw=int(log['data'],16)
                                dec=get_token_decimals(w3, token_contract); amount_token=amount_raw/(10**dec)
                                info=get_token_info_quick(token_contract,chain)
                                if not info: continue
                                name,mcap,dex_link,price,_,liq,pair = info
                                usd_val=(amount_token*price) if price>0 else 0
                                if usd_val<10: continue
                                is_sell = from_addr.lower()==frm.lower(); is_buy = to_addr.lower()==frm.lower()
                                if not (is_sell or is_buy): continue
                                last_tx_time=datetime.now(); mark_active(frm)
                                is_new = frm.lower() in new_children
                                label_str = format_wallet(frm)
                                dex_short=f'<a href="{dex_link}">📊 Chart</a>'
                                if is_sell:
                                    send_tg(f"🔴 <b>SELL {chain}</b> <b>{name}</b> {mcap}\n💰 ${usd_val:,.2f}\n👤 {label_str}\n{dex_short}")
                                else:
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    if is_new:
                                        grp=get_group(frm) or "Unknown"
                                        send_tg(f"🟢 <b>FIRST BUY {chain}</b> [{grp}]\n<b>{name}</b> {mcap}\n💰 ${usd_val:,.2f}\n👤 {label_str}\n{dex_short}")
                                    else:
                                        send_tg(f"🟢 <b>BUY {chain}</b> <b>{name}</b> {mcap}\n💰 ${usd_val:,.2f}\n👤 {label_str}\n{dex_short}")
                                    threading.Thread(target=lambda: check_cluster_1d(token_contract,name,mcap,dex_link,chain),daemon=True).start()
                        except: pass
                except: pass
            await asyncio.sleep(4)
        except Exception as e: print(f"[{chain}] err {e}", flush=True); await asyncio.sleep(5)

def track_funders_polling():
    print(f"Funder watcher V4.9.4 DIR:{DATA_DIR}", flush=True)
    while True:
        time.sleep(60)
        try:
            targets = set()
            targets.update(list(known_splitters)[-15:])
            targets.update(list(known_funders)[-10:])
            targets.update(SOL_WALLETS[-25:])
            targets.update([w for w in list(new_children)[-15:] if not w.startswith("0x")])
            for funder in list(targets):
                try:
                    if len(SOL_WALLETS)>=150: break
                    if not funder or len(funder)<30: continue
                    rpc_url=get_public_rpc()
                    payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[funder, {"limit":8}]}
                    r=requests.post(rpc_url, json=payload, timeout=12).json()
                    for item in r.get("result",[])[:6]:
                        sig=item.get("signature")
                        if not sig: continue
                        for frm,to,amt,_ in get_sol_transfers_cached(sig):
                            if frm==funder and amt>=0.01 and to not in SOL_WALLETS:
                                parent_group = get_group(frm) or wallet_labels.get(frm.lower(),"")
                                if parent_group:
                                    wallet_groups[to.lower()]=parent_group
                                    save_groups()
                                SOL_WALLETS.append(to); new_children.add(to.lower()); mark_active(to)
                                splitter_child_count[funder].add(to.lower())
                                save_wallets(); save_new_children(); save_last_active()
                                send_tg(f"👶 <b>NEW CHILD</b> {amt:.3f} SOL\nFrom <code>{short(funder)}</code> → <code>{short(to)}</code>\nGroup: {parent_group or 'No group'}")
                                time.sleep(0.8)
                    if len(splitter_child_count[funder])>=2 and funder not in known_splitters:
                        known_splitters.add(funder); save_funders()
                        send_tg(f"⬆️ <b>PROMOTE</b> <code>{short(funder)}</code> → SPLITTER")
                except: continue
        except Exception as e: print(f"recursive SOL err {e}", flush=True)
        time.sleep(10)
        try:
            evm_targets=set()
            evm_targets.update([x for x in known_splitters if x.startswith("0x")][-10:])
            evm_targets.update(EVM_WALLETS[-15:])
            for funder in list(evm_targets):
                if not funder.startswith("0x"): continue
                for chain in ["BASE","BSC"]:
                    try:
                        if len(EVM_WALLETS)>=120: break
                        w3=get_w3_with_fallback(chain)
                        if not w3: continue
                        latest=w3.eth.block_number
                        block=w3.eth.get_block(latest, full_transactions=True)
                        for tx in block.transactions:
                            if tx.get('from','').lower()==funder.lower() and int(tx.get('value',0))>0:
                                to_addr=tx.get('to','')
                                if to_addr and to_addr.lower() not in [x.lower() for x in EVM_WALLETS]:
                                    val=float(w3.from_wei(tx['value'],'ether'))
                                    if val>=0.005:
                                        parent_group = get_group(funder) or wallet_labels.get(funder.lower(),"")
                                        if parent_group:
                                            wallet_groups[to_addr.lower()]=parent_group
                                            save_groups()
                                        try: EVM_WALLETS.append(Web3.to_checksum_address(to_addr))
                                        except: EVM_WALLETS.append(to_addr)
                                        new_children.add(to_addr.lower()); mark_active(to_addr)
                                        splitter_child_count[funder].add(to_addr.lower())
                                        save_wallets(); save_new_children(); save_last_active()
                                        send_tg(f"👶 <b>NEW EVM CHILD {chain}</b> {val:.4f}\nFrom <code>{short(funder)}</code> → <code>{short(to_addr)}</code>")
                    except: continue
        except Exception as e: print(f"recursive EVM err {e}", flush=True)

# === COMMAND HANDLER + NEW DELETE LABELS ===
def handle_command(text):
    global SOL_WALLETS, EVM_WALLETS, wallet_labels, wallet_groups, last_bundle, pending_label, new_children, last_active
    try:
        t=text.strip()
        if not t.startswith("/"): return
        cmd = t.split()[0].lower().split('@')[0]
        args = t.split()

        if cmd=="/skip":
            if pending_label:
                pending_label=False
                last_bundle={"addresses":[],"funder":"","root":"","time":None}
                send_tg("Skipped.")
            else:
                send_tg("No pending bundle.")
            return

        # === NEW COMMANDS: /deletelabels, /deletelabel, /removelabel ===
        if cmd in ["/deletelabels", "/deletelabel", "/removelabel"]:
            if len(args)>=2:
                # direct: /deletelabel <name>
                label_name = " ".join(args[1:]).strip()
                del_count, total_found = delete_label_and_addresses(label_name)
                if total_found==0:
                    send_tg(f"❌ Label not found: <b>{label_name}</b>")
                else:
                    send_tg(f"✅ Deleted label <b>{label_name}</b> + {del_count} wallets (found {total_found})")
                return
            # no args -> show list with inline buttons
            groups = get_all_labels_with_counts()
            if not groups:
                send_tg("No labels/groups to delete.")
                return
            keyboard = []
            for label, addrs in groups.items():
                # callback_data max 64 bytes, label max 30
                safe_label = label[:30]
                keyboard.append([{"text": f"🗑️ {safe_label} ({len(addrs)})", "callback_data": f"dellbl_ask:{safe_label}"}])
            keyboard.append([{"text": "❌ Cancel", "callback_data": "dellbl_cancel"}])
            markup = {"inline_keyboard": keyboard}
            msg = f"<b>🗑️ DELETE LABELS</b> — {len(groups)} found\n\nTap to delete label + ALL its wallets:\n"
            for lbl, adrs in list(groups.items())[:15]:
                msg+=f"• <b>{lbl}</b>: {len(adrs)} wallets\n"
            send_tg_markup(msg, markup)
            return

        if cmd in ["/remove", "/delete", "/rm", "/delwallet", "/untrack"]:
            if len(args)<2:
                send_tg("Usage: /remove &lt;addr&gt;\nExample: /remove 0xfd87... or /remove Beqv6...\nBulk: /remove all_sol /remove all_evm /remove all")
                return
            target = args[1].strip()
            target_lower = target.lower()

            if target_lower in ["all", "all_sol", "all_evm"]:
                if target_lower == "all_sol":
                    c = len(SOL_WALLETS); SOL_WALLETS.clear()
                    send_tg(f"✅ Removed all {c} SOL wallets")
                elif target_lower == "all_evm":
                    c = len(EVM_WALLETS); EVM_WALLETS.clear()
                    send_tg(f"✅ Removed all {c} EVM wallets")
                else:
                    c1=len(SOL_WALLETS); c2=len(EVM_WALLETS)
                    SOL_WALLETS.clear(); EVM_WALLETS.clear()
                    send_tg(f"✅ Removed all {c1} SOL + {c2} EVM wallets")
                save_wallets(); save_new_children(); save_last_active()
                return

            before_sol=len(SOL_WALLETS)
            SOL_WALLETS=[w for w in SOL_WALLETS if w.lower()!=target_lower]
            found_sol = len(SOL_WALLETS) < before_sol

            before_evm=len(EVM_WALLETS)
            EVM_WALLETS=[w for w in EVM_WALLETS if w.lower()!=target_lower]
            found_evm = len(EVM_WALLETS) < before_evm

            new_children.discard(target_lower)
            last_active.pop(target_lower, None)
            removed_label=False
            for d in [wallet_labels, wallet_groups]:
                for k in list(d.keys()):
                    if k.lower()==target_lower:
                        del d[k]; removed_label=True

            if found_sol or found_evm or removed_label:
                save_wallets(); save_new_children(); save_last_active(); save_labels(); save_groups()
                send_tg(f"✅ Removed <code>{short(target)}</code> — stopped tracking SOL & EVM")
            else:
                send_tg(f"❌ Not found: <code>{short(target)}</code>\nUse /listwallets or /listevm to see addresses")
            return

        if cmd=="/labelgroup":
            if len(args)<2:
                send_tg("Usage: /labelgroup &lt;name&gt;"); return
            group_name=" ".join(args[1:]).strip()[:30]
            if not last_bundle or not last_bundle.get("addresses"):
                send_tg("No bundle. /bundle first"); return
            for addr in last_bundle["addresses"]:
                if not addr: continue
                wallet_groups[addr.lower()]=group_name
            save_groups(); save_labels()
            pending_label=False
            send_tg(f"✅ Group '<b>{group_name}</b>' → {len(last_bundle['addresses'])} wallets")
            return
        if cmd=="/label":
            if len(args)<3:
                send_tg("Usage: /label &lt;addr&gt; &lt;name&gt;"); return
            addr=args[1].strip(); label_name=" ".join(args[2:]).strip()[:30]
            wallet_labels[addr.lower()]=label_name; wallet_groups[addr.lower()]=label_name
            save_labels(); save_groups()
            send_tg(f"Labeled <code>{short(addr)}</code> as '{label_name}'")
            return
        elif cmd=="/unlabel":
            if len(args)<2: send_tg("Usage: /unlabel &lt;addr&gt;"); return
            addr=args[1].strip(); removed=False
            for d in [wallet_labels, wallet_groups]:
                for k in list(d.keys()):
                    if k.lower()==addr.lower(): del d[k]; removed=True
            save_labels(); save_groups()
            send_tg(f"Removed label <code>{short(addr)}</code>" if removed else f"No label for <code>{short(addr)}</code>")
            return
        elif cmd=="/labels":
            if not wallet_groups and not wallet_labels:
                send_tg("No labels yet."); return
            groups=defaultdict(list)
            for addr, grp in wallet_groups.items(): groups[grp].append(addr)
            msg=f"<b>Groups {len(groups)}:</b>\n"
            for grp, addrs in list(groups.items())[:10]:
                msg+=f"\n<b>'{grp}'</b> {len(addrs)}:\n" + "\n".join([f"• <code>{short(a)}</code>" for a in addrs[:3]])
                if len(addrs)>3: msg+=f"\n +{len(addrs)-3} more\n"
            msg+="\nUse /deletelabels to delete"
            send_tg(msg); return
        if cmd=="/start":
            groups_count=len(set(wallet_groups.values())) if wallet_groups else 0
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            send_tg(f"<b>V4.9.4 CLICKABLE LIVE + DELETE</b>\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}\nGroups:{groups_count} Last:{mins}m DIR:{DATA_DIR}\n\n/deletelabels - delete label + wallets")
        elif cmd=="/bundle":
            if len(args)<2: send_tg("Usage: /bundle &lt;addr&gt; [BASE/BSC]")
            else:
                addr=args[1].strip(); chain=args[2].upper() if len(args)>2 else ("SOL" if not addr.startswith("0x") else "BASE")
                if addr.startswith("0x"):
                    if chain not in ["BASE","BSC"]: chain="BASE"
                    threading.Thread(target=analyze_evm_bundle, args=(chain, addr), daemon=True).start()
                else: threading.Thread(target=analyze_wallet_bundle, args=(addr,), daemon=True).start()
        elif cmd=="/track_funders":
            if not known_splitters: send_tg("No splitters")
            else:
                msg=f"<b>Splitters {len(known_splitters)}</b>\n" + "\n".join([f"• <code>{short(s)}</code>" for s in list(known_splitters)[-10:]])
                send_tg(msg)
        elif cmd=="/listwallets":
            if not SOL_WALLETS:
                send_tg("No SOL wallets"); return
            chunk=8
            for i in range(0, len(SOL_WALLETS), chunk):
                slice_wallets = SOL_WALLETS[i:i+chunk]
                msg = f"<b>SOL {len(SOL_WALLETS)} — Full + Clickable:</b>\n\n"
                for idx, w in enumerate(slice_wallets, start=i+1):
                    lbl = wallet_labels.get(w.lower(),"")
                    label_txt = f" [{lbl}]" if lbl else ""
                    msg += f"{idx}.{label_txt}\n<code>{w}</code>\n/remove {w}\n\n"
                send_tg(msg)
                time.sleep(0.6)
            return
        elif cmd=="/listevm":
            if not EVM_WALLETS:
                send_tg("No EVM wallets"); return
            chunk=8
            for i in range(0, len(EVM_WALLETS), chunk):
                slice_wallets = EVM_WALLETS[i:i+chunk]
                msg = f"<b>EVM {len(EVM_WALLETS)} — Full + Clickable:</b>\n\n"
                for idx, w in enumerate(slice_wallets, start=i+1):
                    lbl = wallet_labels.get(w.lower(),"")
                    label_txt = f" [{lbl}]" if lbl else ""
                    msg += f"{idx}.{label_txt}\n<code>{w}</code>\n/remove {w}\n\n"
                send_tg(msg)
                time.sleep(0.6)
            return
        elif cmd=="/testalert": send_tg(f"✅ V4.9.4 WORKING DIR:{DATA_DIR}")
        elif cmd=="/help": send_tg("/bundle <addr> [BASE/BSC]\n/labelgroup <name>\n/remove <addr>\n/deletelabels - list + delete labels with all wallets\n/deletelabel <name> - delete directly\n/listwallets\n/lisevm")
    except Exception as e:
        print(f"cmd err {e}",flush=True)

def handle_callback_query(cb):
    try:
        data = cb.get("data","")
        cb_id = cb.get("id")
        msg = cb.get("message",{})
        chat_id = msg.get("chat",{}).get("id") or CHAT_ID
        msg_id = msg.get("message_id")

        # answer callback to remove loading
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=5)
        except: pass

        if data.startswith("dellbl_ask:"):
            label = data.split(":",1)[1]
            groups = get_all_labels_with_counts()
            count = len(groups.get(label, []))
            keyboard = [
                [{"text": f"✅ YES DELETE {label} + {count} wallets", "callback_data": f"dellbl_confirm:{label}"}],
                [{"text": "❌ Cancel", "callback_data": "dellbl_cancel"}]
            ]
            text = f"⚠️ Delete <b>{label}</b>?\n\nThis will delete the label AND all <b>{count}</b> addresses linked to it.\nCannot be undone."
            # edit message
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={
                    "chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": keyboard}
                }, timeout=10)
            except:
                send_tg_markup(text, {"inline_keyboard": keyboard})

        elif data.startswith("dellbl_confirm:"):
            label = data.split(":",1)[1]
            del_count, total_found = delete_label_and_addresses(label)
            text = f"✅ Deleted <b>{label}</b>\nRemoved {del_count} wallets (found {total_found})." if total_found else f"❌ Label {label} not found."
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={
                    "chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"
                }, timeout=10)
            except:
                send_tg(text)

        elif data == "dellbl_cancel":
            try:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json={
                    "chat_id": chat_id, "message_id": msg_id, "text": "Cancelled.", "parse_mode": "HTML"
                }, timeout=10)
            except:
                send_tg("Cancelled.")
    except Exception as e:
        print(f"callback err {e}", flush=True)

def set_bot_commands():
    cmds=[
        {"command":"start","description":"V4.9.4 status"},
        {"command":"bundle","description":"Bundle deep"},
        {"command":"labelgroup","description":"Label bundle"},
        {"command":"skip","description":"Skip"},
        {"command":"label","description":"Label wallet"},
        {"command":"labels","description":"List groups"},
        {"command":"deletelabels","description":"DELETE labels + wallets with buttons"},
        {"command":"deletelabel","description":"DELETE label directly"},
        {"command":"unlabel","description":"Remove label only"},
        {"command":"remove","description":"DELETE wallet SOL/EVM"},
        {"command":"listwallets","description":"List SOL FULL clickable"},
        {"command":"listevm","description":"List EVM FULL clickable"},
        {"command":"track_funders","description":"Splitters"},
        {"command":"testalert","description":"Test"},
        {"command":"help","description":"Help"}
    ]
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",json={"commands":cmds},timeout=10)
    except: pass

def heartbeat():
    while True:
        time.sleep(120)
        try:
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            print(f"HEARTBEAT V4.9.4 {mins}m SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} DIR:{DATA_DIR}", flush=True)
        except: pass

async def main_loop():
    print(f">>> V4.9.4 CLICKABLE + DELETE {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM DIR:{DATA_DIR}", flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=track_funders_polling, daemon=True).start()
    threading.Thread(target=prune_inactive, daemon=True).start()
    send_tg(f"<b>V4.9.4 DELETE LABELS DEPLOYED</b>\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}\nDIR:{DATA_DIR}\n/deletelabels to delete label + wallets ✅"); set_bot_commands()
    tasks=[track_chain("BASE"), track_chain("BSC"), track_sol_polling()]
    await asyncio.gather(*tasks)

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e:
        print(f"CRASHED {e}", flush=True); time.sleep(5); start_bot()

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    def poll_cmd():
        off=0
        while True:
            try:
                if not BOT_TOKEN: time.sleep(5); continue
                r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=10",timeout=15).json()
                for u in r.get("result",[]):
                    off=u["update_id"]+1
                    # message commands
                    txt=u.get("message",{}).get("text","")
                    if txt and txt.startswith("/"):
                        print(f"CMD {txt}", flush=True)
                        handle_command(txt)
                    # callback queries for delete labels
                    if "callback_query" in u:
                        print(f"CALLBACK {u['callback_query'].get('data')}", flush=True)
                        handle_callback_query(u["callback_query"])
            except Exception as e:
                print(f"poll err {e}", flush=True); time.sleep(3)
    threading.Thread(target=poll_cmd,daemon=True).start()
    time.sleep(1); start_bot()
