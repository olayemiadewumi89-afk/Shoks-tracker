from flask import Flask
import threading, os, time, requests
from datetime import datetime

app = Flask(__name__)

# === CONFIG FROM ENV ===
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
HELIUS_KEY = os.getenv("HELIUS_API_KEY")
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY")

# === YOUR WALLETS ===
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

# Override with env if provided
WATCH_RAW = os.getenv("WATCH_WALLETS","")
if WATCH_RAW:
    all_raw = [w.strip() for w in WATCH_RAW.split(",") if w.strip()]
    evm_from_env = [w for w in all_raw if w.startswith("0x")]
    sol_from_env = [w for w in all_raw if not w.startswith("0x") and len(w) > 30]
    if evm_from_env: EVM_WALLETS = evm_from_env
    if sol_from_env: SOL_WALLETS = sol_from_env

seen_evm = set()
seen_sol = set()

def send_tg(t):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", 
                      json={"chat_id":CHAT_ID,"text":t,"parse_mode":"HTML","disable_web_page_preview":True}, timeout=15)
        print(f"TG: {t[:80]}")
    except Exception as e:
        print(f"TG err: {e}")

# --- EVM LOGIC ---
def get_evm_transfers(rpc_url, wallet):
    payload = {"jsonrpc":"2.0","id":1,"method":"alchemy_getAssetTransfers","params":[{"toAddress":wallet,"category":["erc20"],"order":"desc","maxCount":"0x3","withMetadata":True}]}
    try:
        r = requests.post(rpc_url, json=payload, timeout=15)
        return r.json().get("result",{}).get("transfers",[])
    except: return []

def evm_loop():
    print(f"EVM TRACKER STARTED - {len(EVM_WALLETS)} wallets")
    if not ALCHEMY_KEY:
        print("No ALCHEMY_KEY - EVM disabled")
        return
    eth_rpc = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    base_rpc = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    rpcs = [("BASE", base_rpc), ("ETH", eth_rpc)]
    
    while True:
        for chain, rpc in rpcs:
            for wallet in EVM_WALLETS:
                try:
                    transfers = get_evm_transfers(rpc, wallet)
                    for tx in transfers[:1]:
                        h = tx.get("hash")
                        if not h or h in seen_evm:
                            continue
                        if len(seen_evm) < len(EVM_WALLETS)*2:
                            seen_evm.add(h)
                            continue
                        seen_evm.add(h)
                        token = tx.get("asset","Token")
                        value = tx.get("value","?")
                        short_w = wallet[:6]+"..."+wallet[-4:]
                        scan = "basescan.org" if chain=="BASE" else "etherscan.io"
                        msg = f"🔥 <b>EVM BUY [{chain}]</b>\n\n💼 <code>{short_w}</code>\n🪙 <b>{token}</b> - {value}\n🔗 <a href='https://{scan}/tx/{h}'>View TX</a>\n⏰ {datetime.now().strftime('%H:%M:%S')}"
                        send_tg(msg)
                except Exception as e:
                    print(f"EVM err: {e
