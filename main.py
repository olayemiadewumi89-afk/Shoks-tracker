import os, asyncio, threading, time, requests, json, html
from flask import Flask
from datetime import datetime
from collections import defaultdict, deque, Counter

WALLETS_FILE="wallets.json"; GROUPS_FILE="groups.json"; LABELS_FILE="labels.json"; SEEN_FILE="seen.json"; TRADES_FILE="trades.json"
SOL_WALLETS=[]; GROUPS={}; LABELS={}; seen_sigs=set(); recent_children=defaultdict(list)
tg_queue=deque(); funder_cache={}; last_tx_time=datetime.now(); last_bundled=None; last_activity={}; last_dist_alert={}
pending_buys={}; TRADES={}

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
            {"command":"bundle","description":"Bundle SOL or EVM"},
            {"command":"labelgroup","description":"Label bundle VEIL"},
            {"command":"listgroups","description":"List groups"},
            {"command":"listwallets","description":"List wallets"},
            {"command":"pnl","description":"Check bundle PnL SOL+EVM"},
            {"command":"status","description":"Bot status"},
            {"command":"remove","description":"Remove wallet"},
            {"command":"clear_all","description":"Clear all"},
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
    except: pass

app=Flask(__name__)
@app.route('/')
def home(): return "Shoks V13 Recursive PnL",200
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
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=8).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=10]
        if not valid: valid=pairs
        best=sorted(valid,key=lambda x:x.get("liquidity",{}).get("usd",0) or 0,reverse=True)[0]
        fdv=best.get("fdv") or best.get("marketCap") or 0
        if fdv>=1_000_000: mcap_str=f"${fdv/1_000_000:.2f}M"
        elif fdv>=1000: mcap_str=f"${fdv/1000:.0f}k"
        else: mcap_str=f"${fdv:.0f}"
        price=float(best.get("priceUsd",0) or 0)
        return {"name":best.get("baseToken",{}).get("symbol",token_address[:6]),"mcap_str":mcap_str,"mcap_val":fdv,"price":price if price>0 else 0.00000001,"mint":token_address,"chain":chain}
    except: return None

# ============ PnL SYSTEM ============
def record_buy(wallet, mint, amount, price, mcap_val, chain):
    if wallet not in TRADES: TRADES[wallet]={}
    if mint in TRADES[wallet]:
        old=TRADES[wallet][mint]
        total_amt=old["amount"]+amount
        avg_price=(old["price"]*old["amount"]+price*amount)/total_amt if total_amt>0 else price
        TRADES[wallet][mint]={"amount":total_amt,"price":avg_price,"mcap_at_buy":old.get("mcap_at_buy",mcap_val),"chain":chain,"time":time.time()}
    else:
        TRADES[wallet][mint]={"amount":amount,"price":price,"mcap_at_buy":mcap_val,"chain":chain,"time":time.time()}
    save_all()

def get_sol_holdings(wallet):
    holdings=[]
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getTokenAccountsByOwner","params":[wallet,{"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},{"encoding":"jsonParsed"}]}
        res=requests.post(get_sol_rpc(),json=payload,timeout=12).json().get("result",{}).get("value",[])
        for acc in res:
            try:
                info=acc["account"]["data"]["parsed"]["info"]
                mint=info["mint"]; amt=float(info["tokenAmount"]["uiAmount"] or 0)
                if amt>0 and mint not in BLACKLIST_MINTS: holdings.append((mint,amt))
            except: continue
    except: pass
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
    if not holdings and wallet in TRADES:
        for mint,dat in TRADES[wallet].items(): holdings.append((mint,dat["amount"]))
    return holdings

