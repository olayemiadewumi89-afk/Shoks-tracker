import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque
from web3 import Web3

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
]

WALLETS_FILE="wallets.json"; MEMORY_FILE="cluster_memory.json"; FUNDERS_FILE="funders.json"; NEW_CHILDREN_FILE="new_children.json"; LAST_ACTIVE_FILE="last_active.json"; LABELS_FILE="labels.json"; GROUPS_FILE="groups.json"
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
            d=json.load(open(WALLETS_FILE)); SOL_WALLETS=d.get("sol",SOL_WALLETS); EVM_WALLETS=d.get("evm",EVM_WALLETS)
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
    try: return f"{a[:4]}...{a[-4:]}" if len(a)>8 else a
    except: return "?"
def format_wallet(addr, is_new_child=False, parent_addr=None):
    lbl = wallet_labels.get(addr.lower(), wallet_labels.get(addr, ""))
    grp = wallet_groups.get(addr.lower(), "")
    sh = f"{addr[:4]}...{addr[-4:]}" if len(addr)>8 else addr
    if is_new_child and parent_addr:
        parent_label = get_label(parent_addr) or short(parent_addr)
        return f"{parent_label} -> NEW CHILD ({sh})"
    if lbl and grp and lbl!=grp: return f"{lbl} [{grp}] ({sh})"
    if lbl: return f"{lbl} ({sh})"
    if grp: return f"{grp} ({sh})"
    return sh

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
                send_tg(f"Auto-cleaned {removed} dead >30d | SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}")
        except Exception as e: print(f"prune err {e}", flush=True)

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com"]
rpc_idx=0
def get_public_rpc():
    global rpc_idx, last_rpc_call
    now=time.time()
    if now-last_rpc_call<0.5: time.sleep(0.5-(now-last_rpc_call))
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
def home(): return "Shok V4.7 INNER FIX",200
@app.route('/health')
def health(): return "OK",200
@app.route('/debug')
def debug():
    try:
        groups_len = len(set(wallet_groups.values())) if wallet_groups else 0
        return f"V4.7 SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} Groups:{groups_len} Last:{int((datetime.now()-last_tx_time).total_seconds()/60)}m Busy:{bundle_in_progress}",200
    except Exception as e:
        return f"DEBUG ERR {e}",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))

def send_tg_worker():
    while True:
        try:
            if tg_queue:
                text=tg_queue.popleft()
                if not BOT_TOKEN or not CHAT_ID: print(text[:500], flush=True)
                else:
                    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15)
                    except: pass
                time.sleep(1.0)
            else: time.sleep(0.4)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker, daemon=True).start()
def send_tg(text):
    if len(tg_queue)>20: tg_queue.clear()
    tg_queue.append(text)

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

