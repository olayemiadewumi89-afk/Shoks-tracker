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

WALLETS_FILE = "wallets.json"
def load_wallets():
    global SOL_WALLETS, EVM_WALLETS
    try:
        if os.path.exists(WALLETS_FILE):
            with open(WALLETS_FILE, "r") as f:
                data=json.load(f)
                SOL_WALLETS=data.get("sol",SOL_WALLETS)
                EVM_WALLETS=data.get("evm",EVM_WALLETS)
    except: pass
def save_wallets():
    try:
        with open(WALLETS_FILE,"w") as f:
            json.dump({"sol":SOL_WALLETS,"evm":EVM_WALLETS},f)
    except: pass
load_wallets()

HELIUS_KEY = "3ac60377-a024-4177-8ef4-b8c36a692a57"

RPCS_FALLBACK = {
    "ETH": ["https://ethereum-rpc.publicnode.com","https://eth.llamarpc.com","https://rpc.ankr.com/eth","https://1rpc.io/eth"],
    "BSC": ["https://bsc-rpc.publicnode.com","https://bsc.llamarpc.com","https://rpc.ankr.com/bsc"],
    "BASE": ["https://base-rpc.publicnode.com","https://base.llamarpc.com","https://rpc.ankr.com/base","https://1rpc.io/base"],
    "ARB": ["https://arbitrum-one-rpc.publicnode.com","https://arbitrum.llamarpc.com","https://rpc.ankr.com/arbitrum"],
    "POLY": ["https://polygon-bor-rpc.publicnode.com","https://polygon.llamarpc.com","https://rpc.ankr.com/polygon"]
}

def get_w3_with_fallback(chain):
    for url in RPCS_FALLBACK.get(chain, []):
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout':6}))
            if w3.is_connected():
                return w3
        except: continue
    return None

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)
@app.route('/')
def home(): return "Shok Tracker V2.8.0 Overlap",200
@app.route('/health')
def health(): return "OK",200
def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT",10000)))

cluster_memory = defaultdict(list)
holdings = defaultdict(dict)
pnl_tracker = defaultdict(lambda: {"buys":0,"sells":0,"pnl":0})

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text[:1000]); return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True}, timeout=10)
    except Exception as e:
        print(f"TG err {e}")

def set_bot_commands():
    if not BOT_TOKEN: return
    cmds=[
        {"command":"start","description":"Status & chains"},
        {"command":"listwallets","description":"List wallets"},
        {"command":"pnl","description":"PnL board"},
        {"command":"addsol","description":"Add SOL: /addsol <addr>"},
        {"command":"addevm","description":"Add EVM: /addevm 0x..."},
        {"command":"delsol","description":"Del SOL: /delsol <addr>"},
        {"command":"delevm","description":"Del EVM: /delevm 0x..."},
        {"command":"history","description":"Current cluster holders"},
        {"command":"overlap","description":"Find 2+ wallets holding same coin"},
        {"command":"testalert","description":"Test buy/sell format"},
    ]
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
    except: pass

def get_token_info_quick(token_address, chain_hint="base"):
    chain_map={"ETH":"ethereum","BSC":"bsc","BASE":"base","ARB":"arbitrum","POLY":"polygon","SOL":"solana"}
    ds_chain=chain_map.get(chain_hint, chain_hint.lower())
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=6).json()
        pairs=r.get("pairs",[])
        if pairs:
            best=sorted(pairs, key=lambda x: x.get("liquidity",{}).get("usd",0), reverse=True)[0]
            name=best.get("baseToken",{}).get("symbol", token_address[:6])
            fdv=best.get("fdv") or best.get("marketCap") or 0
            if fdv>=1_000_000: mcap=f"${fdv/1_000_000:.2f}M"
            elif fdv>=1000: mcap=f"${fdv/1000:.1f}k"
            else: mcap=f"${fdv:.0f}" if fdv else "N/A"
            price=float(best.get("priceUsd",0) or 0)
            pair_addr=best.get("pairAddress",token_address)
            chain_id=best.get("chainId",ds_chain)
            dex_link=f"https://dexscreener.com/{chain_id}/{pair_addr}"
            return name, mcap, dex_link, price, chain_id.upper()
    except: pass
    return token_address[:6], "N/A", f"https://dexscreener.com/{ds_chain}/{token_address}", 0, chain_hint

def short(a):
    if not a: return "?"
    return f"{a[:4]}...{a[-4:]}"

