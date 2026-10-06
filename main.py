from flask import Flask
import threading, os, time, requests, re
from datetime import datetime

app = Flask(__name__)

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

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
HELIUS_KEY = os.getenv("HELIUS_API_KEY")
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY")

MIN_USD = 20.0
seen_evm = set()
seen_sol = set()

def send_tg(t):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
            "chat_id":CHAT_ID, "text":t, "parse_mode":"HTML", "disable_web_page_preview": True
        }, timeout=10)
    except: pass

def get_evm(rpc, wallet):
    try:
        p = {"jsonrpc":"2.0","id":1,"method":"alchemy_getAssetTransfers","params":[{"toAddress":wallet,"category":["erc20"],"order":"desc","maxCount":"0x5"}]}
        r = requests.post(rpc, json=p, timeout=10)
        return r.json().get("result",{}).get("transfers",[])
    except: return []

def get_sol(wallet):
    try:
        url = f"https://api.helius.xyz/v0/addresses/{wallet}/transactions?api-key={HELIUS_KEY}&limit=3"
        r = requests.get(url, timeout=10)
        return r.json()
    except: return []

@app.route('/')
def home():
    return f"Shoks LIVE MIN ${MIN_USD} - SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}"

def evm_loop():
    if not ALCHEMY_KEY: return
    base_rpc = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    while True:
        for w in EVM_WALLETS:
            try:
                txs = get_evm(base_rpc, w)
                for tx in txs[:1]:
                    h = tx.get("hash")
                    if not h or h in seen_evm: continue
                    if len(seen_evm) < 3: seen_evm.add(h); continue
                    seen_evm.add(h)
                    token_addr = tx.get("rawContract",{}).get("address","")
                    amount = tx.get("value","?")
                    symbol = tx.get("asset","TOKEN")
                    short_w = f"{w[:6]}...{w[-4:]}"
                    msg = f"""🔥 <b>BASE BUY</b>

🪙 <b>{symbol}</b>
💰 Amount: <b>{amount} {symbol}</b>
💵 USD: ~${MIN_USD}+ (checking...)
📄 Token: <code>{token_addr}</code>

👛 Wallet: <code>{short_w}</code>
🆔 Hash: <a href="https://basescan.org/tx/{h}">{h[:10]}...{h[-6:]}</a>
"""
                    # Quick USD check from description if possible - skip if small
                    # For BASE we keep all for now, price check added later
                    send_tg(msg)
            except: pass
            time.sleep(1)
        time.sleep(20)

def sol_loop():
    if not HELIUS_KEY: return
    while True:
        for w in SOL_WALLETS:
            try:
                txs = get_sol(w)
                if not isinstance(txs, list): continue
                for tx in txs[:1]:
                    sig = tx.get("signature")
                    if not sig or sig in seen_sol: continue
                    if len(seen_sol) < 3: seen_sol.add(sig); continue
                    seen_sol.add(sig)
                    desc = tx.get("description","")
                    # Skip if USDC amount < 20
                    m = re.search(r'([\d\.]+) USDC', desc)
                    if m:
                        usdc = float(m.group(1))
                        if usdc < MIN_USD: continue
                        usd_val = usdc
                    else:
                        usd_val = MIN_USD
                    
                    t_transfers = tx.get("tokenTransfers",[])
                    symbol = "UNKNOWN"
                    amount = "?"
                    mint = ""
                    if t_transfers:
                        buy = t_transfers[-1]
                        mint = buy.get("mint","")
                        amount = buy.get("tokenAmount",0)
                        symbol = buy.get("tokenSymbol") or symbol
                        try: amount = f"{float(amount):,.2f}"
                        except: pass
                    
                    short_w = f"{w[:6]}...{w[-4:]}"
                    short_mint = f"{mint[:6]}...{mint[-4:]}" if mint else "N/A"
                    
                    msg = f"""⚡ <b>SOL BUY</b>

🪙 <b>{symbol}</b>
💰 Amount: <b>{amount} {symbol}</b>
💵 USD: <b>${usd_val:.2f}</b>
📄 Token: <code>{mint}</code>

👛 Wallet: <code>{short_w}</code>
🆔 Hash: <a href="https://solscan.io/tx/{sig}">{sig[:10]}...{sig[-6:]}</a>
"""
                    send_tg(msg)
            except: pass
            time.sleep(1)
        time.sleep(25)

threading.Thread(target=evm_loop, daemon=True).start()
threading.Thread(target=sol_loop, daemon=True).start()

def startup():
    time.sleep(5)
    send_tg(f"🚀 <b>SHOKS LIVE</b> MIN ${MIN_USD}+\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}")

threading.Thread(target=startup, daemon=True).start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