# V4.7 FIXED - CATCHES INNER INSTRUCTIONS (your HsQV bug)
def get_sol_transfers_cached(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<300:
        return funder_cache[sig][1]
    transfers=[]
    try:
        rpc_url=get_public_rpc()
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        r=requests.post(rpc_url, json=payload, timeout=12)
        res=r.json().get("result")
        if not res:
            funder_cache[sig]=(datetime.now(),[]); return []
        # outer
        for instr in res.get("transaction",{}).get("message",{}).get("instructions",[]):
            try:
                if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                    info=instr["parsed"]["info"]
                    transfers.append((info["source"], info["destination"], float(info.get("lamports",0))/1e9, sig))
            except: continue
        # inner - THIS IS THE KEY FIX for HsQV wallet
        for inner in res.get("meta",{}).get("innerInstructions",[]) or []:
            for instr in inner.get("instructions",[]) or []:
                try:
                    if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                        info=instr["parsed"]["info"]
                        transfers.append((info["source"], info["destination"], float(info.get("lamports",0))/1e9, sig))
                except: continue
    except: pass
    funder_cache[sig]=(datetime.now(),transfers)
    if len(funder_cache)>500: funder_cache.clear()
    return transfers

def analyze_wallet_bundle(wallet):
    global last_bundle, pending_label, bundle_in_progress
    if bundle_in_progress:
        send_tg("Bundle busy, wait 20s...")
        return
    bundle_in_progress=True
    wallet=wallet.strip()
    send_tg(f"Scanning SOL {format_wallet(wallet)}...")
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet, {"limit":30}]}
        r=requests.post(get_public_rpc(), json=payload, timeout=15).json()
        sigs=r.get("result",[]); funders=[]; children=[]
        for item in sigs[:25]:
            sig=item.get("signature")
            if not sig: continue
            for frm,to,amt,_ in get_sol_transfers_cached(sig):
                if to==wallet and amt>=0.008:
                    if frm not in [f[0] for f in funders]: funders.append((frm, amt, sig))
                if frm==wallet and amt>=0.008 and to!=wallet:
                    if to not in [c[0] for c in children]: children.append((to, amt))
        if funders:
            main_funder, amt, _ = funders[0]; siblings=[]
            payload2={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[main_funder, {"limit":30}]}
            r2=requests.post(get_public_rpc(), json=payload2, timeout=15).json()
            for item in r2.get("result",[])[:20]:
                s=item.get("signature")
                if not s: continue
                for frm,to,amt2,_ in get_sol_transfers_cached(s):
                    if frm==main_funder and amt2>=0.008 and to!=wallet:
                        if to not in [x[0] for x in siblings]: siblings.append((to, amt2))
            all_group=[wallet, main_funder] + [s for s,_ in siblings[:8]]
            added=0
            for addr in [a for a in all_group if a!=wallet]:
                if addr not in SOL_WALLETS and len(SOL_WALLETS)<150:
                    SOL_WALLETS.append(addr); added+=1; new_children.add(addr.lower()); mark_active(addr)
            if added>0: save_wallets(); save_new_children(); save_last_active()
            known_splitters.add(main_funder); save_funders()
            last_bundle={"addresses":all_group,"funder":main_funder,"root":wallet,"time":datetime.now().isoformat()}
            pending_label=True
            msg=f"BUNDLE SOL {format_wallet(wallet)}\nSPLITTER {format_wallet(main_funder)} {amt:.3f}\nSIBLINGS {len(siblings)}\n" + "".join([f" - {format_wallet(s)} {a:.3f}\n" for s,a in siblings[:6]])
            msg+=f"\nAuto-added {added} - /labelgroup <name> or /skip"
            send_tg(msg[:3500])
        elif children:
            all_group=[wallet] + [c for c,_ in children[:12]]
            added=0
            for c, _ in children[:12]:
                if c not in SOL_WALLETS and len(SOL_WALLETS)<150:
                    SOL_WALLETS.append(c); added+=1; new_children.add(c.lower()); mark_active(c)
            save_wallets(); save_new_children(); save_funders()
            known_splitters.add(wallet); known_funders.add(wallet); save_funders()
            last_bundle={"addresses":all_group,"funder":wallet,"root":wallet,"time":datetime.now().isoformat()}
            pending_label=True
            msg=f"BUNDLE SOL ROOT {format_wallet(wallet)}\nFunded {len(children)} children (inner scan):\n" + "".join([f" - {format_wallet(c)} {a:.3f} SOL\n" for c,a in children[:10]])
            msg+=f"\nAuto-added {added} CHILDREN\n/labellgroup <name> or /skip".replace("labellgroup","labelgroup")
            send_tg(msg[:3500])
        else:
            send_tg(f"No transfers found for {format_wallet(wallet)} in last 25 txs (checked inner too). Wallet may be dormant. Check solscan.io/tx history")
    except Exception as e: send_tg(f"Bundle err {e}")
    finally: bundle_in_progress=False