def build_overlap_report():
    # combine current holdings + cluster memory for previous holds
    token_to_wallets = defaultdict(list)

    # from current holdings
    for token, wallets_dict in holdings.items():
        for w_addr, data in wallets_dict.items():
            token_to_wallets[token].append({
                "wallet": w_addr,
                "chain": data.get("chain","?"),
                "usd": data.get("amount_usd",0),
                "name": data.get("token_name", token[:6]),
                "mcap": data.get("mcap","N/A"),
                "status": "holding"
            })

    # from cluster_memory (previous buys)
    for token, events in cluster_memory.items():
        for w, ts, chain in events:
            # avoid dupe if already holding
            if any(x["wallet"]==w for x in token_to_wallets[token]):
                continue
            token_to_wallets[token].append({
                "wallet": w,
                "chain": chain,
                "usd": 0,
                "name": token[:6],
                "mcap": "N/A",
                "status": "prev"
            })

    overlaps = {t:ws for t,ws in token_to_wallets.items() if len(ws)>=2}

    if not overlaps:
        return "📊 *No overlaps yet*\n\nNo coin is held by 2+ tracked wallets (current or previous 24h).\nWaiting for buys..."

    msg = f"🔍 *OVERLAP SCAN*\nFound {len(overlaps)} coin(s) held by 2+ wallets\n\n"
    count=0
    for token, wallets in sorted(overlaps.items(), key=lambda x: len(x[1]), reverse=True):
        if count>=10: break
        # get fresh info
        sample_chain = wallets[0].get("chain","BASE")
        name, mcap, dex_link, price, chain_id = get_token_info_quick(token, sample_chain)

        # explorer for token
        scan_map = {"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com","SOL":"solscan.io"}
        chain_key = chain_id if chain_id in scan_map else sample_chain
        explorer_token = f"https://{scan_map.get(chain_key,'basescan.org')}/token/{token}" if chain_key!="SOL" else f"https://solscan.io/token/{token}"
        explorer_chain_link = f"https://dexscreener.com/{chain_id.lower()}/{token}"

        msg+=f"*{count+1}) {name} - {mcap}*\n"
        msg+=f"Chain: {chain_key}\n"
        msg+=f"Token: `{short(token)}` - [View]({explorer_token})\n"
        msg+=f"Holders ({len(wallets)}):\n"
        for h in wallets[:6]:
            w = h["wallet"]
            usd = h["usd"]
            status = "holding" if h["status"]=="holding" else "prev held"
            scan_url = f"https://solscan.io/account/{w}" if len(w)<50 else f"https://{scan_map.get(chain_key,'basescan.org')}/address/{w}"
            if usd>0:
                msg+=f"• `{short(w)}` - ${usd:.0f} ({status}) - [Scan]({scan_url})\n"
            else:
                msg+=f"• `{short(w)}` - {status} - [Scan]({scan_url})\n"
        msg+=f"📈 [Chart]({dex_link})\n\n"
        count+=1

    return msg[:4000]

