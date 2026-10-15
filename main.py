import os
import json
import asyncio
import aiohttp
import time
from collections import defaultdict, deque
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
HELIUS_KEY = os.getenv("HELIUS_KEY")
CHAT_ID_ENV = os.getenv("CHAT_ID")

PREP_THRESHOLD_USD = 50
BUY_THRESHOLD_USD = 50
PREP_INTERVAL = 30
SWAP_INTERVAL = 10
CLUSTER_WINDOW = 180

FACTORIES_FILE = "factories.json"

def load_factories():
    if os.path.exists(FACTORIES_FILE):
        try:
            with open(FACTORIES_FILE,"r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {addr: addr[:6] for addr in data}
                return data
        except:
            return {}
    return {}

def save_factories():
    with open(FACTORIES_FILE,"w") as f:
        json.dump(factories, f)

factories = load_factories()
watched_workers = set()
last_signatures = set()
cluster_buys = defaultdict(lambda: deque())
sol_price = 160.0

HELIUS_URL = f"https://api.helius.xyz/v0/addresses/{{}}/transactions?api-key={HELIUS_KEY}&limit=20"

async def get_sol_price():
    global sol_price
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://price.jup.ag/v6/price?ids=So11111111111111111111111111111111111111112") as r:
                j = await r.json()
                sol_price = float(j['data']['So11111111111111111111111111111111111111112']['price'])
    except:
        pass

async def get_txs(address):
    url = HELIUS_URL.format(address)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=15) as r:
                if r.status!= 200:
                    return []
                return await r.json()
    except:
        return []

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"<b>V30 BUY ONLY + LABELS RUNNING</b>\n"
        f"Prep: {PREP_INTERVAL}s | Swap: {SWAP_INTERVAL}s\n"
        f"Filter: >${BUY_THRESHOLD_USD}\n"
        f"Factories: {len(factories)}\n"
        f"Watched workers: {len(watched_workers)}\n"
        f"SOL: ${sol_price:.2f}\n\n"
        f"<b>Commands:</b>\n"
        f"/add <addr> [label] - e.g. /add 6F5x.. PoorGoat\n"
        f"/remove <addr> or /remove <label>\n"
        f"/list\n"
        f"/clear_all"
    )
    await update.message.reply_text(msg, parse_mode='HTML')

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add <address> [label]\nExample: /add 6F5xv9Kq... PoorGoat")
        return
    addr = context.args[0].strip()
    label = " ".join(context.args[1:]) if len(context.args) > 1 else addr[:6]
    factories[addr] = label
    save_factories()
    await update.message.reply_text(f"✅ Added factory [{label}]:\n{addr}")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /remove <address> or label")
        return
    addr = context.args[0].strip()
    if addr in factories:
        lab = factories.pop(addr)
        save_factories()
        await update.message.reply_text(f"❌ Removed [{lab}]: {addr}")
    else:
        found = [k for k,v in factories.items() if v.lower()==addr.lower()]
        if found:
            for k in found:
                factories.pop(k)
            save_factories()
            await update.message.reply_text(f"❌ Removed by label {addr}")
        else:
            await update.message.reply_text("Not found")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not factories:
        await update.message.reply_text("No factories. Use /add <addr> [label]")
        return
    txt = "<b>Factories:</b>\n"
    for addr, label in factories.items():
        txt += f"• [{label}] - <code>{addr}</code>\n"
    txt += f"\nWatched workers: {len(watched_workers)}"
    await update.message.reply_text(txt, parse_mode='HTML')

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    factories.clear()
    watched_workers.clear()
    save_factories()
    await update.message.reply_text("✅ Cleared all. Fresh start. Use /add <addr> [label]")

