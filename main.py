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

WALLETS_FILE="wallets.json"; MEMORY_FILE="cluster_memory.json"; HOLDINGS_FILE="holdings.json"; PNL_FILE="pnl.json"; FUNDERS_FILE="funders.json"; NEW_CHILDREN_FILE="new_children.json"; LAST_ACTIVE_FILE="last_active.json"
cluster_memory=defaultdict(list); holdings=defaultdict(dict)
pnl_tracker=defaultdict(lambda: {"buys":0,"sells":0,"spent":0.0,"realized":0.0})
seen_sigs=set(); cluster_alerted={}; funder_cache={}; tg_queue=deque()
STABLES={"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}
last_tx_time=datetime.now(); known_splitters=set(); known_funders=set(); new_children=set(); last_active={}; last_rpc_call=0

def load_wallets():
    global SOL_WALLETS, EVM_WALLETS, known_splitters, known_funders, new_children, last_active
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE)); SOL_WALLETS=d.get("sol",SOL_WALLETS); EVM_WALLETS=d.get("evm",EVM_WALLETS)
    except: pass
    try:
        if os.path.exists(FUNDERS_FILE):
            d=json.load(open(FUNDERS_FILE)); known_splitters=set(d.get("splitters",[])); known_funders=set(d.get("funders",[]))
    except: pass
    try:
        if os.path.exists(NEW_CHILDREN_FILE):
            new_children=set([x.lower() for x in json.load(open(NEW_CHILDREN_FILE))])
    except: pass
    try:
        if os.path.exists(LAST_ACTIVE_FILE):
            last_active=json.load(open(LAST_ACTIVE_FILE))
    except: pass

def save_wallets():
    try: json.dump({"sol":SOL_WALLETS,"evm":EVM_WALLETS}, open(WALLETS_FILE,"w"))
    except: pass
def save_funders():
    try: json.dump({"splitters":list(known_splitters)[-100:], "funders":list(known_funders)[-100:]}, open(FUNDERS_FILE,"w"))
    except: pass
def save_new_children():
    try: json.dump(list(new_children)[-150:], open(NEW_CHILDREN_FILE,"w"))
    except: pass
def save_last_active():
    try: json.dump(last_active, open(LAST_ACTIVE_FILE,"w"))
    except: pass
def save_memory():
    try:
        data={k:[(w,ts.isoformat(),ch) for w,ts,ch in v[-200:]] for k,v in cluster_memory.items()}
        json.dump(data, open(MEMORY_FILE,"w"))
    except: pass
def save_holdings():
    try: json.dump(dict(holdings), open(HOLDINGS_FILE,"w"))
    except: pass
def save_pnl():
    try: json.dump(dict(pnl_tracker), open(PNL_FILE,"w"))
    except: pass
def load_persistent():
    try:
        if os.path.exists(MEMORY_FILE):
            for k,v in json.load(open(MEMORY_FILE)).items():
                for w,ts_str,ch in v:
                    try: ts=datetime.fromisoformat(ts_str)
                    except: ts=datetime.now()
                    cluster_memory[k].append((w,ts,ch))
    except: pass
    try:
        if os.path.exists(HOLDINGS_FILE):
            for k,v in json.load(open(HOLDINGS_FILE)).items(): holdings[k]=v
    except: pass
    try:
        if os.path.exists(PNL_FILE):
            pnl_tracker.update(json.load(open(PNL_FILE)))
    except: pass

load_wallets(); load_persistent()

def mark_active(wallet):
    try:
        last_active[wallet.lower()]=datetime.now().isoformat()
        if len(last_active)%20==0: save_last_active()
    except: pass

def prune_inactive():
    while True:
        time.sleep(3600*6)
        try:
            now=datetime.now(); to_remove_sol=[]; to_remove_evm=[]
            for w in SOL_WALLETS[:]:
                la_str=last_active.get(w.lower())
                if not la_str: continue
                try:
                    la=datetime.fromisoformat(la_str)
                    if (now-la).days > 30:
                        if w in known_splitters or w in known_funders: continue
                        to_remove_sol.append(w)
                except: pass
            for w in EVM_WALLETS[:]:
                la_str=last_active.get(w.lower())
                if not la_str: continue
                try:
                    la=datetime.fromisoformat(la_str)
                    if (now-la).days > 30:
                        if w.lower() in [x.lower() for x in known_splitters]: continue
                        if w.lower() in [x.lower() for x in known_funders]: continue
                        to_remove_evm.append(w)
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
                send_tg(f"Auto-cleaned {removed} dead wallets >30d inactive\nSOL: {len(SOL_WALLETS)} EVM: {len(EVM_WALLETS)} remaining - Funder/Splitter kept safe")
            else:
                save_last_active()
        except Exception as e: print(f"prune err {e}", flush=True)

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com","https://rpc.ankr.com/solana"]
rpc_idx=0
def get_public_rpc():
    global rpc_idx, last_rpc_call
    now=time.time()
    if now-last_rpc_call < 0.6: time.sleep(0.6-(now-last_rpc_call))
    last_rpc_call=time.time()
    url=SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]; rpc_idx+=1
    return url