def analyze_evm_bundle(chain, wallet):
    global last_bundle, pending_label, bundle_in_progress
    if bundle_in_progress:
        send_tg("Bundle busy, wait 20s...")
        return
    bundle_in_progress=True
    send_tg(f"Scanning EVM {chain} {format_wallet(wallet)}...")
    try:
        w3=get_w3_with_fallback(chain)
        if not w3: send_tg(f"{chain} RPC down"); bundle_in_progress=False; return
        wallet_chk=Web3.to_checksum_address(wallet); latest=w3.eth.block_number; funder=None; start_time=time.time()
        for bn in range(latest, max(latest-1500,0), -1):
            if time.time()-start_time>15: break
            try:
                block=w3.eth.get_block(bn, full_transactions=True)
                for tx in block.transactions:
                    if tx.get('to','') and tx.get('to','').lower()==wallet_chk.lower() and int(tx.get('value',0))>0:
                        val=float(w3.from_wei(tx['value'],'ether'))
                        if val>=0.005:
                            funder=(tx.get('from'), val); break
                if funder: break
            except: continue
        if not funder:
            send_tg(f"No funder in last 1500 blocks {chain} for {format_wallet(wallet)}\nAdding single")
            if wallet.lower() not in [x.lower() for x in EVM_WALLETS]:
                EVM_WALLETS.append(wallet_chk); save_wallets()
                last_bundle={"addresses":[wallet],"funder":"","root":wallet,"time":datetime.now().isoformat()}
                pending_label=True
                send_tg(f"Added single: {format_wallet(wallet)}\n/labelgroup <name> or /skip")
            bundle_in_progress=False; return
        funder_addr, amt = funder; siblings=[]; start_time=time.time()
        try:
            latest=w3.eth.block_number
            for bn in range(latest, max(latest-1000,0), -1):
                if time.time()-start_time>10: break
                if len(siblings)>=8: break
                try:
                    block=w3.eth.get_block(bn, full_transactions=True)
                    for tx in block.transactions:
                        if tx.get('from','') and tx.get('from','').lower()==funder_addr.lower() and int(tx.get('value',0))>0:
                            to_addr=tx.get('to','')
                            if to_addr.lower()!=wallet.lower():
                                val=float(w3.from_wei(tx['value'],'ether'))
                                if val>=0.005 and to_addr.lower() not in [s[0].lower() for s in siblings]:
                                    siblings.append((to_addr, val))
                except: continue
        except: pass
        msg=f"BUNDLE {chain} {format_wallet(wallet)}\nFUNDER {format_wallet(funder_addr)} {amt:.4f}\n\n"
        if siblings: msg+=f"SIBLINGS {len(siblings)}:\n" + "".join([f" - {format_wallet(s)} {a:.4f}\n" for s,a in siblings[:6]])
        added=0; all_group=[wallet, funder_addr]
        if funder_addr.lower() not in [x.lower() for x in EVM_WALLETS]:
            try: EVM_WALLETS.append(Web3.to_checksum_address(funder_addr))
            except: EVM_WALLETS.append(funder_addr)
            added+=1; known_splitters.add(funder_addr); mark_active(funder_addr)
        for sib,_ in siblings[:6]:
            if sib.lower() not in [x.lower() for x in EVM_WALLETS]:
                try: EVM_WALLETS.append(Web3.to_checksum_address(sib))
                except: EVM_WALLETS.append(sib)
                added+=1; new_children.add(sib.lower()); mark_active(sib); all_group.append(sib)
        if added>0: save_wallets(); save_funders(); save_new_children(); save_last_active()
        last_bundle={"addresses":all_group,"funder":funder_addr,"root":wallet,"time":datetime.now().isoformat()}
        pending_label=True
        msg+=f"\nAuto-added {added} EVM\n/labellgroup <name> or /skip".replace("labellgroup","labelgroup")
        send_tg(msg[:3500])
    except Exception as e: send_tg(f"EVM bundle err {e}")
    finally: bundle_in_progress=False

def check_cluster_1d(mint, name, mcap, dex_link, chain):
    now=datetime.now(); events=cluster_memory.get(mint,[]); recent=[e for e in events if (now-e[1])<=timedelta(days=1)]
    uniq={}; [uniq.__setitem__(w.lower(),w) for w,_,_ in recent]
    uniq_wallets=list(uniq.values())
    if len(uniq_wallets)<2: return
    last=cluster_alerted.get(mint.lower())
    if last and (now-last)<=timedelta(hours=12): return
    cluster_alerted[mint.lower()]=now
    wallets_str="\n".join([f"- {format_wallet(w)}" for w in uniq_wallets[:6]])
    send_tg(f"CLUSTER BUY 1D\n\n{name} ({mcap}) {chain}\n{len(uniq_wallets)} wallets:\n{wallets_str}\nChart: {dex_link}")

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
    if is_buy:
        if is_new:
            grp = get_group(owner)
            if grp: label_str = f"{grp} -> NEW CHILD FIRST BUY ({short(owner)})"
            send_tg(f"NEW CHILD FIRST BUY SOL! [Group: {grp or 'Unknown'}]\n{name} ({mcap}) Liq ${liq:,.0f}\nWallet: {label_str}\nBuy ${usd:,.2f} | {amt:,.0f}\n{dex_link}\nTx https://solscan.io/tx/{sig}")
        else:
            send_tg(f"SOL BUY $10+\n{name} ({mcap})\nWallet: {label_str} ${usd:,.2f}\n{dex_link}\nTx https://solscan.io/tx/{sig}")
        threading.Thread(target=lambda: check_cluster_1d(mint,name,mcap,dex_link,"SOL"),daemon=True).start()
    else:
        send_tg(f"SOL SELL $10+\n{name} ({mcap})\nWallet: {label_str} ${usd:,.2f}\n{dex_link}")

