from flask import Flask
import threading, os, time, requests, re
app = Flask(__name__)

EVM_WALLETS = ["0xfd87eda88be6c372453b721da63d58ad1a5b2d94","0x2a17e1e796dd7bd3b27efe2cda72d4b909baaacf","0x4bc1782fafb967834e0e75947ba15113e48fc70e","0x5a08728a6704d99468d8919ba0adc2e1d2733083","0xae97104fde93a82797afec819ac0e09b8d544907","0xabf122e41ce873a3c6a71fbf1c6419ca5df07f17","0xd1c77a04b87393e98a1220532e72e8f7d0a31c5a","0x0c175c6a0065ee05f871a68783d2de432a1e6cbe","0x696d1265c8fc4f14797abebfae3c43ebfa9d8e28","0x0121525f755c9e7bbc525bba6672716ab46ced57","0x27d6a5a1d9a5e89bfce0239f08d9c95cf2256ebb","0xb8f305f27ccc406373de0082cc06cb1d065504ea","0xb02208d1b27811480cf6171bccc2b7af9513cddb","0x06de9c48b1e639ed5c13ec8fbd4080a38e39f2d1","0x1890e719822bc704c4f117aa4109401c2bab6f79","0xd4cf04bc9d7c80b49c6c30a633f7b9bd5370b4d6"]
SOL_WALLETS = ["Beqv6dzTcjV2eodo8RRXCiCcnSYrS1vkQKhfqwHXqeit","5t8FA8z7SFiEpjau2nTBYKN8jd5CGo8Ttcgt1m73C7d","6xmMW5JPSEfeRuNdkxixHWsm4Sf57SdXybA3BdwZzrM1","2Ysos2B2S6rb9FBNYUB7Yrk54Xv2nNbr8eBHs1sX8M6m","Agt534WQKGXuVt9UFamcgqH9djySF9PmxqNS5caiMCAV","Ab25e8CyZ6119HSY6r4wdKpBPxY3UBLWfrZ7PEBvHD4F","4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo","9BMzTpSo4URse1oN666pmexhdjpU1vA5p7LtroCFQdLU","498g1rVnFcnjBjpfw1xyqA1WvgQXUU8RWuELjxkjAayQ","8f39XhhZoRD8sYb6K5K9N7iSHXkL7BDmFFQNDF3TtsEr","83b2LMf12aLec92kur3duRA8VvUkLaXUtML31aCcZNCM","7aoGoRSexZu1DE4vC24CVo4SqWrBDeoJ34qLRGUsL9ha","2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V","7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9","H2QSGECp13sFLJgdTsDtayX3dk18Dm6sQMSQKcew7Xzk","DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39","6My97BBVoJz3j7mQWFVz5pykFxMsCs44gjhaqmczcAND","D9tPQeij7vSTZwkxzxZibso4GFuRW8aBMpCg5QhCSfVL"]

BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN");CHAT_ID=os.getenv("TELEGRAM_CHAT_ID");HELIUS_KEY=os.getenv("HELIUS_API_KEY");ALCHEMY_KEY=os.getenv("ALCHEMY_API_KEY");MIN_USD=20.0
seen_evm=set();seen_sol=set()

buy_history_sol = {}
buy_history_evm = {}

# NEW: Multiple users support
subscribers = set()
if CHAT_ID: subscribers.add(str(CHAT_ID))
tg_offset = 0

def send_tg_all(t):
    for cid in list(subscribers):
        try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":cid,"text":t,"parse_mode":"HTML","disable_web_page_preview":True},timeout=10)
        except: pass

def send_tg(t): send_tg_all(t)

def time_ago(ts):
    diff=int(time.time()-ts)
    if diff<60: return f"{diff}s ago"
    if diff<3600: return f"{diff//60}m ago"
    if diff<86400: return f"{diff//3600}h {(diff%3600)//60}m ago"
    return f"{diff//86400}d ago"

@app.route('/')
def home(): return f"LIVE - {len(subscribers)} users"

def telegram_listener():
    global tg_offset
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={tg_offset}&timeout=20",timeout=25).json()
            for upd in r.get("result",[]):
                tg_offset = upd["update_id"]+1
                msg=upd.get("message",{})
                chat_id=str(msg.get("chat",{}).get("id",""))
                text=msg.get("text","")
                if not chat_id: continue
                if text.startswith("/start"):
                    if chat_id not in subscribers:
                        subscribers.add(chat_id)
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"🔥 <b>Shoks Tracker ACTIVE</b>\n\nYou will now get alerts when my {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} BASE wallets buy.\n\n✅ Re-buy detection ON\n✅ Time history ON\n\nWait for next buy...","parse_mode":"HTML"},timeout=10)
        except: pass
        time.sleep(2)

