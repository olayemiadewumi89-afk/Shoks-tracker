from flask import Flask
import threading, os, time, requests, re, json
app = Flask(__name__)

WALLETS_FILE = "wallets.json"
SUBS_FILE = "subscribers.json"

DEFAULT_EVM = ["0xfd87eda88be6c372453b721da63d58ad1a5b2d94","0x2a17e1e796dd7bd3b27efe2cda72d4b909baaacf","0x4bc1782fafb967834e0e75947ba15113e48fc70e","0x5a08728a6704d99468d8919ba0adc2e1d2733083","0xae97104fde93a82797afec819ac0e09b8d544907","0xabf122e41ce873a3c6a71fbf1c6419ca5df07f17","0xd1c77a04b87393e98a1220532e72e8f7d0a31c5a","0x0c175c6a0065ee05f871a68783d2de432a1e6cbe","0x696d1265c8fc4f14797abebfae3c43ebfa9d8e28","0x0121525f755c9e7bbc525bba6672716ab46ced57","0x27d6a5a1d9a5e89bfce0239f08d9c95cf2256ebb","0xb8f305f27ccc406373de0082cc06cb1d065504ea","0xb02208d1b27811480cf6171bccc2b7af9513cddb","0x06de9c48b1e639ed5c13ec8fbd4080a38e39f2d1","0x1890e719822bc704c4f117aa4109401c2bab6f79","0xd4cf04bc9d7c80b49c6c30a633f7b9bd5370b4d6"]
DEFAULT_SOL = ["Beqv6dzTcjV2eodo8RRXCiCcnSYrS1vkQKhfqwHXqeit","5t8FA8z7SFiEpjau2nTBYKN8jd5CGo8Ttcgt1m73C7d","6xmMW5JPSEfeRuNdkxixHWsm4Sf57SdXybA3BdwZzrM1","2Ysos2B2S6rb9FBNYUB7Yrk54Xv2nNbr8eBHs1sX8M6m","Agt534WQKGXuVt9UFamcgqH9djySF9PmxqNS5caiMCAV","Ab25e8CyZ6119HSY6r4wdKpBPxY3UBLWfrZ7PEBvHD4F","4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo","9BMzTpSo4URse1oN666pmexhdjpU1vA5p7LtroCFQdLU","498g1rVnFcnjBjpfw1xyqA1WvgQXUU8RWuELjxkjAayQ","8f39XhhZoRD8sYb6K5K9N7iSHXkL7BDmFFQNDF3TtsEr","83b2LMf12aLec92kur3duRA8VvUkLaXUtML31aCcZNCM","7aoGoRSexZu1DE4vC24CVo4SqWrBDeoJ34qLRGUsL9ha","2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V","7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9","H2QSGECp13sFLJgdTsDtayX3dk18Dm6sQMSQKcew7Xzk","DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39","6My97BBVoJz3j7mQWFVz5pykFxMsCs44gjhaqmczcAND","D9tPQeij7vSTZwkxzxZibso4GFuRW8aBMpCg5QhCSfVL"]

def load_json(f, d):
    if os.path.exists(f):
        try: return json.load(open(f))
        except: return d
    return d
def save_json(f, d):
    try: json.dump(d, open(f,"w"))
    except: pass

saved = load_json(WALLETS_FILE, {})
EVM_WALLETS = saved.get("base", DEFAULT_EVM) if saved and saved.get("base") else DEFAULT_EVM
SOL_WALLETS = saved.get("sol", DEFAULT_SOL) if saved and saved.get("sol") else DEFAULT_SOL
for w in DEFAULT_EVM:
    if w not in EVM_WALLETS: EVM_WALLETS.append(w)
for w in DEFAULT_SOL:
    if w not in SOL_WALLETS: SOL_WALLETS.append(w)

BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN");CHAT_ID=os.getenv("TELEGRAM_CHAT_ID");HELIUS_KEY=os.getenv("HELIUS_API_KEY");ALCHEMY_KEY=os.getenv("ALCHEMY_API_KEY");ADMIN_ID=os.getenv("ADMIN_ID")
MIN_USD=20.0
seen_evm=set();seen_sol=set()
buy_history_sol = {}
buy_history_evm = {}
price_cache = {} # mint -> price for PnL

subscribers = set(load_json(SUBS_FILE, []))
if CHAT_ID: subscribers.add(str(CHAT_ID))
tg_offset = 0
ADMIN_ID = str(ADMIN_ID) if ADMIN_ID else ""

def save_wallets(): save_json(WALLETS_FILE, {"sol": SOL_WALLETS, "base": EVM_WALLETS})
def save_subs(): save_json(SUBS_FILE, list(subscribers))

def is_admin(cid):
    global ADMIN_ID
    if not ADMIN_ID:
        if subscribers: ADMIN_ID = list(subscribers)[0]
        else: ADMIN_ID = str(cid)
    return str(cid) == str(ADMIN_ID)