RPCS_FALLBACK={
    "ETH":["https://ethereum-rpc.publicnode.com","https://eth.llamarpc.com"],
    "BSC":["https://bsc-rpc.publicnode.com","https://bsc.llamarpc.com"],
    "BASE":["https://base-rpc.publicnode.com","https://base.llamarpc.com"],
    "ARB":["https://arbitrum-one-rpc.publicnode.com","https://arbitrum.llamarpc.com"],
    "POLY":["https://polygon-bor-rpc.publicnode.com","https://polygon.llamarpc.com"]
}
def get_w3_with_fallback(chain):
    for url in RPCS_FALLBACK.get(chain,[]):
        try:
            w3=Web3(Web3.HTTPProvider(url,request_kwargs={'timeout':6}))
            if w3.is_connected(): return w3
        except: continue
    return None

BOT_TOKEN=os.getenv("BOT_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")
app=Flask(__name__)
@app.route('/')
def home(): return "Shok V3.18 FIXED PRUNE 30D",200
@app.route('/health')
def health(): return "OK",200
@app.route('/debug')
def debug(): return f"V3.18 FIX EVM:{len(EVM_WALLETS)} SOL:{len(SOL_WALLETS)} Spl:{len(known_splitters)} New:{len(new_children)} Active:{len(last_active)} Last:{int((datetime.now()-last_tx_time).total_seconds()/60)}m",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))

def send_tg_worker():
    while True:
        try:
            if tg_queue:
                text=tg_queue.popleft()
                if not BOT_TOKEN or not CHAT_ID:
                    print(text[:500], flush=True)
                else:
                    # Try Markdown first, fallback to plain if Telegram rejects it
                    try:
                        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True},timeout=15)
                        if r.status_code!= 200 or not r.json().get("ok"):
                            # fallback plain
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15)
                    except:
                        try:
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True},timeout=15)
                        except: pass
                time.sleep(1.2)
            else: time.sleep(0.5)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker, daemon=True).start()

def send_tg(text):
    if len(tg_queue)>30: tg_queue.clear()
    tg_queue.append(text)

def short(a):
    try: return f"{a[:4]}...{a[-4:]}" if a and len(a)>8 else a
    except: return "?"

def get_token_info_quick(token_address, chain_hint="SOL"):
    if token_address in STABLES: return None
    for attempt in range(2):
        try:
            r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=5).json()
            pairs=r.get("pairs",[])
            if not pairs: continue
            valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0) >= 500]
            if not valid:
                if attempt==0: time.sleep(1.5); continue
                else: return None
            best=sorted(valid, key=lambda x: x.get("liquidity",{}).get("usd",0), reverse=True)[0]
            liq=best.get("liquidity",{}).get("usd",0); name=best.get("baseToken",{}).get("symbol", token_address[:6])
            fdv=best.get("fdv") or best.get("marketCap") or 0
            mcap=f"${fdv/1_000_000:.2f}M" if fdv>=1_000_000 else f"${fdv/1000:.1f}k" if fdv>=1000 else f"${fdv:.0f}" if fdv else "New"
            price=float(best.get("priceUsd",0) or 0)
            if price==0: return None
            dex_link=f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}"
            return name, mcap, dex_link, price, best.get('chainId','SOL').upper(), liq, best.get('pairAddress',token_address)
        except:
            if attempt==0: time.sleep(1)
            continue
    return None

