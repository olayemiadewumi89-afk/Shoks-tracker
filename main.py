"""
V33.4 FINAL MERGED - FACTORY LISTENER + ANY DEX (DFLOW/JUPITER/PUMP/RAYDIUM)
Fix 1: transfer_loop = factory-only (catches funding after 1PM)
Fix 2: swap_loop = listens to Jupiter, DFlow, Token Program (catches USDC swaps)
"""
import os, asyncio, time, gc, json, threading
from collections import defaultdict
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
MAX_DEPTH = 3

ALCHEMY_WSS = f"wss://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
ALCHEMY_HTTPS = f"https://solana-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

dust_tracker = defaultdict(list)
main_tracker = defaultdict(list)
mixed_tracker = defaultdict(list)
last_alert = defaultdict(float)
watched_workers = {}
factories = {}
bundle_buys = defaultdict(list)
slot_tracker = defaultdict(list)
sol_price = 0.0
recent_bundles = []

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"OK V33.4 Factories:{len(factories)} Workers:{len(watched_workers)} SOL:${sol_price:.2f}".encode())
    def log_message(self, *args): pass

def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    price_txt = f"${sol_price:.2f}" if sol_price > 0 else "FETCHING..."
    await update.message.reply_text(
        f"✅ V33.4 SMART HUNTER RUNNING\n"
        f"Factories: {len(factories)} Workers: {len(watched_workers)} SOL:{price_txt}\n"
        f"Logic1: Recursive depth {MAX_DEPTH}\n"
        f"Logic2: Fresh wallet\n"
        f"Logic3: Same-slot Jito\n"
        f"Logic4: Mixed cluster 5/5min\n"
        f"Funding: Factory-only (FIXED)\n"
        f"Swaps: Pump+Ray+Jupiter+DFlow+Token (FIXED)\n\n"
        f"/bundle address label\n/status /remove /clear_all"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    fresh = sum(1 for w in watched_workers.values() if w.get('fresh'))
    price_txt = f"${sol_price:.2f}" if sol_price > 0 else "FETCHING..."
    listening = ", ".join([k[:4] for k in factories.keys()][:10])
    await update.message.reply_text(
        f"V33.4 STATUS\n"
        f"SOL Live: {price_txt}\n"
        f"Factories: {len(factories)} (L0-{MAX_DEPTH})\n"
        f"Workers: {len(watched_workers)} Fresh:{fresh}\n"
        f"Dust: {sum(len(v) for v in dust_tracker.values())}/15 Main: {sum(len(v) for v in main_tracker.values())}/3 Mixed: {sum(len(v) for v in mixed_tracker.values())}/5\n"
        f"Listening: {listening}"
    )

async def cmd_bundle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Use: /bundle address label")
        return
    addr = ctx.args[0]
    label = ctx.args[1] if len(ctx.args) > 1 else "factory"
    factories[addr] = {"label": label, "level": 0}
    await update.message.reply_text(f"✅ ADDED L0 Factory [{addr[:4]}] {label} -> auto L{MAX_DEPTH}\nNow listening directly to it.")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return
    factories.pop(ctx.args[0], None)
    await update.message.reply_text(f"✅ Removed {ctx.args[0][:6]}")

async def cmd_clear_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    factories.clear(); watched_workers.clear()
    dust_tracker.clear(); main_tracker.clear(); mixed_tracker.clear(); bundle_buys.clear(); slot_tracker.clear()
    await update.message.reply_text("✅ V33.4 Cleared all")

async def cmd_label(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) < 2: return
    if ctx.args[0] in factories:
        factories[ctx.args[0]]["label"] = ctx.args[1]
    await update.message.reply_text(f"✅ Labeled {ctx.args[0][:4]} as {ctx.args[1]}")

async def update_sol_price():
    global sol_price
    while True:
        price = None
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get("https://price.jup.ag/v6/price?ids=So11111111111111111111111111111111111111112", timeout=8)
                price = float(r.json()["data"]["So11111111111111111111111111111111111111112"]["price"])
        except: pass
        if not price:
            try:
                async with httpx.AsyncClient() as c:
                    r = await c.get("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112", timeout=8)
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        sol_pair = sorted(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0), reverse=True)[0]
                        price = float(sol_pair["priceUsd"])
            except: pass
        if not price:
            try:
                async with httpx.AsyncClient() as c:
                    r = await c.get("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", timeout=8)
                    price = float(r.json()["solana"]["usd"])
            except: pass
        if price and 10 < price < 1000:
            sol_price = price
        await asyncio.sleep(30)

async def get_token_info(mint):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
            data = r.json()
            if not data.get("pairs"): return mint[:6], 0, "0"
            pair = sorted(data["pairs"], key=lambda x: x.get("fdv",0) or 0, reverse=True)[0]
            return pair["baseToken"]["symbol"], float(pair.get("fdv",0) or 0), pair.get("priceUsd","0")
    except: return mint[:6], 0, "0"

