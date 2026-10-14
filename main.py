import asyncio, requests, time, os, threading
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
if not BOT_TOKEN:
    print("FATAL: BOT_TOKEN missing")
    raise SystemExit(1)

SOL_WALLETS = {}
BOUGHT_CACHE = {}
PRE_SELL_QUEUE = []
CHAT_ID = None
LAST_PRE_SELL = 0
LABELS = {}
SEEN_TX = set()
DELETED = set()

app_flask = Flask(__name__)
@app_flask.route('/')
def home(): return "Bot Running V18.3 - NO SPAM"
def run_flask():
    app_flask.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

def rpc(m,p):
    try: return requests.post(RPC, json={"jsonrpc":"2.0","id":1,"method":m,"params":p}, timeout=10).json().get('result')
    except: return None
def get_sigs(a,l=10): return rpc("getSignaturesForAddress", [a, {"limit":l}]) or []
def get_tx(s): return rpc("getTransaction", [s, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}])
def get_tokens(a):
    res = rpc("getTokenAccountsByOwner", [a, {"programId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding":"jsonParsed"}])
    return res.get('value',[]) if res else []
def get_bal(a):
    res = rpc("getBalance", [a])
    return res.get('value',0)/1e9 if res and 'value' in res else 0
def get_ata_info(ata):
    res = rpc("getAccountInfo", [ata, {"encoding":"jsonParsed"}])
    try: d=res['data']['parsed']['info']; return d.get('mint'), d.get('owner')
    except: return None,None

# ---- COMMANDS FOR YOUR MENU ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    SOL_WALLETS.clear(); BOUGHT_CACHE.clear(); PRE_SELL_QUEUE.clear(); SEEN_TX.clear(); DELETED.clear()
    await update.message.reply_text("🚀 V18.3 READY - NO SPAM\n/bundle <addr>\n/listwallets\n/listgroups\n/labelgroup <addr> <name>\n/status\n/pnl\n/clear_all\n/debug_buy <addr>")

async def bundle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if not context.args:
        await update.message.reply_text("Use: /bundle <wallet>"); return
    a = context.args[0].strip()
    SOL_WALLETS[a] = {"parent":"ROOT","gen":0,"added_at":time.time()}
    SEEN_TX.clear() # clear to force scan new bundle once
    await update.message.reply_text(f"✅ Added ROOT {a[:6]}.. tracking forward\nWill detect new forwards only once.")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS:
        await update.message.reply_text("Empty. /bundle <addr>"); return
    msg = f"📋 {len(SOL_WALLETS)} WALLETS:\n"
    for i,(addr,inf) in enumerate(list(SOL_WALLETS.items())[:80]):
        label = LABELS.get(addr,"")
        msg+=f"{i+1}. {addr[:6]}.. g{inf['gen']} {label}\n"
    await update.message.reply_text(msg)

