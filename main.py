import asyncio, requests, time, os, threading
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")

SOL_WALLETS = {}
BOUGHT_CACHE = {}
PRE_SELL_QUEUE = []
CHAT_ID = None
LAST_PRE_SELL = 0

app_flask = Flask(__name__)
@app_flask.route('/')
def home(): return "Bot Running V18.2 - OK"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

def rpc(method, params):
    try:
        r = requests.post(RPC, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}, timeout=10).json()
        return r.get('result')
    except: return None

def get_sigs(addr, limit=20): return rpc("getSignaturesForAddress", [addr, {"limit":limit}]) or []
def get_tx(sig): return rpc("getTransaction", [sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}])
def get_tokens(addr):
    res = rpc("getTokenAccountsByOwner", [addr, {"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding":"jsonParsed"}])
    return res.get('value',[]) if res else []
def get_bal(addr):
    res = rpc("getBalance", [addr])
    return res.get('value',0)/1e9 if res and 'value' in res else 0
def get_ata_info(ata):
    res = rpc("getAccountInfo", [ata, {"encoding":"jsonParsed"}])
    try:
        data = res['data']['parsed']['info']
        return data.get('mint'), data.get('owner')
    except: return None, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    SOL_WALLETS.clear(); BOUGHT_CACHE.clear(); PRE_SELL_QUEUE.clear()
    await update.message.reply_text("🚀 V18.2 READY\n/add <addr>\n/list\n/list_group\n/clear_all")

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Use: /add <wallet>"); return
    a = context.args[0].strip()
    SOL_WALLETS[a] = {"parent":"ROOT","gen":0,"added_at":time.time()}
    await update.message.reply_text(f"✅ Added ROOT {a[:6]}.. tracking forward")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS: await update.message.reply_text("Empty. /add <addr>"); return
    msg = f"📋 {len(SOL_WALLETS)} wallets:\n"
    for i,(addr,inf) in enumerate(list(SOL_WALLETS.items())[:100]):
        msg+=f"{i+1}. {addr[:6]}.. g{inf['gen']} p:{inf['parent'][:4]}\n"
    await update.message.reply_text(msg)

async def list_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS: await update.message.reply_text("Empty"); return
    groups = defaultdict(list)
    for addr,inf in SOL_WALLETS.items():
        cur_addr = addr; cur_inf = inf; seen=set()
        while cur_inf['parent']!="ROOT" and cur_inf['parent'] in SOL_WALLETS and cur_inf['parent'] not in seen:
            seen.add(cur_addr); cur_addr = cur_inf['parent']; cur_inf = SOL_WALLETS[cur_addr]
        root = cur_addr if cur_inf['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=addr
        groups[root].append(addr)
    txt=f"👥 {len(groups)} GROUPS:\n"
    for r,addrs in groups.items(): txt+=f"ROOT {r[:6]}..: {len(addrs)} wallets\n"
    await update.message.reply_text(txt)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SOL_WALLETS.clear(); BOUGHT_CACHE.clear(); PRE_SELL_QUEUE.clear()
    await update.message.reply_text("🗑️ Cleared. /add <new_whale>")

async def forward_loop(app):
    global CHAT_ID
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(2); continue
            for addr in list(SOL_WALLETS.keys()):
                sigs = await asyncio.to_thread(get_sigs, addr, 15)
                for s in sigs[:8]:
                    tx = await asyncio.to_thread(get_tx, s['signature'])
                    if not tx: continue
                    all_instr = []
                    all_instr.extend(tx['transaction']['message'].get('instructions',[]))
                    for inner in tx['meta'].get('innerInstructions',[]): all_instr.extend(inner.get('instructions',[]))
                    for ins in all_instr:
                        parsed = ins.get('parsed')
                        if not parsed: continue
                        prog = ins.get('program')
                        if prog=="system" and parsed.get('type')=="transfer":
                            dest = parsed['info'].get('destination'); amt = parsed['info'].get('lamports',0)/1e9
                            if dest and amt>=0.001 and dest not in SOL_WALLETS:
                                SOL_WALLETS[dest] = {"parent":addr,"gen":SOL_WALLETS[addr]['gen']+1,"added_at":time.time()}
                                try: await app.bot.send_message(CHAT_ID, f"➕ Forward: {addr[:4]}.. -> {dest[:4]}.. {amt:.4f} SOL g{SOL_WALLETS[dest]['gen']}")
                                except: pass
                        if prog in ("spl-token","spl-associated-token-account") and parsed.get('type') in ("transfer","transferChecked"):
                            info = parsed['info']; src_ata = info.get('source'); dst_ata = info.get('destination'); mint = info.get('mint')
                            if not mint and src_ata: m,_ = await asyncio.to_thread(get_ata_info, src_ata); mint=m
                            if mint and dst_ata:
                                _, owner = await asyncio.to_thread(get_ata_info, dst_ata)
                                if owner: PRE_SELL_QUEUE.append({"from":addr,"mint":mint,"owner":owner,"sig":s['signature'],"time":time.time()})
            await asyncio.sleep(4)
        except: await asyncio.sleep(4)

async def presell_loop(app):
    global LAST_PRE_SELL
    while True:
        await asyncio.sleep(8)
        try:
            if not PRE_SELL_QUEUE or not CHAT_ID: continue
            now=time.time(); recent=[x for x in PRE_SELL_QUEUE if now-x['time']<20]
            if not recent: PRE_SELL_QUEUE.clear(); continue
            if len(recent)>=1 and (now-LAST_PRE_SELL)>25:
                by_mint=defaultdict(list)
                for r in recent: by_mint[r['mint']].append(r)
                for mint, items in by_mint.items():
                    try:
                        ds = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=5).json()
                        sym = ds['pairs'][0]['baseToken']['symbol'] if ds.get('pairs') else mint[:6]
                    except: sym=mint[:6]
                    froms=set([x['from'][:4] for x in items])
                    await app.bot.send_message(CHAT_ID, f"⚠️ ABOUT TO SELL PREP\nToken: ${sym}\nCA: {mint}\n{len(items)} transfers from {len(froms)} wallets -> new wallets\nFrom: {', '.join(froms)}..\nTX: https://solscan.io/tx/{items[0]['sig']}")
                LAST_PRE_SELL=now; PRE_SELL_QUEUE.clear()
        except: pass

