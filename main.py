from flask import Flask
import threading, os, time, requests
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
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={
            "chat_id":CHAT_ID,
            "text":t,
            "parse_mode":"HTML",
            "disable_web_page_preview": True
        }, timeout=10)
    except: pass

def get_token_price_sol(mint):
    try:
        r = requests.get(f"https://price.jup.ag/v4/price?ids={mint}", timeout=5).json()
        price = r.get('data',{}).get(mint,{}).get('price')
        if price: return float(price)
    except: pass
    return None

def get_token_price_evm(token_address):
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=5).json()
        pairs = r.get('pairs',[])
        if pairs: return float(pairs[0].get('priceUsd',0))
    except: pass
    return None

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
    return f"Shoks DUAL LIVE - MIN ${MIN_USD} - SOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)} - {datetime.now()}"

def evm_loop():
    if not ALCHEMY_KEY:
        print("EVM disabled")
        return
    print(f"EVM tracking {len(EVM_WALLETS)} wallets")
    base_rpc = f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    while True:
        for w in EVM_WALLETS:
            try:
                txs = get_evm(base_rpc, w)
                for tx in txs[:1]:
                    h = tx.get("hash")
                    if not h or h in seen_evm: continue
                    if len(seen_evm) < 3:
                        seen_evm.add(h); continue
                    seen_evm.add(h)
                    token_addr = tx.get("rawContract",{}).get("address")
                    amount_raw = tx.get("value")
                    symbol = tx.get("asset","UNKNOWN")
                    usd_val = 0
                    try:
                        price = get_token_price_evm(token_addr) if token_addr else None
                        if price and amount_raw: usd_val = float(amount_raw) * price
                    except: pass
                    if usd_val > 0 and usd_val < MIN_USD: continue
                    short = f"{w[:6]}...{w[-4:]}"
                    usd_str = f"${usd_val:.2f}" if usd_val > 0 else "N/A"
                    msg = f"""🔥 <b>BASE BUY</b>

💰 <b>{amount_raw} {symbol}</b>
💵 {usd_str}
👛 Wallet: <code>{short}</code>
<code>{w}</code>

🆔 Hash: <code>{h}</code>
🔗 https://basescan.org/tx/{h}"""
                    send_tg(msg)
            except: pass
            time.sleep(1)
        time.sleep(20)

def sol_loop():
    if not HELIUS_KEY:
        print("SOL disabled")
        return
    print(f"SOL tracking {len(SOL_WALLETS)} wallets")
    while True:
        for w in SOL_WALLETS:
            try:
                txs = get_sol(w)
                if not isinstance(txs, list): continue
                for tx in txs[:1]:
                    sig = tx.get("signature")
                    if not sig or sig in seen_sol: continue
                    if len(seen_sol) < 3:
                        seen_sol.add(sig); continue
                    seen_sol.add(sig)
                    desc = tx.get("description","")
                    usd_val = 0
                    amount_str = ""
                    symbol = "TOKEN"
                    t_transfers = tx.get("tokenTransfers",[])
                    if t_transfers:
                        buy = t_transfers[-1]
                        if buy:
                            mint = buy.get("mint","")
                            amt = buy.get("tokenAmount",0)
                            symbol = buy.get("tokenSymbol") or symbol
                            price = get_token_price_sol(mint) if mint else None
                            if price: usd_val = float(amt) * price
                            amount_str = f"{float(amt):,.2f}"
                    if not amount_str: amount_str = desc[:80] if desc else "New buy"
                    if usd_val > 0 and usd_val < MIN_USD: continue
                    if usd_val == 0:
                        try:
                            import re
                            m = re.search(r'([\d\.]+) USDC', desc)
                            if m:
                                usdc = float(m.group(1))
                                if usdc < MIN_USD: continue
                                usd_val = usdc
                        except: pass
                    short = f"{w[:6]}...{w[-4:]}"
                    usd_str = f"${usd_val:.2f}" if usd_val>0 else ""
                    if not amount_str or amount_str == "TOKEN":
                        amount_str = symbol
                    else:
                        amount_str = f"{amount_str} {symbol}"
                    msg = f"""⚡ <b>SOL BUY</b>

💰 <b>{amount_str}</b> {usd_str}
👛 Wallet: <code>{short}</code>
<code>{w}</code>

🆔 Hash: <code>{sig}</code>
🔗 https://solscan.io/tx/{sig}"""
                    send_tg(msg)
            except: pass
            time.sleep(1)
        time.sleep(25)

threading.Thread(target=evm_loop, daemon=True).start()
threading.Thread(target=sol_loop, daemon=True).start()

def startup():
    time.sleep(5)
    send_tg(f"🚀 <b>SHOKS TRACKER LIVE</b> - MIN ${MIN_USD}+\nSOL:{len(SOL_WALLETS)} EVM:{len(EVM_WALLETS)}")

threading.Thread(target=startup, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