def calculate_group_pnl(group_id):
    wallets=[w for w in SOL_WALLETS if w.get("group")==group_id or w.get("addr")==group_id]
    if not wallets: wallets=[w for w in SOL_WALLETS if GROUPS.get(w["addr"])==group_id]
    total_invest=0; total_current=0; details=[]
    for wdict in wallets:
        w=wdict["addr"]; chain=wdict.get("chain","sol")
        holdings = get_sol_holdings(w) if chain=="sol" else get_evm_holdings(w)
        # fallback to recorded trades if holdings empty
        if not holdings and w in TRADES:
            for mint,dat in TRADES[w].items(): holdings.append((mint,dat["amount"]))
        for mint, live_amt in holdings[:10]:
            trade=TRADES.get(w,{}).get(mint)
            if not trade: continue
            info=get_token_info(mint, chain)
            if not info or info["price"]==0: continue
            buy_val=trade["amount"]*trade["price"]
            curr_val=live_amt*info["price"] if live_amt>0 else trade["amount"]*info["price"]
            pnl=curr_val-buy_val
            pnl_pct=(curr_val/buy_val-1)*100 if buy_val>0 else 0
            total_invest+=buy_val; total_current+=curr_val
            details.append({"wallet":w,"mint":mint,"symbol":info["name"],"pnl":pnl,"pnl_pct":pnl_pct,"chain":chain})
            time.sleep(0.25)
    total_pnl=total_current-total_invest
    total_pct=(total_current/total_invest-1)*100 if total_invest>0 else 0
    return total_invest,total_current,total_pnl,total_pct,details

def auto_pnl_checker():
    while True:
        time.sleep(600)
        try:
            if not SOL_WALLETS: continue
            groups=set(GROUPS.values())
            for gid in list(groups)[:15]:
                inv,curr,pnl,pct,details=calculate_group_pnl(gid)
                if abs(pnl)<10: continue
                if pnl>50 or pct>50 or pnl<-30:
                    gl=display_group(gid)
                    top=sorted(details,key=lambda x:x["pnl"],reverse=True)[:3]
                    line="\n".join([f"{'🟢' if d['pnl']>=0 else '🔴'} {d['symbol']} {d['pnl_pct']:+.1f}% ${d['pnl']:+.0f} {d['chain'].upper()}" for d in top])
                    send_tg(f"📈 <b>Auto PnL {gl}</b>\nInvest ${inv:.2f} -> Now ${curr:.2f}\n<b>PnL ${pnl:+.2f} ({pct:+.1f}%)</b>\n{line}")
        except Exception as e:
            print(f"pnl checker err {e}",flush=True)

# ============ RPC + TX HELPERS ============
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
        for b in res.get("meta",{}).get("postTokenBalances",[]) or []:
            owner=b.get("owner",""); mint=b.get("mint","")
            if mint in BLACKLIST_MINTS: continue
            post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0); diff=post_amt-pre.get((owner,mint),0)
            if abs(diff)>0.000001: token_transfers.append(("unknown",owner,mint,abs(diff),diff>0))
    except: pass
    funder_cache[sig]=(datetime.now(),(sol_transfers,token_transfers))
    if len(funder_cache)>800: funder_cache.clear()
    return sol_transfers,token_transfers

def get_token_balance_quick(wallet):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":3}]}
        sigs=requests.post(get_sol_rpc(),json=payload,timeout=8).json().get("result",[]) or []
        for item in sigs[:2]:
            sig=item.get("signature")
            if not sig: continue
            _,tokens=get_tx_details(sig)
            for _,owner,mint,amt,is_buy in tokens:
                if owner==wallet and is_buy and amt>100: return mint,amt,sig
    except: pass
    return None,0,None

def get_evm_txs(wallet, chain="base", limit=12):
    try:
        if chain in ["base","evm"]:
            url=f"https://base.blockscout.com/api?module=account&action=txlist&address={wallet}&sort=desc&page=1&offset={limit}"
            r=requests.get(url, timeout=10).json()
            if r.get("result"): return r["result"]
        return []
    except: return []

def get_evm_token_transfers(wallet, chain="base"):
    try:
        url=f"https://base.blockscout.com/api?module=account&action=tokentx&address={wallet}&page=1&offset=5&sort=desc"
        r=requests.get(url, timeout=10).json()
        res=r.get("result",[])
        out=[]
        for tx in res[:5]:
            try:
                is_buy = tx["to"].lower()==wallet.lower()
                out.append((tx["from"], tx["to"], tx["contractAddress"], float(tx.get("value",0))/10**18, is_buy))
            except: continue
        return out
    except: return []

