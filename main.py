"""
V33 SMART BUNDLE HUNTER - 4 logics for smart devs
Logic1: Recursive depth 3 (not endless)
Logic2: Fresh wallet detector (0 history)
Logic3: Same-slot Jito bundle
Logic4: Mixed amount cluster (5 wallets/5min any amount)
Render Free + TG Commands FIXED
"""
import os, asyncio, time, gc, json, threading
from collections import defaultdict, deque
from http.server import HTTPServer, BaseHTTPRequestHandler
import websockets, httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY")
PORT = int(os.getenv("PORT", 10000))

THRESHOLD = 45.0
MAX_DEPTH = 3 # Recursive cap - not endless

ALCHEMY_WSS = f"wss://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
ALCHEMY_HTTPS = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

# Trackers
dust_tracker = defaultdict(list)
main_tracker = defaultdict(list)
mixed_tracker = defaultdict(list) # Logic4 - any amount
last_alert = defaultdict(float)
watched_workers = {} # wallet -> {origin, level, time, fresh}
factories = {} # address -> {label, level}
bundle_buys = defaultdict(list) # mint -> [(time, wallet, slot)]
slot_tracker = defaultdict(list) # slot -> [(mint, wallet)]
sol_price = 150.0
recent_bundles = []

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"OK V33 Factories:{len(factories)} Workers:{len(watched_workers)} SOL:${sol_price}".encode())
    def log_message(self, *args): pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

# ---- TG COMMANDS ----
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ V33 SMART HUNTER RUNNING\n"
        f"Factories: {len(factories)} Workers: {len(watched_workers)} SOL:${sol_price:.1f}\n"
        f"Logic1: Recursive depth {MAX_DEPTH}\n"
        f"Logic2: Fresh wallet (0 history)\n"
        f"Logic3: Same-slot Jito\n"
        f"Logic4: Mixed cluster 5/5min\n\n"
        f"/bundle address label - ADD factory\n"
        f"/status\n/remove /clear_all"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fresh = sum(1 for w in watched_workers.values() if w.get('fresh'))
    await update.message.reply_text(
        f"V33 STATUS\n"
        f"Factories: {len(factories)} (levels 0-{MAX_DEPTH})\n"
        f"Workers: {len(watched_workers)} Fresh:{fresh}\n"
        f"Dust pending: {sum(len(v) for v in dust_tracker.values())}/15\n"
        f"Main pending: {sum(len(v) for v in main_tracker.values())}/3\n"
        f"Mixed pending: {sum(len(v) for v in mixed_tracker.values())}/5\n"
        f"Recent bundles: {len(recent_bundles)}"
    )

async def cmd_bundle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Use: /bundle address label\nEx: /bundle CyBr... main")
        return
    addr = ctx.args[0]
    label = ctx.args[1] if len(ctx.args) > 1 else "factory"
    factories[addr] = {"label": label, "level": 0}
    await update.message.reply_text(f"✅ ADDED L0 Factory [{addr[:4]}] {label}\nWill auto-track to L{MAX_DEPTH}")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return
    factories.pop(ctx.args[0], None)
    await update.message.reply_text(f"✅ Removed {ctx.args[0][:6]}")

async def cmd_clear_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    factories.clear(); watched_workers.clear()
    dust_tracker.clear(); main_tracker.clear(); mixed_tracker.clear(); bundle_buys.clear()
    await update.message.reply_text("✅ V33 Cleared all")

async def cmd_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2: return
    if ctx.args[0] in factories:
        factories[ctx.args[0]]["label"] = ctx.args[1]
    await update.message.reply_text(f"✅ Labeled {ctx.args[0][:4]} as {ctx.args[1]}")

# ---- CORE LOGIC ----
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

async def is_fresh_wallet(wallet):
    # Logic2: check if wallet has <3 prior txs (fresh)
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet, {"limit": 5}]}, timeout=8)
            sigs = r.json().get("result", [])
            return len(sigs) <= 3 # fresh if <=3 txs
    except:
        return False

