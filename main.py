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
EVM_CHAINS = {k:v[0] for k,v in RPCS_FALLBACK.items()}

def get_w3_with_fallback(chain):
    for url in RPCS_FALLBACK.get(chain, []):
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout':6}))
            if w3.is_connected():
                _ = w3.eth.block_number
                return w3
        except:
            continue
    return None

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)
@app.route('/')
def home(): return "Shok's Tracker V2.6 - Clickable Links + Sell + Fallback",200
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
        {"command":"start","description":"🚀 Status & chains"},
        {"command":"listwallets","description":"📋 List wallets"},
        {"command":"pnl","description":"📊 PnL board"},
        {"command":"addsol","description":"➕ Add SOL: /addsol <addr>"},
        {"command":"addevm","description":"➕ Add EVM: /addevm 0x..."},
        {"command":"delsol","description":"➖ Del SOL: /delsol <addr>"},
        {"command":"delevm","description":"➖ Del EVM: /delevm 0x..."},
        {"command":"history","description":"📜 Current cluster holders"},
        {"command":"testalert","description":"🧪 Test buy/sell format"},
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
            return name, mcap, dex_link, price
    except: pass
    return token_address[:6], "N/A", f"https://dexscreener.com/{ds_chain}/{token_address}", 0

def handle_command(text):
    t=text.strip()
    low=t.lower()
    if low.startswith("/start"):
        chains=", ".join(RPCS_FALLBACK.keys())
        send_tg(f"🚀 *Shok's Tracker V2.6 ACTIVE*\n\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x {len(RPCS_FALLBACK)} chains\nChains: {chains}\n\n✅ Sell >$500 + mcap\n✅ Short clickable links\n✅ Fallback RPCs\n✅ Add/Del wallets\n✅ History\n\nPress `/` to see commands")
        set_bot_commands()
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL ({len(SOL_WALLETS)}):*\n{sol}\n\n*EVM ({len(EVM_WALLETS)}):*\n{evm}")[:4000])
    elif low.startswith("/pnl"):
        if not pnl_tracker: send_tg("📊 No PnL yet")
        else:
            msg="📊 *PnL Board*\n\n"
            for w,d in list(pnl_tracker.items())[:20]:
                msg+=f"`{w[:6]}..` Buys:{d['buys']} Sells:{d['sells']}\n"
            send_tg(msg)
    elif low.startswith("/addsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: `/addsol <address>`"); return
        addr=parts[1].strip()
        if addr in SOL_WALLETS: send_tg(f"Already tracking `{addr[:8]}..`")
        else:
            SOL_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added SOL\n`{addr}`\nTotal SOL: {len(SOL_WALLETS)}")
    elif low.startswith("/addevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: `/addevm 0x...`"); return
        addr=parts[1].strip()
        if addr.lower() in [x.lower() for x in EVM_WALLETS]: send_tg("Already tracking")
        else:
            EVM_WALLETS.append(addr); save_wallets()
            send_tg(f"✅ Added EVM (all 5 chains)\n`{addr}`\nTotal EVM: {len(EVM_WALLETS)}")
    elif low.startswith("/delsol"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: `/delsol <address>`"); return
        addr=parts[1].strip()
        if addr in SOL_WALLETS:
            SOL_WALLETS.remove(addr); save_wallets()
            send_tg(f"🗑️ Removed SOL `{addr[:8]}..`\nLeft: {len(SOL_WALLETS)}")
        else: send_tg("Not found")
    elif low.startswith("/delevm"):
        parts=t.split()
        if len(parts)<2: send_tg("Usage: `/delevm 0x...`"); return
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
            name2, mcap, dex_link, _ = get_token_info_quick(token, chain)
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
        send_tg(
            "💰 *BASE BUY*\n"
            "🪙 DRIP ($2.10M)\n"
            "👤 `0x3f2...9a1b` | `$5,200`\n"
            "📄 Token: `0x1234...5678`\n"
            "📊 [Chart](https://dexscreener.com/base/0x1234567890abcdef1234567890abcdef12345678) | 🔍 [Tx](https://basescan.org/tx/0xabc123hash)\n
