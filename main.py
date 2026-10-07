import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime
from collections import defaultdict
from web3 import Web3
import websockets

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
seen_sigs=set()
STABLES={"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}

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

HELIUS_KEY="3ac60377-a024-4177-8ef4-b8c36a692a57"
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
def home(): return "Shok V3.11.11 CLEAR PNL - NO DUPE",200
@app.route('/health')
def health(): return "OK",200
def run_flask(): app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: print(text[:1200]); return
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True},timeout=15)
    except: pass

def set_bot_commands():
    if not BOT_TOKEN: return
    cmds=[{"command":"start","description":"Status"},{"command":"listwallets","description":"List"},{"command":"pnl","description":"Clear PnL board"},{"command":"resetpnl","description":"Reset PnL stats"},{"command":"addsol","description":"Add SOL"},{"command":"addevm","description":"Add EVM"},{"command":"delsol","description":"Del SOL"},{"command":"delevm","description":"Del EVM"},{"command":"history","description":"History"},{"command":"overlap","description":"Overlap"},{"command":"testalert","description":"Test"}]
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",json={"commands":cmds},timeout=10)
    except: pass

def get_token_info_quick(token_address, chain_hint="SOL"):
    if token_address in STABLES: return None
    for attempt in range(2):
        try:
            r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",timeout=4).json()
            pairs=r.get("pairs",[])
            if pairs:
                best=sorted(pairs, key=lambda x: x.get("liquidity",{}).get("usd",0), reverse=True)[0]
                name=best.get("baseToken",{}).get("symbol", token_address[:6])
                fdv=best.get("fdv") or best.get("marketCap") or 0
                mcap=f"${fdv/1_000_000:.2f}M" if fdv>=1_000_000 else f"${fdv/1000:.1f}k" if fdv>=1000 else f"${fdv:.0f}" if fdv else "New"
                price=float(best.get("priceUsd",0) or 0)
                dex_link=f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress',token_address)}"
                if price>0 or attempt==1:
                    return name, mcap, dex_link, price, best.get('chainId','SOL').upper()
        except: pass
        if attempt==0: time.sleep(1.2)
    try:
        pr=requests.get(f"https://frontend-api-v2.pump.fun/coins/{token_address}",timeout=3).json()
        name=pr.get("symbol", token_address[:6])
        mcap_val=pr.get("usd_market_cap",0)
        mcap=f"${mcap_val/1_000_000:.2f}M" if mcap_val>=1_000_000 else f"${mcap_val/1000:.1f}k" if mcap_val else "New"
        return name, mcap, f"https://dexscreener.com/solana/{token_address}", 0, "SOL"
    except: pass
    return token_address[:6], "New", f"https://dexscreener.com/{chain_hint.lower()}/{token_address}", 0, chain_hint

def short(a): return f"{a[:4]}...{a[-4:]}" if a else "?"
def get_helius_holdings(wallet):
    try: return requests.get(f"https://api.helius.xyz/v0/addresses/{wallet}/balances?api-key={HELIUS_KEY}",timeout=10).json().get("tokens",[])
    except: return []
def get_evm_holdings_blockscout(wallet, base_url):
    try:
        r=requests.get(f"{base_url}/api/v2/addresses/{wallet}/token-balances",timeout=8).json()
        return r if isinstance(r,list) else []
    except: return []