def get_sol_transfers_cached(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<300:
        return funder_cache[sig][1]
    transfers=[]
    try:
        rpc_url=get_public_rpc()
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed", "maxSupportedTransactionVersion":0}]}
        r=requests.post(rpc_url, json=payload, timeout=12)
        res=r.json().get("result")
        if not res: funder_cache[sig]=(datetime.now(),[]); return []
        for instr in res.get("transaction",{}).get("message",{}).get("instructions",[]):
            try:
                if instr.get("program")=="system" and instr.get("parsed",{}).get("type")=="transfer":
                    info=instr["parsed"]["info"]; lamports=info.get("lamports",0)
                    transfers.append((info["source"], info["destination"], lamports/1e9, sig))
            except: continue
    except: pass
    funder_cache[sig]=(datetime.now(),transfers)
    if len(funder_cache)>500: funder_cache.clear()
    return transfers

def analyze_wallet_bundle(wallet):
    wallet=wallet.strip()
    send_tg(f"Scanning SOL wallet {short(wallet)}...")
    try:
        rpc_url=get_public_rpc()
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet, {"limit":15}]}
        r=requests.post(rpc_url, json=payload, timeout=12).json()
        sigs=r.get("result",[]); funders=[]
        for item in sigs[:12]:
            sig=item.get("signature")
            if not sig: continue
            for frm,to,amt,_ in get_sol_transfers_cached(sig):
                if to==wallet and amt>=0.05:
                    if frm not in [f[0] for f in funders]: funders.append((frm, amt, sig))
        if not funders:
            send_tg(f"No funder found for {short(wallet)}"); return
        main_funder, amt, _ = funders[0]; siblings=[]; children=[]
        payload2={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[main_funder, {"limit":20}]}
        r2=requests.post(get_public_rpc(), json=payload2, timeout=12).json()
        for item in r2.get("result",[])[:15]:
            s=item.get("signature")
            if not s: continue
            for frm,to,amt2,_ in get_sol_transfers_cached(s):
                if frm==main_funder and amt2>=0.05 and to!=wallet:
                    if to not in [x[0] for x in siblings]: siblings.append((to, amt2))
        for item in sigs[:10]:
            s=item.get("signature")
            if not s: continue
            for frm,to,amt3,_ in get_sol_transfers_cached(s):
                if frm==wallet and amt3>=0.05:
                    if to not in [x[0] for x in children]: children.append((to, amt3))
        grandparent=None
        try:
            payload3={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[main_funder, {"limit":10}]}
            r3=requests.post(get_public_rpc(), json=payload3, timeout=12).json()
            for item in r3.get("result",[])[:8]:
                s=item.get("signature")
                if not s: continue
                for frm,to,amt4,_ in get_sol_transfers_cached(s):
                    if to==main_funder and amt4>=0.2: grandparent=(frm, amt4); break
                if grandparent: break
        except: pass
        msg=f"📦 BUNDLE TREE SOL for {short(wallet)}\n\n"
        if grandparent: msg+=f"FUNDER: {short(grandparent[0])} {grandparent[1]:.2f} SOL\n{grandparent[0]}\n\n"
        msg+=f"SPLITTER: {short(main_funder)} -> you {amt:.3f} SOL\n{main_funder}\n\n YOU: {short(wallet)}\n"
        if siblings: msg+=f" SIBLINGS ({len(siblings)}):\n" + "".join([f" - {short(s)} {a:.2f} SOL\n" for s,a in siblings[:5]])
        if children: msg+=f" CHILDREN ({len(children)}):\n" + "".join([f" - {short(c)} {a:.2f} SOL\n" for c,a in children[:5]])
        msg+=f"\nSolscan: https://solscan.io/account/{main_funder}"
        added=0; to_add=[]
        if main_funder not in SOL_WALLETS: to_add.append(main_funder)
        if grandparent and grandparent[0] not in SOL_WALLETS: to_add.append(grandparent[0])
        for sib,_ in siblings[:5]:
            if sib not in SOL_WALLETS and sib not in to_add: to_add.append(sib)
        for ch,_ in children[:5]:
            if ch not in SOL_WALLETS and ch not in to_add: to_add.append(ch)
        to_add=to_add[:10]
        for addr in to_add:
            if len(SOL_WALLETS)>=120: break
            SOL_WALLETS.append(addr); added+=1; known_splitters.add(main_funder)
            if grandparent: known_funders.add(grandparent[0])
            if addr!=main_funder and (not grandparent or addr!=grandparent[0]): new_children.add(addr.lower()); mark_active(addr)
        if added>0: save_wallets(); save_funders(); save_new_children(); save_last_active(); msg+=f"\n\nAuto-added {added} (ALL tracked, 30d prune active)"
        send_tg(msg[:4000])
    except Exception as e: send_tg(f"Bundle error: {e}")

