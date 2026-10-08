import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
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

# --- FIX: ONLY ENV KEYS, NO HARDCODED BURNED KEYS ---
_raw_keys=[os.getenv("HELIUS_KEY",""), os.getenv("HELIUS_KEY_2",""), os.getenv("HELIUS_KEY_3","")]
HELIUS_KEYS=[k.strip() for k in _raw_keys if k and len(k)>20]
if not HELIUS_KEYS:
    HELIUS_KEYS=["9c731b63-ffc1-4e5e-b2f5-ce2aa9b9bf82"] # fallback, will be 429 until you set env
key_idx=0
def get_helius_key():
    global key_idx
    k=HELIUS_KEYS[key_idx % len(HELIUS_KEYS)]
    key_idx+=1
    return k

SOLANA_PUB_RPCS=["https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com","https://solana.llamarpc.com"]
rpc_idx=0
def get_public_rpc():
    global rpc_idx
    rpc_idx+=1
    return SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]

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
def home(): return "Shok V3.11.19 PUBLIC POLL FIXED",200
@app.route('/health')
def health(): return "OK",200
@app.route('/debug')
def debug():
    try:
        out=f"Keys: {len(HELIUS_KEYS)}\n"
        for i,k in enumerate(HELIUS_KEYS):
            url=f"https://api.helius.xyz/v0/addresses/{SOL_WALLETS[0]}/transactions?api-key={k}&limit=1"
            r=requests.get(url,timeout=10)
            out+=f"Key{i} {k[:8]} Status:{r.status_code}\n"
        return f"{out}\nBOT:{bool(BOT_TOKEN)} Holdings:{len(holdings)} Seen:{len(seen_sigs)} LastTx:{int((datetime.now()-last_tx_time).total_seconds()/60)}m",200
    except Exception as e: return f"DEBUG ERR {e}",200

def run_flask():
    app.run(host='0.0.0.0',port=int(os.getenv("PORT",10000)))
def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True},timeout=15)
    except: pass
def set_bot_commands():
    if not BOT_TOKEN: return
    cmds=[{"command":"start","description":"Status"},{"command":"listwallets","description":"List"},{"command":"pnl","description":"PnL"},{"command":"resetpnl","description":"Reset"},{"command":"addsol","description":"Add SOL"},{"command":"addevm","description":"Add EVM"},{"command":"delsol","description":"Del SOL"},{"command":"delevm","description":"Del EVM"},{"command":"history","description":"History"},{"command":"overlap","description":"Overlap"},{"command":"testalert","description":"Test"}]
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
                if price>0 or attempt==1: return name, mcap, dex_link, price, best.get('chainId','SOL').upper()
        except: pass
        if attempt==0: time.sleep(1.2)
    try:
        pr=requests.get(f"https://frontend-api-v2.pump.fun/coins/{token_address}",timeout=3).json()
        name=pr.get("symbol", token_address[:6]); mcap_val=pr.get("usd_market_cap",0)
        mcap=f"${mcap_val/1_000_000:.2f}M" if mcap_val>=1_000_000 else f"${mcap_val/1000:.1f}k" if mcap_val else "New"
        return name, mcap, f"https://dexscreener.com/solana/{token_address}", 0, "SOL"
    except: pass
    return token_address[:6], "New", f"https://dexscreener.com/{chain_hint.lower()}/{token_address}", 0, chain_hint
def short(a): return f"{a[:4]}...{a[-4:]}" if a else "?"

def get_helius_holdings(wallet): return [] # DISABLED to save credits

def get_evm_holdings_blockscout(wallet, base_url):
    try:
        r=requests.get(f"{base_url}/api/v2/addresses/{wallet}/token-balances",timeout=8).json()
        return r if isinstance(r,list) else []
    except: return []

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

def live_scan_overlap(): return "📊 *Overlap disabled to save credits - live alerts work*"