def live_scan_overlap():
    SCAN_MAP={"SOL":"https://solscan.io/account/","BASE":"https://basescan.org/address/","ETH":"https://etherscan.io/address/","BSC":"https://bscscan.com/address/","ARB":"https://arbiscan.io/address/","POLY":"https://polygonscan.com/address/"}
    token_to_wallets=defaultdict(list)
    for sol_w in SOL_WALLETS:
        try:
            for t in get_helius_holdings(sol_w):
                mint=t.get("mint"); amt=t.get("amount",0)
                if not mint or mint in STABLES or amt==0: continue
                token_to_wallets[f"SOL:{mint}"].append({"mint":mint,"wallet":sol_w,"amount":amt,"decimals":t.get("decimals",6),"chain":"SOL","source":"live"})
        except: continue
    EVM_SCN={"ETH":"https://eth.blockscout.com","BASE":"https://base.blockscout.com","BSC":"https://bsc.blockscout.com","ARB":"https://arbitrum.blockscout.com","POLY":"https://polygon.blockscout.com"}
    for evm_w in EVM_WALLETS:
        for chain, base_url in EVM_SCN.items():
            try:
                for h in get_evm_holdings_blockscout(evm_w, base_url)[:200]:
                    tok=h.get("token",{}); t_addr=tok.get("address_hash") or (tok.get("address",{}).get("hash") if isinstance(tok.get("address"),dict) else tok.get("address"))
                    if not t_addr or t_addr.lower()=="0x0000000000000000000000000000000000000000": continue
                    try: val=int(h.get("value",0))
                    except: continue
                    if val==0: continue
                    dec=int(tok.get("decimals",18) or 18)
                    if dec==0: continue
                    real=val/(10**dec) if dec else 0
                    if real in [1.0,100000.0] or real<0.000001: continue
                    token_to_wallets[f"{chain}:{t_addr.lower()}"].append({"mint":t_addr,"wallet":evm_w,"amount":val,"decimals":dec,"chain":chain,"source":"live"})
            except: continue
    now=datetime.now()
    for token, events in cluster_memory.items():
        if token in STABLES: continue
        recent=[e for e in events if (now-e[1]).days<=7]
        if len(recent)<2 or len(set([w.lower() for w,_,_ in recent]))<2: continue
        for w,ts,ch in recent:
            key=f"{ch}:{token.lower()}" if ch!="SOL" else f"SOL:{token}"
            if any(x["wallet"].lower()==w.lower() for x in token_to_wallets.get(key,[])): continue
            token_to_wallets[key].append({"mint":token,"wallet":w,"amount":0,"decimals":18,"chain":ch,"source":"history","time":ts})
    final={}
    for key, holders in token_to_wallets.items():
        uniq={h["wallet"].lower():h for h in holders}
        if len(uniq)<2: continue
        chain=list(uniq.values())[0]["chain"]; mint=list(uniq.values())[0]["mint"]
        info=get_token_info_quick(mint,chain)
        if not info: continue
        name,mcap,dex_link,price,_=info
        if price==0 and mcap=="New": continue
        if price>0 and price<0.00000001: continue
        good=[h for h in uniq.values() if h.get("source")=="history" or (price>0 and h["amount"]/(10**h["decimals"])*price>=5) or h["amount"]>0]
        if len(good)<2: continue
        final[key]=(chain,mint,name,mcap,dex_link,price,good)
    if not final: return "📊 *No real overlaps in 7d*"
    msg=f"🔍 *OVERLAP {len(final)} REAL*\n\n"; c=0
    for key,(chain,mint,name,mcap,dex_link,price,holders_list) in sorted(final.items(),key=lambda x: len(x[1][6]),reverse=True)[:10]:
        msg+=f"*{c+1}) {name} - {mcap}* {chain}\n"
        for h in holders_list[:6]:
            w=h["wallet"]; usd=h["amount"]/(10**h["decimals"])*price if price>0 and h.get("source")!="history" else 0
            scan_url = SCAN_MAP.get(chain,"https://basescan.org/address/") + w
            msg+=f"• `{short(w)}` - ${usd:,.0f} - [Scan]({scan_url})\n"
        msg+=f"📈 [Chart]({dex_link})\n\n"; c+=1
        if len(msg)>3800: break
    return msg[:4000]