def fmt_links(mint,sig=None,wallet=None,chain="sol"):
    if chain=="sol":
        ds=f"https://dexscreener.com/solana/{mint}"
        out=f'<a href="{ds}">DS</a>'
    else:
        ds=f"https://dexscreener.com/base/{mint}"
        out=f'<a href="{ds}">DS</a> | <a href="https://basescan.org/token/{mint}">BS</a>'
    if sig: out+=f' | <a href="https://solscan.io/tx/{sig}">TX</a>' if chain=="sol" else f' | <a href="https://basescan.org/tx/{sig}">TX</a>'
    if wallet: out+=f' | <a href="https://solscan.io/account/{wallet}">SC</a>' if chain=="sol" else f' | <a href="https://basescan.org/address/{wallet}">SC</a>'
    return out

# ============ BUNDLE ============
def handle_bundle(wallet):
    global last_bundled
    wallet=wallet.strip(); chain=detect_chain(wallet); evm=is_evm(wallet)
    if not evm and len(wallet)<30: send_tg("❌ Invalid wallet"); return
    send_tg(f"🔍 <b>FORWARD {short(wallet)}</b> ⛓️ {chain.upper()} - AGGRESSIVE RECURSIVE ON")
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
            send_tg(f"⚠️ No parent — CEX\n<code>{short(wallet)}</code> ⛓️ {chain.upper()}"); return
        main_funder=funders[0][0]; last_bundled=main_funder
        GROUPS[wallet]=main_funder; GROUPS[main_funder]=main_funder
        if not wallet_exists(main_funder): SOL_WALLETS.append({"addr":main_funder,"chain":chain,"group":main_funder})
        if not wallet_exists(wallet): SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":main_funder})
        last_activity[wallet]=datetime.now(); last_activity[main_funder]=datetime.now()
        added=0
        if not evm:
            sib_sigs=get_signatures(main_funder,15); now_ts=time.time()
            for item in sib_sigs[:12]:
                bt=item.get("blockTime",0)
                if bt and (now_ts-bt)>900: continue
                sig=item.get("signature")
                if not sig: continue
                sols,_=get_tx_details(sig)
                for frm,to,amt in sols:
                    if frm==main_funder and to!=wallet and not wallet_exists(to) and amt>=0.001 and added<20:
                        SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                        recent_children[main_funder].append((to,amt,datetime.now(),False,None)); last_activity[to]=datetime.now()
        else:
            txs=get_evm_txs(main_funder, chain, 15)
            for tx in txs[:12]:
                try:
                    if tx["from"].lower()==main_funder.lower() and tx["to"].lower()!=wallet.lower() and float(tx["value"])/1e18>=0.00003 and added<20:
                        to=tx["to"]
                        if not wallet_exists(to):
                            SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                            recent_children[main_funder].append((to,float(tx["value"])/1e18,datetime.now(),False,None)); last_activity[to]=datetime.now()
                except: continue
        save_all()
        send_tg(f"📦 <b>Bundled {short(wallet)}</b> ⛓️ {chain.upper()}\nParent <code>{short(main_funder)}</code> G:{display_group(main_funder)}\n✅ +{added} sibs | Total {len(SOL_WALLETS)} | Recursive tracking ON for all")
    except Exception as e: send_tg(f"❌ Bundle error: {e}")