async def is_fresh_wallet(wallet):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet, {"limit": 5}]}, timeout=8)
            sigs = r.json().get("result", [])
            return len(sigs) <= 3
    except: return False

async def handle_funding(factory, to_wallet, lamports, app):
    usd = (lamports / 1e9) * sol_price if sol_price > 0 else (lamports / 1e9) * 105.54
    if usd < 0.05: return
    now = time.time()
    f_info = factories.get(factory, {"level": 0, "label": factory[:4]})
    f_level = f_info.get("level", 0) if isinstance(f_info, dict) else 0
    print(f"FUNDING CAUGHT {factory[:4]} -> {to_wallet[:4]} {lamports/1e9:.3f} SOL ${usd:.1f}")

    mixed_tracker[factory].append((now, to_wallet, usd))
    mixed_tracker[factory] = [x for x in mixed_tracker[factory] if now - x[0] < 300]
    if len(mixed_tracker[factory]) >= 5 and now - last_alert.get(f"mix_{factory}", 0) > 300:
        total = sum(x[2] for x in mixed_tracker[factory])
        msg = f"🧩 MIXED CLUSTER L{f_level} [{factory[:4]}] {len(mixed_tracker[factory])} wallets 5min Total ${total:.0f}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert[f"mix_{factory}"] = now
        for _, w, _ in mixed_tracker[factory]:
            if w not in watched_workers and f_level < MAX_DEPTH:
                fresh = await is_fresh_wallet(w)
                watched_workers[w] = {"origin": factory, "level": f_level+1, "time": now, "fresh": fresh}
                if f_level+1 < MAX_DEPTH:
                    factories[w] = {"label": f"sub_{factory[:4]}_L{f_level+1}", "level": f_level+1}
        mixed_tracker[factory].clear()

    is_dust = usd < THRESHOLD
    tracker = dust_tracker if is_dust else main_tracker
    key = f"{factory}_{'dust' if is_dust else 'main'}"
    tracker[factory].append((now, to_wallet, usd))
    tracker[factory] = [x for x in tracker[factory] if now - x[0] < 600]
    count = len(tracker[factory])
    total = sum(x[2] for x in tracker[factory])
    limit = 15 if is_dust else 3

    if count >= limit and now - last_alert.get(key, 0) > 600:
        fresh_wallets = []
        for _, w, _ in tracker[factory]:
            if w not in watched_workers:
                fresh = await is_fresh_wallet(w) if count >= 3 else False
                watched_workers[w] = {"origin": factory, "level": f_level+1, "time": now, "fresh": fresh}
                if fresh: fresh_wallets.append(w[:4])
                if f_level+1 < MAX_DEPTH:
                    factories[w] = {"label": f"auto_L{f_level+1}_{factory[:4]}", "level": f_level+1}
        if is_dust:
            msg = f"⚠️ DUST L{f_level} [{factory[:4]}] {count} wallets <${THRESHOLD:.0f} 10min Total ${total:.0f} Fresh:{len(fresh_wallets)}/{count}"
        else:
            preview = ", ".join([w[:4] for _,w,_ in tracker[factory][:4]])
            msg = f"💰 MAIN L{f_level} [{factory[:4]}] {count} wallets >${THRESHOLD:.0f} 10min Total ${total:.0f} Wallets:{preview} Fresh:{len(fresh_wallets)}"
        if fresh_wallets:
            msg += f" 🔥 FRESH {len(fresh_wallets)}!"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg)
        last_alert[key] = now
        tracker[factory].clear()

# ---- BUY ALERT WITH SHORT CLICKABLE LINKS ----
async def handle_buy(wallet, mint, slot, sig, app):
    if wallet not in watched_workers: return
    now = time.time()
    w_info = watched_workers[wallet]
    slot_tracker[slot].append((mint, wallet, now, sig))
    for s in list(slot_tracker.keys()):
        if slot_tracker[s] and now - slot_tracker[s][0][2] > 60:
            del slot_tracker[s]
    same_slot = [(w,sg) for m,w,t,sg in slot_tracker.get(slot, []) if m == mint]
    same_slot_wallets = [w for w,sg in same_slot]

    bundle_buys[mint].append((now, wallet, slot, sig))
    bundle_buys[mint] = [x for x in bundle_buys[mint] if now - x[0] < 30]
    wc = len(bundle_buys[mint])

    if wc >= 3 or len(same_slot_wallets) >= 2:
        symbol, mcap, price = await get_token_info(mint)
        origin = w_info["origin"]
        level = w_info["level"]
        fresh_tag = "🔥 FRESH" if w_info.get("fresh") else ""

        dex_link = f"https://dexscreener.com/solana/{mint}"
        photon_link = f"https://photon-sol.tinyastro.io/en/lp/{mint}"
        solscan_link = f"https://solscan.io/token/{mint}"
        tx_link = f"https://solscan.io/tx/{sig}" if sig else solscan_link

        if len(same_slot_wallets) >= 2:
            msg = (
                f"⚡ JITO BUNDLE L{level} {fresh_tag}\n"
                f"Token: {symbol} | ${mcap:,.0f} MCap\n"
                f"Mint: <code>{mint}</code>\n"
                f"Slot: {slot} | {len(same_slot_wallets)} SAME SLOT!\n"
                f"Origin: {origin[:4]} | Wallets: {', '.join([w[:4] for w in same_slot_wallets[:4]])}\n\n"
                f"<a href=\"{dex_link}\">DEX</a> | <a href=\"{photon_link}\">Photon</a> | <a href=\"{tx_link}\">TX</a> | <a href=\"{solscan_link}\">Solscan</a>"
            )
        else:
            msg = (
                f"🔥 BUNDLE L{level} {fresh_tag}\n"
                f"Token: {symbol} | ${mcap:,.0f} MCap\n"
                f"Mint: <code>{mint}</code>\n"
                f"Buy: {wc} wallets 30s | Origin: {origin[:4]}\n\n"
                f"<a href=\"{dex_link}\">DEX</a> | <a href=\"{photon_link}\">Photon</a> | <a href=\"{tx_link}\">TX</a> | <a href=\"{solscan_link}\">Solscan</a>"
            )

        recent_bundles.append({"symbol": symbol, "mcap": mcap, "mint": mint, "slot": slot})
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML", disable_web_page_preview=True)
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
            if diff > 50_000:
                for j in range(len(post)):
                    if post[j] - pre[j] > 50_000:
                        transfers.append((keys[i], keys[j], post[j]-pre[j]))
    except: pass
    return transfers

