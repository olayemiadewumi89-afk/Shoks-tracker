import os, asyncio, threading, time, requests, json, html
from flask import Flask
from datetime import datetime
from collections import defaultdict, deque, Counter

WALLETS_FILE="wallets.json"; GROUPS_FILE="groups.json"; LABELS_FILE="labels.json"; SEEN_FILE="seen.json"; RECENT_FILE="recent.json"
SOL_WALLETS=[]; GROUPS={}; LABELS={}; seen_sigs=set(); recent_children=defaultdict(list); recent_token_buys=defaultdict(list)
sniper_marked={}; tg_queue=deque(); funder_cache={}; last_tx_time=datetime.now(); last_bundled=None; last_activity={}; last_dist_alert={}; best_alert_cache={}
pending_buys={}

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com","https://rpc.ankr.com/solana","https://solana.api.onchainkit.io"]
rpc_idx=0; last_rpc_call=0
def get_public_rpc():
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
            {"command":"status","description":"Bot status"},
            {"command":"remove","description":"Remove wallet"},
            {"command":"clear_all","description":"Clear all"},
        ]
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
    except: pass

app=Flask(__name__)
@app.route('/')
def home(): return "Shoks V10 24H",200
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
    if len(tg_queue)>200: tg_queue.popleft()
    tg_queue.append(text)

def short(a): return f"{a[:4]}..{a[-4:]}" if len(a)>8 else a
def is_evm(addr): return addr.startswith("0x") and len(addr)==42
def detect_chain(addr): return "base" if is_evm(addr) else "sol"
def wallet_exists(addr): return any((w["addr"] if isinstance(w, dict) else w)==addr for w in SOL_WALLETS)
def display_group(wallet_or_group):
    if not wallet_or_group: return "?"
    gid=GROUPS.get(wallet_or_group,wallet_or_group)
    return LABELS.get(gid,LABELS.get(wallet_or_group,short(gid)))

def load_all():
    global SOL_WALLETS,GROUPS,LABELS,seen_sigs
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
load_all()
def save_all():
    try:
        json.dump({"sol":SOL_WALLETS,"groups":GROUPS},open(WALLETS_FILE,"w"))
        json.dump(GROUPS,open(GROUPS_FILE,"w")); json.dump(LABELS,open(LABELS_FILE,"w"))
    except: pass
def save_seen():
    try: json.dump(list(seen_sigs)[-2000:],open(SEEN_FILE,"w"))
    except: pass
def save_recent():
    try:
        out={k:[(a,b,c.isoformat(),d,e) for a,b,c,d,e in v[-15:]] for k,v in recent_children.items()}
        json.dump(out,open(RECENT_FILE,"w"))
    except: pass

BLACKLIST_MINTS=["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"]
def get_token_info(token_address):
    if token_address in BLACKLIST_MINTS: return None
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=7).json()
        pairs=r.get("pairs",[])
        if not pairs: return None
        valid=[p for p in pairs if (p.get("liquidity",{}).get("usd",0) or 0)>=10]
        if not valid: valid=pairs
        best=sorted(valid,key=lambda x:x.get("liquidity",{}).get("usd",0) or 0,reverse=True)[0]
        fdv=best.get("fdv") or best.get("marketCap") or 0
        if fdv>=1_000_000: mcap_str=f"${fdv/1_000_000:.2f}M"
        elif fdv>=1000: mcap_str=f"${fdv/1000:.0f}k"
        else: mcap_str=f"${fdv:.0f}"
        price=float(best.get("priceUsd",0) or 0); is_early=price==0
        if price==0: price=0.00000001
        age_min=0
        try:
            pc=best.get("pairCreatedAt")
            if pc: age_min=int((time.time()*1000-pc)/60000)
        except: pass
        return {"name":best.get("baseToken",{}).get("symbol",token_address[:6]),"mcap_str":mcap_str,"mcap_val":fdv,"price":price,"age_min":age_min,"is_early":is_early,"mint":token_address}
    except: return None

def get_signatures(wallet,limit=10):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":limit}]}
        return requests.post(get_public_rpc(),json=payload,timeout=12).json().get("result",[]) or []
    except: return []