EVM_NATIVE_MIN = {"ETH":0.02, "BSC":0.05, "BASE":0.02, "ARB":0.02, "POLY":5.0}
def get_evm_funder(chain, wallet):
    try:
        w3=get_w3_with_fallback(chain)
        if not w3: return None
        wallet_chk=Web3.to_checksum_address(wallet); latest=w3.eth.block_number
        for bn in range(latest, max(latest-4000,0), -1):
            try:
                block=w3.eth.get_block(bn, full_transactions=True)
                for tx in block.transactions:
                    to_addr=tx.get('to')
                    if not to_addr: continue
                    if to_addr.lower()==wallet_chk.lower() and int(tx.get('value',0))>0:
                        val=float(w3.from_wei(tx['value'],'ether'))
                        if val >= EVM_NATIVE_MIN.get(chain,0.02): return tx.get('from'), val
            except: continue
    except: pass
    return None

def analyze_evm_bundle(chain, wallet):
    send_tg(f"Scanning EVM {chain} wallet {short(wallet)}...")
    try:
        funder=get_evm_funder(chain, wallet)
        if not funder: send_tg(f"No EVM funder found for {short(wallet)} on {chain}"); return
        funder_addr, amt = funder; w3=get_w3_with_fallback(chain); siblings=[]
        try:
            latest=w3.eth.block_number
            for bn in range(latest, max(latest-2500,0), -1):
                block=w3.eth.get_block(bn, full_transactions=True)
                for tx in block.transactions:
                    if tx.get('from','').lower()==funder_addr.lower() and int(tx.get('value',0))>0:
                        to_addr=tx.get('to','')
                        if to_addr.lower()!=wallet.lower():
                            val=float(w3.from_wei(tx['value'],'ether'))
                            if val >= EVM_NATIVE_MIN.get(chain,0.02):
                                if to_addr.lower() not in [s[0].lower() for s in siblings]: siblings.append((to_addr, val))
                if len(siblings)>=10: break
        except: pass
        msg=f"📦 BUNDLE TREE {chain} for {short(wallet)}\n\nFUNDER: {short(funder_addr)} {amt:.4f}\n{funder_addr}\n\n YOU: {short(wallet)}\n"
        if siblings: msg+=f" SIBLINGS ({len(siblings)}):\n" + "".join([f" - {short(s)} {a:.4f}\n" for s,a in siblings[:5]])
        added=0; to_add=[]
        if funder_addr.lower() not in [x.lower() for x in EVM_WALLETS]: to_add.append(funder_addr)
        for sib,_ in siblings[:5]:
            if sib.lower() not in [x.lower() for x in EVM_WALLETS]: to_add.append(sib)
        to_add=to_add[:8]
        for addr in to_add:
            if len(EVM_WALLETS)>=100: break
            try: EVM_WALLETS.append(Web3.to_checksum_address(addr))
            except: EVM_WALLETS.append(addr)
            added+=1; new_children.add(addr.lower()); known_splitters.add(funder_addr); mark_active(addr)
        if added>0: save_wallets(); save_funders(); save_new_children(); save_last_active(); msg+=f"\n\nAuto-added {added} EVM wallets (30d prune active)"
        send_tg(msg[:4000])
    except Exception as e: send_tg(f"EVM bundle error: {e}")

def check_cluster_1d(mint, name, mcap, dex_link, chain):
    now=datetime.now(); events=cluster_memory.get(mint,[]); recent=[e for e in events if (now-e[1]) <= timedelta(days=1)]
    uniq_map={}; [uniq_map.__setitem__(w.lower(),w) for w,_,_ in recent]
    uniq_wallets=list(uniq_map.values())
    if len(uniq_wallets) < 2: return
    last=cluster_alerted.get(mint.lower())
    if last and (now-last) <= timedelta(hours=12): return
    cluster_alerted[mint.lower()]=now
    wallets_str="\n".join([f"- {short(w)}" for w in uniq_wallets[:6]])
    bubble=f"https://bubblemaps.io/sol/token/{mint}" if chain=="SOL" else f"https://bubblemaps.io/{chain.lower()}/token/{mint}"
    send_tg(f"CLUSTER BUY 1D\n\n{name} ({mcap}) {chain}\n{len(uniq_wallets)} wallets:\n{wallets_str}\n\nChart: {dex_link}\nBubble: {bubble}")

