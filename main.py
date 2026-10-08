import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict
from web3 import Web3

SOL_WALLETS = [
"Beqv6dzTcjV2eodo8RRXCiCcnSYrS1vkQKhfqwHXqeit",
"5t8FA8z7SFiEpjau2nTBYKN8jd5CGo8Ttcgt1m73C7d",
"6xmMW5JPSEfeRuNdkxixHWsm4Sf57SdXybA3BdwZzrM1",
"2Ysos2B2S6rb9FBNYUB7Yrk54Xv2nNbr8eBHs1sX8M6m",
"Agt534WQKGXuVt9UFamcgqH9djySF9PmxqNS5caiMCAV",
"Ab25e8CyZ6119HSY6r4wdKpBPxY3UBLWfrZ7PEBvHD4F",
"4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo",
"9BMzTpSo4URse1oN666pmexhdjpU1vA5p7LtroCFQdLU",
"498g1rVnFcnjBjpfw1xyqA1WvgQXUU8RWuELjxkjAayQ",
"8f39XhhZoRD8sYb6K5K9N7iSHXkL7BDmFFQNDF3TtsEr",
"83b2LMf12aLec92kur3duRA8VvUkLaXUtML31aCcZNCM",
"7aoGoRSexZu1DE4vC24CVo4SqWrBDeoJ34qLRGUsL9ha",
"2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V",
"7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9",
"H2QSGECp13sFLJgdTsDtayX3dk18Dm6sQMSQKcew7Xzk",
"DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39",
"6My97BBVoJz3j7mQWFVz5pykFxMsCs44gjhaqmczcAND",
"D9tPQeij7vSTZwkxzxZibso4GFuRW8aBMpCg5QhCSfVL",
"59mWSDjx5VFQz15FGJio8BJzupKorK5SVHSbaJaZGAxC",
]
EVM_WALLETS = [
"0xfd87eda88be6c372453b721da63d58ad1a5b2d94",
"0x2a17e1e796dd7bd3b27efe2cda72d4b909baaacf",
"0x4bc1782fafb967834e0e75947ba15113e48fc70e",
"0x5a08728a6704d99468d8919ba0adc2e1d2733083",
"0xae97104fde93a82797afec819ac0e09b8d544907",
"0xabf122e41ce873a3c6a71fbf1c6419ca5df07f17",
"0xd1c77a04b87393e98a1220532e72e8f7d0a31c5a",
"0x0c175c6a0065ee05f871a68783d2de432a1e6cbe",
"0x696d1265c8fc4f14797abebfae3c43ebfa9d8e28",
"0x0121525f755c9e7bbc525bba6672716ab46ced57",
"0x27d6a5a1d9a5e89bfce0239f08d9c95cf2256ebb",
"0xb8f305f27ccc406373de0082cc06cb1d065504ea",
"0xb02208d1b27811480cf6171bccc2b7af9513cddb",
"0x06de9c48b1e639ed5c13ec8fbd4080a38e39f2d1",
"0x1890e719822bc704c4f117aa4109401c2bab6f79",
"0xd4cf04bc9d7c80b49c6c30a633f7b9bd5370b4d6",
"0x7f6e4e1e59be05dc717c8e5429154a7408609ee2",
]

