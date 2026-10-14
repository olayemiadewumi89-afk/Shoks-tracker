import os, asyncio, threading, time, requests, json, html
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter

WALLETS_FILE="wallets.json"; GROUPS_FILE="groups.json"; LABELS_FILE="labels.json"; SEEN_FILE="seen.json"; TRADES_FILE="trades.json"
SOL_WALLETS=[]; GROUPS={}; LABELS={}; seen_sigs=set()
tg_queue=deque(); funder_cache={}; last_tx_time=datetime.now(); last_bundled=None; last_activity={}; pending_buys={}; TRADES={}
bundle_start_time={} # funder -> time when bundle started

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com","https://rpc.ankr.com/solana","https://solana.meowrpc.com"]
rpc_idx=0; last_rpc_call=0
def get_sol_rpc():
    global rpc_idx,last_rpc_call
    now=time.time()
    if now-last_rpc_call<0.35: time.sleep(0.35-(now-last_rpc_call))
    last_rpc_call=time.time(); url=SOLANA_PUB_RPCS[rpc_idx%len(SOLANA_PUB_RPCS)]; rpc_idx+=1; return url

BOT_TOKEN=os.getenv("BOT_TOKEN"); CHAT_ID=os.getenv("CHAT_ID")

def auto_set_commands():
    try:
        if not BOT_TOKEN: return
        cmds=[
            {"command":"start","description":"Start bot"},
            {"command":"bundle","description":"Bundle - 1/2/5m prep only"},
            {"command":"labelgroup","description":"Label group"},
            {"command":"listgroups","description":"List groups"},
            {"command":"listwallets","description":"List wallets"},
            {"command":"pnl","description":"Check PnL"},
            {"command":"status","description":"Status"},
            {"command":"clear_all","description":"Clear all"},
            {"command":"debug_buy","description":"Debug buy for wallet"},
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
    except: pass

app=Flask(__name__)
@app.route('/')
def home(): return "Shoks V15 FORWARD 1-2-5m",200
@app.route('/health')
def health(): return "OK",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))

def send_tg_worker():
    while True:
        try:
            if tg_queue:
                text=tg_queue.popleft()
                if not BOT_TOKEN or not CHAT_ID: print(text[:800],flush=True)
                else:
                    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":CHAT_ID,"text":text,"disable_web_page_preview":True,"parse_mode":"HTML"},timeout=15)
                    except: pass
                time.sleep(0.5)
            else: time.sleep(0.2)
        except: time.sleep(1)
threading.Thread(target=send_tg_worker,daemon=True).start()
def send_tg(text):
    if len(tg_queue)>250: tg_queue.popleft()
    tg_queue.append(text)

def short(a): return f"{a[:4]}..{a[-4:]}" if len(a)>8 else a
def is_evm(addr): return addr.startswith("0x") and len(addr)==42
def detect_chain(addr): return "evm" if is_evm(addr) else "sol"
def wallet_exists(addr): return any((w["addr"] if isinstance(w, dict) else w)==addr for w in SOL_WALLETS)
def display_group(wallet_or_group):
    if not wallet_or_group: return "?"
    gid=GROUPS.get(wallet_or_group,wallet_or_group)
    return LABELS.get(gid,LABELS.get(wallet_or_group,short(gid)))

def load_all():
    global SOL_WALLETS,GROUPS,LABELS,seen_sigs,TRADES
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE)); raw=d.get("sol",[])
            if raw and len(raw)>0 and isinstance(raw[0],str): SOL_WALLETS=[{"addr":x,"chain":"sol","group":d.get("groups",{}).get(x,x)} for x in raw]
            else: SOL_WALLETS=raw
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
        if os.path.exists(TRADES_FILE): TRADES=json.load(open(TRADES_FILE))
    except: pass