async def listgroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS:
        await update.message.reply_text("Empty"); return
    groups = defaultdict(list)
    for addr,inf in SOL_WALLETS.items():
        cur=addr; ci=inf; seen=set()
        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS and ci['parent'] not in seen:
            seen.add(cur); cur=ci['parent']; ci=SOL_WALLETS[cur]
        root = cur if ci['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=addr
        groups[root].append(addr)
    txt=f"👥 {len(groups)} GROUPS:\n"
    for r,addrs in groups.items():
        lbl = LABELS.get(r,"")
        txt+=f"ROOT {r[:6]}.. {lbl}: {len(addrs)} wallets\n"
    await update.message.reply_text(txt)

async def labelgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2:
        await update.message.reply_text("Use: /labelgroup <wallet> <name>"); return
    addr = context.args[0]; name = " ".join(context.args[1:])
    LABELS[addr]=name
    await update.message.reply_text(f"🏷️ Labeled {addr[:6]}.. as {name}")

async def pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS:
        await update.message.reply_text("Empty"); return
    msg="📊 PnL (quick):\n"
    for addr in list(SOL_WALLETS.keys())[:10]:
        bal = await asyncio.to_thread(get_bal, addr)
        toks = await asyncio.to_thread(get_tokens, addr)
        msg+=f"{addr[:4]}..: {bal:.4f} SOL, {len(toks)} tokens\n"
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt=f"⚙️ STATUS V18.3 NO-SPAM\nWallets: {len(SOL_WALLETS)}\nSeen TX: {len(SEEN_TX)}\nDeleted blocked: {len(DELETED)}\nCHAT_ID: {CHAT_ID}\nLabels: {len(LABELS)}"
    await update.message.reply_text(txt)

async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SOL_WALLETS.clear(); BOUGHT_CACHE.clear(); PRE_SELL_QUEUE.clear(); LABELS.clear(); SEEN_TX.clear(); DELETED.clear()
    await update.message.reply_text("🗑️ Cleared all. Spam lists reset. /bundle <new>")

async def debug_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Use: /debug_buy <wallet>"); return
    a=context.args[0].strip()
    toks = await asyncio.to_thread(get_tokens, a)
    bal = await asyncio.to_thread(get_bal, a)
    txt=f"🔍 Debug {a[:6]}..\nBal: {bal}\nTokens: {len(toks)}\n"
    for tok in toks[:5]:
        try:
            info=tok['account']['data']['parsed']['info']
            txt+=f"{info['mint'][:6]}.. amt {info['tokenAmount']['uiAmount']}\n"
        except: pass
    await update.message.reply_text(txt)

# ---- FIXED LOOPS - NO SPAM ----
async def forward_loop(app):
    global CHAT_ID
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(3); continue
            for addr in list(SOL_WALLETS.keys()):
                sigs = await asyncio.to_thread(get_sigs, addr, 5)
                for s in sigs[:3]:
                    sig = s['signature']
                    if sig in SEEN_TX: continue
                    SEEN_TX.add(sig)
                    if len(SEEN_TX) > 5000: SEEN_TX.clear()
                    # IGNORE OLD HISTORY - ONLY NEW TX
                    if s.get('blockTime') and time.time() - s['blockTime'] > 600:
                        continue
                    tx = await asyncio.to_thread(get_tx, sig)
                    if not tx: continue
                    all_instr=[]
                    all_instr.extend(tx['transaction']['message'].get('instructions',[]))
                    for inner in tx['meta'].get('innerInstructions',[]): all_instr.extend(inner.get('instructions',[]))
                    for ins in all_instr:
                        parsed=ins.get('parsed')
                        if not parsed: continue
                        if ins.get('program')=="system" and parsed.get('type')=="transfer":
                            dest=parsed['info'].get('destination'); amt=parsed['info'].get('lamports',0)/1e9
                            if dest and amt>=0.001 and dest not in SOL_WALLETS and dest not in DELETED:
                                SOL_WALLETS[dest]={"parent":addr,"gen":SOL_WALLETS[addr]['gen']+1,"added_at":time.time()}
                                try: await app.bot.send_message(CHAT_ID, f"➕ Forward: {addr[:4]}.. -> {dest[:4]}.. {amt:.4f} SOL g{SOL_WALLETS[dest]['gen']}")
                                except: pass
                        if ins.get('program') in ("spl-token","spl-associated-token-account") and parsed.get('type') in ("transfer","transferChecked"):
                            info=parsed['info']; src_ata=info.get('source'); dst_ata=info.get('destination'); mint=info.get('mint')
                            if not mint and src_ata: m,_=await asyncio.to_thread(get_ata_info, src_ata); mint=m
                            if mint and dst_ata:
                                _,owner=await asyncio.to_thread(get_ata_info, dst_ata)
                                if owner: PRE_SELL_QUEUE.append({"from":addr,"mint":mint,"owner":owner,"sig":sig,"time":time.time()})
            await asyncio.sleep(8)
        except: await asyncio.sleep(8)

async def presell_loop(app):
    global LAST_PRE_SELL
    while True:
        await asyncio.sleep(10)
        try:
            if not PRE_SELL_QUEUE or not CHAT_ID: continue
            now=time.time(); recent=[x for x in PRE_SELL_QUEUE if now-x['time']<30]
            if not recent: PRE_SELL_QUEUE.clear(); continue
            if len(recent)>=1 and (now-LAST_PRE_SELL)>30:
                by_mint=defaultdict(list)
                for r in recent: by_mint[r['mint']].append(r)
                for mint, items in by_mint.items():
                    try:
                        ds=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=5).json()
                        sym=ds['pairs'][0]['baseToken']['symbol'] if ds.get('pairs') else mint[:6]
                    except: sym=mint[:6]
                    froms=set([x['from'][:4] for x in items])
                    await app.bot.send_message(CHAT_ID, f"⚠️ ABOUT TO SELL PREP\nToken: ${sym}\nCA: {mint}\n{len(items)} transfers\nFrom: {', '.join(froms)}..\nTX: https://solscan.io/tx/{items[0]['sig']}")
                LAST_PRE_SELL=now; PRE_SELL_QUEUE.clear()
        except: pass