async def prep_loop(app):
    await asyncio.sleep(5)
    while True:
        try:
            await get_sol_price()
            for f_addr, f_label in list(factories.items()):
                txs = await get_txs(f_addr)
                for tx in txs:
                    sig = tx.get('signature')
                    if not sig or sig in last_signatures:
                        continue
                    native = tx.get('nativeTransfers', []) or []
                    for nt in native:
                        if nt.get('fromUserAccount') == f_addr:
                            to_acc = nt.get('toUserAccount')
                            amount_sol = nt.get('amount',0) / 1e9
                            usd = amount_sol * sol_price
                            if usd >= PREP_THRESHOLD_USD and to_acc not in factories:
                                last_signatures.add(sig)
                                watched_workers.add(to_acc)
                                try:
                                    explorer = f"https://solscan.io/tx/{sig}"
                                    msg = (
                                        f"🟡 PREP [{f_label}]\n"
                                        f"{f_addr[:6]}.. -> {to_acc[:6]}..\n"
                                        f"{amount_sol:.2f} SOL (${usd:.0f})\n"
                                        f"<a href='{explorer}'>Tx</a>"
                                    )
                                    if CHAT_ID_ENV:
                                        await app.bot.send_message(chat_id=int(CHAT_ID_ENV) if CHAT_ID_ENV.lstrip('-').isdigit() else CHAT_ID_ENV, text=msg, parse_mode='HTML')
                                except Exception as e:
                                    print(e)
                await asyncio.sleep(1)
        except Exception as e:
            print("prep loop error", e)
        await asyncio.sleep(PREP_INTERVAL)

async def swap_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            for worker in list(watched_workers)[:100]:
                txs = await get_txs(worker)
                for tx in txs:
                    sig = tx.get('signature')
                    if not sig or sig in last_signatures:
                        continue
                    ttype = tx.get('type','')
                    if ttype == 'SWAP' or 'swap' in ttype.lower():
                        token_transfers = tx.get('tokenTransfers',[]) or []
                        for tt in token_transfers:
                            if tt.get('toUserAccount') == worker:
                                mint = tt.get('mint','')
                                if mint == 'So11111111111111111111111111111111111111112':
                                    continue
                                native_spent = 0
                                for nt in tx.get('nativeTransfers',[]):
                                    if nt.get('fromUserAccount') == worker:
                                        native_spent += nt.get('amount',0)/1e9
                                usd = native_spent * sol_price
                                if usd < BUY_THRESHOLD_USD:
                                    continue
                                last_signatures.add(sig)
                                now = time.time()
                                desc = tx.get('description','')
                                symbol = mint[:6]
                                if desc:
                                    parts = desc.split()
                                    symbol = parts[-2] if len(parts)>2 else mint[:6]
                                dq = cluster_buys[mint]
                                dq.append((now, usd))
                                while dq and now - dq[0][0] > CLUSTER_WINDOW:
                                    dq.popleft()
                                total_cluster = sum(x[1] for x in dq)
                                count_cluster = len(dq)
                                explorer = f"https://solscan.io/tx/{sig}"
                                chart = f"https://dexscreener.com/solana/{mint}"
                                msg = (
                                    f"🟢 MAIN BUY\n"
                                    f"Token: {symbol}\n"
                                    f"Buyer: <code>{worker[:8]}..</code> (${usd:.0f})\n"
                                    f"Cluster: {count_cluster} wallets, ${total_cluster:.0f} total in {CLUSTER_WINDOW//60}m\n"
                                    f"<a href='{chart}'>Chart</a> | <a href='{explorer}'>Tx</a>\n"
                                    f"<code>{mint}</code>"
                                )
                                if CHAT_ID_ENV:
                                    await app.bot.send_message(chat_id=int(CHAT_ID_ENV) if CHAT_ID_ENV.lstrip('-').isdigit() else CHAT_ID_ENV, text=msg, parse_mode='HTML', disable_web_page_preview=True)
                                break
                await asyncio.sleep(0.3)
        except Exception as e:
            print("swap loop error", e)
        await asyncio.sleep(SWAP_INTERVAL)

def main():
    print("Starting V30 with labels")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("clear_all", clear_cmd))

    async def on_startup(app):
        asyncio.create_task(prep_loop(app))
        asyncio.create_task(swap_loop(app))

    app.post_init = on_startup
    app.run_polling()

if __name__ == "__main__":
    main()