def alert_prep(funder):
    now=datetime.now()
    recent=[x for x in recent_children[funder] if (now-x[2]).total_seconds()<=600]
    recent_children[funder]=recent
    if not recent: return
    sol_only=[x for x in recent if not x[3]]
    if len(sol_only)<1: return
    key=(funder,"SOL_PREP"); last=last_dist_alert.get(key)
    if last and (now-last[0]).total_seconds()<45 and len(sol_only)<=last[1]: return
    last_dist_alert[key]=(now,len(sol_only))
    gl=display_group(funder)
    chain = next((w.get("chain","sol") for w in SOL_WALLETS if w.get("addr")==funder), "sol")
    unit = "E" if chain!="sol" else "S"
    childs=" ".join([f"{short(c[0])} {c[1]:.4f}{unit}" for c in sol_only[:6]])
    for child_addr,_,_,_,_ in sol_only:
        if child_addr not in pending_buys: pending_buys[child_addr]=(funder,now,gl)
    mint_counter=Counter(); mint_details={}; time.sleep(2.5)
    for child_addr,_,_,_,_ in sol_only[-6:]:
        if chain=="sol":
            mint,amt,sig=get_token_balance_quick(child_addr)
            if mint and amt>100:
                mint_counter[mint]+=1
                if mint not in mint_details: mint_details[mint]=(amt,sig,child_addr)
        else:
            transfers=get_evm_token_transfers(child_addr, chain)
            for frm,to,mint,amt,is_buy in transfers[:2]:
                if is_buy and amt>0:
                    mint_counter[mint]+=1
                    if mint not in mint_details: mint_details[mint]=(amt,"",child_addr)
    coin_line=""; cluster_tag=""
    if mint_counter:
        common_mint, common_count = mint_counter.most_common(1)[0]
        amt,sig,child_addr=mint_details[common_mint]
        info=get_token_info(common_mint, chain)
        name=info["name"] if info else f"{common_mint[:6]}"; mcap=info["mcap_str"] if info else "early"
        if common_count>=2: cluster_tag=f" 🔥 CLUSTER {common_count}w"
        for c_addr in [v[2] for v in mint_details.values()]: pending_buys.pop(c_addr,None)
        coin_line=f"\n\n💰 <b>{html.escape(name)} {mcap}</b>{cluster_tag}\nCA: <code>{common_mint}</code>\nAmt: {amt:.0f} x{common_count}\n{fmt_links(common_mint,sig,child_addr,chain)}"
    else: coin_line="\n\n⏳ Funded - watching buys 24h..."
    msg=f"⚠️ <b>PREP {len(sol_only)}w • 10m{cluster_tag}</b> ⛓️ {chain.upper()}\nG:{gl} • {short(funder)}\n{childs}{coin_line}"
    send_tg(msg)

def watch_pending_buys_loop():
    while True:
        time.sleep(4)
        try:
            now=datetime.now()
            for child_addr in list(pending_buys.keys()):
                parent_funder, funded_time, gl = pending_buys[child_addr]
                if (now-funded_time).total_seconds()>86400:
                    pending_buys.pop(child_addr,None); continue
                chain = next((w.get("chain","sol") for w in SOL_WALLETS if w.get("addr")==child_addr), "sol")
                if chain=="sol":
                    mint,amt,sig=get_token_balance_quick(child_addr)
                    if mint and amt>100:
                        info=get_token_info(mint, chain)
                        name=info["name"] if info else short(mint); mcap=info["mcap_str"] if info else "early"
                        if info: record_buy(child_addr,mint,amt,info["price"],info["mcap_val"],chain)
                        pending_buys.pop(child_addr,None)
                        send_tg(f"💰 <b>BOUGHT {html.escape(name)} {mcap}</b>\nChild <code>{short(child_addr)}</code> G:{gl}\nParent {short(parent_funder)}\nCA: <code>{mint}</code>\nAmt: {amt:.0f}\n{fmt_links(mint,sig,child_addr,chain)}")
                    # RECURSIVE: if this child funds new wallet, track it
                    sigs=get_signatures(child_addr,4)
                    for item in sigs[:2]:
                        sig=item.get("signature")
                        if not sig or sig in seen_sigs: continue
                        sols,_=get_tx_details(sig)
                        for frm,to,amt_sol in sols:
                            if frm==child_addr and amt_sol>=0.001 and not wallet_exists(to):
                                grp=GROUPS.get(child_addr,parent_funder)
                                SOL_WALLETS.append({"addr":to,"chain":chain,"group":grp}); GROUPS[to]=grp
                                pending_buys[to]=(grp,now,display_group(grp))
                                seen_sigs.add(sig); save_all()
                else:
                    transfers=get_evm_token_transfers(child_addr, chain)
                    for frm,to,mint,amt,is_buy in transfers[:3]:
                        if is_buy and amt>0:
                            info=get_token_info(mint, chain)
                            name=info["name"] if info else short(mint); mcap=info["mcap_str"] if info else "early"
                            if info: record_buy(child_addr,mint,amt,info["price"],info["mcap_val"],chain)
                            pending_buys.pop(child_addr,None)
                            send_tg(f"💰 <b>BOUGHT {html.escape(name)} {mcap}</b> ⛓️ {chain.upper()}\nChild <code>{short(child_addr)}</code> G:{gl}\nCA: <code>{mint}</code>\n{fmt_links(mint,chain=chain)}")
                            break
        except Exception as e:
            print(f"watch err {e}",flush=True)