def handle_command(text):
    t=text.strip()
    low=t.lower()
    if low.startswith("/start"):
        chains=", ".join(RPCS_FALLBACK.keys())
        send_tg(f"🚀 *Shok Tracker V2.8.0 ACTIVE*\n\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x {len(RPCS_FALLBACK)} chains\nChains: {chains}\n\n✅ Sell >$500 + mcap\n✅ Short clickable [Chart] | [Tx]\n✅ Fallback RPCs\n✅ Add/Del wallets\n✅ /overlap\n\nPress / to see commands")
        set_bot_commands()
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL ({len(SOL_WALLETS)}):*\n{sol}\n\n*EVM ({len(EVM_WALLETS)}):*\n{evm}")[:4000])
    elif low.startswith("/overlap"):
        send_tg("⏳ Scanning for overlaps across 36 wallets...")
        report = build_overlap_report()
        send_tg(report)
    elif low.startswith("/pnl"):
        if not pnl_tracker: send_tg("📊 No PnL yet")
        else:
            msg="📊 *PnL Board*\n\n"
            for w,d in list(pnl_tracker.items())[:20]:
                msg+=f"`{w[:6]}..` Buys:{d['buys']} Sells:{d['sells']}\n"
            send_tg(msg)
    elif low.startswith("/addsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /addsol <address>"); return
        addr=parts[1].strip()
        if addr in SOL_WALLETS: send_tg(f"Already tracking `{addr[:8]}..`")
        else:
            SOL_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added SOL\n`{addr}`\nTotal SOL: {len(SOL_WALLETS)}")
    elif low.startswith("/addevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /addevm 0x..."); return
        addr=parts[1].strip()
        if addr.lower() in [x.lower() for x in EVM_WALLETS]: send_tg("Already tracking")
        else:
            EVM_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added EVM (all 5 chains)\n`{addr}`\nTotal EVM: {len(EVM_WALLETS)}")
    elif low.startswith("/delsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /delsol <address>"); return
        addr=parts[1].strip()
        if addr in SOL_WALLETS:
            SOL_WALLETS.remove(addr); save_wallets()
            send_tg(f"🗑️ Removed SOL `{addr[:8]}..`\nLeft: {len(SOL_WALLETS)}")
        else: send_tg("Not found")
    elif low.startswith("/delevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: /delevm 0x..."); return
        addr=parts[1].strip().lower()
        found=[x for x in EVM_WALLETS if x.lower()==addr]
        if found:
            EVM_WALLETS.remove(found[0]); save_wallets()
            send_tg(f"🗑️ Removed EVM `{found[0][:8]}..`\nLeft: {len(EVM_WALLETS)}")
        else: send_tg("Not found")
    elif low.startswith("/history"):
        if not holdings:
            send_tg("📜 *Current Cluster Holders*\n\nNo clusters yet. Waiting for buys...")
            return
        msg="📜 *Current Cluster Holders*\n\n"
        shown=0
        for token, wallets_dict in holdings.items():
            if len(wallets_dict)<2: continue
            sample=list(wallets_dict.values())[0]
            token_name=sample.get("token_name","?")
            chain=sample.get("chain","?")
            name2, mcap, dex_link, _, _ = get_token_info_quick(token, chain)
            if name2!="?" and name2!=token[:6]: token_name=name2
            msg+=f"🪙 *{token_name}* ({mcap})\nChain: {chain}\n"
            for w_addr, data in wallets_dict.items():
                usd=data.get("amount_usd",0)
                msg+=f"• `${usd:.0f}` - `{w_addr[:6]}...{w_addr[-4:]}` holding ${usd:.0f}\n"
            msg+=f"📊 [Chart]({dex_link})\n\n"
            shown+=1
            if shown>=6: break
        if shown==0:
            msg+="All clustered wallets have sold. No current holders with 2+ wallets holding same coin."
        send_tg(msg[:4000])
    elif low.startswith("/testalert"):
        test_msg = "💰 *BASE BUY*\n🪙 DRIP ($2.10M)\n👤 `0x3f2...9a1b` | `$5,200`\n📄 Token: `0x1234...5678`\n📊 [Chart](https://dexscreener.com/base/0x1234567890abcdef1234567890abcdef12345678) | 🔍 [Tx](https://basescan.org/tx/0xabc123hash)\n\n🚨 *SELL ALERT* 🚨\n🪙 Coin: *DRIP* ($2.10M)\n💸 `$1,240` sold by `0x3f2...9a1b`\n🔗 Chain: BASE\n👤 Wallet: `0xfd87eda88be6c372453b721da63d58ad1a5b2d94`\n📄 Token: `0x1234...5678`\n📊 [Chart](https://dexscreener.com/base/0x1234567890abcdef1234567890abcdef12345678) | 🔍 [Tx](https://basescan.org/tx/0xabc123hash)"
        send_tg(test_msg)