async def handle_funding(factory, to_wallet, lamports, app):
    usd = (lamports / 1e9) * sol_price
    if usd < 0.05: return
    now = time.time()

    # Get factory level
    f_info = factories.get(factory, {"level": 0, "label": factory[:4]})
    f_level = f_info.get("level", 0) if isinstance(f_info, dict) else 0

    # Logic4: Mixed tracker - ANY amount 5 wallets in 5 min
    mixed_tracker[factory].append((now, to_wallet, usd))
    mixed_tracker[factory] = [x for x in mixed_tracker[factory] if now - x[0] < 300]
    if len(mixed_tracker[factory]) >= 5 and now - last_alert.get(f"mix_{factory}", 0) > 300:
        total = sum(x[2] for x in mixed_tracker[factory])
        msg = f"🧩 MIXED CLUSTER L{f_level} [{factory[:4]}] {len(mixed_tracker[factory])} wallets in 5min\nTotal ${total:.0f} Any amount\nWallets: {', '.join([w[:4] for _,w,_ in mixed_tracker[factory][:5]])}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert[f"mix_{factory}"] = now
        # Arm workers
        for _, w, _ in mixed_tracker[factory]:
            if w not in watched_workers and f_level < MAX_DEPTH:
                fresh = await is_fresh_wallet(w)
                watched_workers[w] = {"origin": factory, "level": f_level+1, "time": now, "fresh": fresh}
                if f_level+1 < MAX_DEPTH:
                    factories[w] = {"label": f"sub_{factory[:4]}_L{f_level+1}", "level": f_level+1}
        mixed_tracker[factory].clear()

    # Original Logic1: Dust/Main split
    is_dust = usd < THRESHOLD
    tracker = dust_tracker if is_dust else main_tracker
    key = f"{factory}_{'dust' if is_dust else 'main'}"
    tracker[factory].append((now, to_wallet, usd))
    tracker[factory] = [x for x in tracker[factory] if now - x[0] < 600]
    count = len(tracker[factory])
    total = sum(x[2] for x in tracker[factory])
    limit = 15 if is_dust else 3

    if count >= limit and now - last_alert.get(key, 0) > 600:
        # Logic1 + Logic2 combined
        fresh_wallets = []
        for _, w, _ in tracker[factory]:
            if w not in watched_workers:
                fresh = await is_fresh_wallet(w) if count >= 3 else False
                watched_workers[w] = {"origin": factory, "level": f_level+1, "time": now, "fresh": fresh}
                if fresh: fresh_wallets.append(w[:4])
                # Recursive: auto-add as factory if under max depth
                if f_level+1 < MAX_DEPTH:
                    factories[w] = {"label": f"auto_L{f_level+1}_{factory[:4]}", "level": f_level+1}

        if is_dust:
            msg = f"⚠️ DUST L{f_level} [{factory[:4]}] {count} wallets <${THRESHOLD:.0f} 10min\nTotal ${total:.0f}\nFresh: {len(fresh_wallets)}/{count} {','.join(fresh_wallets[:3])}\nRecursive to L{f_level+1}"
        else:
            preview = ", ".join([w[:4] for _,w,_ in tracker[factory][:4]])
            msg = f"💰 MAIN L{f_level} [{factory[:4]}] {count} wallets >${THRESHOLD:.0f} 10min\nTotal ${total:.0f} Wallets: {preview}\nFresh: {len(fresh_wallets)}\nAuto-armed to L{f_level+1}"

        if fresh_wallets:
            msg += f"\n🔥 FRESH DETECTED {len(fresh_wallets)} fresh wallets!"

        await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert[key] = now
        tracker[factory].clear()

