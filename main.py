import os, asyncio, time, aiohttp, threading
from collections import deque
from flask import Flask
from telegram import Bot
from telegram.ext import Application, CommandHandler

# === YOUR ENV NAMES FROM RENDER ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY") # your Helius key is here
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

# === FIX RENDER WEB SERVICE - FAKE PORT ===
web_app = Flask(__name__)
@web_app.route('/')
def home(): return "V29 BUY ONLY RUNNING - TG: /status"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# === V29 CONFIG ===
FACTORY = [] # fresh start - add via /add
WATCHED = {} # address -> placeholder
SEEN_TX = set()
SWAP_HISTORY = deque(maxlen=1000)

PREP_FILTER = 50
SWAP_FILTER = 50
PREP_POLL = 30
SWAP_POLL = 10
CLUSTER_WINDOW = 120
SOL_PRICE = 160

bot = Bot(token=BOT_TOKEN)
print("Starting V29...")

async def get_sol_price():
    global SOL_PRICE
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://price.jup.ag/v4/price?ids=SOL", timeout=5) as r:
                data = await r.json()
                SOL_PRICE = float(data['data']['SOL']['price'])
                print(f"SOL Price: {SOL_PRICE}")
    except:
        SOL_PRICE = 160

async def fetch_txs(address):
    if not ALCHEMY_KEY: return []
    try:
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={ALCHEMY_KEY}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
                else:
                    print(f"Helius error {r.status} for {address[:6]}")
                    return []
    except Exception as e:
        print(f"fetch error: {e}")
        return []

def short(addr):
    if not addr or len(addr) < 8: return addr
    return f"{addr[:4]}..{addr[-4:]}"

async def send(msg):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        print(f"TG send error: {e}")

async def prep_loop():
    await get_sol_price()
    print("PREP LOOP 30s STARTED")
    while True:
        try:
            if not FACTORY:
                await asyncio.sleep(5)
                continue
            for factory in FACTORY:
                txs = await fetch_txs(factory)
                for tx in txs[:20]:
                    sig = tx.get('signature')
                    if not sig or sig in SEEN_TX: continue

                    for tr in tx.get('nativeTransfers', []):
                        try:
                            amount_sol = tr.get('amount', 0) / 1e9
                            usd = amount_sol * SOL_PRICE
                            if usd < PREP_FILTER: continue # ignore 0.0029 ATA dust

                            to_user = tr.get('toUserAccount')
                            from_user = tr.get('fromUserAccount')

                            # SILENT RECURSIVE - add worker without TG spam
                            if to_user and to_user not in WATCHED and to_user not in FACTORY:
                                WATCHED[to_user] = 999
                                print(f"Added worker {short(to_user)} via recursive")

                            msg = f"PREP: {short(from_user)} -> {short(to_user)} | {amount_sol:.2f} SOL (${usd:.0f}) | [Tx](https://solscan.io/tx/{sig})"
                            await send(msg)
                            SEEN_TX.add(sig)
                        except: continue
        except Exception as e:
            print(f"PREP ERR: {e}")
        await asyncio.sleep(PREP_POLL)