# ---- FIXED TRANSFER LOOP - FACTORY ONLY LISTENER ----
async def transfer_loop(app):
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                print("V33.4 transfer loop connected - factory-only mode")
                subscribed = set()
                for f in list(factories.keys()):
                    await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions":[f]}, {"commitment":"confirmed"}]}))
                    subscribed.add(f)
                    print(f"SUBSCRIBED factory {f[:8]}")
                while True:
                    for f in list(factories.keys()):
                        if f not in subscribed:
                            await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions":[f]}, {"commitment":"confirmed"}]}))
                            subscribed.add(f)
                            print(f"NEW SUBSCRIBED {f[:8]}")
                            try:
                                await app.bot.send_message(chat_id=CHAT_ID, text=f"🔍 Now listening to new factory {f[:4]}")
                            except: pass
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        data = json.loads(raw)
                        if "params" not in data: continue
                        sig = data["params"]["result"]["value"].get("signature")
                        if not sig: continue
                        tx = await get_tx(sig)
                        if not tx or tx["meta"].get("err"): continue
                        keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                        pre = tx["meta"].get("preBalances", [])
                        post = tx["meta"].get("postBalances", [])
                        for i in range(min(len(keys), len(pre))):
                            if keys[i] in factories and pre[i] > post[i]:
                                for j in range(min(len(keys), len(post))):
                                    if post[j] > pre[j] and keys[j]!= keys[i]:
                                        recv = post[j] - pre[j]
                                        if recv > 5_000_000:
                                            await handle_funding(keys[i], keys[j], recv, app)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            print(f"transfer err {e} - reconnect in 5s")
            await asyncio.sleep(5)

# ---- FIXED SWAP LOOP - CATCHES DFLOW/JUPITER/ANY DEX ----
async def swap_loop(app):
    PROGRAMS = [
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", # Pump
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", # Raydium
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", # Jupiter V6
        "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB", # Jupiter V4
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", # SPL Token - catches DFlow v4 + ANY swap
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc", # Orca Whirlpool
    ]
    STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                for i, prog in enumerate(PROGRAMS):
                    await ws.send(json.dumps({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe","params":[{"mentions":[prog]}, {"commitment":"confirmed"}]}))
                    print(f"Swap listener {prog[:4]}")
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
                            mint = post["mint"]
                            if mint in STABLES: continue
                            await handle_buy(wallet, mint, slot, sig, app)
        except Exception as e:
            print(f"swap err {e}")
            await asyncio.sleep(5)

async def keep_alive():
    while True:
        await asyncio.sleep(600)
        now = time.time()
        for w in list(watched_workers.keys()):
            if now - watched_workers[w]["time"] > 7200:
                del watched_workers[w]
        print(f"V33.4 alive {len(watched_workers)} workers {len(factories)} factories SOL ${sol_price}")
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
    print("V33.4 commands started")
    price_init = f"${sol_price:.2f}" if sol_price>0 else "fetching..."
    await app.bot.send_message(chat_id=CHAT_ID, text=f"✅ V33.4 LIVE + LINKS + FIXED BOTH\nSOL: {price_init} live 30s\nFunding: Factory-only (fixes 1PM miss)\nSwaps: Pump+Ray+Jup+DFlow+Token (fixes USDC miss)\nBuy: DEX | Photon | TX | Solscan clickable\n/bundle addr to add\nPort {PORT}")
    await asyncio.gather(update_sol_price(), transfer_loop(app), swap_loop(app), keep_alive())

if __name__ == "__main__":
    asyncio.run(main())