WALLETS_FILE="wallets.json"; MEMORY_FILE="cluster_memory.json"; HOLDINGS_FILE="holdings.json"; PNL_FILE="pnl.json"
cluster_memory=defaultdict(list); holdings=defaultdict(dict)
pnl_tracker=defaultdict(lambda: {"buys":0,"sells":0,"spent":0.0,"realized":0.0})
seen_sigs=set(); cluster_alerted={}
STABLES={"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}
last_tx_time=datetime.now()

def load_wallets():
    global SOL_WALLETS, EVM_WALLETS
    try:
        if os.path.exists(WALLETS_FILE):
            d=json.load(open(WALLETS_FILE)); SOL_WALLETS=d.get("sol",SOL_WALLETS); EVM_WALLETS=d.get("evm",EVM_WALLETS)
    except: pass
def save_wallets():
    try: json.dump({"sol":SOL_WALLETS,"evm":EVM_WALLETS}, open(WALLETS_FILE,"w"))
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
        if os.path.exists(HOLDINGS_FILE): holdings.update(json.load(open(HOLDINGS_FILE)))
    except: pass
    try:
        if os.path.exists(PNL_FILE): pnl_tracker.update(json.load(open(PNL_FILE)))
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
load_wallets(); load_persistent()

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com","https://rpc.ankr.com/solana"]
rpc_idx=0
def get_public_rpc():
    global rpc_idx
    url=SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]
    rpc_idx+=1
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
def home(): return "Shok V3.11.23 FULL COMMANDS + FILTER",200
@app.route('/health')
def health(): return "OK",200
@app.route('/debug')
def debug(): return f"SOL:PUBLIC EVM:{len(EVM_WALLETS)} SOL:{len(SOL_WALLETS)} Holdings:{len(holdings)} LastTx:{int((datetime.now()-last_tx_time).total_seconds()/60)}m",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: print(text[:200], flush=True); return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True},timeout=15)
    except: pass

def short(a): return f"{a[:4]}...{a[-4:]}" if a else "?"

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
            liq=best.get("liquidity",{}).get("usd",0)
            name=best.get("baseToken",{}).get("symbol", token_address[:6])
            fdv=best.get("fdv") or best.get("marketCap") or 0
            mcap=f"${fdv/1_000_000:.2f}M" if fdv>=1_000_000 else f"${fdv/1000:.1f}k" if fdv>=1000 else f"${fdv:.0f}" if fdv else "New"
            price=float(best.get("priceUsd",0) or 0)
            if price==0: return None
            dex_link=f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}"
            return name, mcap, dex_link, price, best.get('chainId','SOL').upper(), liq
        except:
            if attempt==0: time.sleep(1)
            continue
    return None

def check_cluster_1d(mint, name, mcap, dex_link, chain):
    now=datetime.now(); events=cluster_memory.get(mint,[]); recent=[e for e in events if (now-e[1]) <= timedelta(days=1)]
    uniq_map={}; [uniq_map.__setitem__(w.lower(),w) for w,_,_ in recent]
    uniq_wallets=list(uniq_map.values())
    if len(uniq_wallets) < 2: return
    last=cluster_alerted.get(mint.lower())
    if last and (now-last) <= timedelta(hours=12): return
    cluster_alerted[mint.lower()]=now
    wallets_str="\n".join([f"• `{short(w)}` {w[:6]}...{w[-4:]}" for w in uniq_wallets[:6]])
    send_tg(f"🔥 *CLUSTER BUY 1D - EDGE!* 🔥\n\n🪙 *{name}* ({mcap}) {chain}\n👥 {len(uniq_wallets)} wallets in 1D:\n{wallets_str}\n\n📈 [Chart]({dex_link})")

