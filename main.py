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

dust_tracker = defaultdict(list)
main_tracker = defaultdict(list)
mixed_tracker = defaultdict(list)
last_alert = defaultdict(float)
watched_workers = {}
factories = {}
bundle_buys = defaultdict(list)
slot_tracker = defaultdict(list)
sol_price = 0.0

SAVE_FILE = "save.json"
if os.path.exists(SAVE_FILE):
    try:
        with open(SAVE_FILE, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                factories.update(data.get("factories", {}))
                watched_workers.update(data.get("workers", {}))
        print(f"LOADED {len(factories)} factories")
    except:
        print("save.json corrupted - deleting")
        try: os.remove(SAVE_FILE)
        except: pass
        factories = {}
        watched_workers = {}

def save_all():
    try:
        with open(SAVE_FILE, "w") as f:
            json.dump({"factories": factories, "workers": watched_workers}, f)
    except Exception as e:
        print(f"save err {e}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"OK V33.9 F:{len(factories)} W:{len(watched_workers)}".encode())
    def log_message(self, *args): pass

def run_web_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(f"✅ V33.9 ALIVE\nFactories:{len(factories)} Workers:{len(watched_workers)} SOL:${sol_price:.2f}\n/status works now")
    except Exception as e:
        print(f"cmd_start err {e}")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        fresh = sum(1 for w in watched_workers.values() if w.get('fresh'))
        listening = ", ".join([k[:4] for k in factories.keys()][:12])
        await update.message.reply_text(f"V33.9 STATUS\nSOL:${sol_price:.2f}\nFactories:{len(factories)} Workers:{len(watched_workers)} Fresh:{fresh}\nDust:{sum(len(v) for v in dust_tracker.values())}/15 Main:{sum(len(v) for v in main_tracker.values())}/3\nListening:{listening}\nSAVE_FILE:{os.path.exists(SAVE_FILE)}")
    except Exception as e:
        print(f"status err {e}")

async def cmd_bundle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not ctx.args:
            await update.message.reply_text("Use: /bundle ADDR label"); return
        addr = ctx.args[0].strip(); label = ctx.args[1] if len(ctx.args)>1 else "factory"
        if len(addr) < 32:
            await update.message.reply_text("❌ Invalid address"); return
        factories[addr] = {"label": label, "level": 0}
        save_all()
        await update.message.reply_text(f"✅ ADDED [{addr[:4]}] {label} SAVED. Total:{len(factories)}")
    except Exception as e:
        print(f"bundle err {e}")
        await update.message.reply_text(f"Error: {e}")

async def cmd_clear_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        factories.clear(); watched_workers.clear(); dust_tracker.clear(); main_tracker.clear(); mixed_tracker.clear(); bundle_buys.clear(); slot_tracker.clear()
        if os.path.exists(SAVE_FILE): os.remove(SAVE_FILE)
        await update.message.reply_text("✅ CLEARED ALL + deleted save.json")
    except Exception as e:
        await update.message.reply_text(f"Error clearing: {e}")

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: return
    factories.pop(ctx.args[0], None); save_all()
    await update.message.reply_text(f"✅ Removed {ctx.args[0][:4]}")

async def update_sol_price():
    global sol_price
    while True:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get("https://price.jup.ag/v6/price?ids=So11111111111111111111111111111111111111112", timeout=10)
                sol_price = float(r.json()["data"]["So11111111111111111111111111111111111111112"]["price"])
        except: pass
        await asyncio.sleep(30)

async def get_token_info(mint):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
            data = r.json()
            if not data.get("pairs"): return mint[:6],0,"0"
            pair = sorted(data["pairs"], key=lambda x: x.get("fdv",0) or 0, reverse=True)[0]
            return pair["baseToken"]["symbol"], float(pair.get("fdv",0) or 0), pair.get("priceUsd","0")
    except: return mint[:6],0,"0"

async def is_fresh_wallet(wallet):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":5}]}, timeout=8)
            return len(r.json().get("result",[])) <=3
    except: return False

async def handle_funding(factory, to_wallet, lamports, app):
    try:
        usd = (lamports/1e9)*sol_price if sol_price>0 else (lamports/1e9)*104
        if usd < 0.05: return
        now = time.time()
        f_level = factories.get(factory,{}).get("level",0)
        print(f"FUNDING {factory[:4]}->{to_wallet[:4]} ${usd:.2f}", flush=True)
        tracker = dust_tracker if usd < THRESHOLD else main_tracker
        key = f"{factory}_{'dust' if usd < THRESHOLD else 'main'}"
        tracker[factory].append((now,to_wallet,usd))
        tracker[factory] = [x for x in tracker[factory] if now-x[0] < 600]
        limit = 15 if usd < THRESHOLD else 3
        if len(tracker[factory]) >= limit and now-last_alert.get(key,0) > 600:
            for _,w,_ in tracker[factory]:
                if w not in watched_workers:
                    watched_workers[w] = {"origin":factory,"level":f_level+1,"time":now,"fresh":await is_fresh_wallet(w)}
                    if f_level+1 < MAX_DEPTH: factories[w] = {"label":f"auto_L{f_level+1}_{factory[:4]}","level":f_level+1}
            msg = f"{'⚠️ DUST' if usd < THRESHOLD else '💰 MAIN'} L{f_level} [{factory[:4]}] {len(tracker[factory])} wallets"
            await app.bot.send_message(chat_id=CHAT_ID, text=msg)
            last_alert[key]=now; tracker[factory].clear(); save_all()
    except Exception as e:
        print(f"handle_funding err {e}")

async def handle_buy(wallet, mint, slot, sig, app):
    try:
        if wallet not in watched_workers: return
        now = time.time()
        w_info = watched_workers[wallet]
        slot_tracker[slot].append((mint,wallet,now,sig))
        bundle_buys[mint].append((now,wallet,slot,sig))
        bundle_buys[mint] = [x for x in bundle_buys[mint] if now-x[0] < 30]
        wc = len(bundle_buys[mint])
        same_slot = [w for m,w,t,sg in slot_tracker.get(slot,[]) if m==mint]
        if wc>=3 or len(same_slot)>=2:
            symbol,mcap,_ = await get_token_info(mint)
            dex = f"https://dexscreener.com/solana/{mint}"
            photon = f"https://photon-sol.tinyastro.io/en/lp/{mint}"
            tx = f"https://solscan.io/tx/{sig}"
            msg = f"🔥 BUNDLE L{w_info['level']}\nToken:{symbol} | ${mcap:,.0f}\nMint:<code>{mint}</code>\nBuy:{wc} wallets\n\n<a href=\"{dex}\">DEX</a> | <a href=\"{photon}\">Photon</a> | <a href=\"{tx}\">TX</a>"
            await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="HTML", disable_web_page_preview=True)
            bundle_buys[mint].clear()
    except Exception as e:
        print(f"handle_buy err {e}")

async def get_tx(sig):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}, timeout=10)
            return r.json().get("result")
    except: return None