async def track_sol_polling():
    while True:
        try:
            if last_activity:
                sorted_w=sorted(SOL_WALLETS,key=lambda wdict: last_activity.get(wdict["addr"],datetime.min),reverse=True)
                active_list=(sorted_w[:120])[:180]
            else: active_list=SOL_WALLETS[-60:]
            for wdict in active_list:
                w=wdict["addr"]; chain=wdict.get("chain","sol")
                if chain=="sol":
                    sigs=get_signatures(w,6)
                    for item in sigs[:4]:
                        sig=item.get("signature")
                        if not sig or sig in seen_sigs: continue
                        sols,tokens=get_tx_details(sig)
                        for _,owner,mint,amt,is_buy in tokens:
                            if owner==w and amt>0 and is_buy:
                                info=get_token_info(mint, chain)
                                if not info: continue
                                seen_sigs.add(sig); save_seen(); last_tx_time=datetime.now(); last_activity[w]=datetime.now()
                                record_buy(w,mint,amt,info["price"],info["mcap_val"],chain)
                                send_tg(f"🟢 <b>BUY {html.escape(info['name'])} {info['mcap_str']}</b>\n<code>{short(w)}</code> G:{display_group(w)}\nCA: <code>{mint}</code>\n{fmt_links(mint,sig,w,chain)}")
        except Exception as e: print(f"poll err {e}",flush=True)
        await asyncio.sleep(3)

# ============ AGGRESSIVE RECURSIVE TRACKER ============
def track_funders_aggressive():
    while True:
        time.sleep(7)
        try:
            # Track ALL wallets, not just parents - infinite depth
            all_wallets = SOL_WALLETS[-180:] # last 180 wallets to avoid rate limit
            for wdict in all_wallets:
                funder=wdfunc=w = wdict["addr"]
                chain=wdict.get("chain","sol")
                try:
                    if chain=="sol":
                        sigs=get_signatures(funder,8)
                        for item in sigs[:5]:
                            sig=item.get("signature")
                            if not sig or sig in seen_sigs: continue
                            if item.get("blockTime") and time.time()-item["blockTime"]>900: continue # only last 15m
                            sols,_=get_tx_details(sig)
                            for frm,to,amt in sols:
                                if frm==funder and amt>=0.0008 and not wallet_exists(to):
                                    grp=GROUPS.get(funder,funder)
                                    SOL_WALLETS.append({"addr":to,"chain":chain,"group":grp})
                                    GROUPS[to]=grp
                                    recent_children[funder].append((to,amt,datetime.now(),False,None))
                                    last_activity[to]=datetime.now()
                                    seen_sigs.add(sig); save_all()
                                    # Silent recursive alert only if parent is tracked group
                                    if len(recent_children[funder])>=1:
                                        alert_prep(funder)
                    else:
                        txs=get_evm_txs(funder, chain, 8)
                        for tx in txs[:5]:
                            try:
                                if tx["from"].lower()==funder.lower() and float(tx["value"])/1e18>=0.00003:
                                    if time.time()-int(tx.get("timeStamp",time.time()))>900: continue
                                    to=tx["to"]
                                    if not wallet_exists(to):
                                        grp=GROUPS.get(funder,funder)
                                        SOL_WALLETS.append({"addr":to,"chain":chain,"group":grp})
                                        GROUPS[to]=grp
                                        recent_children[funder].append((to,float(tx["value"])/1e18,datetime.now(),False,None))
                                        save_all()
                                        alert_prep(funder)
                            except: continue
                except: continue
        except Exception as e: print(f"recursive err {e}",flush=True)