def send_tg_all(t):
    for cid in list(subscribers):
        try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":cid,"text":t,"parse_mode":"HTML","disable_web_page_preview":True},timeout=10)
        except: pass

def time_ago(ts):
    diff=int(time.time()-ts)
    if diff<60: return f"{diff}s ago"
    if diff<3600: return f"{diff//60}m ago"
    if diff<86400: return f"{diff//3600}h {(diff%3600)//60}m ago"
    return f"{diff//86400}d ago"

# --- V2: PnL FUNCTIONS ---
def get_sol_price(mint):
    try:
        if mint in price_cache and time.time() - price_cache[mint][1] < 60:
            return price_cache[mint][0]
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",timeout=5).json()
        if r.get("pairs"):
            price=float(r["pairs"][0]["priceUsd"])
            price_cache[mint]=(price,time.time())
            return price
    except: pass
    return 0

def get_base_price(addr):
    try:
        if addr in price_cache and time.time() - price_cache[addr][1] < 60:
            return price_cache[addr][0]
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}",timeout=5).json()
        if r.get("pairs"):
            price=float(r["pairs"][0]["priceUsd"])
            price_cache[addr]=(price,time.time())
            return price
    except: pass
    return 0

@app.route('/')
def home(): return f"Shok's TRACKER V2 LIVE - {len(subscribers)} users | SOL:{len(SOL_WALLETS)} BASE:{len(EVM_WALLETS)} | Total:{len(SOL_WALLETS)+len(EVM_WALLETS)}", 200
@app.route('/health')
def health(): return "OK", 200