def get_tx_parsed_public(sig):
    for _ in range(2):
        rpc_url=get_public_rpc()
        try:
            payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed", "maxSupportedTransactionVersion":0}]}
            r=requests.post(rpc_url, json=payload, timeout=10)
            if r.status_code!=200: continue
            res=r.json().get("result")
            if not res: continue
            meta=res.get("meta",{}); pre_balances=meta.get("preTokenBalances",[]); post_balances=meta.get("postTokenBalances",[])
            bal_map={}
            for b in pre_balances: bal_map[(b.get("owner",""), b.get("mint",""))]=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            transfers=[]
            for b in post_balances:
                owner=b.get("owner",""); mint=b.get("mint","")
                if mint in STABLES: continue
                post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0); pre_amt=bal_map.get((owner,mint),0); diff=post_amt-pre_amt
                if abs(diff) < 0.000001: continue
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
        if tr["owner"] in wallet_list and tr["amount"] > 0.0001: best=tr; break
    if not best: return
    mint=best["mint"]; amt=best["amount"]; owner=best["owner"]; is_buy=best["is_buy"]
    if mint in STABLES: return
    info=get_token_info_quick(mint,"SOL")
    if not info: return
    name,mcap,dex_link,price,_,liq,pair = info
    usd=amt*price if price>0 else 0
    if usd < 10: return
    seen_sigs.add(sig)
    if len(seen_sigs)>800: seen_sigs.clear()
    last_tx_time=datetime.now(); mark_active(owner)
    photon_link=f"https://photon-sol.tinyastro.io/en/lp/{mint}"; bubble_link=f"https://bubblemaps.io/sol/token/{mint}"
    is_new_child = owner.lower() in new_children
    if is_buy:
        prev=holdings.get(mint,{}).get(owner, {"token_amount":0,"cost_usd":0})
        new_amount=prev.get("token_amount",0)+amt; new_cost=prev.get("cost_usd",0)+usd
        holdings[mint][owner]={"token_amount":new_amount,"cost_usd":new_cost,"avg_price":new_cost/new_amount if new_amount>0 else price,"chain":"SOL","token_name":name,"mcap":mcap}
        save_holdings(); pnl_tracker[owner.lower()]["buys"]+=1; pnl_tracker[owner.lower()]["spent"]+=usd; save_pnl()
        cluster_memory[mint].append((owner,datetime.now(),"SOL")); save_memory()
        if is_new_child:
            send_tg(f"NEW CHILD FIRST BUY!\n\n{name} ({mcap}) Liq ${liq:,.0f}\n{short(owner)} fresh wallet!\nFirst buy ${usd:,.2f} | {amt:,.0f} tokens\nEarly! Dex: {dex_link}\nTx: https://solscan.io/tx/{sig}")
        else:
            send_tg(f"SOL BUY $10+\n{name} ({mcap})\n{short(owner)} | ${usd:,.2f}\nDex: {dex_link} | Bubble: {bubble_link} | Tx: https://solscan.io/tx/{sig}")
        threading.Thread(target=lambda: check_cluster_1d(mint,name,mcap,dex_link,"SOL"),daemon=True).start()
    else:
        prev=holdings.get(mint,{}).get(owner)
        if prev:
            held_amt=prev.get("token_amount",0); cost_basis=prev.get("cost_usd",0); avg_price=prev.get("avg_price",price)
            pct_sold=(amt/held_amt*100) if held_amt>0 else 100; cost_of_sale=cost_basis*(amt/held_amt) if held_amt>0 else 0
            pnl_usd=usd-cost_of_sale; pnl_pct=((price-avg_price)/avg_price*100) if avg_price>0 else 0
            remaining=held_amt-amt
            if remaining <= 0.0001: holdings[mint].pop(owner,None)
            else: holdings[mint][owner]["token_amount"]=remaining; holdings[mint][owner]["cost_usd"]=cost_basis-cost_of_sale
            save_holdings()
            emoji="🟢" if pnl_usd>=0 else "🔴"
            send_tg(f"SOL SELL {emoji} $10+\n{name} ({mcap})\n{short(owner)} sold {pct_sold:.0f}% | ${usd:,.2f} PnL {pnl_pct:+.1f}%\nDex: {dex_link}")
        else:
            send_tg(f"SOL SELL $10+\n{name} ({mcap}) ${usd:,.2f}\nDex: {dex_link}")
        pnl_tracker[owner.lower()]["sells"]+=1; pnl_tracker[owner.lower()]["realized"]+=usd; save_pnl()