def get_tx_parsed_public(sig):
    for _ in range(3):
        rpc_url=get_public_rpc()
        try:
            payload={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed", "maxSupportedTransactionVersion":0}]}
            r=requests.post(rpc_url, json=payload, timeout=12)
            if r.status_code!=200: continue
            res=r.json().get("result")
            if not res: continue
            meta=res.get("meta",{})
            pre_balances=meta.get("preTokenBalances",[])
            post_balances=meta.get("postTokenBalances",[])
            bal_map={}
            for b in pre_balances:
                bal_map[(b.get("owner",""), b.get("mint",""))]=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            transfers=[]
            for b in post_balances:
                owner=b.get("owner",""); mint=b.get("mint","")
                if mint in STABLES: continue
                post_amt=float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
                pre_amt=bal_map.get((owner,mint),0)
                diff=post_amt-pre_amt
                if abs(diff) < 0.000001: continue
                transfers.append({"mint":mint,"owner":owner,"amount":abs(diff),"is_buy":diff>0})
            if transfers:
                return {"transfers":transfers}
        except Exception as e:
            print(f"public parse err {e}", flush=True)
            continue
    return None

def process_sol_tx_public(wallet_list, parsed, sig):
    global last_tx_time
    if sig in seen_sigs: return
    if not parsed or not parsed.get("transfers"): return
    transfers=sorted(parsed["transfers"], key=lambda x: x["amount"], reverse=True)
    best=None
    for tr in transfers:
        if tr["owner"] in wallet_list and tr["amount"] > 0.0001:
            best=tr
            break
    if not best: return
    mint=best["mint"]; amt=best["amount"]; owner=best["owner"]; is_buy=best["is_buy"]
    if mint in STABLES: return
    info=get_token_info_quick(mint,"SOL")
    if not info:
        print(f"SKIP no chart {mint[:6]}", flush=True)
        return
    name,mcap,dex_link,price,_,liq = info
    usd=amt*price if price>0 else 0
    if usd < 10:
        print(f"SKIP dust ${usd:.2f} {name}", flush=True)
        return
    seen_sigs.add(sig)
    if len(seen_sigs)>800: seen_sigs.clear()
    last_tx_time=datetime.now()
    print(f"ALERT PUBLIC {name} {'BUY' if is_buy else 'SELL'} ${usd:.2f} Liq ${liq:.0f}", flush=True)
    photon_link=f"https://photon-sol.tinyastro.io/en/lp/{mint}"
    if is_buy:
        pnl_tracker[owner.lower()]["buys"]+=1; pnl_tracker[owner.lower()]["spent"]+=usd; save_pnl()
        cluster_memory[mint].append((owner,datetime.now(),"SOL")); save_memory()
        holdings[mint][owner]={"amount_usd":usd,"chain":"SOL","token_name":name,"mcap":mcap}; save_holdings()
        send_tg(f"💰 *SOL BUY*\n🪙 {name} ({mcap}) Liq ${liq:,.0f}\n👤 `{owner[:6]}...{owner[-4:]}` | ${usd:,.2f}\n📊 [Dex]({dex_link}) | [Photon]({photon_link}) | [Tx](https://solscan.io/tx/{sig}) [PUBLIC]")
        threading.Thread(target=lambda: check_cluster_1d(mint,name,mcap,dex_link,"SOL"),daemon=True).start()
    else:
        pnl_tracker[owner.lower()]["sells"]+=1; pnl_tracker[owner.lower()]["realized"]+=usd; save_pnl()
        if mint in holdings and owner in holdings[mint]: holdings[mint].pop(owner,None); save_holdings()
        send_tg(f"🚨 *SOL SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${usd:,.2f} sold by `{owner[:6]}...{owner[-4:]}`\n📊 [Dex]({dex_link}) | [Photon]({photon_link}) | [Tx](https://solscan.io/tx/{sig}) [PUBLIC]")

async def track_sol_polling():
    print("🔵 SOL PUBLIC RPC 5s FILTERED $10+ + EVM", flush=True)
    while True:
        for w in SOL_WALLETS:
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
                    print(f"Found sig {sig[:8]} for {w[:6]} via PUBLIC", flush=True)
                    parsed=get_tx_parsed_public(sig)
                    if not parsed: continue
                    process_sol_tx_public([w], parsed, sig)
            except Exception as e:
                print(f"public poll err {e}", flush=True)
        await asyncio.sleep(5)

async def track_chain(chain):
    global last_tx_time
    seen=set(); scan_map={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}
    scan=scan_map[chain]; tracked_lower=set([x.lower() for x in EVM_WALLETS])
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
                        if not frm or frm.lower() not in tracked_lower: continue
                        h=tx.hash.hex() if hasattr(tx.hash,'hex') else tx['hash'].hex()
                        try:
                            receipt=w3.eth.get_transaction_receipt(h)
                            for log in receipt.get('logs',[]):
                                if len(log.get('topics',[]))!=3: continue
                                if log['topics'][0].hex()!='ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef': continue
                                from_addr='0x'+log['topics'][1].hex()[-40:]; to_addr='0x'+log['topics'][2].hex()[-40:]
                                token_contract=log['address']; amount_raw=int(log['data'],16)
                                info=get_token_info_quick(token_contract,chain)
                                if not info: continue
                                name,mcap,dex_link,price,_,liq = info
                                usd_val=(amount_raw/1e18*price) if price>0 else 0
                                if usd_val < 10: continue
                                last_tx_time=datetime.now()
                                if from_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["sells"]+=1; pnl_tracker[frm.lower()]["realized"]+=usd_val; save_pnl()
                                    if token_contract in holdings and frm in holdings[token_contract]: holdings[token_contract].pop(frm,None); save_holdings()
                                    print(f"ALERT {chain} SELL {name} ${usd_val:.2f}", flush=True)
                                    send_tg(f"🚨 *{chain} SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${usd_val:,.2f} sold by `{frm[:6]}...{frm[-4:]}`\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                                elif to_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["buys"]+=1; pnl_tracker[frm.lower()]["spent"]+=usd_val; save_pnl()
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    holdings[token_contract][frm]={"amount_usd": usd_val,"chain":chain,"token_name":name,"mcap":mcap}; save_holdings()
                                    print(f"ALERT {chain} BUY {name} ${usd_val:.2f}", flush=True)
                                    send_tg(f"💰 *{chain} BUY*\n🪙 {name} ({mcap})\n👤 `{frm[:6]}...{frm[-4:]}` | ${usd_val:,.2f}\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                                    threading.Thread(target=lambda: check_cluster_1d(token_contract,name,mcap,dex_link,chain),daemon=True).start()
                        except: pass
                except: pass
            await asyncio.sleep(5)
        except Exception as e: print(f"[{chain}] err {e}", flush=True); await asyncio.sleep(5)

# --- FULL COMMANDS RESTORED ---
def handle_command(text):
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        send_tg(f"🚀 *V3.11.23 FULL ACTIVE*\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ PUBLIC RPC no 429\n✅ Filtered $10+ | Liq $500+\n⏰ Last tx: {mins}m ago\nHoldings: {len(holdings)}\n\n/listwallets - wallets\n/pnl - PnL\n/overlap - find overlaps\n/history - holdings\n/addsol /addevm /delsol /delevm\n/testalert")
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL ({len(SOL_WALLETS)}):*\n{sol}\n\n*EVM ({len(EVM_WALLETS)}):*\n{evm}")[:4000])
    elif low.startswith("/pnl"):
        if not pnl_tracker: send_tg("📊 *PnL Board*\n\nNo trades yet.")
        else:
            total_spent=sum(d['spent'] for d in pnl_tracker.values()); total_real=sum(d['realized'] for d in pnl_tracker.values()); total_pnl=total_real-total_spent
            msg=f"📊 *PnL* Net: ${total_pnl:,.0f} {'🟢' if total_pnl>=0 else '🔴'}\nSpent: ${total_spent:,.0f} Realized: ${total_real:,.0f}\n\n"
            for w,d in sorted(pnl_tracker.items(), key=lambda x: x[1]['realized']-x[1]['spent'], reverse=True)[:15]:
                net=d['realized']-d['spent']; msg+=f"`{w[:6]}...` Net: ${net:+.0f} B:{d['buys']} S:{d['sells']}\n"
            send_tg(msg[:4000])
    elif low.startswith("/resetpnl"):
        pnl_tracker.clear(); save_pnl(); send_tg("🗑️ *PnL Reset done*")
    elif low.startswith("/addsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /addsol <addr>"); return
        addr=parts[1].strip()
        if addr not in SOL_WALLETS: SOL_WALLETS.append(addr); save_wallets(); send_tg(f"✅ Added SOL Total: {len(SOL_WALLETS)}")
        else: send_tg("Already exists")
    elif low.startswith("/addevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /addevm 0x..."); return
        addr=parts[1].strip()
        if addr.lower() not in [x.lower() for x in EVM_WALLETS]: EVM_WALLETS.append(addr); save_wallets(); send_tg(f"✅ Added EVM Total: {len(EVM_WALLETS)}")
        else: send_tg("Already exists")
    elif low.startswith("/delsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /delsol <addr>"); return
        addr=parts[1].strip()
        if addr in SOL_WALLETS: SOL_WALLETS.remove(addr); save_wallets(); send_tg(f"🗑️ Removed SOL Left: {len(SOL_WALLETS)}")
        else: send_tg("Not found")
    elif low.startswith("/delevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /delevm 0x..."); return
        addr=parts[1].strip().lower()
        found=[x for x in EVM_WALLETS if x.lower()==addr]
        if found: EVM_WALLETS.remove(found[0]); save_wallets(); send_tg(f"🗑️ Removed EVM Left: {len(EVM_WALLETS)}")
        else: send_tg("Not found")
    elif low.startswith("/history"):
        if not holdings: send_tg("📜 No holdings"); return
        msg="📜 *Holdings* (top 10)\n\n"; c=0
        for token, wallets_dict in holdings.items():
            if c>=10: break
            if not wallets_dict: continue
            sample=list(wallets_dict.values())[0]
            msg+=f"*{sample.get('token_name',token[:6])}* - {len(wallets_dict)} wallets ${sample.get('amount_usd',0):.0f}\n"
            c+=1
        send_tg(msg if c>0 else "All sold")
    elif low.startswith("/overlap"):
        try:
            counts=defaultdict(list)
            for mint, ws in holdings.items():
                for w in ws.keys(): counts[mint].append(w)
            overlaps={k:v for k,v in counts.items() if len(v)>=2}
            if not overlaps: send_tg("📊 No overlaps currently held")
            else:
                msg=f"🔍 *Overlap {len(overlaps)}*\n\n"
                for mint, ws in list(overlaps.items())[:6]:
                    sample=list(holdings[mint].values())[0]
                    msg+=f"*{sample.get('token_name',mint[:6])}* - {len(ws)} wallets\n"
                send_tg(msg[:4000])
        except Exception as e: send_tg(f"Overlap err {e}")
    elif low.startswith("/testalert"):
        send_tg("💰 *TEST V3.11.23 FULL WORKING*\nSOL PUBLIC + EVM + All 11 commands back + Filter $10+")

def set_bot_commands():
    cmds=[
        {"command":"start","description":"Status"},
        {"command":"listwallets","description":"List wallets"},
        {"command":"pnl","description":"PnL board"},
        {"command":"resetpnl","description":"Reset PnL"},
        {"command":"addsol","description":"Add SOL wallet"},
        {"command":"addevm","description":"Add EVM wallet"},
        {"command":"delsol","description":"Del SOL wallet"},
        {"command":"delevm","description":"Del EVM wallet"},
        {"command":"history","description":"Holdings"},
        {"command":"overlap","description":"Overlap scan"},
        {"command":"testalert","description":"Test alert"}
    ]
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",json={"commands":cmds},timeout=10)
    except: pass

# --- FIX FOR UPTIMEROBOT DOWN - self-ping ---
def heartbeat():
    while True:
        time.sleep(120)
        try:
            port=int(os.getenv("PORT",10000))
            try: requests.get(f"http://127.0.0.1:{port}/health",timeout=5)
            except: pass
            public_url=os.getenv("RENDER_EXTERNAL_URL","")
            if public_url:
                try: requests.get(public_url,timeout=8)
                except: pass
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            print(f"HEARTBEAT {mins}m ago + self-ping", flush=True)
        except: pass

async def main_loop():
    print(f">>> MAIN LOOP V3.11.23 FULL {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM", flush=True)
    threading.Thread(target=heartbeat, daemon=True).start()
    send_tg(f"🚀 *V3.11.23 FULL ACTIVE*\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ Min $10 + Liq $500+ = no Token Not Found\n✅ Photon link\n✅ All commands restored\n✅ Self-ping ON (no UptimeRobot needed)"); set_bot_commands()
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
                    msg=u.get("message",{})
                    txt=msg.get("text","")
                    if txt.startswith("/"):
                        handle_command(txt)
            except: time.sleep(4)
    threading.Thread(target=poll_cmd,daemon=True).start()
    time.sleep(1); start_bot()