async def buy_loop(app):
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(2); continue
            for addr in list(SOL_WALLETS.keys()):
                toks = await asyncio.to_thread(get_tokens, addr)
                for tok in toks:
                    try:
                        info = tok['account']['data']['parsed']['info']; mint = info['mint']; amt = float(info['tokenAmount']['uiAmount'])
                        if amt==0: continue
                        if mint not in BOUGHT_CACHE or (time.time()-BOUGHT_CACHE.get(mint,0))>300:
                            BOUGHT_CACHE[mint]=time.time()
                            try:
                                ds = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=5).json()
                                pair=ds.get('pairs',[{}])[0]; sym=pair.get('baseToken',{}).get('symbol','UNKNOWN'); mcap=pair.get('fdv','N/A')
                            except: sym="UNKNOWN"; mcap="N/A"
                            await app.bot.send_message(CHAT_ID, f"💰 BOUGHT ${sym}\nCA: {mint}\nBuyer {addr[:4]}.. g{SOL_WALLETS[addr]['gen']}\nMCap {mcap}\nhttps://dexscreener.com/solana/{mint}", disable_web_page_preview=True)
                    except: continue
            await asyncio.sleep(3)
        except: await asyncio.sleep(3)

async def dead_loop(app):
    while True:
        await asyncio.sleep(40)
        try:
            for addr in list(SOL_WALLETS.keys()):
                if time.time()-SOL_WALLETS[addr]['added_at']<300: continue
                bal = await asyncio.to_thread(get_bal, addr); toks = await asyncio.to_thread(get_tokens, addr)
                if bal<0.0002 and len(toks)==0:
                    del SOL_WALLETS[addr]
                    try: await app.bot.send_message(CHAT_ID, f"🗑️ Deleted dead {addr[:4]}..")
                    except: pass
        except: pass

async def main():
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("bundle", add_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("list_group", list_group_cmd))
    app.add_handler(CommandHandler("clear_all", clear_cmd))
    app.add_handler(CommandHandler("label_group", list_group_cmd))
    asyncio.create_task(forward_loop(app))
    asyncio.create_task(presell_loop(app))
    asyncio.create_task(buy_loop(app))
    asyncio.create_task(dead_loop(app))
    await app.run_polling()

if __name__=="__main__": asyncio.run(main())