async def transfer_loop(app):
    last_sig = {}
    while True:
        try:
            # POLL every 6 sec - never crashes main bot
            for factory in list(factories.keys()):
                try:
                    async with httpx.AsyncClient() as c:
                        r = await c.post(ALCHEMY_HTTPS, json={"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[factory,{"limit":10}]}, timeout=10)
                        sigs = r.json().get("result",[])
                        for s in reversed(sigs):
                            sig = s["signature"]
                            if last_sig.get(factory)==sig: break
                            if s.get("err"): continue
                            tx = await get_tx(sig)
                            if not tx or tx["meta"].get("err"): continue
                            keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                            pre = tx["meta"].get("preBalances",[]); post = tx["meta"].get("postBalances",[])
                            for i in range(min(len(keys), len(pre))):
                                if keys[i]==factory and pre[i]>post[i]:
                                    for j in range(min(len(keys), len(post))):
                                        if post[j]>pre[j] and keys[j]!=keys[i]:
                                            recv = post[j]-pre[j]
                                            if recv>1_000_000: await handle_funding(factory, keys[j], recv, app)
                        if sigs: last_sig[factory]=sigs[0]["signature"]
                except: pass
            await asyncio.sleep(6)
        except Exception as e:
            print(f"transfer_loop outer {e}"); await asyncio.sleep(5)

async def swap_loop(app):
    PROGRAMS = ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P","675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8","JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4","TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"]
    STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v","Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB","So11111111111111111111111111111111111111112"}
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS, ping_interval=20) as ws:
                for i,p in enumerate(PROGRAMS):
                    await ws.send(json.dumps({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe","params":[{"mentions":[p]},{"commitment":"confirmed"}]}))
                async for m in ws:
                    try:
                        data = json.loads(m)
                        if "params" not in data: continue
                        sig = data["params"]["result"]["value"].get("signature"); slot = data["params"]["result"]["value"].get("slot",0)
                        tx = await get_tx(sig)
                        if not tx or tx["meta"].get("err"): continue
                        keys = [k if isinstance(k,str) else k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                        wallet = next((k for k in keys if k in watched_workers),None)
                        if not wallet: continue
                        for post in tx["meta"].get("postTokenBalances",[]):
                            if post.get("owner")==wallet and post.get("mint") and post["mint"] not in STABLES:
                                await handle_buy(wallet, post["mint"], slot, sig, app)
                    except: pass
        except Exception as e:
            print(f"swap err {e}"); await asyncio.sleep(5)

async def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("bundle", cmd_bundle))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("clear_all", cmd_clear_all))
    await app.initialize()
    # CRITICAL FIX - delete webhook or polling never receives
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        print("Webhook deleted")
    except Exception as e:
        print(f"delete webhook err {e}")
    await app.start()
    await app.updater.start_polling()
    print("V33.9 commands started - UNFREEZABLE")
    try:
        await app.bot.send_message(chat_id=CHAT_ID, text=f"✅ V33.9 LIVE FIXED UNFREEZABLE\nFactories:{len(factories)} restored\nSOL:${sol_price:.2f}\nNow try /status")
    except Exception as e:
        print(f"send start err {e}")
    # Run loops with isolation - one crash won't kill telegram
    await asyncio.gather(update_sol_price(), transfer_loop(app), swap_loop(app))

if __name__ == "__main__":
    asyncio.run(main())