load_all()
def save_all():
    try:
        json.dump({"sol":SOL_WALLETS,"groups":GROUPS},open(WALLETS_FILE,"w"))
        json.dump(GROUPS,open(GROUPS_FILE,"w")); json.dump(LABELS,open(LABELS_FILE,"w"))
        json.dump(TRADES,open(TRADES_FILE,"w"))
    except: pass
def save_seen():
    try: json.dump(list(seen_sigs)[-4000:],open(SEEN_FILE,"w"))
    except: pass

BLACKLIST_MINTS=["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"]

def get_token_info(token_address, chain="sol"):
    if token_address in BLACKLIST_MINTS: return None
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=10).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=1]
        if not valid: valid=pairs
        best=sorted(valid,key=lambda x:x.get("liquidity",{}).get("usd",0) or 0,reverse=True)[0]
        fdv=best.get("fdv") or best.get("marketCap") or 0
        if fdv>=1_000_000: mcap_str=f"${fdv/1_000_000:.2f}M"
        elif fdv>=1000: mcap_str=f"${fdv/1000:.0f}k"
        else: mcap_str=f"${fdv:.0f}"
        price=float(best.get("priceUsd",0) or 0)
        if price==0:
            try: price=float(best.get("priceUsd","0") or 0)
            except: price=0.00000001
        return {"name":best.get("baseToken",{}).get("symbol",token_address[:6]),"mcap_str":mcap_str,"mcap_val":fdv,"price":price if price>0 else 0.00000001,"mint":token_address,"chain":chain}
    except Exception as e:
        print(f"dex err {e}",flush=True)
        return None

def record_buy(wallet, mint, amount, price, mcap_val, chain):
    if wallet not in TRADES: TRADES[wallet]={}
    TRADES[wallet][mint]={"amount":amount,"price":price,"mcap_at_buy":mcap_val,"chain":chain,"time":time.time()}
    save_all()

