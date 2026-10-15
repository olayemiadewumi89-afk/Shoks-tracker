import os
import json
import asyncio
import aiohttp
import time
import threading
from collections import defaultdict, deque
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

flask_app = Flask(__name__)
@flask_app.route('/')
def home():
    return f"Shoks Tracker V30.5 Running - SOL ${sol_price:.2f}"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host='0.0.0.0', port=port)
threading.Thread(target=run_flask, daemon=True).start()

BOT_TOKEN = os.getenv("BOT_TOKEN")
HELIUS_KEY = os.getenv("HELIUS_KEY")
CHAT_ID_ENV = os.getenv("CHAT_ID")

PREP_THRESHOLD_USD = 50
BUY_THRESHOLD_USD = 50
PREP_INTERVAL = 30
SWAP_INTERVAL = 10
CHAIN_INTERVAL = 20
FACTORIES_FILE = "factories.json"
MAX_CHAIN_DEPTH = 2
CHAIN_WATCH_TTL = 1800

def load_factories():
    if os.path.exists(FACTORIES_FILE):
        try:
            with open(FACTORIES_FILE,"r") as f:
                data = json.load(f)
                if isinstance(data, list): return {addr: addr[:6] for addr in data}
                return data
        except: return {}
    return {}

def save_factories():
    with open(FACTORIES_FILE,"w") as f: json.dump(factories, f)

factories = load_factories()
watched_workers = {}
last_signatures = set()
sol_price = 105.0
HELIUS_URL = f"https://api.helius.xyz/v0/addresses/{{}}/transactions?api-key={HELIUS_KEY}&limit=20"

async def get_sol_price():
    global sol_price
    urls = [
        "https://lite-api.jup.ag/price/v3?ids=So11111111111111111111111111111111111111112",
        "https://api.jup.ag/price/v2?ids=So11111111111111111111111111111111111111112",
        "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
    ]
    for url in urls:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=6) as r:
                    if r.status != 200: continue
                    j = await r.json()
                    if 'data' in j and 'So11111111111111111111111111111111111111112' in j.get('data',{}):
                        sol_price = float(j['data']['So11111111111111111111111111111111111111112']['price'])
                        print(f"[PRICE] SOL ${sol_price:.2f} live")
                        return
                    if 'So11111111111111111111111111111111111111112' in j:
                        sol_price = float(j['So11111111111111111111111111111111111111112']['price'])
                        print(f"[PRICE] SOL ${sol_price:.2f} live v2")
                        return
                    if 'solana' in j:
                        sol_price = float(j['solana']['usd'])
                        print(f"[PRICE] SOL ${sol_price:.2f} live CG")
                        return
        except: continue

async def get_txs(address):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(HELIUS_URL.format(address), timeout=15) as r:
                if r.status!=200: return []
                return await r.json()
    except: return []

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"V30.5 RUNNING\nFactories: {len(factories)}\nWatched: {len(watched_workers)}\nSOL: ${sol_price:.2f}\nChain depth: {MAX_CHAIN_DEPTH} (silent)\n\nCommands:\n/add address label\n/remove address\n/list\n/clear_all")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("Usage: /add ADDRESS LABEL"); return
    addr=context.args[0].strip(); label=" ".join(context.args[1:]) if len(context.args)>1 else addr[:6]
    factories[addr]=label; save_factories(); await update.message.reply_text(f"Added [{label}]: {addr}")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    addr=context.args[0].strip()
    if addr in factories:
        lab=factories.pop(addr); save_factories(); await update.message.reply_text(f"Removed [{lab}]")
    else:
        found=[k for k,v in factories.items() if v.lower()==addr.lower()]
        if found:
            for k in found: factories.pop(k)
            save_factories(); await update.message.reply_text(f"Removed label {addr}")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not factories: await update.message.reply_text("No factories"); return
    txt="Factories:\n"
    for a,l in factories.items(): txt+=f"- [{l}] {a}\n"
    txt+=f"\nWorkers: {len(watched_workers)}"; await update.message.reply_text(txt)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    factories.clear(); watched_workers.clear(); save_factories(); await update.message.reply_text("Cleared all")