async def swap_loop():
    print("SWAP LOOP 10s STARTED")
    while True:
        try:
            await get_sol_price()
            workers = list(WATCHED.keys()) + FACTORY
            if not workers:
                await asyncio.sleep(5)
                continue

            for worker in workers[:50]: # max 50 to stay light, no crash
                txs = await fetch_txs(worker)
                for tx in txs[:10]:
                    sig = tx.get('signature')
                    if not sig or sig in SEEN_TX: continue
                    if tx.get('type')!= 'SWAP': continue # catches Photon/Trojan/BullX/Jupiter all

                    for tt in tx.get('tokenTransfers', []):
                        if tt.get('toUserAccount') == worker:
                            try:
                                # SOL spent = native transfers from worker
                                native_spent = sum([n.get('amount',0) for n in tx.get('nativeTransfers',[]) if n.get('fromUserAccount')==worker]) / 1e9
                                usd_spent = native_spent * SOL_PRICE
                                if usd_spent < SWAP_FILTER: continue

                                mint = tt.get('mint')
                                if not mint: continue
                                token_name = tt.get('tokenSymbol') or short(mint)

                                now = time.time()
                                SWAP_HISTORY.append((now, mint, worker, usd_spent, sig))
                                recent = [s for s in SWAP_HISTORY if now - s[0] <= CLUSTER_WINDOW and s[1] == mint]
                                total = sum(s[3] for s in recent)

                                cluster_line = f"\nCluster: {len(recent)} wallets, ${total:.0f} total in 120s" if len(recent) > 1 else ""

                                msg = f"MAIN BUY: {token_name}\nMint: `{mint}`\nBuyer: {short(worker)} (${usd_spent:.0f}){cluster_line}\n[Chart](https://dexscreener.com/solana/{mint}) | [Tx](https://solscan.io/tx/{sig})"
                                await send(msg)
                                SEEN_TX.add(sig)
                                break
                            except: continue
        except Exception as e:
            print(f"SWAP ERR: {e}")
        await asyncio.sleep(SWAP_POLL)

# === TG COMMANDS - 5 ESSENTIAL ===
async def cmd_status(update, ctx):
    msg = f"V29 BUY ONLY RUNNING\nPrep: {PREP_POLL}s | Swap: {SWAP_POLL}s\nFilter: >${PREP_FILTER}\nFactories: {len(FACTORY)}\nWatched workers: {len(WATCHED)}\nSOL: ${SOL_PRICE:.0f}\n\nCommands:\n/add <addr>\n/remove <addr>\n/list\n/clear_all"
    await update.message.reply_text(msg)

async def cmd_add(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Use: /add <factory_address>\nExample: /add 3MmgY...")
        return
    addr = ctx.args[0].strip()
    if addr in FACTORY:
        await update.message.reply_text(f"Already tracking {short(addr)}")
        return
    FACTORY.append(addr)
    await update.message.reply_text(f"✅ Added factory {short(addr)}\nNow tracking >$50 buys\nDo /list to see")

async def cmd_remove(update, ctx):
    if not ctx.args:
        await update.message.reply_text("Use: /remove <address>")
        return
    addr = ctx.args[0].strip()
    removed = False
    if addr in FACTORY:
        FACTORY.remove(addr)
        removed = True
    if addr in WATCHED:
        del WATCHED[addr]
        removed = True
    await update.message.reply_text(f"✅ Removed {short(addr)}" if removed else f"Not found {short(addr)}")

async def cmd_clear(update, ctx):
    FACTORY.clear()
    WATCHED.clear()
    SEEN_TX.clear()
    SWAP_HISTORY.clear()
    await update.message.reply_text("✅ Cleared all. Fresh start.\nAdd new factory with /add")

async def cmd_list(update, ctx):
    if not FACTORY and not WATCHED:
        await update.message.reply_text("Empty - no factories yet.\nAdd with: /add <address>")
        return
    lines = []
    for f in FACTORY:
        lines.append(f"Factory: {short(f)}")
    for w in list(WATCHED.keys())[:20]:
        lines.append(f"Worker: {short(w)}")
    txt = "\n".join(lines)
    await update.message.reply_text(f"Active ({len(FACTORY)} factories, {len(WATCHED)} workers):\n{txt}")

async def main():
    if not BOT_TOKEN or not CHAT_ID or not ALCHEMY_KEY:
        print("ERROR: Missing ENV - BOT_TOKEN, CHAT_ID, ALCHEMY_API_KEY required")
        while True: await asyncio.sleep(3600)

    print(f"BOT TOKEN OK: {BOT_TOKEN[:10]}...")
    print(f"CHAT ID: {CHAT_ID}")
    print("Starting TG Bot polling...")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_status))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("clear_all", cmd_clear))
    app.add_handler(CommandHandler("list", cmd_list))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    print("V29 FRESH START READY - TG COMMANDS LIVE")
    await asyncio.gather(prep_loop(), swap_loop())

if __name__ == "__main__":
    asyncio.run(main())