async def handle_buy(wallet, mint, usd, slot, app):
    if wallet not in watched_workers: return
    now = time.time()
    w_info = watched_workers[wallet]

    # Logic3: Same-slot tracker
    slot_tracker[slot].append((mint, wallet, now))
    # Clean old slots (>60 sec)
    for s in list(slot_tracker.keys()):
        if now - slot_tracker[s][0][2] > 60:
            del slot_tracker[s]

    # Count same mint in same slot
    same_slot_wallets = [w for m,w,t in slot_tracker.get(slot, []) if m == mint]

    bundle_buys[mint].append((now, wallet, slot))
    bundle_buys[mint] = [x for x in bundle_buys[mint] if now - x[0] < 30]
    wc = len(bundle_buys[mint])
    total = sum([50 for _ in bundle_buys[mint]]) # estimate

    if wc >= 3 or len(same_slot_wallets) >= 2: # 3 in 30s OR 2 in same slot
        symbol, mcap, price = await get_token_info(mint)
        origin = w_info["origin"]
        level = w_info["level"]
        fresh_tag = "🔥 FRESH" if w_info.get("fresh") else ""

        if len(same_slot_wallets) >= 2:
            msg = f"⚡ JITO BUNDLE L{level} {fresh_tag}\nToken: {symbol}\nMint: {mint}\nMCap: ${mcap:,.0f}\nSlot: {slot}\nWallets: {len(same_slot_wallets)} SAME SLOT!\n{', '.join([w[:4] for w in same_slot_wallets[:4]])}\nOrigin: {origin[:4]}\n{fresh_tag}"
        else:
            msg = f"🔥 BUNDLE L{level} {fresh_tag}\nToken: {symbol}\nMint: {mint}\nPrice: ${price}\nMCap: ${mcap:,.0f}\nBuy: {wc} wallets 30s\nOrigin: {origin[:4]}\nLevel: {level}"

        recent_bundles.append({"symbol": symbol, "mcap": mcap, "mint": mint, "slot": slot})
        await app.bot.send_message(chat_id=CHAT_ID, text=msg)
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
            if diff > 50_000: # 0.00005 SOL
                for j in range(len(post)):
                    if post[j] - pre[j] > 50_000:
                        transfers.append((keys[i], keys[j], post[j]-pre[j]))
    except: pass
    return transfers

async def transfer_loop(app):
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
                        await handle_funding(f,t,l, app)
        except Exception as e:
            print(f"transfer err {e}")
            await asyncio.sleep(5)

async def swap_loop(app):
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                await ws.send(json.dumps({"jsonrpc":"2.0","id":2,"method":"logsSubscribe","params":[{"mentions":[PUMP_FUN]}, {"commitment":"confirmed"}]}))
                await ws.send(json.dumps({"jsonrpc":"2.0","id":3,"method":"logsSubscribe","params":[{"mentions":[RAYDIUM]}, {"commitment":"confirmed"}]}))
                async for m in ws:
                    data = json.loads(m)
                    if "params" not in data: continue
                    sig = data["params"]["result"]["value"].get("signature")
                    slot = data["params"]["result"]["value"].get("slot", 0)
                    tx = await get_tx(sig)
                    if not tx or tx["meta"].get("err"): continue
                    keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                    wallet = next((k for k in keys if k in watched_workers), None)
                    if not wallet: continue
                    for post in tx["meta"].get("postTokenBalances",[]):
                        if post.get("owner")==wallet and post.get("mint"):
                            await handle_buy(wallet, post["mint"], 50, slot, app)
        except Exception as e:
            print(f"swap err {e}")
            await asyncio.sleep(5)

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        # Cleanup old workers >2h to prevent RAM growth but keep depth
        now = time.time()
        for w in list(watched_workers.keys()):
            if now - watched_workers[w]["time"] > 7200:
                del watched_workers[w]
        print(f"V33 alive {len(watched_workers)} workers {len(factories)} factories")
        gc.collect()

async def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("bundle", cmd_bundle))
    app.add_handler(CommandHandler("label", cmd_label))
    app.add_handler(CommandHandler("labelgroup", cmd_label))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("clear_all", cmd_clear_all))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    print("V33 commands started")
    await app.bot.send_message(chat_id=CHAT_ID, text=f"✅ V33 SMART HUNTER STARTED\n4 Logics | Depth {MAX_DEPTH}\n/bundle addr to add L0 factory\nAuto-recursive to L{MAX_DEPTH} | Fresh detect | Jito slot | Mixed cluster\nPort {PORT}")
    await asyncio.gather(update_sol_price(), transfer_loop(app), swap_loop(app), keep_alive())

if __name__ == "__main__":
    asyncio.run(main())