def handle_command(text):
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        send_tg(f"🚀 *V3.11.11 CLEAR PNL*\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ NO DUPE 1/tx\n✅ Clear PnL + /resetpnl\n✅ WS+POLL Dual"); set_bot_commands()
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL:*\n{sol}\n\n*EVM:*\n{evm}")[:4000])
    elif low.startswith("/overlap"):
        send_tg("⏳ Scanning...")
        threading.Thread(target=lambda: send_tg(live_scan_overlap()),daemon=True).start()
    elif low.startswith("/resetpnl"):
        pnl_tracker.clear(); save_pnl()
        send_tg("🗑️ *PnL Reset*\nAll stats cleared. Tracking fresh from now.")
    elif low.startswith("/pnl"):
        if not pnl_tracker: send_tg("📊 *PnL Board*\n\nNo trades tracked yet.\nStart: bot is tracking from now.")
        else:
            total_spent=sum(d['spent'] for d in pnl_tracker.values())
            total_real=sum(d['realized'] for d in pnl_tracker.values())
            total_pnl=total_real-total_spent
            msg=f"📊 *PnL Board - Since Bot Started*\n"
            msg+=f"Total Spent: ${total_spent:,.0f} | Total Realized: ${total_real:,.0f} | Net: ${total_pnl:,.0f} {'🟢' if total_pnl>=0 else '🔴'}\n\n"
            # sort by net profit
            sorted_wallets=sorted(pnl_tracker.items(), key=lambda x: x[1]['realized']-x[1]['spent'], reverse=True)[:12]
            for w,d in sorted_wallets:
                net=d['realized']-d['spent']
                status="🟢 PROFIT" if net>=0 else "🔴 LOSS"
                # explain B:0 case
                note=""
                if d['buys']==0 and d['sells']>0: note=" (sold old bag)"
                elif d['sells']==0 and d['buys']>0: note=" (holding)"
                msg+=f"`{w[:6]}...{w[-4:]}` {status}{note}\n"
                msg+=f" Buys: {d['buys']} | Sells: {d['sells']}\n"
                msg+=f" Spent: ${d['spent']:.0f} | Got: ${d['realized']:.0f}\n"
                msg+=f" Net: ${net:+.0f}\n\n"
            msg+=f"_Note: B:0 S:1 = sold token bought before tracking started_"
            send_tg(msg[:4000])
    elif low.startswith("/addsol"):
        try:
            addr=t.split()[1].strip()
            if addr not in SOL_WALLETS: SOL_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added SOL Total: {len(SOL_WALLETS)}")
        except: send_tg("Usage: /addsol <addr>")
    elif low.startswith("/addevm"):
        try:
            addr=t.split()[1].strip()
            if addr.lower() not in [x.lower() for x in EVM_WALLETS]: EVM_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added EVM Total: {len(EVM_WALLETS)}")
        except: send_tg("Usage: /addevm 0x...")
    elif low.startswith("/delsol"):
        try:
            addr=t.split()[1].strip()
            if addr in SOL_WALLETS: SOL_WALLETS.remove(addr); save_wallets(); send_tg(f"🗑️ Removed Left: {len(SOL_WALLETS)}")
        except: send_tg("Usage: /delsol <addr>")
    elif low.startswith("/delevm"):
        try:
            addr=t.split()[1].strip().lower()
            found=[x for x in EVM_WALLETS if x.lower()==addr]
            if found: EVM_WALLETS.remove(found[0]); save_wallets(); send_tg(f"🗑️ Removed Left: {len(EVM_WALLETS)}")
        except: send_tg("Usage: /delevm 0x...")
    elif low.startswith("/history"):
        if not holdings: send_tg("No clusters yet"); return
        msg="📜 *Holders*\n\n"; shown=0
        for token,wallets_dict in holdings.items():
            if len(wallets_dict)<2 or token in STABLES: continue
            sample=list(wallets_dict.values())[0]; info=get_token_info_quick(token,sample.get("chain","?"))
            if not info: continue
            name2,mcap,dex_link,_,_=info
            msg+=f"🪙 *{name2}* ({mcap})\n"
            for w_addr,data in wallets_dict.items(): msg+=f"• ${data.get('amount_usd',0):.0f} - `{w_addr[:6]}...`\n"
            msg+=f"[Chart]({dex_link})\n\n"; shown+=1
            if shown>=6: break
        send_tg(msg[:4000] if shown else "All sold")
    elif low.startswith("/testalert"):
        send_tg("💰 *TEST*\n✅ V3.11.11 CLEAR PNL working")

def get_sol_parsed(sig):
    try:
        r=requests.post(f"https://api.helius.xyz/v0/transactions/?api-key={HELIUS_KEY}",json={"transactions":[sig]},timeout=10).json()
        return r[0] if r and isinstance(r,list) else None
    except: return None

def process_sol_tx(wallet_list, tx_obj, sig, source):
    if sig in seen_sigs: return
    transfers = [tr for tr in tx_obj.get("tokenTransfers",[]) if tr.get("mint") not in STABLES]
    relevant=[]
    for tr in transfers:
        mint=tr.get("mint",""); from_u=tr.get("fromUserAccount",""); to_u=tr.get("toUserAccount",""); amt=float(tr.get("tokenAmount",0) or 0)
        if not mint or amt==0: continue
        if from_u not in wallet_list and to_u not in wallet_list: continue
        info=get_token_info_quick(mint,"SOL")
        if not info: continue
        name,mcap,dex_link,price,_=info
        usd=amt*price if price>0 else 0
        if price>0 and usd>0 and usd<10: continue
        relevant.append((mint,from_u,to_u,amt,name,mcap,dex_link,price,usd))
    if not relevant: return
    relevant.sort(key=lambda x: x[8] if x[8]>0 else x[3], reverse=True)
    mint,from_u,to_u,amt,name,mcap,dex_link,price,usd = relevant[0]
    seen_sigs.add(sig)
    if len(seen_sigs)>800: seen_sigs.clear()
    target = from_u if from_u in wallet_list else to_u
    is_buy = to_u == target
    display_usd = usd if usd>0 else amt
    if is_buy:
        pnl_tracker[target.lower()]["buys"]+=1; pnl_tracker[target.lower()]["spent"]+= (usd if usd>0 else 50); save_pnl()
        cluster_memory[mint].append((target,datetime.now(),"SOL")); save_memory()
        holdings[mint][target]={"amount_usd":display_usd,"chain":"SOL","token_name":name,"mcap":mcap}; save_holdings()
        send_tg(f"💰 *SOL BUY*\n🪙 {name} ({mcap})\n👤 `{target[:6]}...{target[-4:]}` | ${display_usd:,.2f}\n📊 [Chart]({dex_link}) | [Tx](https://solscan.io/tx/{sig}) [{source}]")
    else:
        pnl_tracker[target.lower()]["sells"]+=1; pnl_tracker[target.lower()]["realized"]+= (usd if usd>0 else 50); save_pnl()
        if mint in holdings and target in holdings[mint]: holdings[mint].pop(target,None); save_holdings()
        send_tg(f"🚨 *SOL SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${display_usd:,.2f} sold by `{target[:6]}...{target[-4:]}`\n📊 [Chart]({dex_link}) | [Tx](https://solscan.io/tx/{sig}) [{source}]")