def handle_command(text):
    t=text.strip(); low=t.lower()
    if low.startswith("/start"):
        mins=int((datetime.now()-last_tx_time).total_seconds()/60)
        send_tg(f"🚀 *V3.11.19 PUBLIC RPC*\n{len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ NO FILTER\n✅ Public RPC polling = NO 429\n✅ Helius only for parse\n⏰ Last tx: {mins}m ago\nKeys: {len(HELIUS_KEYS)}"); set_bot_commands()
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL:*\n{sol}\n\n*EVM:*\n{evm}")[:4000])
    elif low.startswith("/overlap"): send_tg("⏳ Overlap disabled to save Helius credits")
    elif low.startswith("/resetpnl"): pnl_tracker.clear(); save_pnl(); send_tg("🗑️ *PnL Reset*")
    elif low.startswith("/pnl"):
        if not pnl_tracker: send_tg("📊 *No trades yet*")
        else:
            total_spent=sum(d['spent'] for d in pnl_tracker.values()); total_real=sum(d['realized'] for d in pnl_tracker.values()); total_pnl=total_real-total_spent
            msg=f"📊 *PnL* Net: ${total_pnl:,.0f}\n\n"
            for w,d in sorted(pnl_tracker.items(), key=lambda x: x[1]['realized']-x[1]['spent'], reverse=True)[:10]:
                net=d['realized']-d['spent']
                msg+=f"`{w[:6]}...` Net: ${net:+.0f} B:{d['buys']} S:{d['sells']}\n"
            send_tg(msg[:4000])
    elif low.startswith("/testalert"): send_tg("💰 *TEST V3.11.19 PUBLIC RPC WORKING*")

def get_sol_parsed(sig):
    for _ in range(len(HELIUS_KEYS)):
        k=get_helius_key()
        try:
            r=requests.post(f"https://api.helius.xyz/v0/transactions/?api-key={k}",json={"transactions":[sig]},timeout=12)
            if r.status_code==429:
                print(f"429 parse rotate sleep", flush=True)
                time.sleep(2)
                continue
            if r.status_code!=200 or not r.text.strip(): continue
            j=r.json()
            return j[0] if j and isinstance(j,list) else None
        except: continue
    return None

def process_sol_tx(wallet_list, tx_obj, sig, source):
    global last_tx_time
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
        relevant.append((mint,from_u,to_u,amt,name,mcap,dex_link,price,usd))
    if not relevant: return
    relevant.sort(key=lambda x: x[8] if x[8]>0 else x[3], reverse=True)
    mint,from_u,to_u,amt,name,mcap,dex_link,price,usd = relevant[0]
    seen_sigs.add(sig)
    if len(seen_sigs)>500: seen_sigs.clear()
    target = from_u if from_u in wallet_list else to_u
    is_buy = to_u == target
    display_usd = usd if usd>0 else amt
    last_tx_time=datetime.now()
    print(f"ALERT {source} {name} {'BUY' if is_buy else 'SELL'} ${display_usd:.2f}", flush=True)
    if is_buy:
        pnl_tracker[target.lower()]["buys"]+=1; pnl_tracker[target.lower()]["spent"]+= (usd if usd>0 else 50); save_pnl()
        cluster_memory[mint].append((target,datetime.now(),"SOL")); save_memory()
        holdings[mint][target]={"amount_usd":display_usd,"chain":"SOL","token_name":name,"mcap":mcap}; save_holdings()
        send_tg(f"💰 *SOL BUY*\n🪙 {name} ({mcap})\n👤 `{target[:6]}...{target[-4:]}` | ${display_usd:,.2f}\n📊 [Chart]({dex_link}) | [Tx](https://solscan.io/tx/{sig}) [{source}]")
        threading.Thread(target=lambda: check_cluster_1d(mint,name,mcap,dex_link,"SOL"),daemon=True).start()
    else:
        pnl_tracker[target.lower()]["sells"]+=1; pnl_tracker[target.lower()]["realized"]+= (usd if usd>0 else 50); save_pnl()
        if mint in holdings and target in holdings[mint]: holdings[mint].pop(target,None); save_holdings()
        send_tg(f"🚨 *SOL SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${display_usd:,.2f} sold by `{target[:6]}...{target[-4:]}`\n📊 [Chart]({dex_link}) | [Tx](https://solscan.io/tx/{sig}) [{source}]")