async def track_chain(chain):
    seen=set()
    scan={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}[chain]
    while True:
        try:
            w3=get_w3_with_fallback(chain)
            if not w3:
                await asyncio.sleep(10); continue
            bn=w3.eth.block_number
            for b in range(max(0,bn-2), bn+1):
                if b in seen: continue
                seen.add(b)
                if len(seen)>150: seen=set(list(seen)[-80:])
                try:
                    block=w3.eth.get_block(b, full_transactions=True)
                    for tx in block.transactions:
                        frm=tx.get('from')
                        if not frm: continue
                        if frm.lower() not in [x.lower() for x in EVM_WALLETS]: continue
                        h=tx.hash.hex() if hasattr(tx.hash,'hex') else tx['hash'].hex()
                        try:
                            receipt=w3.eth.get_transaction_receipt(h)
                            logs=receipt.get('logs',[])
                            for log in logs:
                                if len(log.get('topics',[]))!=3: continue
                                if log['topics'][0].hex()!= 'ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef': continue
                                from_addr='0x'+log['topics'][1].hex()[-40:]
                                to_addr_topic='0x'+log['topics'][2].hex()[-40:]
                                token_contract=log['address']
                                amount_raw=int(log['data'],16)
                                name, mcap, dex_link, price, _ = get_token_info_quick(token_contract, chain)
                                usd_val = (amount_raw/1e18*price) if price>0 else 0
                                if from_addr.lower() == frm.lower():
                                    if usd_val!=0 and usd_val <500: continue
                                    if token_contract in holdings and frm in holdings[token_contract]:
                                        holdings[token_contract].pop(frm, None)
                                    usd_str = f"${usd_val:.0f}" if usd_val>0 else "unknown amount"
                                    send_tg(f"🚨 *SELL ALERT* 🚨\n🪙 Coin: *{name}* ({mcap})\n💸 {usd_str} sold by `{frm[:6]}...{frm[-4:]}`\n🔗 Chain: {chain}\n👤 Wallet: `{frm}`\n📄 Token: `{token_contract}`\n📊 [Chart]({dex_link}) | 🔍 [Tx](https://{scan}/tx/{h})")
                                else:
                                    if to_addr_topic.lower() == frm.lower():
                                        if usd_val>0 and usd_val<1: continue
                                        cluster_memory[token_contract].append((frm, datetime.now(), chain))
                                        cluster_memory[token_contract]=[x for x in cluster_memory[token_contract] if datetime.now()-x[1] < timedelta(hours=24)]
                                        holdings[token_contract][frm]={"amount_usd": usd_val if usd_val>0 else 100, "chain": chain, "token_name": name, "mcap": mcap}
                                        usd_str = f"${usd_val:.0f}" if usd_val>0 else ""
                                        send_tg(f"💰 *{chain} BUY*\n🪙 {name} ({mcap})\n👤 `{frm[:6]}...{frm[-4:]}` | {usd_str}\n📄 Token: `{token_contract}`\n📊 [Chart]({dex_link}) | 🔍 [Tx](https://{scan}/tx/{h})")
                                        if len(cluster_memory[token_contract])>=2:
                                            recent=cluster_memory[token_contract][-5:]
                                            det="\n".join([f"- `${w[:6]}..` ${holdings.get(token_contract,{}).get(w,{}).get('amount_usd',0):.0f} on {c}" for w,c,t in [(x[0],x[2],x[1]) for x in recent]])
                                            send_tg(f"🔁 *RE-BUY CLUSTER ({chain})*\n🪙 {name} ({mcap})\n{det}\n📊 [Chart]({dex_link})")
                        except:
                            to_addr=tx.get('to')
                            if to_addr:
                                name, mcap, dex_link, _, _ = get_token_info_quick(to_addr, chain)
                                send_tg(f"💰 *{chain} BUY* (direct)\n`{frm[:6]}...{frm[-4:]}` -> {name} ({mcap})\n📊 [Chart]({dex_link}) | 🔍 [Tx](https://{scan}/tx/{h})")
                except: pass
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[{chain}] loop err {e}"); await asyncio.sleep(5)

import websockets
async def track_sol():
    uri=f"wss://atlas-mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                sub={"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions": SOL_WALLETS},{"commitment":"confirmed"}]}
                await ws.send(json.dumps(sub))
                async for msg in ws:
                    try:
                        data=json.loads(msg)
                        if "params" not in data: continue
                        sig=data["params"]["result"]["value"].get("signature","")
                        if sig:
                            send_tg(f"💰 *SOL BUY*\nSig: `{sig[:24]}...`\n📊 [Chart](https://dexscreener.com/solana/{sig}) | 🔍 [Tx](https://solscan.io/tx/{sig})")
                    except: pass
        except Exception as e:
            print(f"[SOL] WS err {e}"); await asyncio.sleep(5)

async def main_loop():
    send_tg(f"🚀 *Shok Tracker V2.8.0 ACTIVE*\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x {len(RPCS_FALLBACK)} chains\nChains: {', '.join(RPCS_FALLBACK.keys())}\n✅ Sell >$500 + mcap + Short [Chart]|[Tx] + Fallback + /add /del /overlap")
    set_bot_commands()
    tasks=[track_chain(c) for c in RPCS_FALLBACK.keys()]
    tasks.append(track_sol())
    await asyncio.gather(*tasks)

def start_bot():
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_loop())

if __name__=="__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    def poll_cmd():
        off=0
        while True:
            try:
                if not BOT_TOKEN: time.sleep(5); continue
                r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=10", timeout=15).json()
                for u in r.get("result",[]):
                    off=u["update_id"]+1
                    txt=u.get("message",{}).get("text","")
                    if txt.startswith("/"): handle_command(txt)
            except: time.sleep(4)
    threading.Thread(target=poll_cmd, daemon=True).start()
    start_bot()