def handle_command(text):
    global last_bundled, LABELS, GROUPS, SOL_WALLETS
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if w.get("chain")=="sol"); evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"🚀 <b>V13 Recursive PnL</b> • {len(SOL_WALLETS)}w SOL:{sol_c} EVM:{evm_c} • {uniq} groups\n/bundle <addr>\n/labelgroup VEIL\n/pnl [group]\n/listgroups\n/listwallets\n/status")
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
    elif low.startswith("/pnl"):
        parts=t.split(maxsplit=1)
        target_gid=last_bundled
        if len(parts)>1:
            q=parts[1].strip().upper()
            for gid,lbl in LABELS.items():
                if lbl==q: target_gid=gid; break
            else: target_gid=parts[1].strip()
        if not target_gid: send_tg("❌ No bundle. /bundle first"); return
        send_tg(f"⏳ Calculating PnL for {display_group(target_gid)}... ⛓️ SOL+EVM Recursive")
        def calc_and_send():
            inv,curr,pnl,pct,details=calculate_group_pnl(target_gid)
            if inv==0: send_tg(f"📉 No PnL data yet for {display_group(target_gid)}. Wait for buys."); return
            gl=display_group(target_gid)
            lines=[f"📊 <b>PnL {gl}</b> | Invest ${inv:.2f} | Now ${curr:.2f} | <b>${pnl:+.2f} ({pct:+.1f}%)</b>"]
            for d in sorted(details,key=lambda x:x["pnl"],reverse=True)[:10]:
                emoji="🟢" if d["pnl"]>=0 else "🔴"
                lines.append(f"{emoji} {d['symbol']} {d['chain'].upper()} {d['pnl_pct']:+.1f}% ${d['pnl']:+.2f} {short(d['wallet'])}")
            send_tg("\n".join(lines))
        threading.Thread(target=calc_and_send,daemon=True).start()
    elif low.startswith("/listgroups"):
        if not GROUPS: send_tg("No groups"); return
        c=Counter(GROUPS.values())
        lines=[f"{LABELS.get(g, short(g))}: {cnt}w" for g,cnt in c.most_common(25)]
        send_tg("📋 <b>Groups:</b>\n" + "\n".join(lines))
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
        msg=f"👛 <b>{len(wallets)} wallets:</b>\n"
        for wdict in wallets[-60:]:
            w=wdict["addr"]; ch=wdict.get("chain","sol").upper(); g=display_group(w)
            msg+=f"<code>{short(w)}</code> {ch} G:{g}\n"
        send_tg(msg)
    elif low.startswith("/remove"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /remove <addr>"); return
        a=p[1].strip()
        to_del=[w for w in SOL_WALLETS if w["addr"]==a]
        if to_del:
            for w in to_del: SOL_WALLETS.remove(w); GROUPS.pop(w["addr"],None)
            save_all(); send_tg(f"Removed <code>{short(a)}</code>")
        else: send_tg("Not found")
    elif low.startswith("/clear_all"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); recent_children.clear(); pending_buys.clear(); TRADES.clear(); save_all(); send_tg("🧹 Cleared all")
    elif low.startswith("/clear"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); recent_children.clear(); pending_buys.clear(); TRADES.clear(); save_all(); send_tg("🧹 Cleared all")
    elif low.startswith("/status"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if w.get("chain")=="sol"); evm_c=len(SOL_WALLETS)-sol_c
        send_tg(f"V13 Recursive PnL • {len(SOL_WALLETS)}w SOL:{sol_c} EVM:{evm_c} • {uniq} groups • Last {mins}m • Bundle {short(last_bundled) if last_bundled else 'none'} • Recursive ON")

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
    threading.Thread(target=track_funders_aggressive,daemon=True).start()
    threading.Thread(target=watch_pending_buys_loop,daemon=True).start()
    threading.Thread(target=auto_pnl_checker,daemon=True).start()
    send_tg("🚀 <b>V13 RECURSIVE PnL DEPLOYED</b>\n✅ Infinite recursive A->B->C->D\n✅ SOL + Base both\n✅ /pnl VEIL\n✅ Auto PnL every 10m")
    await track_sol_polling()

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e: print(f"CRASH {e}",flush=True); time.sleep(5); start_bot()

if __name__=="__main__":
    auto_set_commands()
    threading.Thread(target=run_flask,daemon=True).start()
    threading.Thread(target=poll_commands,daemon=True).start()
    time.sleep(1); start_bot()