async def track_sol_polling():
    print("🔵 PUBLIC RPC POLLING 6s - 0 HELIUS CREDITS", flush=True)
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
                    print(f"Found sig {sig[:10]} for {w[:6]} via PUBLIC", flush=True)
                    parsed=get_sol_parsed(sig)
                    if not parsed:
                        print(f"Parse fail {sig[:10]} - Helius 429?", flush=True)
                        continue
                    process_sol_tx([w], parsed, sig, "PUBLIC")
            except Exception as e:
                print(f"public poll err {e}", flush=True)
        await asyncio.sleep(6)

async def track_chain(chain):
    global last_tx_time
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
                                last_tx_time=datetime.now()
                                if from_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["sells"]+=1; pnl_tracker[frm.lower()]["realized"]+=usd_val; save_pnl()
                                    send_tg(f"🚨 *SELL* 🚨\n🪙 {name} ({mcap})\n💸 ${usd_val:,.2f} `{frm[:6]}...`\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                                elif to_addr.lower()==frm.lower():
                                    pnl_tracker[frm.lower()]["buys"]+=1; pnl_tracker[frm.lower()]["spent"]+=usd_val; save_pnl()
                                    cluster_memory[token_contract].append((frm,datetime.now(),chain)); save_memory()
                                    send_tg(f"💰 *{chain} BUY*\n🪙 {name} ({mcap})\n👤 `{frm[:6]}...` | ${usd_val:,.2f}\n📊 [Chart]({dex_link}) | [Tx](https://{scan}/tx/{h})")
                        except: pass
                except: pass
            await asyncio.sleep(6)
        except Exception as e: print(f"[{chain}] err {e}", flush=True); await asyncio.sleep(5)

async def track_sol():
    while True:
        if not HELIUS_KEYS: await asyncio.sleep(10); continue
        k=get_helius_key()
        uri=f"wss://atlas-mainnet.helius-rpc.com/?api-key={k}"
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions": SOL_WALLETS},{"commitment":"confirmed"}]}))
                print(f"🟢 WS CONNECTED {k[:8]}", flush=True)
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
                    except Exception as e: print(f"SOL err {e}", flush=True)
        except Exception as e: print(f"[SOL] WS err {e} rotate", flush=True); await asyncio.sleep(8)

def boot_restore():
    print(f"BOOT SKIP holdings to save credits - {len(holdings)} loaded from file", flush=True)

def heartbeat():
    while True:
        time.sleep(300)
        try:
            requests.get(f"http://127.0.0.1:{int(os.getenv('PORT',10000))}/health",timeout=5)
            mins=int((datetime.now()-last_tx_time).total_seconds()/60)
            print(f"HEARTBEAT {mins}m ago", flush=True)
        except: pass

async def main_loop():
    print(">>> MAIN LOOP STARTING V3.11.19", flush=True)
    threading.Thread(target=boot_restore, daemon=True).start()
    threading.Thread(target=heartbeat, daemon=True).start()
    send_tg(f"🚀 *V3.11.19 PUBLIC FIXED*\n{len(SOL_WALLETS)} SOL\n✅ Public RPC polling = NO 429\n✅ Helius only for parse"); set_bot_commands()
    tasks=[track_chain(c) for c in RPCS_FALLBACK.keys()]
    tasks.append(track_sol()); tasks.append(track_sol_polling())
    await asyncio.gather(*tasks)

def start_bot():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    try: loop.run_until_complete(main_loop())
    except Exception as e:
        print(f"!!! CRASHED {e}", flush=True); time.sleep(5); start_bot()

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
            except Exception as e: print(f"poll_cmd err {e}", flush=True); time.sleep(4)
    threading.Thread(target=poll_cmd,daemon=True).start()
    time.sleep(1); start_bot()