async def track_sol_polling():
    print("SOL V3.18 FIXED CLEAN $10+", flush=True)
    while True:
        for w in SOL_WALLETS[-60:]:
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
            except Exception as e: print(f"sol poll err {e}", flush=True)
        await asyncio.sleep(6)

def get_token_decimals(w3, token_addr):
    try:
        abi='[{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]'
        contract=w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=json.loads(abi))
        return contract.functions.decimals().call()
    except: return 18

async def track_chain(chain):
    global last_tx_time
    seen=set(); scan_map={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}
    scan=scan_map[chain]
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
                                if usd_val < 10: continue
                                is_sell = from_addr.lower()==frm.lower(); is_buy = to_addr.lower()==frm.lower()
                                if not (is_sell or is_buy): continue
                                last_tx_time=datetime.now(); mark_active(frm)
                                bubble_link=f"https://bubblemaps.io/{chain.lower()}/token/{token_contract}"
                                is_new = frm.lower() in new_children
                                if is_sell:
                                    send_tg(f"{chain} SELL $10+\n{name} ({mcap}) ${usd_val:,.2f}\nChart: {dex_link} | Tx: https://{scan}/tx/{h}")
                                else:
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    if is_new:
                                        send_tg(f"NEW EVM CHILD FIRST BUY {chain}!\n{name} ({mcap}) ${usd_val:,.2f}\n{short(frm)} fresh first buy! Early!\nChart: {dex_link} | Tx: https://{scan}/tx/{h}")
                                    else:
                                        send_tg(f"{chain} BUY $10+\n{name} ({mcap}) ${usd_val:,.2f}\nChart: {dex_link} | Tx: https://{scan}/tx/{h}")
                                    threading.Thread(target=lambda: check_cluster_1d(token_contract,name,mcap,dex_link,chain),daemon=True).start()
                        except: pass
                except: pass
            await asyncio.sleep(5)
        except Exception as e: print(f"[{chain}] err {e}", flush=True); await asyncio.sleep(5)

def track_funders_polling():
    print("Funder watcher SAFE 45s", flush=True)
    while True:
        time.sleep(45)
        try:
            targets=list(known_splitters)[-12:] + list(known_funders)[-8:]
            for funder in targets:
                try:
                    if len(SOL_WALLETS)>=120: break
                    rpc_url=get_public_rpc()
                    payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[funder, {"limit":8}]}
                    r=requests.post(rpc_url, json=payload, timeout=12).json()
                    for item in r.get("result",[])[:6]:
                        sig=item.get("signature")
                        if not sig: continue
                        for frm,to,amt,_ in get_sol_transfers_cached(sig):
                            if frm==funder and amt>=0.1 and to not in SOL_WALLETS:
                                SOL_WALLETS.append(to); new_children.add(to.lower()); mark_active(to); save_wallets(); save_new_children(); save_last_active()
                                send_tg(f"NEW CHILD FUNDED!\nSplitter {short(funder)} funded:\n{to} {amt:.2f} SOL\nAuto-added (Total {len(SOL_WALLETS)})\nWatching FIRST BUY...\nhttps://solscan.io/account/{to}")
                                time.sleep(1)
                except: continue
        except: pass
        time.sleep(15)
        try:
            evm_targets=list(known_splitters)[-8:]
            for funder in evm_targets:
                if not funder.startswith("0x"): continue
                for chain in ["BASE","BSC","ETH"]:
                    try:
                        if len(EVM_WALLETS)>=100: break
                        w3=get_w3_with_fallback(chain)
                        if not w3: continue
                        block=w3.eth.get_block(w3.eth.block_number, full_transactions=True)
                        for tx in block.transactions:
                            if tx.get('from','').lower()==funder.lower() and int(tx.get('value',0))>0:
                                to_addr=tx.get('to','')
                                if to_addr and to_addr.lower() not in [x.lower() for x in EVM_WALLETS]:
                                    val=float(w3.from_wei(tx['value'],'ether'))
                                    if val >= EVM_NATIVE_MIN.get(chain,0.02):
                                        try: EVM_WALLETS.append(Web3.to_checksum_address(to_addr))
                                        except: EVM_WALLETS.append(to_addr)
                                        new_children.add(to_addr.lower()); mark_active(to_addr); save_wallets(); save_new_children(); save_last_active()
                                        send_tg(f"NEW EVM CHILD FUNDED {chain}!\nFunder {short(funder)} -> {short(to_addr)} {val:.4f}\nAuto-added EVM (Total {len(EVM_WALLETS)})")
                    except: continue
        except: pass