async def track_sol_polling():
    print("🔵 POLLING STARTED")
    last_sig={}
    while True:
        for w in SOL_WALLETS:
            try:
                url=f"https://api.helius.xyz/v0/addresses/{w}/transactions?api-key={HELIUS_KEY}&limit=3"
                txs=requests.get(url,timeout=10).json()
                if not isinstance(txs,list): continue
                for tx in txs[:2]:
                    sig=tx.get("signature")
                    if not sig or sig in seen_sigs: continue
                    if last_sig.get(w)==sig: break
                    process_sol_tx([w], tx, sig, "POLL")
                if txs: last_sig[w]=txs[0].get("signature")
            except Exception as e: print(f"poll e {e}")
        await asyncio.sleep(2.0)

async def track_chain(chain):
    seen=set(); scan_map={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}
    scan=scan_map[chain]; tracked_lower=set([x.lower() for x in EVM_WALLETS])
    while True:
        try:
            w3=get_w3_with_fallback(chain)
            if not w3: await asyncio.sleep(10); continue
            bn=w3.eth.block_number
            for b in range(max(0,bn-3), bn+1):
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
                                name,mcap,dex_link,price,_=info
                                usd_val=(amount_raw/1e18*price) if price>0 else 50
                                if price>0 and usd_val>0 and usd_val<10: continue
                                if from_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["sells"]+=1; pnl_tracker[frm.lower()]["realized"]+=usd_val; save_pnl()
                                    if token_contract in holdings and frm in holdings[token_contract]: holdings[token_contract].pop(frm,None); save_holdings()
                                    send_tg(f"🚨 *SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${usd_val:,.2f} `{frm[:6]}...`\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                                elif to_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["buys"]+=1; pnl_tracker[frm.lower()]["spent"]+=usd_val; save_pnl()
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    holdings[token_contract][frm]={"amount_usd": usd_val,"chain":chain,"token_name":name,"mcap":mcap}; save_holdings()
                                    send_tg(f"💰 *{chain} BUY*\n🪙 {name} ({mcap})\n👤 `{frm[:6]}...` | ${usd_val:,.2f}\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                        except: pass
                except: pass
            await asyncio.sleep(1.5)
        except Exception as e: print(f"[{chain}] err {e}"); await asyncio.sleep(5)

async def track_sol():
    uri=f"wss://atlas-mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions": SOL_WALLETS},{"commitment":"confirmed"}]}))
                print("🟢 WS CONNECTED")
                async for msg in ws:
                    try:
                        data=json.loads(msg)
                        if "params" not in data: continue
                        sig=data["params"]["result"]["value"].get("signature","")
                        if not sig or sig in seen_sigs: continue
                        await asyncio.sleep(1.2)
                        parsed=get_sol_parsed(sig)
                        if not parsed: continue
                        process_sol_tx(SOL_WALLETS, parsed, sig, "WS")
                    except Exception as e: print(f"SOL err {e}")
        except Exception as e: print(f"[SOL] WS err {e}"); await asyncio.sleep(5)

async def main_loop():
    send_tg(f"🚀 *V3.11.11 CLEAR PNL ACTIVE*\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ Clear PnL format + /resetpnl\n✅ 1 alert per tx\n✅ WS+POLL Dual"); set_bot_commands()
    tasks=[track_chain(c) for c in RPCS_FALLBACK.keys()]
    tasks.append(track_sol())
    tasks.append(track_sol_polling())
    await asyncio.gather(*tasks)

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(main_loop())

if __name__=="__main__":
    threading.Thread(target=run_flask,daemon=True).start()
    def poll_cmd():
        off=0
        while True:
            try:
                if not BOT_TOKEN: time.sleep(5); continue
                r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=10",timeout=15).json()
                for u in r.get("result",[]):
                    off=u["update_id"]+1; txt=u.get("message",{}).get("text","")
                    if txt.startswith("/"): handle_command(txt)
            except: time.sleep(4)
    threading.Thread(target=poll_cmd,daemon=True).start(); start_bot()