async def prep_loop(app):
    await asyncio.sleep(5)
    while True:
        try:
            await get_sol_price()
            for f_addr,f_label in list(factories.items()):
                txs=await get_txs(f_addr)
                for tx in txs:
                    sig=tx.get('signature')
                    if not sig or sig in last_signatures: continue
                    for nt in tx.get('nativeTransfers',[]) or []:
                        if nt.get('fromUserAccount')==f_addr:
                            to_acc=nt.get('toUserAccount')
                            if not to_acc or to_acc in factories or to_acc in watched_workers: continue
                            amt=nt.get('amount',0)/1e9; usd=amt*sol_price
                            if usd>=PREP_THRESHOLD_USD:
                                last_signatures.add(sig)
                                watched_workers[to_acc]={"origin":f_label,"depth":1,"added":time.time(),"parent":f_addr}
                                print(f"[CLOUD] PREP L1 {f_label} -> {to_acc[:8]}")
                                if CHAT_ID_ENV:
                                    try: await app.bot.send_message(chat_id=CHAT_ID_ENV, text=f"PREP [{f_label}] {amt:.2f} SOL (${usd:.0f}) -> {to_acc[:6]}")
                                    except: pass
                await asyncio.sleep(1)
        except: pass
        await asyncio.sleep(PREP_INTERVAL)

async def chain_forward_loop(app):
    await asyncio.sleep(15)
    while True:
        try:
            for w_addr, info in list(watched_workers.items())[:40]:
                if info.get("depth",1) >= MAX_CHAIN_DEPTH: continue
                if time.time() - info.get("added",0) > CHAIN_WATCH_TTL: continue
                txs=await get_txs(w_addr)
                for tx in txs:
                    sig=tx.get('signature')
                    if not sig or sig in last_signatures: continue
                    for nt in tx.get('nativeTransfers',[]) or []:
                        if nt.get('fromUserAccount')==w_addr:
                            to_acc=nt.get('toUserAccount')
                            if not to_acc or to_acc in factories or to_acc in watched_workers: continue
                            amt=nt.get('amount',0)/1e9; usd=amt*sol_price
                            if usd>=PREP_THRESHOLD_USD:
                                last_signatures.add(sig)
                                watched_workers[to_acc]={"origin":info.get("origin"),"depth":info.get("depth",1)+1,"added":time.time(),"parent":w_addr}
                                print(f"[CLOUD] PREP L{info.get('depth',1)+1} CHAIN {info.get('origin')} : {w_addr[:6]} -> {to_acc[:6]}")
                await asyncio.sleep(0.5)
        except: pass
        await asyncio.sleep(CHAIN_INTERVAL)

async def swap_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            for worker in list(watched_workers.keys())[:80]:
                info = watched_workers.get(worker, {})
                txs=await get_txs(worker)
                for tx in txs:
                    sig=tx.get('signature')
                    if not sig or sig in last_signatures: continue
                    if tx.get('type','')=='SWAP':
                        for tt in tx.get('tokenTransfers',[]) or []:
                            if tt.get('toUserAccount')==worker:
                                mint=tt.get('mint','')
                                if mint=='So11111111111111111111111111111111111111112': continue
                                spent=sum(nt.get('amount',0)/1e9 for nt in tx.get('nativeTransfers',[]) if nt.get('fromUserAccount')==worker)
                                usd=spent*sol_price
                                if usd<BUY_THRESHOLD_USD: continue
                                last_signatures.add(sig)
                                origin=info.get("origin","unknown"); depth=info.get("depth",1)
                                msg=f"MAIN BUY L{depth}\n[{origin}] Buyer: {worker[:8]} (${usd:.0f})\n{mint}"
                                print(f"[CLOUD] MAIN BUY L{depth} {worker[:6]} -> {mint[:10]}")
                                if CHAT_ID_ENV:
                                    try: await app.bot.send_message(chat_id=CHAT_ID_ENV, text=msg)
                                    except: pass
                                break
                await asyncio.sleep(0.4)
        except: pass
        await asyncio.sleep(SWAP_INTERVAL)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("clear_all", clear_cmd))
    async def on_startup(app_param):
        await app_param.bot.delete_webhook(drop_pending_updates=True)
        await get_sol_price()
        asyncio.create_task(prep_loop(app_param))
        asyncio.create_task(chain_forward_loop(app_param))
        asyncio.create_task(swap_loop(app_param))
    app.post_init=on_startup
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__": main()
