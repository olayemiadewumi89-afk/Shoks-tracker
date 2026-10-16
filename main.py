"""
V32-Web Service FIX - For Render Free (No Background Worker)
Works with Uptimerobot
"""
import os, asyncio, time, gc, json, threading
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
import websockets, httpx
from telegram import Bot
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
PORT = int(os.getenv("PORT", 10000))

THRESHOLD = 45.0
bot = Bot(token=BOT_TOKEN)

ALCHEMY_WSS = f"wss://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
ALCHEMY_HTTPS = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

dust_tracker = defaultdict(list)
main_tracker = defaultdict(list)
last_alert = defaultdict(float)
watched_workers = {}
bundle_buys = defaultdict(list)
sol_price = 150.0

# ---- WEB SERVER FOR RENDER (STOPS SLEEP) ----
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"OK - Watching {len(watched_workers)} workers - SOL ${sol_price}".encode())
    def log_message(self, *args): pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"Web server on {PORT}")
    server.serve_forever()

# ---- YOUR LOGIC (same as before) ----
async def update_sol_price():
    global sol_price
    while True:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112", timeout=10)
                sol_price = float(r.json()["pairs"][0]["priceUsd"])
        except: pass
        await asyncio.sleep(60)

async def get_token_info(mint):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
            pair = sorted(r.json()["pairs"], key=lambda x: x.get("fdv",0) or 0, reverse=True)[0]
            return pair["baseToken"]["symbol"], float(pair.get("fdv",0) or 0), pair.get("priceUsd","0")
    except: return mint[:6], 0, "0"

async def handle_funding(factory, to_wallet, lamports):
    usd = (lamports / 1e9) * sol_price
    if usd < 0.1: return
    now = time.time()
    is_dust = usd < THRESHOLD
    tracker = dust_tracker if is_dust else main_tracker
    key = f"{factory}_{'dust' if is_dust else 'main'}"
    tracker[factory].append((now, to_wallet, usd))
    tracker[factory] = [x for x in tracker[factory] if now - x[0] < 600]
    count = len(tracker[factory])
    total = sum(x[2] for x in tracker[factory])
    limit = 15 if is_dust else 3
    if count >= limit and now - last_alert[key] > 600:
        if is_dust:
            msg = f"⚠️ DUST FACTORY [{factory[:4]}] {count} wallets <${THRESHOLD:.0f} in 10min\nTotal ${total:.0f} | Avg ${total/count:.2f}"
        else:
            preview = ", ".join([w[:4] for _,w,_ in tracker[factory][:4]])
            msg = f"💰 MAIN FACTORY [{factory[:4]}] {count} wallets >${THRESHOLD:.0f} in 10min\nTotal ${total:.0f} | Avg ${total/count:.0f}\nWallets: {preview}"
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert[key] = now
        for _, w, _ in tracker[factory]:
            watched_workers[w] = {"origin": factory, "type": "dust" if is_dust else "main", "time": now}
        tracker[factory].clear()

async def handle_buy(wallet, mint, usd):
    if wallet not in watched_workers: return
    now = time.time()
    bundle_buys[mint].append((now, wallet, usd))
    bundle_buys[mint] = [x for x in bundle_buys[mint] if now - x[0] < 30]
    wc = len(bundle_buys[mint])
    total = sum(x[2] for x in bundle_buys[mint])
    if wc >= 3 and total >= 1000:
        symbol, mcap, price = await get_token_info(mint)
        origin = watched_workers[wallet]["origin"]
        msg = f"🔥 BUNDLE ALERT\nToken: {symbol}\nMint: {mint}\nPrice: ${price}\nMCap: ${mcap:,.0f}\nBuy: {wc} wallets ${total:.0f} in 30s\nOrigin: {origin[:4]}\nTime: NOW!"
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        bundle_buys[mint].clear()

async def get_tx(sig):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}, timeout=10)
            return r.json().get("result")
    except: return None

def parse_transfer(meta, keys):
    transfers = []
    try:
        pre, post = meta.get("preBalances",[]), meta.get("postBalances",[])
        for i in range(len(pre)):
            diff = pre[i] - post[i]
            if diff > 1_000_000:
                for j in range(len(post)):
                    if post[j] - pre[j] > 1_000_000:
                        transfers.append((keys[i], keys[j], post[j]-pre[j]))
    except: pass
    return transfers

async def transfer_loop():
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions":["11111111111111111111111111111111"]}, {"commitment":"confirmed"}]}))
                async for m in ws:
                    data = json.loads(m)
                    if "params" not in data: continue
                    sig = data["params"]["result"]["value"].get("signature")
                    if not sig: continue
                    tx = await get_tx(sig)
                    if not tx or tx["meta"].get("err"): continue
                    keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                    for f,t,l in parse_transfer(tx["meta"], keys):
                        await handle_funding(f,t,l)
        except Exception as e:
            await asyncio.sleep(5)

async def swap_loop():
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                await ws.send(json.dumps({"jsonrpc":"2.0","id":2,"method":"logsSubscribe","params":[{"mentions":[PUMP_FUN]}, {"commitment":"confirmed"}]}))
                await ws.send(json.dumps({"jsonrpc":"2.0","id":3,"method":"logsSubscribe","params":[{"mentions":[RAYDIUM]}, {"commitment":"confirmed"}]}))
                async for m in ws:
                    data = json.loads(m)
                    if "params" not in data: continue
                    sig = data["params"]["result"]["value"].get("signature")
                    tx = await get_tx(sig)
                    if not tx or tx["meta"].get("err"): continue
                    keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                    wallet = next((k for k in keys if k in watched_workers), None)
                    if not wallet: continue
                    for post in tx["meta"].get("postTokenBalances",[]):
                        if post.get("owner")==wallet and post.get("mint"):
                            await handle_buy(wallet, post["mint"], 50)
        except: await asyncio.sleep(5)

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        print(f"alive {len(watched_workers)} workers")
        gc.collect()

async def main():
    # Start web server in thread
    threading.Thread(target=run_web_server, daemon=True).start()
    await bot.send_message(chat_id=CHAT_ID, text=f"✅ V32 Web Service Started\nWatching <$45 AND >$45\nPort {PORT}\nAdd URL to Uptimerobot")
    await asyncio.gather(update_sol_price(), transfer_loop(), swap_loop(), keep_alive())

if __name__ == "__main__":
    asyncio.run(main())