def get_sol_holdings(wallet):
    holdings=[]
    # Try 2 RPCs for reliability
    for _ in range(2):
        try:
            payload={"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":[wallet,{"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},{"encoding":"jsonParsed"}]}
            res=requests.post(get_sol_rpc(),json=payload,timeout=12).json().get("result",{}).get("value",[])
            for acc in res:
                try:
                    info=acc["account"]["data"]["parsed"]["info"]
                    mint=info["mint"]; amt=float(info["tokenAmount"]["uiAmount"] or 0)
                    if amt>0 and mint not in BLACKLIST_MINTS: holdings.append((mint,amt))
                except: continue
            if holdings: break
        except: time.sleep(0.5)
    return holdings

def get_evm_holdings(wallet):
    holdings=[]
    try:
        url=f"https://base.blockscout.com/api?module=account&action=tokenlist&address={wallet}"
        r=requests.get(url,timeout=10).json()
        for t in r.get("result",[]):
            try:
                if t.get("type")=="ERC-20":
                    amt=float(t.get("balance",0))/10**int(t.get("decimals",18))
                    if amt>0: holdings.append((t["contractAddress"],amt))
            except: continue
    except: pass
    return holdings

def get_signatures(wallet,limit=10):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":limit}]}
        return requests.post(get_sol_rpc(),json=payload,timeout=12).json().get("result",[]) or []
    except: return []

def get_tx_details(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<600: return funder_cache[sig][1]
    sol_transfers=[]; token_transfers=[]
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        res=requests.post(get_sol_rpc(),json=payload,timeout=14).json().get("result")
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
        post_list=res.get("meta",{}).get("postTokenBalances",[]) or []
        for b in post_list:
            owner=b.get("owner",""); mint=b.get("mint","")
            if mint in BLACKLIST_MINTS: continue
            post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            pre_amt=pre.get((owner,mint),0)
            if pre_amt==0:
                for (p_owner,p_mint),p_amt in pre.items():
                    if p_mint==mint: pre_amt=p_amt; break
            diff=post_amt-pre_amt
            if abs(diff)>0.0000001: token_transfers.append(("unknown",owner,mint,abs(diff),diff>0))
    except: pass
    funder_cache[sig]=(datetime.now(),(sol_transfers,token_transfers))
    if len(funder_cache)>800: funder_cache.clear()
    return sol_transfers,token_transfers

def get_evm_txs(wallet, chain="base", limit=10):
    try:
        url=f"https://base.blockscout.com/api?module=account&action=txlist&address={wallet}&sort=desc&page=1&offset={limit}"
        r=requests.get(url, timeout=10).json()
        if r.get("result"): return r["result"]
        return []
    except: return []

def fmt_links(mint,sig=None,wallet=None,chain="sol"):
    if chain=="sol":
        ds=f"https://dexscreener.com/solana/{mint}"
        photon=f"https://photon-sol.tinyastro.io/en/lp/{mint}"
        out=f'<a href="{ds}">DS</a> | <a href="{photon}">Photon</a>'
    else:
        ds=f"https://dexscreener.com/base/{mint}"
        out=f'<a href="{ds}">DS</a>'
    if sig and sig!="holdings":
        out+=f' | <a href="https://solscan.io/tx/{sig}">TX</a>' if chain=="sol" else f' | <a href="https://basescan.org/tx/{sig}">TX</a>'
    if wallet:
        out+=f' | <a href="https://solscan.io/account/{wallet}">Wallet</a>' if chain=="sol" else f' | <a href="https://basescan.org/address/{wallet}">Wallet</a>'
    return out

def handle_bundle(wallet):
    global last_bundled
    wallet=wallet.strip(); chain=detect_chain(wallet); evm=is_evm(wallet)
    if not evm and len(wallet)<30: send_tg("❌ Invalid wallet"); return
    send_tg(f"🔍 <b>Bundling {short(wallet)}</b> ⛓️ {chain.upper()} - Tracking 1m/2m/5m then FORWARD")
    try:
        funders=[]
        if not evm:
            sigs=get_signatures(wallet,15)
            for item in sigs[:12]:
                sig=item.get("signature")
                if not sig: continue
                sols,_=get_tx_details(sig)
                for frm,to,amt in sols:
                    if to==wallet and amt>=0.001 and frm not in [f[0] for f in funders]: funders.append((frm,amt,sig))
        else:
            txs=get_evm_txs(wallet, chain, 15)
            for tx in txs[:10]:
                try:
                    if tx["to"].lower()==wallet.lower() and float(tx["value"])/1e18 >=0.00003:
                        frm=tx["from"]
                        if frm not in [f[0] for f in funders]: funders.append((frm,float(tx["value"])/1e18,tx["hash"]))
                except: continue
        if not funders:
            if not wallet_exists(wallet): SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":wallet})
            GROUPS[wallet]=wallet; LABELS[wallet]=f"CEX_{short(wallet).upper()}"
            last_bundled=wallet; save_all(); last_activity[wallet]=datetime.now()
            bundle_start_time[wallet]=datetime.now()
            send_tg(f"⚠️ No parent (CEX)\n<code>{short(wallet)}</code> - Tracking forward for buys"); return

        main_funder=funders[0][0]; last_bundled=main_funder
        bundle_start_time[main_funder]=datetime.now()
        GROUPS[wallet]=main_funder; GROUPS[main_funder]=main_funder
        if not wallet_exists(main_funder): SOL_WALLETS.append({"addr":main_funder,"chain":chain,"group":main_funder})
        if not wallet_exists(wallet): SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":main_funder})
        last_activity[wallet]=datetime.now(); last_activity[main_funder]=datetime.now()

        # FIND CHILDREN ONLY FROM LAST 5 MIN - NOT 15
        added=0
        children_1m=[]; children_2m=[]; children_5m=[]
        if not evm:
            sib_sigs=get_signatures(main_funder,30); now_ts=time.time()
            for item in sib_sigs[:20]:
                bt=item.get("blockTime",0)
                if not bt: continue
                age_sec=now_ts-bt
                if age_sec>300: continue # ONLY 5 MIN
                sig=item.get("signature")
                if not sig: continue
                sols,_=get_tx_details(sig)
                for frm,to,amt in sols:
                    if frm==main_funder and to!=wallet and not wallet_exists(to) and amt>=0.001 and added<40:
                        SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                        pending_buys[to]=(main_funder,datetime.now(),display_group(main_funder))
                        last_activity[to]=datetime.now()
                        if age_sec<=60: children_1m.append(to)
                        if age_sec<=120: children_2m.append(to)
                        if age_sec<=300: children_5m.append(to)
        else:
            txs=get_evm_txs(main_funder, chain, 25)
            now_ts=time.time()
            for tx in txs[:20]:
                try:
                    ts=int(tx.get("timeStamp",now_ts))
                    age_sec=now_ts-ts
                    if age_sec>300: continue
                    if tx["from"].lower()==main_funder.lower() and tx["to"].lower()!=wallet.lower() and float(tx["value"])/1e18>=0.00003 and added<30:
                        to=tx["to"]
                        if not wallet_exists(to):
                            SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                            pending_buys[to]=(main_funder,datetime.now(),display_group(main_funder))
                            if age_sec<=60: children_1m.append(to)
                            if age_sec<=120: children_2m.append(to)
                            if age_sec<=300: children_5m.append(to)
                except: continue
        save_all()
        # ONE TIME PREP - 1m/2m/5m breakdown - NO MORE SPAM AFTER
        gl=display_group(main_funder)
        msg=f"📦 <b>Bundled {short(wallet)}</b> ⛓️ {chain.upper()}\nFunder <code>{short(main_funder)}</code> G:{gl}\n\n"
        msg+=f"<b>PREP 1m: {len(children_1m)}w | 2m: {len(children_2m)}w | 5m: {len(children_5m)}w</b>\n"
        if children_5m:
            msg+=" ".join([short(c) for c in children_5m[:10]]) + "\n"
        msg+=f"\n✅ Total +{added} children tracked\n🔕 Now silent FORWARD - only BUY alerts will show"
        send_tg(msg)
    except Exception as e: send_tg(f"❌ Bundle error: {e}")

def silent_forward_tracker():
    while True:
        time.sleep(8)
        try:
            all_wallets = SOL_WALLETS[-200:]
            for wdict in all_wallets:
                funder = wdict["addr"]
                chain = wdict.get("chain","sol")
                # Stop tracking old funder after 5m for PREP, but keep forward tracking for buys
                try:
                    if chain=="sol":
                        sigs=get_signatures(funder,5)
                        for item in sigs[:3]:
                            sig=item.get("signature")
                            if not sig or sig in seen_sigs: continue
                            sols,_=get_tx_details(sig)
                            for frm,to,amt in sols:
                                if frm==funder and amt>=0.0005 and not wallet_exists(to):
                                    grp=GROUPS.get(funder,funder)
                                    SOL_WALLETS.append({"addr":to,"chain":chain,"group":grp})
                                    GROUPS[to]=grp
                                    pending_buys[to]=(grp,datetime.now(),display_group(grp))
                                    last_activity[to]=datetime.now()
                                    seen_sigs.add(sig); save_all()
                                    # NO MESSAGE - SILENT FORWARD
                                elif frm==funder:
                                    seen_sigs.add(sig)
                    else:
                        txs=get_evm_txs(funder, chain, 5)
                        for tx in txs[:3]:
                            try:
                                if tx["from"].lower()==funder.lower() and float(tx["value"])/1e18>=0.00002:
                                    if tx["hash"] in seen_sigs: continue
                                    to=tx["to"]
                                    if not wallet_exists(to):
                                        grp=GROUPS.get(funder,funder)
                                        SOL_WALLETS.append({"addr":to,"chain":chain,"group":grp})
                                        GROUPS[to]=grp
                                        pending_buys[to]=(grp,datetime.now(),display_group(grp))
                                        save_all()
                                    seen_sigs.add(tx["hash"])
                            except: continue
                except: continue
        except Exception as e: print(f"silent err {e}",flush=True)

def buy_detector_loop():
    while True:
        time.sleep(2.5)
        try:
            # Check pending buys + all active wallets
            wallets_to_check = list(pending_buys.keys())[:40] + [w["addr"] for w in SOL_WALLETS[-80:]]
            checked=set()
            for w in wallets_to_check:
                if w in checked: continue
                checked.add(w)
                chain = next((wd.get("chain","sol") for wd in SOL_WALLETS if wd["addr"]==w), "sol")
                holdings = get_sol_holdings(w) if chain=="sol" else get_evm_holdings(w)
                for mint, amt in holdings:
                    if mint in BLACKLIST_MINTS: continue
                    if w in TRADES and mint in TRADES[w]: continue
                    info=get_token_info(mint, chain)
                    if not info:
                        # Very early token - still alert with CA
                        send_tg(f"💰 <b>NEW BUY (early) {short(mint)}</b> ⛓️ {chain.upper()}\nWallet <code>{short(w)}</code> G:{display_group(w)}\nCA: <code>{mint}</code>\nAmt: {amt:.2f}\n{fmt_links(mint,chain=chain,wallet=w)}")
                        record_buy(w,mint,amt,0.00000001,0,chain)
                        pending_buys.pop(w,None)
                        continue
                    # FOUND BUY
                    record_buy(w,mint,amt,info["price"],info["mcap_val"],chain)
                    pending_buys.pop(w,None)
                    last_tx_time=datetime.now()
                    last_activity[w]=datetime.now()
                    send_tg(f"💰 <b>BOUGHT {html.escape(info['name'])} {info['mcap_str']}</b> ⛓️ {chain.upper()}\nWallet <code>{short(w)}</code> G:{display_group(w)}\nCA: <code>{mint}</code>\nAmt: {amt:.2f}\n{fmt_links(mint,chain=chain,wallet=w)}")
                    break
        except Exception as e:
            print(f"buy loop err {e}",flush=True)

async def sig_buy_tracker():
    while True:
        try:
            if last_activity:
                sorted_w=sorted(SOL_WALLETS,key=lambda wdict: last_activity.get(wdict["addr"],datetime.min),reverse=True)
                active_list=(sorted_w[:60])
            else: active_list=SOL_WALLETS[-40:]
            for wdict in active_list:
                w=wdict["addr"]; chain=wdict.get("chain","sol")
                if chain!="sol": continue
                sigs=get_signatures(w,4)
                for item in sigs[:2]:
                    sig=item.get("signature")
                    if not sig or sig in seen_sigs: continue
                    sols,tokens=get_tx_details(sig)
                    for _,owner,mint,amt,is_buy in tokens:
                        if amt>0 and is_buy and mint not in BLACKLIST_MINTS:
                            if w in TRADES and mint in TRADES[w]:
                                seen_sigs.add(sig); continue
                            info=get_token_info(mint, chain)
                            if not info: continue
                            seen_sigs.add(sig); save_seen(); last_activity[w]=datetime.now()
                            record_buy(w,mint,amt,info["price"],info["mcap_val"],chain)
                            send_tg(f"🟢 <b>BUY {html.escape(info['name'])} {info['mcap_str']}</b>\n<code>{short(w)}</code> G:{display_group(w)}\nCA: <code>{mint}</code>\n{fmt_links(mint,sig,w,chain)}")
        except Exception as e: print(f"sig err {e}",flush=True)
        await asyncio.sleep(3)

def handle_command(text):
    global last_bundled, LABELS, GROUPS, SOL_WALLETS
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if w.get("chain")=="sol"); evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"🚀 <b>V15 1-2-5m FORWARD</b> • {len(SOL_WALLETS)}w SOL:{sol_c} EVM:{evm_c} • {uniq} groups\n/bundle <addr>\nPREP shows only 1m/2m/5m once, then only BUY\n/debug_buy <wallet> to test holdings\n/listwallets\n/status")
    elif low.startswith("/debug_buy"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /debug_buy <wallet>"); return
        w=p[1].strip()
        send_tg(f"🔍 Checking holdings for <code>{short(w)}</code>...")
        def debug():
            holdings=get_sol_holdings(w)
            if not holdings:
                send_tg(f"❌ No holdings found for <code>{short(w)}</code>\nCheck RPC or wallet empty")
                return
            for mint, amt in holdings[:3]:
                info=get_token_info(mint, "sol")
                name=info["name"] if info else short(mint); mcap=info["mcap_str"] if info else "unknown"
                send_tg(f"✅ Found {name} {mcap}\nCA: <code>{mint}</code> Amt: {amt}\n{fmt_links(mint,wallet=w)}")
        threading.Thread(target=debug,daemon=True).start()
    elif low.startswith("/bundle"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /bundle <addr>")
        else: threading.Thread(target=handle_bundle, args=(p[1],), daemon=True).start()
    elif low.startswith("/labelgroup"):
        p=t.split(maxsplit=1)
        if len(p)<2: send_tg("Usage: /labelgroup VEIL"); return
        new_label=p[1].strip().upper().replace(" ","_")[:20]
        target=last_bundled
        if not target: send_tg("❌ No bundle yet. /bundle first"); return
        GROUPS[target]=target; LABELS[target]=new_label; save_all()
        send_tg(f"✅ Label <code>{short(target)}</code> -> <b>{new_label}</b>")
    elif low.startswith("/listwallets"):
        parts=t.split(maxsplit=1); filter_gid=None
        if len(parts)>1:
            fg=parts[1].strip().upper()
            for gid,lbl in LABELS.items():
                if lbl==fg: filter_gid=gid; break
            if not filter_gid: filter_gid=parts[1].strip()
        wallets=SOL_WALLETS
        if filter_gid: wallets=[w for w in SOL_WALLETS if w.get("group")==filter_gid or w.get("addr")==filter_gid]
        if not wallets: send_tg("Empty"); return
        msg=f"👛 <b>{len(wallets)} wallets forward:</b>\n"
        for wdict in wallets[-60:]:
            w=wdict["addr"]; ch=wdict.get("chain","sol").upper(); g=display_group(w)
            msg+=f"<code>{short(w)}</code> {ch} G:{g}\n"
        send_tg(msg)
    elif low.startswith("/clear_all") or low.startswith("/clear"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); pending_buys.clear(); TRADES.clear(); bundle_start_time.clear(); save_all(); send_tg("🧹 Cleared all - Ready")
    elif low.startswith("/status"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if w.get("chain")=="sol"); evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"V15 FORWARD 1/2/5m • {len(SOL_WALLETS)}w SOL:{sol_c} EVM:{evm_c} • {uniq} groups • Last buy {mins}m ago • Silent forward ON")

def poll_commands():
    off=0
    while True:
        try:
            if not BOT_TOKEN: time.sleep(5); continue
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=20",timeout=25).json()
            for u in r.get("result",[]):
                off=u["update_id"]+1; txt=u.get("message",{}).get("text","")
                if txt.startswith("/"): handle_command(txt)
        except: time.sleep(3)

async def main_loop():
    threading.Thread(target=silent_forward_tracker,daemon=True).start()
    threading.Thread(target=buy_detector_loop,daemon=True).start()
    send_tg("🚀 <b>V15 DEPLOYED</b>\n✅ PREP only 1m/2m/5m once\n✅ No 10m spam\n✅ Forward silent + BUY only")
    await sig_buy_tracker()

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e: print(f"CRASH {e}",flush=True); time.sleep(5); start_bot()

if __name__=="__main__":
    auto_set_commands()
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=poll_commands,daemon=True).start()
    time.sleep(1); start_bot()