async def track_sol_polling():
    print("SOL V4.7 INNER $10+", flush=True)
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
                                if is_new:
                                    grp = get_group(frm)
                                    if grp: label_str = f"{grp} -> NEW CHILD ({short(frm)})"
                                if is_sell:
                                    send_tg(f"{chain} SELL $10+\n{name} ({mcap}) ${usd_val:,.2f}\nWallet: {label_str}\n{dex_link}")
                                else:
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    if is_new:
                                        send_tg(f"NEW CHILD FIRST BUY {chain}! [Group: {get_group(frm) or 'Unknown'}]\n{name} ({mcap}) ${usd_val:,.2f}\nWallet: {label_str}\n{dex_link}")
                                    else:
                                        send_tg(f"{chain} BUY $10+\n{name} ({mcap}) ${usd_val:,.2f}\nWallet: {label_str}\n{dex_link}")
                                    threading.Thread(target=lambda: check_cluster_1d(token_contract,name,mcap,dex_link,chain),daemon=True).start()
                        except: pass
                except: pass
            await asyncio.sleep(4)
        except Exception as e: print(f"[{chain}] err {e}", flush=True); await asyncio.sleep(5)

def track_funders_polling():
    print("Funder watcher V4.7 60s", flush=True)
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
                            if frm==funder and amt>=0.02 and to not in SOL_WALLETS:
                                parent_group = get_group(frm) or wallet_labels.get(frm.lower(),"")
                                if parent_group:
                                    wallet_groups[to.lower()]=parent_group
                                    save_groups()
                                SOL_WALLETS.append(to); new_children.add(to.lower()); mark_active(to)
                                splitter_child_count[funder].add(to.lower())
                                save_wallets(); save_new_children(); save_last_active()
                                send_tg(f"NEW CHILD FUNDED! RECURSIVE\nParent {format_wallet(funder)} -> {format_wallet(to, is_new_child=True, parent_addr=funder)} {amt:.3f} SOL\nGroup: {parent_group or 'No group'}")
                                time.sleep(0.8)
                    if len(splitter_child_count[funder])>=2 and funder not in known_splitters:
                        known_splitters.add(funder); save_funders()
                        send_tg(f"RECURSIVE PROMOTE\n{format_wallet(funder)} funded {len(splitter_child_count[funder])} wallets -> SPLITTER")
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
                                        send_tg(f"NEW EVM CHILD FUNDED RECURSIVE {chain}!\nParent {format_wallet(funder)} -> {format_wallet(to_addr, is_new_child=True, parent_addr=funder)} {val:.4f}")
                    except: continue
        except Exception as e: print(f"recursive EVM err {e}", flush=True)