def handle_command(text):
    # Robust parsing - handles /start, /start@botname, /START etc
    t=text.strip()
    if not t.startswith("/"): return
    # remove @botname
    cmd_part = t.split()[0].lower().split('@')[0] # e.g. /start@shoks_bot -> /start
    low = cmd_part
    args = t.split()

    if low == "/start":
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        msg = f"V3.18 FIXED PRUNE 30D LIVE\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n$10+ CLEAN\nFunder->Splitter->Children ALL tracked\nVIP NEW CHILD FIRST BUY\nAuto-prune dead >30d (Funder/Splitter kept)\nLast tx: {mins}m ago\nSplitters: {len(known_splitters)} Funders: {len(known_funders)} New: {len(new_children)} Active: {len(last_active)}\n\nCommands:\n/bundle <addr> [chain]\n/track_funders\n/listwallets /listevm\n/addsol /addevm\n/removesol /removeevm\n/holdings /clusters /pnl\n/testalert /help"
        send_tg(msg)
    elif low == "/bundle":
        if len(args)<2: send_tg("Usage: /bundle <addr> [chain]\nEx: /bundle 2Yso... or /bundle 0xfd87... BASE")
        else:
            addr=args[1].strip(); chain=args[2].upper() if len(args)>2 else ("SOL" if not addr.startswith("0x") else "BASE")
            if addr.startswith("0x"): threading.Thread(target=analyze_evm_bundle, args=(chain, addr), daemon=True).start()
            else: threading.Thread(target=analyze_wallet_bundle, args=(addr,), daemon=True).start()
    elif low == "/track_funders":
        msg=f"Splitters ({len(known_splitters)})\n"
        for s in list(known_splitters)[-10:]: msg+=f"- {short(s)} {s}\n"
        msg+=f"\nFunders ({len(known_funders)})\n"
        for f in list(known_funders)[-10:]: msg+=f"- {short(f)} {f}\n"
        msg+=f"\nNew Children ({len(new_children)})\n" + "\n".join([f"- {short(n)}" for n in list(new_children)[-8:]])
        if not known_splitters: msg="No splitters yet - use /bundle <wallet> to seed"
        send_tg(msg[:4000])
    elif low == "/listwallets":
        sol="\n".join([f"{i+1}. {w}" for i,w in enumerate(SOL_WALLETS[-30:])])
        send_tg((f"Last 30 SOL ({len(SOL_WALLETS)} total):\n{sol}")[:4000])
    elif low == "/listevm":
        evm="\n".join([f"{i+1}. {w}" for i,w in enumerate(EVM_WALLETS[-25:])])
        send_tg((f"Last 25 EVM ({len(EVM_WALLETS)} total):\n{evm}")[:4000])
    elif low == "/addsol":
        if len(args)>=2:
            addr=args[1].strip()
            if addr not in SOL_WALLETS: SOL_WALLETS.append(addr); mark_active(addr); save_wallets(); save_last_active(); send_tg(f"Added SOL {short(addr)} Total {len(SOL_WALLETS)}")
            else: send_tg("Already tracked")
    elif low == "/addevm":
        if len(args)>=2:
            addr=args[1].strip()
            try: addr=Web3.to_checksum_address(addr)
            except: pass
            if addr.lower() not in [x.lower() for x in EVM_WALLETS]: EVM_WALLETS.append(addr); mark_active(addr); save_wallets(); save_last_active(); send_tg(f"Added EVM {short(addr)} Total {len(EVM_WALLETS)}")
            else: send_tg("Already tracked")
    elif low == "/removesol":
        if len(args)>=2:
            addr=args[1].strip()
            if addr in SOL_WALLETS: SOL_WALLETS.remove(addr); save_wallets(); send_tg(f"Removed SOL {short(addr)}")
            else: send_tg("Not found")
    elif low == "/removeevm":
        if len(args)>=2:
            addr=args[1].strip()
            before=len(EVM_WALLETS)
            EVM_WALLETS=[x for x in EVM_WALLETS if x.lower()!=addr.lower()]; save_wallets()
            send_tg(f"Removed EVM {short(addr)}" if len(EVM_WALLETS)<before else "Not found")
    elif low == "/pnl":
        if not pnl_tracker: send_tg("No trades yet.")
        else:
            total_spent=sum(d['spent'] for d in pnl_tracker.values()); total_real=sum(d['realized'] for d in pnl_tracker.values()); total_pnl=total_real-total_spent
            msg=f"PnL Net: ${total_pnl:,.0f}\n" + "\n".join([f"{w[:6]}... ${d['realized']-d['spent']:+.0f}" for w,d in sorted(pnl_tracker.items(), key=lambda x: x[1]['realized']-x[1]['spent'], reverse=True)[:15]])
            send_tg(msg[:4000])
    elif low == "/holdings":
        if not holdings: send_tg("No holdings.")
        else:
            msg="Holdings:\n"
            for mint,wallets in list(holdings.items())[:15]:
                for owner,data in list(wallets.items())[:2]: msg+=f"{data.get('token_name','?')} {short(owner)} {data.get('token_amount',0):.0f}\n"
            send_tg(msg[:4000])
    elif low == "/clusters":
        if not cluster_memory: send_tg("No clusters yet.")
        else:
            msg="Recent clusters:\n"
            for mint,ev in list(cluster_memory.items())[-10:]: msg+=f"{mint[:8]}... {len(ev)} buys\n"
            send_tg(msg[:4000])
    elif low == "/help":
        send_tg("Commands:\n/start status\n/bundle <addr> [chain]\n/track_funders\n/listwallets /listevm\n/addsol <addr>\n/addevm <addr>\n/removesol <addr>\n/removeevm <addr>\n/holdings\n/clusters\n/pnl\n/testalert")
    elif low == "/testalert":
        send_tg(f"V3.18 FIXED WORKING\n{len(SOL_WALLETS)} SOL {len(EVM_WALLETS)} EVM\nPrune 30d active\nBuy/sell $10+ active\nCluster 1D 12h active")