def get_tx_details(sig):
    if sig in funder_cache and (datetime.now()-funder_cache[sig][0]).total_seconds()<600: return funder_cache[sig][1]
    sol_transfers=[]; token_transfers=[]
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        res=requests.post(get_public_rpc(),json=payload,timeout=14).json().get("result")
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
    if len(funder_cache)>700: funder_cache.clear()
    return sol_transfers,token_transfers
def get_token_balance_quick(wallet):
    try:
        payload={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":3}]}
        sigs=requests.post(get_public_rpc(),json=payload,timeout=8).json().get("result",[]) or []
        for item in sigs[:2]:
            sig=item.get("signature")
            if not sig: continue
            _,tokens=get_tx_details(sig)
            for _,owner,mint,amt,is_buy in tokens:
                if owner==wallet and is_buy and amt>100: return mint,amt,sig
    except: pass
    return None,0,None
def fmt_links(mint,sig=None,wallet=None):
    ds=f"https://dexscreener.com/solana/{mint}"
    ph=f"https://photon-sol.tinyastro.io/en/lp/{mint}"
    out=f'<a href="{ds}">DS</a> | <a href="{ph}">PH</a>'
    if sig: out+=f' | <a href="https://solscan.io/tx/{sig}">TX</a>'
    if wallet: out+=f' | <a href="https://solscan.io/account/{wallet}">SC</a>'
    return out

def handle_bundle(wallet):
    global last_bundled
    wallet=wallet.strip(); chain=detect_chain(wallet); evm=is_evm(wallet)
    if not evm and len(wallet)<30: send_tg("❌ Invalid wallet"); return
    send_tg(f"🔍 <b>FORWARD {short(wallet)}</b> ⛓️ {chain.upper()}")
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
        if not funders:
            if not wallet_exists(wallet): SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":wallet})
            GROUPS[wallet]=wallet; LABELS[wallet]=f"CEX_{short(wallet).upper()}"
            last_bundled=wallet; save_all(); last_activity[wallet]=datetime.now()
            send_tg(f"⚠️ No parent — CEX\n<code>{short(wallet)}</code> ⛓️ {chain.upper()}\nFORWARD ON"); return
        main_funder=funders[0][0]; last_bundled=main_funder
        GROUPS[wallet]=main_funder; GROUPS[main_funder]=main_funder
        if not wallet_exists(main_funder): SOL_WALLETS.append({"addr":main_funder,"chain":chain,"group":main_funder})
        if not wallet_exists(wallet): SOL_WALLETS.append({"addr":wallet,"chain":chain,"group":main_funder})
        last_activity[wallet]=datetime.now(); last_activity[main_funder]=datetime.now()
        added=0; sib_sigs=get_signatures(main_funder,15); now_ts=time.time()
        for item in sib_sigs[:12]:
            bt=item.get("blockTime",0)
            if bt and (now_ts-bt)>900: continue
            sig=item.get("signature")
            if not sig: continue
            sols,_=get_tx_details(sig)
            for frm,to,amt in sols:
                if frm==main_funder and to!=wallet and not wallet_exists(to) and amt>=0.001 and added<15:
                    SOL_WALLETS.append({"addr":to,"chain":chain,"group":main_funder}); GROUPS[to]=main_funder; added+=1
                    recent_children[main_funder].append((to,amt,datetime.now(),False,None)); last_activity[to]=datetime.now()
        save_all(); save_recent()
        send_tg(f"📦 <b>Bundled {short(wallet)}</b> ⛓️ {chain.upper()}\nParent <code>{short(main_funder)}</code> G:{display_group(main_funder)}\n✅ +{added} sibs | Total {len(SOL_WALLETS)}")
    except Exception as e: send_tg(f"❌ Bundle error: {e}")

def alert_prep(funder):
    now=datetime.now()
    recent=[x for x in recent_children[funder] if (now-x[2]).total_seconds()<=600]
    recent_children[funder]=recent; save_recent()
    if not recent: return
    sol_only=[x for x in recent if not x[3]]
    if len(sol_only)<1: return
    key=(funder,"SOL_PREP"); last=last_dist_alert.get(key)
    if last and (now-last[0]).total_seconds()<45 and len(sol_only)<=last[1]: return
    last_dist_alert[key]=(now,len(sol_only))
    gl=display_group(funder)
    childs=" ".join([f"{short(c[0])} {c[1]:.3f}S" for c in sol_only[:6]])
    for child_addr,_,_,_,_ in sol_only:
        if child_addr not in pending_buys:
            pending_buys[child_addr]=(funder,now,gl)
    mint_counter=Counter(); mint_details={}; time.sleep(2.5)
    for child_addr,_,_,_,_ in sol_only[-6:]:
        mint,amt,sig=get_token_balance_quick(child_addr)
        if mint and amt>100:
            mint_counter[mint]+=1
            if mint not in mint_details: mint_details[mint]=(amt,sig,child_addr)
    coin_line=""; cluster_tag=""
    if mint_counter:
        common_mint, common_count = mint_counter.most_common(1)[0]
        amt,sig,child_addr=mint_details[common_mint]
        info=get_token_info(common_mint)
        name=info["name"] if info else f"{common_mint[:6]}"; mcap=info["mcap_str"] if info else "early"
        if common_count>=2: cluster_tag=f" 🔥 CLUSTER {common_count}w"
        for c_addr in [v[2] for v in mint_details.values()]: pending_buys.pop(c_addr,None)
        coin_line=f"\n\n💰 <b>{html.escape(name)} {mcap}</b>{cluster_tag}\nCA: <code>{common_mint}</code>\nAmt: {amt:.0f} x{common_count}\n{fmt_links(common_mint,sig,child_addr)}"
    else: coin_line="\n\n⏳ Funded - watching buys 24h..."
    msg=f"⚠️ <b>PREP {len(sol_only)}w • 10m{cluster_tag}</b>\nG:{gl} • {short(funder)}\n{childs}{coin_line}\n\n<a href=\"https://solscan.io/account/{funder}\">Factory SC</a>"
    send_tg(msg)

def alert_sell_prep(funder, token_mint, sends):
    now=datetime.now()
    key=(funder, token_mint, "SELL_PREP"); last=last_dist_alert.get(key)
    if last and (now-last[0]).total_seconds()<120: return
    last_dist_alert[key]=(now,len(sends))
    gl=display_group(funder)
    info=get_token_info(token_mint)
    name=info["name"] if info else short(token_mint)
    childs=" ".join([f"{short(c[0])} {c[1]:.0f}" for c in sends[:6]])
    msg=f"🔴 <b>SELL PREP {len(sends)}w • 10m</b>\nG:{gl} • {short(funder)} sending <b>{html.escape(name)}</b>\n{childs}\nCA: <code>{token_mint}</code>\n{fmt_links(token_mint)}\n⚠️ They will SELL soon"
    send_tg(msg)

def watch_pending_buys_loop():
    while True:
        time.sleep(4)
        try:
            now=datetime.now()
            for child_addr in list(pending_buys.keys()):
                parent_funder, funded_time, gl = pending_buys[child_addr]
                if (now-funded_time).total_seconds()>86400: # 24H TRACKING - was 600
                    pending_buys.pop(child_addr,None); continue
                mint,amt,sig=get_token_balance_quick(child_addr)
                if mint and amt>100:
                    info=get_token_info(mint)
                    name=info["name"] if info else short(mint); mcap=info["mcap_str"] if info else "early"
                    pending_buys.pop(child_addr,None)
                    recent_token_buys[mint].append((parent_funder,50,now))
                    send_tg(f"💰 <b>BOUGHT {html.escape(name)} {mcap}</b>\nChild <code>{short(child_addr)}</code> G:{gl}\nParent {short(parent_funder)}\nCA: <code>{mint}</code>\nAmt: {amt:.0f}\n{fmt_links(mint,sig,child_addr)}")
                    continue
                sigs=get_signatures(child_addr,5)
                for item in sigs[:3]:
                    sig=item.get("signature")
                    if not sig or sig in seen_sigs: continue
                    sols,_=get_tx_details(sig)
                    for frm,to,amt_sol in sols:
                        if frm==child_addr and amt_sol>=0.001 and not wallet_exists(to):
                            SOL_WALLETS.append({"addr":to,"chain":"sol","group":GROUPS.get(parent_funder,parent_funder)})
                            GROUPS[to]=GROUPS.get(parent_funder,parent_funder)
                            recent_children[child_addr].append((to,amt_sol,datetime.now(),False,None))
                            pending_buys[to]=(parent_funder,now,gl)
                            seen_sigs.add(sig); save_all()
        except Exception as e:
            print(f"watch err {e}",flush=True)

def should_alert(mint,usd,info,wallet_group):
    now=datetime.now()
    recent=[x for x in recent_token_buys[mint] if (now-x[2]).total_seconds()<=600]
    recent_token_buys[mint]=recent; recent.append((wallet_group,usd,now)); recent_token_buys[mint]=recent
    total_5m=sum(x[1] for x in recent); wallets_5m=len(set(x[0] for x in recent))
    if 5<=usd<=300 and ((info["mcap_val"]<500000 and info["age_min"]<180) or info.get("is_early")):
        return f"🟢 <b>PROBE</b> ${usd:.0f} at {info['mcap_str']}"
    if 80<=total_5m<=20000 and wallets_5m>=2: return f"🟡 <b>SWARM</b> {wallets_5m}w ${total_5m:.0f} at {info['mcap_str']}"
    if usd>=400: return f"🔵 <b>WHALE</b> ${usd:.0f} at {info['mcap_str']}"
    return None

async def track_sol_polling():
    while True:
        try:
            if last_activity:
                sorted_w=sorted(SOL_WALLETS,key=lambda wdict: last_activity.get(wdict["addr"] if isinstance(wdict,dict) else wdict,datetime.min),reverse=True)
                bundled_extra=[w for w in SOL_WALLETS if GROUPS.get(w["addr"] if isinstance(w,dict) else w,"")==last_bundled] if last_bundled else []
                active_list=(sorted_w[:100]+bundled_extra)[:150]
            else: active_list=SOL_WALLETS[-50:]
            for wdict in active_list:
                w=wdict["addr"] if isinstance(wdict,dict) else wdict
                if wdict.get("chain","sol")!="sol": continue
                sigs=get_signatures(w,6)
                for item in sigs[:4]:
                    sig=item.get("signature")
                    if not sig or sig in seen_sigs: continue
                    sols,tokens=get_tx_details(sig)
                    for _,owner,mint,amt,is_buy in tokens:
                        if owner==w and amt>0 and is_buy:
                            info=get_token_info(mint)
                            if not info: continue
                            usd=amt*info["price"]
                            if info.get("is_early"): usd=max(usd,15)
                            if usd<5: continue
                            seen_sigs.add(sig); save_seen(); last_tx_time=datetime.now(); last_activity[w]=datetime.now()
                            alert_text=should_alert(mint,usd,info,w)
                            if alert_text:
                                send_tg(f"{alert_text}\n{html.escape(info['name'])} {info['mcap_str']}\nCA: <code>{mint}</code>\n{fmt_links(mint,sig,w)}")
        except Exception as e: print(f"poll err {e}",flush=True)
        await asyncio.sleep(4)

def track_funders_15s():
    while True:
        time.sleep(10)
        try:
            funders=set(GROUPS.values())
            for k in recent_children.keys(): funders.add(k)
            if not funders: funders=set([(w["addr"] if isinstance(w,dict) else w) for w in SOL_WALLETS[:25]])
            for funder in list(funders)[:50]:
                try:
                    sigs=get_signatures(funder,12); now_ts=time.time()
                    for item in sigs[:10]:
                        bt=item.get("blockTime",0)
                        if bt and (now_ts-bt)>900: continue
                        sig=item.get("signature")
                        if not sig or sig in seen_sigs: continue
                        sols,tokens=get_tx_details(sig)
                        sol_found=False
                        token_sends=defaultdict(list)
                        for frm,to,amt in sols:
                            if frm==funder and amt>=0.001:
                                if not wallet_exists(to): SOL_WALLETS.append({"addr":to,"chain":"sol","group":GROUPS.get(funder,funder)}); GROUPS[to]=GROUPS.get(funder,funder)
                                recent_children[funder].append((to,amt,datetime.now(),False,None)); last_activity[to]=datetime.now(); last_activity[funder]=datetime.now(); sol_found=True
                        for _,owner,mint,amt,is_buy in tokens:
                            if not is_buy and amt>100 and owner!=funder:
                                if not wallet_exists(owner):
                                    recent_children[funder].append((owner,amt,datetime.now(),True,mint))
                                    token_sends[mint].append((owner,amt))
                                    last_activity[owner]=datetime.now(); last_activity[funder]=datetime.now()
                        if sol_found or token_sends:
                            seen_sigs.add(sig); save_seen(); save_recent(); save_all()
                        if sol_found:
                            alert_prep(funder)
                        for mint, lst in token_sends.items():
                            all_recent=[x for x in recent_children[funder] if x[3] and x[4]==mint and (datetime.now()-x[2]).total_seconds()<=600]
                            if len(all_recent)>=2:
                                alert_sell_prep(funder, mint, all_recent)
                except: continue
        except Exception as e: print(f"funder err {e}",flush=True)

def handle_command(text):
    global last_bundled, LABELS, GROUPS, SOL_WALLETS, recent_children, sniper_marked, recent_token_buys
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        uniq=len(set(GROUPS.values()))
        sol_c=sum(1 for w in SOL_WALLETS if isinstance(w,dict) and w.get("chain")=="sol")
        send_tg(f"🚀 <b>V10 24H</b> • {len(SOL_WALLETS)}w SOL:{sol_c} • {uniq} groups\n/bundle <addr>\n/labelgroup VEIL\n/listgroups\n/listwallets\n/status")
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
    elif low.startswith("/listgroups"):
        if not GROUPS: send_tg("No groups"); return
        c=Counter(GROUPS.values())
        lines=[f"{LABELS.get(g, short(g))}: {cnt}w" for g,cnt in c.most_common(20)]
        send_tg("📋 <b>Groups:</b>\n" + "\n".join(lines))
    elif low.startswith("/listwallets"):
        parts=t.split(maxsplit=1); filter_gid=None
        if len(parts)>1:
            fg=parts[1].strip().upper()
            for gid,lbl in LABELS.items():
                if lbl==fg: filter_gid=gid; break
            if not filter_gid: filter_gid=parts[1].strip()
        wallets=SOL_WALLETS
        if filter_gid: wallets=[w for w in SOL_WALLETS if (w.get("group") if isinstance(w,dict) else GROUPS.get(w,w))==filter_gid or (w.get("addr") if isinstance(w,dict) else w)==filter_gid]
        if not wallets: send_tg("Empty"); return
        msg=f"👛 <b>{len(wallets)} wallets:</b>\n"
        for wdict in wallets[-50:]:
            w=wdict["addr"] if isinstance(wdict,dict) else wdict
            g=display_group(w)
            msg+=f"<code>{short(w)}</code> G:{g}\n"
        send_tg(msg)
    elif low.startswith("/remove"):
        p=t.split()
        if len(p)<2: send_tg("Usage: /remove <addr>"); return
        a=p[1].strip()
        to_del=[w for w in SOL_WALLETS if (w["addr"] if isinstance(w,dict) else w)==a]
        if to_del:
            for w in to_del: SOL_WALLETS.remove(w); GROUPS.pop(w["addr"] if isinstance(w,dict) else w,None)
            save_all(); send_tg(f"Removed <code>{short(a)}</code>")
        else: send_tg("Not found")
    elif low.startswith("/clear") or low.startswith("/clear_all"):
        SOL_WALLETS.clear(); GROUPS.clear(); LABELS.clear(); recent_children.clear(); sniper_marked.clear(); recent_token_buys.clear(); pending_buys.clear(); save_all(); send_tg("🧹 Cleared all")
    elif low.startswith("/status"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        uniq=len(set(GROUPS.values()))
        send_tg(f"V10 24H • {len(SOL_WALLETS)}w • {uniq} groups • Last {mins}m • Bundle {short(last_bundled) if last_bundled else 'none'}")

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
    threading.Thread(target=track_funders_15s,daemon=True).start()
    threading.Thread(target=watch_pending_buys_loop,daemon=True).start()
    send_tg("🚀 <b>V10 24H DEPLOYED</b>\n✅ Tracks children for 24h + forever in main list\n✅ BUY PREP + BOUGHT + SELL PREP all working")
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