async def buy_loop(app):
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(3); continue
            for addr in list(SOL_WALLETS.keys()):
                toks = await asyncio.to_thread(get_tokens, addr)
                for tok in toks:
                    try:
                        info=tok['account']['data']['parsed']['info']; mint=info['mint']; amt=float(info['tokenAmount']['uiAmount'])
                        if amt==0: continue
                        if mint not in BOUGHT_CACHE or (time.time()-BOUGHT_CACHE.get(mint,0))>600:
                            BOUGHT_CACHE[mint]=time.time()
                            try:
                                ds=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=5).json()
                                pair=ds.get('pairs',[{}])[0]; sym=pair.get('baseToken',{}).get('symbol','UNKNOWN'); mcap=pair.get('fdv','N/A')
                            except: sym="UNKNOWN"; mcap="N/A"
                            await app.bot.send_message(CHAT_ID, f"💰 BOUGHT ${sym}\nCA: {mint}\nBuyer {addr[:4]}.. g{SOL_WALLETS[addr]['gen']}\nMCap {mcap}\nhttps://dexscreener.com/solana/{mint}", disable_web_page_preview=True)
                    except: continue
            await asyncio.sleep(6)
        except: await asyncio.sleep(6)

async def dead_loop(app):
    while True:
        await asyncio.sleep(120)
        try:
            for addr in list(SOL_WALLETS.keys()):
                inf=SOL_WALLETS[addr]
                if inf['parent']=="ROOT": continue
                if time.time()-inf['added_at']<900: continue
                bal=await asyncio.to_thread(get_bal, addr)
                toks=await asyncio.to_thread(get_tokens, addr)
                if bal<0.0002 and len(toks)==0:
                    del SOL_WALLETS[addr]
                    DELETED.add(addr)
                    try: await app.bot.send_message(CHAT_ID, f"🗑️ Deleted dead {addr[:4]}..")
                    except: pass
        except: pass

async def post_init(app):
    print("Bot init, starting no-spam loops")
    asyncio.create_task(forward_loop(app))
    asyncio.create_task(presell_loop(app))
    asyncio.create_task(buy_loop(app))
    asyncio.create_task(dead_loop(app))

def main():
    threading.Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bundle", bundle))
    app.add_handler(CommandHandler("add", bundle))
    app.add_handler(CommandHandler("labelgroup", labelgroup))
    app.add_handler(CommandHandler("label_group", labelgroup))
    app.add_handler(CommandHandler("listgroups", listgroups))
    app.add_handler(CommandHandler("list_groups", listgroups))
    app.add_handler(CommandHandler("list_group", listgroups))
    app.add_handler(CommandHandler("listwallets", listwallets))
    app.add_handler(CommandHandler("list", listwallets))
    app.add_handler(CommandHandler("pnl", pnl))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear_all", clear_all))
    app.add_handler(CommandHandler("clear", clear_all))
    app.add_handler(CommandHandler("debug_buy", debug_buy))
    print("V18.3 NO SPAM READY")
    app.run_polling()

if __name__=="__main__":
    main()