def set_bot_commands():
    cmds=[
        {"command":"start","description":"Status V3.18 FIXED"},
        {"command":"bundle","description":"Bundle tree Funder->Splitter"},
        {"command":"track_funders","description":"List funders/splitters"},
        {"command":"listwallets","description":"List SOL wallets"},
        {"command":"listevm","description":"List EVM wallets"},
        {"command":"addsol","description":"Add SOL wallet"},
        {"command":"addevm","description":"Add EVM wallet"},
        {"command":"removesol","description":"Remove SOL wallet"},
        {"command":"removeevm","description":"Remove EVM wallet"},
        {"command":"holdings","description":"Holdings history"},
        {"command":"clusters","description":"Cluster history"},
        {"command":"pnl","description":"PnL history"},
        {"command":"testalert","description":"Test bot"},
        {"command":"help","description":"All commands"}
    ]
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",json={"commands":cmds},timeout=10)
    except: pass

def heartbeat():
    while True:
        time.sleep(120)
        try:
            port=int(os.getenv("PORT",10000))
            try: requests.get(f"http://127.0.0.1:{port}/health",timeout=5)
            except: pass
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            print(f"HEARTBEAT V3.18 FIX {mins}m q:{len(tg_queue)} SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} active:{len(last_active)}", flush=True)
        except: pass

async def main_loop():
    print(f">>> V3.18 FIXED PRUNE {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM", flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=track_funders_polling, daemon=True).start()
    threading.Thread(target=prune_inactive, daemon=True).start()
    send_tg(f"V3.18 FIXED DEPLOYED - PRUNE 30D\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n$10+ CLEAN\nFunder->Splitter->Children ALL tracked\nVIP NEW CHILD FIRST BUY\nAuto-delete dead >30d (Funder/Splitter safe)\n/start now fixed"); set_bot_commands()
    tasks=[track_chain(c) for c in RPCS_FALLBACK.keys()]
    tasks.append(track_sol_polling())
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
                        print(f"CMD RECEIVED: {txt}", flush=True)
                        handle_command(txt)
            except Exception as e:
                print(f"poll err {e}", flush=True)
                time.sleep(4)
    threading.Thread(target=poll_cmd,daemon=True).start()
    time.sleep(1); start_bot()