def telegram_listener():
    global tg_offset
    while True:
        try:
            r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={tg_offset}&timeout=20",timeout=25).json()
            for upd in r.get("result",[]):
                tg_offset = upd["update_id"]+1
                msg=upd.get("message",{})
                chat_id=str(msg.get("chat",{}).get("id",""))
                text=msg.get("text","").strip()
                if not chat_id: continue

                if text.startswith("/start"):
                    if chat_id not in subscribers:
                        subscribers.add(chat_id); save_subs()
                    is_ad = is_admin(chat_id)
                    admin_text = "\n\n🔧 <b>Admin:</b>\n/addsol\n/addbase\n/removesol\n/removebase\n/listwallets\n/pnl" if is_ad else ""
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"🔥 <b>Shoks Tracker V2 ACTIVE</b>\n\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} BASE\n\n✅ Re-buy Cluster ON\n✅ PnL ON\n✅ Time History ON{admin_text}","parse_mode":"HTML"},timeout=10)

                elif text.startswith("/addsol"):
                    if not is_admin(chat_id): continue
                    try:
                        addr=text.split()[1].strip()
                        if addr not in SOL_WALLETS:
                            SOL_WALLETS.append(addr); save_wallets()
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"✅ Added SOL: {addr}\nTotal: {len(SOL_WALLETS)}"},timeout=10)
                    except: pass

                elif text.startswith("/addbase"):
                    if not is_admin(chat_id): continue
                    try:
                        addr=text.split()[1].strip()
                        if addr not in EVM_WALLETS:
                            EVM_WALLETS.append(addr); save_wallets()
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"✅ Added BASE: {addr}\nTotal: {len(EVM_WALLETS)}"},timeout=10)
                    except: pass

                elif text.startswith("/listwallets") or text.startswith("/listewallets") or text.startswith("//listwallets"):
                    if not is_admin(chat_id): continue
                    s="\n".join([f"{i+1}. {w[:6]}...{w[-4:]} - {w}" for i,w in enumerate(SOL_WALLETS)])
                    b="\n".join([f"{i+1}. {w[:6]}...{w[-4:]} - {w}" for i,w in enumerate(EVM_WALLETS)])
                    txt=f"SOL ({len(SOL_WALLETS)}):\n{s}\n\nBASE ({len(EVM_WALLETS)}):\n{b}\n\nTotal: {len(SOL_WALLETS)+len(EVM_WALLETS)}"
                    if len(txt)>4000:
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"SOL ({len(SOL_WALLETS)}):\n{s[:3800]}"},timeout=10)
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"BASE ({len(EVM_WALLETS)}):\n{b[:3800]}"},timeout=10)
                    else:
                        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":txt},timeout=10)

                elif text.startswith("/removesol"):
                    if not is_admin(chat_id): continue
                    try:
                        q=text.split()[1]; found=[w for w in SOL_WALLETS if q.lower() in w.lower()]
                        if found:
                            SOL_WALLETS.remove(found[0]); save_wallets()
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"🗑️ Removed SOL: {found[0]}"},timeout=10)
                    except: pass

                elif text.startswith("/removebase"):
                    if not is_admin(chat_id): continue
                    try:
                        q=text.split()[1]; found=[w for w in EVM_WALLETS if q.lower() in w.lower()]
                        if found:
                            EVM_WALLETS.remove(found[0]); save_wallets()
                            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":f"🗑️ Removed BASE: {found[0]}"},timeout=10)
                    except: pass

                elif text.startswith("/pnl"):
                    if not is_admin(chat_id): continue
                    # V2 PnL summary
                    txt=f"<b>📊 PnL Summary (V2)</b>\n\nSOL tracked: {len(SOL_WALLETS)}\nBASE tracked: {len(EVM_WALLETS)}\n\nCluster alerts: {len(buy_history_sol)+len(buy_history_evm)} coins in last 24h\n\nUse /listwallets to see all"
                    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",json={"chat_id":chat_id,"text":txt,"parse_mode":"HTML"},timeout=10)

        except Exception as e:
            print(f"tg error {e}")
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
                    if dm: amount_str=dm.group(1); coin_name=dm.group(2)
                    mint=""
                    for tt in tx.get("tokenTransfers",[]):
                        if "EPjFWdd" not in tt.get("mint",""):
                            mint=tt.get("mint","")
                            if tt.get("tokenSymbol"): coin_name=tt.get("tokenSymbol")
                            break
                    if not mint: continue
                    now=time.time()
                    # PnL calc
                    cur_price=get_sol_price(mint)
                    pnl_text=""
                    if cur_price>0:
                        # estimate buy price from amount
                        try:
                            amt=float(amount_str.replace(",",""))
                            if amt>0:
                                buy_price=usdc/amt
                                pnl=((cur_price-buy_price)/buy_price*100)
                                pnl_text=f"\n📊 <b>PnL:</b> {pnl:+.1f}% (Buy ${buy_price:.6f} → Now ${cur_price:.6f})"
                        except: pass

                    history=buy_history_sol.get(mint,[])
                    recent=[h for h in history if now-h[0]<86400]
                    if recent:
                        prev_wallets=list(set([h[1] for h in recent]))
                        total_usd=sum([h[2] for h in recent])+usdc
                        lines=[]
                        for h in recent[-5:]: lines.append(f"• {h[1][:4]}...{h[1][-3:]} bought ${h[2]:.2f} - {time_ago(h[0])}")
                        past_text="\n".join(lines)
                        msg=f"""🔁🔁 <b>RE-BUY CLUSTER ALERT!</b> 🔁🔁

🪙 <b>{coin_name}</b> <code>{mint[:4]}..{mint[-3:]}</code>
💵 <b>New Buy: ${usdc:.2f}</b> by <code>{w[:4]}...{w[-3:]}</code>{pnl_text}

📜 <b>PAST BUYS - last {len(recent)}:</b>
{past_text}
👥 Total: {len(prev_wallets)+1} wallets | 💰 Combined: ${total_usd:.2f}
🔗 <a href="https://dexscreener.com/solana/{mint}">Dex</a> | <a href="https://solscan.io/token/{mint}">Scan</a>"""
                        send_tg_all(msg)
                    else:
                        msg=f"""⚡ <b>SOL BUY ${usdc:.2f}</b>

🪙 <b>{coin_name}</b>
💰 {amount_str} {coin_name}
💵 ${usdc:.2f}{pnl_text}
📄 <code>{mint}</code>
👛 <code>{w[:6]}...{w[-4:]}</code>
🔗 <a href="https://solscan.io/tx/{sig}">Tx {sig[:8]}</a>"""
                        send_tg_all(msg)
                    recent.append((now,w,usdc,coin_name))
                    buy_history_sol[mint]=recent
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
                    cur_price=get_base_price(addr)
                    pnl_text=f"\n📊 Price: ${cur_price:.6f}" if cur_price>0 else ""
                    recent=[x for x in buy_history_evm.get(addr,[]) if now-x[0]<86400]
                    if recent:
                        lines="\n".join([f"• {x[1][:6]}...{x[1][-4:]} - {time_ago(x[0])}" for x in recent[-5:]])
                        msg=f"""🔁🔁 <b>BASE RE-BUY CLUSTER!</b> 🔁🔁

🪙 <b>{sym}</b> <code>{addr[:6]}..{addr[-4:]}</code>{pnl_text}
💵 New Buy by <code>{w[:6]}...{w[-4:]}</code>

📜 Past buys:
{lines}
🔗 <a href="https://basescan.org/tx/{h}">Tx</a>"""
                        send_tg_all(msg)
                    else:
                        msg=f"""🔥 <b>BASE BUY</b>

🪙 <b>{sym}</b>
💰 {val} {sym}{pnl_text}
📄 <code>{addr}</code>
👛 <code>{w[:6]}...{w[-4:]}</code>
🔗 <a href="https://basescan.org/tx/{h}">Tx {h[:8]}</a>"""
                        send_tg_all(msg)
                    recent.append((now,w,sym))
                    buy_history_evm[addr]=recent
            except: pass
            time.sleep(1)
        time.sleep(20)

threading.Thread(target=telegram_listener,daemon=True).start()
threading.Thread(target=sol_loop,daemon=True).start()
threading.Thread(target=evm_loop,daemon=True).start()

if __name__=="__main__":
    save_wallets()
    app.run(host='0.0.0.0',port=int(os.environ.get("PORT",10000)))