def handle_command(text):
    global SOL_WALLETS, EVM_WALLETS, wallet_labels, wallet_groups, last_bundle, pending_label
    try:
        t=text.strip()
        if not t.startswith("/"): return
        cmd = t.split()[0].lower().split('@')[0]
        args = t.split()
        if cmd=="/skip":
            if pending_label:
                pending_label=False
                last_bundle={"addresses":[],"funder":"","root":"","time":None}
                send_tg("Skipped labeling.")
            else:
                send_tg("No pending bundle.")
            return
        if cmd=="/labelgroup":
            if len(args)<2:
                send_tg("Usage: /labelgroup <name>")
                return
            group_name=" ".join(args[1:]).strip()[:30]
            if not last_bundle or not last_bundle.get("addresses"):
                send_tg("No recent bundle. First /bundle <addr>")
                return
            count=0
            for addr in last_bundle["addresses"]:
                if not addr: continue
                wallet_groups[addr.lower()]=group_name
                count+=1
            save_groups(); save_labels()
            pending_label=False
            send_tg(f"Group '{group_name}' -> {count} wallets\nFuture children auto-labeled")
            return
        if cmd=="/label":
            if len(args)<3:
                send_tg("Usage: /label <addr> <name>")
                return
            addr=args[1].strip()
            label_name=" ".join(args[2:]).strip()[:30]
            wallet_labels[addr.lower()]=label_name
            wallet_groups[addr.lower()]=label_name
            save_labels(); save_groups()
            send_tg(f"Labeled {short(addr)} as '{label_name}'")
            return
        elif cmd=="/unlabel":
            if len(args)<2: send_tg("Usage: /unlabel <addr>"); return
            addr=args[1].strip()
            removed=False
            for d in [wallet_labels, wallet_groups]:
                for k in list(d.keys()):
                    if k.lower()==addr.lower(): del d[k]; removed=True
            save_labels(); save_groups()
            send_tg(f"Removed label for {short(addr)}" if removed else f"No label for {short(addr)}")
            return
        elif cmd=="/labels":
            if not wallet_groups and not wallet_labels:
                send_tg("No labels yet."); return
            groups=defaultdict(list)
            for addr, grp in wallet_groups.items(): groups[grp].append(addr)
            msg=f"Groups {len(groups)}:\n"
            for grp, addrs in list(groups.items())[:10]:
                msg+=f"\n'{grp}' {len(addrs)}:\n" + "\n".join([f" - {short(a)} {a}" for a in addrs[:3]])
                if len(addrs)>3: msg+=f"\n +{len(addrs)-3} more\n"
            send_tg(msg[:3500])
            return
        if cmd=="/start":
            groups_count=len(set(wallet_groups.values())) if wallet_groups else 0
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            send_tg(f"V4.7 INNER FIX LIVE\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}\nGroups:{groups_count} Labels:{len(wallet_labels)}\nLast:{mins}m Spl:{len(known_splitters)}\n/bundle -> /labelgroup <name>")
        elif cmd=="/bundle":
            if len(args)<2: send_tg("Usage: /bundle <addr> [BASE/BSC]")
            else:
                addr=args[1].strip(); chain=args[2].upper() if len(args)>2 else ("SOL" if not addr.startswith("0x") else "BASE")
                if addr.startswith("0x"):
                    if chain not in ["BASE","BSC"]: chain="BASE"
                    threading.Thread(target=analyze_evm_bundle, args=(chain, addr), daemon=True).start()
                else: threading.Thread(target=analyze_wallet_bundle, args=(addr,), daemon=True).start()
        elif cmd=="/track_funders":
            if not known_splitters: send_tg("No splitters")
            else:
                msg=f"Splitters {len(known_splitters)}\n" + "\n".join([f"- {format_wallet(s)} ({len(splitter_child_count[s])})" for s in list(known_splitters)[-10:]])
                send_tg(msg[:3500])
        elif cmd=="/listwallets":
            sol="\n".join([f"{i+1}. {format_wallet(w)} {w}" for i,w in enumerate(SOL_WALLETS[-20:])])
            send_tg((f"SOL {len(SOL_WALLETS)}:\n{sol}")[:3500])
        elif cmd=="/listevm":
            evm="\n".join([f"{i+1}. {format_wallet(w)} {w}" for i,w in enumerate(EVM_WALLETS[-20:])])
            send_tg((f"EVM {len(EVM_WALLETS)}:\n{evm}")[:3500])
        elif cmd=="/testalert":
            send_tg(f"V4.7 WORKING")
        elif cmd=="/help":
            send_tg("/bundle <addr>\n/labelgroup <name> or /skip\n/label <addr> <name>\n/labels /unlabel")
    except Exception as e:
        print(f"cmd err {e}",flush=True)

def set_bot_commands():
    cmds=[
        {"command":"start","description":"V4.7 status"},
        {"command":"bundle","description":"Bundle tree"},
        {"command":"labelgroup","description":"Label last bundle"},
        {"command":"skip","description":"Skip labeling"},
        {"command":"label","description":"Label wallet"},
        {"command":"labels","description":"List groups"},
        {"command":"unlabel","description":"Remove label"},
        {"command":"track_funders","description":"List funders"},
        {"command":"listwallets","description":"List SOL"},
        {"command":"listevm","description":"List EVM"},
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
            print(f"HEARTBEAT V4.7 INNER {mins}m SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} Busy:{bundle_in_progress}", flush=True)
        except: pass

async def main_loop():
    print(f">>> V4.7 INNER FIX {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM", flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=track_funders_polling, daemon=True).start()
    threading.Thread(target=prune_inactive, daemon=True).start()
    send_tg(f"V4.7 INNER FIX DEPLOYED\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}\nINNER SCAN FIX FOR HsQV\n/bundle -> /labelgroup"); set_bot_commands()
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
                    txt=u.get("message",{}).get("text","")
                    if txt and txt.startswith("/"):
                        print(f"CMD {txt}", flush=True)
                        handle_command(txt)
            except Exception as e:
                print(f"poll err {e}", flush=True); time.sleep(3)
    threading.Thread(target=poll_cmd,daemon=True).start()
    time.sleep(1); start_bot()