def sol_loop():
    while True:
        for w in SOL_WALLETS:
            try:
                url=f"https://api.helius.xyz/v0/addresses/{w}/transactions?api-key={HELIUS_KEY}&limit=2"
                txs=requests.get(url,timeout=10).json()
                if not isinstance(txs,list): continue
                for tx in txs[:1]:
                    sig=tx.get("signature")
                    if not sig or sig in seen_sol: continue
                    if len(seen_sol)<1: seen_sol.add(sig); continue
                    seen_sol.add(sig)
                    desc=tx.get("description","")
                    m=re.search(r'([\d\.]+) USDC',desc)
                    usdc=float(m.group(1)) if m else 0
                    if usdc<MIN_USD: continue
                    coin_name="NEW TOKEN"; amount_str="?"
                    dm=re.search(r'for ([\d,\.]+) ([A-Za-z0-9\$]+)',desc)
                    if dm:
                        amount_str=dm.group(1); coin_name=dm.group(2)
                    mint=""
                    for tt in tx.get("tokenTransfers",[]):
                        if "EPjFWdd" not in tt.get("mint",""):
                            mint=tt.get("mint","")
                            if tt.get("tokenSymbol"): coin_name=tt.get("tokenSymbol")
                            break
                    if not mint: continue
                    now=time.time()
                    history = buy_history_sol.get(mint, [])
                    recent = [h for h in history if now - h[0] < 86400]
                    if recent:
                        prev_wallets = list(set([h[1] for h in recent]))
                        total_usd = sum([h[2] for h in recent]) + usdc
                        lines=[]
                        for h in recent[-5:]: lines.append(f"• {h[1][:4]}...{h[1][-3:]} bought ${h[2]:.2f} - {time_ago(h[0])}")
                        past_text="\n".join(lines)
                        msg=f"""🔁🔁 <b>RE-BUY ALERT! SAME COIN!</b> 🔁🔁

🪙 <b>{coin_name}</b> <code>{mint[:4]}..{mint[-3:]}</code>
💵 <b>New Buy: ${usdc:.2f}</b> by <code>{w[:4]}...{w[-3:]}</code> (Just now)

📜 <b>PAST BUYS - last {len(recent)} buy(s):</b>
{past_text}

👥 Total: {len(prev_wallets)+1} wallets | 💰 Combined: ${total_usd:.2f}
🔗 <a href="https://dexscreener.com/solana/{mint}">Dexscreener</a> | <a href="https://solscan.io/token/{mint}">Solscan</a>
<a href="https://solscan.io/tx/{sig}">Tx {sig[:8]}</a>"""
                        send_tg_all(msg)
                    else:
                        msg=f"""⚡ <b>SOL BUY ${usdc:.2f}</b>

🪙 <b>{coin_name}</b>
💰 Amount: <code>{amount_str} {coin_name}</code>
💵 USD: <b>${usdc:.2f}</b>
📄 Token: <code>{mint}</code>

👛 Wallet: <code>{w[:6]}...{w[-4:]}</code>
🔗 <a href="https://solscan.io/tx/{sig}">View Tx {sig[:8]}</a>"""
                        send_tg_all(msg)
                    recent.append((now, w, usdc, coin_name))
                    buy_history_sol[mint] = recent
            except: pass
            time.sleep(1)
        time.sleep(20)

def evm_loop():
    if not ALCHEMY_KEY: return
    rpc=f"https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}"
    while True:
        for w in EVM_WALLETS:
            try:
                p={"jsonrpc":"2.0","id":1,"method":"alchemy_getAssetTransfers","params":[{"toAddress":w,"category":["erc20"],"order":"desc","maxCount":"0x3"}]}
                txs=requests.post(rpc,json=p,timeout=10).json().get("result",{}).get("transfers",[])
                for tx in txs[:1]:
                    h=tx.get("hash")
                    if not h or h in seen_evm: continue
                    if len(seen_evm)<1: seen_evm.add(h); continue
                    seen_evm.add(h)
                    addr=tx.get("rawContract",{}).get("address",""); val=tx.get("value","?"); sym=tx.get("asset","TOKEN")
                    now=time.time()
                    recent = [x for x in buy_history_evm.get(addr,[]) if now - x[0] < 86400]
                    if recent:
                        lines="\n".join([f"• {x[1][:6]}...{x[1][-4:]} - {time_ago(x[0])}" for x in recent[-5:]])
                        msg=f"""🔁🔁 <b>BASE RE-BUY ALERT!</b> 🔁🔁

🪙 <b>{sym}</b> <code>{addr[:6]}..{addr[-4:]}</code>
💵 New Buy by <code>{w[:6]}...{w[-4:]}</code> (Just now)

📜 Past buys:
{lines}
🔗 <a href="https://basescan.org/tx/{h}">View Tx</a>"""
                        send_tg_all(msg)
                    else:
                        msg=f"""🔥 <b>BASE BUY</b>

🪙 <b>{sym}</b>
💰 Amount: <code>{val} {sym}</code>
📄 Token: <code>{addr}</code>

👛 Wallet: <code>{w[:6]}...{w[-4:]}</code>
🔗 <a href="https://basescan.org/tx/{h}">View Tx {h[:8]}</a>"""
                        send_tg_all(msg)
                    recent.append((now, w, sym))
                    buy_history_evm[addr]=recent
            except: pass
            time.sleep(1)
        time.sleep(20)

threading.Thread(target=telegram_listener,daemon=True).start()
threading.Thread(target=sol_loop,daemon=True).start()
threading.Thread(target=evm_loop,daemon=True).start()

if __name__=="__main__": app.run(host='0.0.0.0',port=int(os.environ.get("PORT",10000)))
