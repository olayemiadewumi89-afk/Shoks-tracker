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

HELIUS_KEY = "3ac60377-a024-4177-8ef4-b8c36a692a57"

EVM_CHAINS = {
    "ETH": "https://eth.llamarpc.com",
    "BSC": "https://bsc.llamarpc.com",
    "BASE": "https://base.llamarpc.com",
    "ARB": "https://arbitrum.llamarpc.com",
    "POLY": "https://polygon.llamarpc.com"
}

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)
@app.route('/')
def home(): return "Shok's Tracker V2.1 ALL EVM ACTIVE", 200
@app.route('/health')
def health(): return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))

cluster_memory = defaultdict(list)
pnl_tracker = defaultdict(lambda: {"buys": 0, "sells": 0, "pnl": 0})

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text[:500]); return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        print(f"TG error {e}")

def handle_command(text):
    t=text.strip()
    if t=="/start":
        chains=", ".join(EVM_CHAINS.keys())
        send_tg(f"🚀 *Shok's Tracker V2.1 ACTIVE*\n\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x {len(EVM_CHAINS)} chains\nChains: {chains}\n\n✅ Re-buy Cluster ON (24h)\n✅ PnL ON\n✅ All-EVM ON\n✅ Time History ON")
    elif t=="/listwallets":
        sol_list="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm_list="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg(f"*SOL ({len(SOL_WALLETS)}):*\n{sol_list}\n\n*EVM ({len(EVM_WALLETS)}):*\n{evm_list}")
    elif t=="/pnl":
        if not pnl_tracker:
            send_tg("📊 No PnL data yet")
        else:
            msg="📊 *PnL Board*\n\n"
            for w,d in list(pnl_tracker.items())[:20]:
                msg+=f"`{w[:6]}..` PnL: {d['pnl']:.2f} | Buys: {d['buys']}\n"
            send_tg(msg)

web3s = {c: Web3(Web3.HTTPProvider(u)) for c,u in EVM_CHAINS.items()}
evm_lower = [w.lower() for w in EVM_WALLETS]

async def track_chain(chain):
    w3=web3s[chain]
    seen=set()
    print(f"[{chain}] Tracking {len(evm_lower)} wallets")
    while True:
        try:
            bn=w3.eth.block_number
            for b in range(max(0,bn-1),bn+1):
                if b in seen: continue
                seen.add(b)
                if len(seen)>50: seen=set(list(seen)[-30:])
                try:
                    block=w3.eth.get_block(b, full_transactions=True)
                    for tx in block.transactions:
                        frm=tx.get('from')
                        if not frm or frm.lower() not in evm_lower: continue
                        to_addr=tx.get('to') or 'Contract'
                        cluster_memory[to_addr].append((frm, datetime.now(), chain))
                        cluster_memory[to_addr]=[x for x in cluster_memory[to_addr] if datetime.now()-x[1] < timedelta(hours=24)]
                        h=tx.hash.hex() if hasattr(tx.hash,'hex') else tx['hash'].hex()
                        scan={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}[chain]
                        send_tg(f"💰 *{chain} BUY*\n`{frm[:6]}...{frm[-4:]}` on {chain}\nToken: `{to_addr}`\nTx: https://{scan}/tx/{h}\n⏰ {datetime.now().strftime('%H:%M:%S')}")
                        if len(cluster_memory[to_addr])>=2:
                            det="\n".join([f"- `{w[:6]}..` on {c} {int((datetime.now()-t).total_seconds()//60)}m ago" for w,c,t in [(x[0],x[2],x[1]) for x in cluster_memory[to_addr]][-3:]])
                            send_tg(f"🔁🔁 *RE-BUY CLUSTER ALERT! ({chain})*\nToken `{to_addr}`\n{det}\nhttps://dexscreener.com/{chain.lower()}/{to_addr}")
                except: pass
            await asyncio.sleep(4)
        except Exception as e:
            print(f"[{chain}] err {e}"); await asyncio.sleep(8)

import websockets
async def track_sol():
    uri=f"wss://atlas-mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
    print(f"[SOL] Tracking {len(SOL_WALLETS)} wallets")
    while True:
        try:
            async with websockets.connect(uri) as ws:
                sub={"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions": SOL_WALLETS},{"commitment":"confirmed"}]}
                await ws.send(json.dumps(sub))
                print("[SOL] Subscribed")
                async for msg in ws:
                    try:
                        data=json.loads(msg)
                        if "params" not in data: continue
                        sig=data["params"]["result"]["value"].get("signature","")
                        if sig:
                            send_tg(f"💰 *SOL BUY*\nActivity detected\nSig: `{sig[:20]}...`\nhttps://solscan.io/tx/{sig}")
                    except: pass
        except Exception as e:
            print(f"[SOL] WS error {e}"); await asyncio.sleep(5)

async def main_loop():
    send_tg(f"🚀 *Shok's Tracker V2.1 ACTIVE*\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x {len(EVM_CHAINS)} chains\nChains: {', '.join(EVM_CHAINS.keys())}\nHelius: Connected")
    tasks=[track_chain(c) for c in EVM_CHAINS.keys()]
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
