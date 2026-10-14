import asyncio, requests, time, os, threading
from collections import defaultdict
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
SOL_WALLETS = {}
LABELS = {}
CHAT_ID = None
SEEN_TX = set()
DELETED = set()
BUY_BUFFER = defaultdict(list)
SELL_BUFFER = defaultdict(list)
LAST_ALERT = {}
PREP_TRACKER = defaultdict(list)

app_flask = Flask(__name__)
@app_flask.route('/')
def home(): return "SHOKS V25 CONFLICT FIX LIVE"
def run_flask(): app_flask.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

def rpc(m,p):
    try: return requests.post(RPC, json={"jsonrpc":"2.0","id":1,"method":m,"params":p}, timeout=10).json().get('result')
    except: return None
def get_sigs(a,l=20): return rpc("getSignaturesForAddress", [a, {"limit":l}]) or []
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
def dex_info(mint):
    try:
        ds = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6).json()
        pair = ds.get('pairs',[{}])[0]
        return pair.get('baseToken',{}).get('symbol','UNKNOWN'), pair.get('fdv','?'), pair.get('volume',{}).get('h24','?'), pair.get('priceUsd','?')
    except: return "UNKNOWN","?","?","?"

async def find_funder(addr):
    sigs = await asyncio.to_thread(get_sigs, addr, 20)
    for s in sigs:
        tx = await asyncio.to_thread(get_tx, s['signature'])
        if not tx: continue
        instr=[]
        instr.extend(tx['transaction']['message'].get('instructions',[]))
        for inner in tx['meta'].get('innerInstructions',[]): instr.extend(inner.get('instructions',[]))
        for ins in instr:
            parsed=ins.get('parsed')
            if not parsed: continue
            if ins.get('program')=="system" and parsed.get('type')=="transfer":
                info=parsed['info']
                if info.get('destination')==addr:
                    amt=info.get('lamports',0)/1e9
                    if amt>0.001:
                        return info.get('source'), amt, s['signature']
    return None, 0, None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if not LABELS:
        await update.message.reply_text(f"👋 SHOKS TRACKER\nTotal Wallets: {len(SOL_WALLETS)}\nNo labour groups yet.\n/bundle <wallet> then /labelgroup <wallet> <name>")
        return
    groups = defaultdict(list)
    for a,inf in SOL_WALLETS.items():
        cur=a; ci=inf; seen=set()
        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS and ci['parent'] not in seen:
            seen.add(cur); cur=ci['parent']; ci=SOL_WALLETS[cur]
        root = cur if ci['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=a
        groups[root].append(a)
    msg = f"📊 SHOKS - LABOUR GROUPS\nGroups: {len(LABELS)} | Total: {len(SOL_WALLETS)}\n\n"
    for root_addr, team_name in LABELS.items():
        cnt = len(groups.get(root_addr, []))
        if cnt==0 and root_addr in SOL_WALLETS: cnt=1
        msg += f"📌 {team_name}: {cnt} wallets\n"
    await update.message.reply_text(msg)

async def bundle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHAT_ID
    CHAT_ID = update.effective_chat.id
    if not context.args: await update.message.reply_text("Use: /bundle <wallet_address>"); return
    a = context.args[0].strip()
    await update.message.reply_text(f"🔍 Scanning {a[:6]}.. for founder...")
    founder, amt, sig = await find_funder(a)
    if founder:
        msg = f"✅ Found funder: {founder[:4]}.. sent {amt:.4f} SOL\nTX: https://solscan.io/tx/{sig}\n\n"
    else:
        msg = f"✅ No funder found on-chain.\nMaybe it's from Binance / OKX / CEX - central exchange.\nThat's where it's from and that's good.\n\n"
    SOL_WALLETS[a] = {"parent":"ROOT","gen":0,"added_at":time.time()}
    SEEN_TX.clear()
    msg += f"Now tracking {a[:6]}.. forward ONLY >0.001 SOL\nKeep following the money...\n\n👉 You name it:\n/labelgroup {a[:4]}.. <your_team_name>"
    await update.message.reply_text(msg)

async def labelgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: await update.message.reply_text("Use: /labelgroup <wallet> <name>"); return
    addr = context.args[0]; name = " ".join(context.args[1:])
    LABELS[addr] = name
    await update.message.reply_text(f"🏷️ Team named: {name}\nWallet: {addr[:4]}..\n🚀 Forward tracking active >0.001 SOL\nAny wallet buys token = I notify you.")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS: await update.message.reply_text("Empty. Use /bundle <addr>"); return
    msg = f"📋 {len(SOL_WALLETS)} WALLETS:\n"
    for i,(addr,inf) in enumerate(list(SOL_WALLETS.items())[:100]):
        msg+=f"{i+1}. {addr[:4]}.. g{inf['gen']} {LABELS.get(addr,'')}\n"
    await update.message.reply_text(msg)

async def listgroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LABELS: await update.message.reply_text("No labour groups yet.\nUse /labelgroup <wallet> <name>"); return
    groups = defaultdict(list)
    for addr,inf in SOL_WALLETS.items():
        cur=addr; ci=inf; seen=set()
        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS and ci['parent'] not in seen:
            seen.add(cur); cur=ci['parent']; ci=SOL_WALLETS[cur]
        root = cur if ci['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=addr
        groups[root].append(addr)
    msg = f"👥 YOUR LABOUR GROUPS ({len(LABELS)}):\n\n"
    for root_addr, team_name in LABELS.items():
        cnt = len(groups.get(root_addr, []))
        if cnt==0 and root_addr in SOL_WALLETS: cnt=1
        msg += f"📌 {team_name} : {cnt} wallets\n"
    await update.message.reply_text(msg)

async def pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS: await update.message.reply_text("Empty"); return
    msg="📊 Balances:\n"
    for addr in list(SOL_WALLETS.keys())[:15]:
        bal = await asyncio.to_thread(get_bal, addr)
        msg+=f"{addr[:4]}.. g{SOL_WALLETS[addr]['gen']}: {bal:.4f} SOL\n"
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"⚙️ V25 CONFLICT FIX\nWallets: {len(SOL_WALLETS)}\nLabour Groups: {len(LABELS)}\nSeen TX: {len(SEEN_TX)}\nBuy Buffer: {len(BUY_BUFFER)}\nCHAT: {CHAT_ID}")

async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SOL_WALLETS.clear(); SEEN_TX.clear(); DELETED.clear(); BUY_BUFFER.clear(); SELL_BUFFER.clear(); PREP_TRACKER.clear()
    await update.message.reply_text("🗑️ Cleared all wallets & buffers.\nReady for new /bundle")

async def error_handler(update, context):
    print(f"Error: {context.error}")

async def forward_loop(app):
    global CHAT_ID
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(3); continue
            for addr in list(SOL_WALLETS.keys()):
                sigs = await asyncio.to_thread(get_sigs, addr, 5)
                for s in sigs[:3]:
                    sig=s['signature']
                    if sig in SEEN_TX: continue
                    SEEN_TX.add(sig)
                    if len(SEEN_TX)>5000: SEEN_TX.clear()
                    if s.get('blockTime') and time.time()-s['blockTime']>900: continue
                    tx = await asyncio.to_thread(get_tx, sig)
                    if not tx: continue
                    instr=[]; instr.extend(tx['transaction']['message'].get('instructions',[]))
                    for inner in tx['meta'].get('innerInstructions',[]): instr.extend(inner.get('instructions',[]))
                    for ins in instr:
                        parsed=ins.get('parsed')
                        if not parsed: continue
                        if ins.get('program')=="system" and parsed.get('type')=="transfer":
                            dest=parsed['info'].get('destination'); amt=parsed['info'].get('lamports',0)/1e9
                            if dest and amt>0.001 and dest not in SOL_WALLETS and dest not in DELETED:
                                SOL_WALLETS[dest]={"parent":addr,"gen":SOL_WALLETS[addr]['gen']+1,"added_at":time.time()}
                                PREP_TRACKER[addr].append(time.time())
                                recent=[t for t in PREP_TRACKER[addr] if time.time()-t<120]
                                PREP_TRACKER[addr]=recent
                                if len(recent)>=1:
                                    PREP_TRACKER[addr]=[]
                                    try: await app.bot.send_message(CHAT_ID, f"🚨 BUY PREP ALERT\n{addr[:4]}.. sent SOL to {len(recent)} wallet -> buying soon\nGroup: {LABELS.get(addr,'')}\nCheck /listwallets")
                                    except: pass
                        if ins.get('program') in ("spl-token","spl-associated-token-account") and parsed.get('type') in ("transfer","transferChecked"):
                            info=parsed['info']; src_ata=info.get('source'); dst_ata=info.get('destination'); mint=info.get('mint')
                            if not mint and src_ata: m,_=await asyncio.to_thread(get_ata_info, src_ata); mint=m
                            if mint and dst_ata:
                                _,owner=await asyncio.to_thread(get_ata_info, dst_ata)
                                if owner: SELL_BUFFER[mint].append({"from":addr,"to":owner,"sig":sig,"time":time.time()})
            await asyncio.sleep(8)
        except: await asyncio.sleep(8)

async def buy_loop(app):
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(3); continue
            for addr in list(SOL_WALLETS.keys()):
                toks=await asyncio.to_thread(get_tokens, addr)
                for tok in toks:
                    try:
                        info=tok['account']['data']['parsed']['info']; mint=info['mint']; amt=float(info['tokenAmount']['uiAmount'])
                        if amt==0: continue
                        if mint in LAST_ALERT and time.time()-LAST_ALERT[mint]<600: continue
                        name,mcap,vol,price = await asyncio.to_thread(dex_info, mint)
                        try: usd_val = float(price) * amt if price!="?" else 0
                        except: usd_val = 0
                        if not any(b['addr']==addr for b in BUY_BUFFER[mint]):
                            BUY_BUFFER[mint].append({"addr":addr,"time":time.time(),"gen":SOL_WALLETS[addr]['gen'],"amt":amt,"usd":usd_val})
                    except: continue
            await asyncio.sleep(6)
        except: await asyncio.sleep(6)

async def cluster_alert_loop(app):
    while True:
        await asyncio.sleep(15)
        try:
            if not CHAT_ID: continue
            for mint in list(BUY_BUFFER.keys()):
                buys=[b for b in BUY_BUFFER[mint] if time.time()-b['time']<70]
                if not buys: BUY_BUFFER.pop(mint,None); continue
                if time.time()-min(b['time'] for b in buys)>15:
                    name,mcap,vol,price = await asyncio.to_thread(dex_info, mint)
                    total_usd = sum([b['usd'] for b in buys])
                    buyers=" ".join([f"{b['addr'][:4]}..g{b['gen']}" for b in buys[:10]])
                    group_name = ""
                    for b in buys:
                        cur=b['addr']; ci=SOL_WALLETS.get(cur,{"parent":"ROOT"})
                        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS:
                            cur=ci['parent']; ci=SOL_WALLETS[cur]
                        if cur in LABELS: group_name=LABELS[cur]; break
                        if b['addr'] in LABELS: group_name=LABELS[b['addr']]; break
                    if total_usd >= 200:
                        msg=(f"🚨 WAY BUY ALERT - ${name} - ${total_usd:.0f}\n\nCoin: ${name}\nMCAP: {mcap} | VOL: {vol}\nCA: `{mint}`\n\n[Dex](https://dexscreener.com/solana/{mint}) | [Photon](https://photon-sol.tinyastro.io/en/lp/{mint}) | [Solscan](https://solscan.io/token/{mint})\n\nGroup: {group_name}\nBuyers ({len(buys)}): {buyers}")
                    else:
                        msg=(f"💰 BOUGHT - ${name} - ${total_usd:.0f}\n\nCoin: ${name}\nCA: `{mint}`\n[Dex](https://dexscreener.com/solana/{mint}) | [Photon](https://photon-sol.tinyastro.io/en/lp/{mint}) | [Solscan](https://solscan.io/token/{mint})\n\nGroup: {group_name}\nBuyer: {buyers}")
                    await app.bot.send_message(CHAT_ID, msg, parse_mode="Markdown", disable_web_page_preview=True)
                    LAST_ALERT[mint]=time.time(); BUY_BUFFER.pop(mint,None)
            for mint in list(SELL_BUFFER.keys()):
                sells=[s for s in SELL_BUFFER[mint] if time.time()-s['time']<70]
                if not sells: SELL_BUFFER.pop(mint,None); continue
                if len(sells)>=2 and time.time()-min(s['time'] for s in sells)>15:
                    name,mcap,vol,price = await asyncio.to_thread(dex_info, mint)
                    msg=(f"⚠️ SELL PREP\nToken: ${name}\nCA: `{mint}`\n{len(sells)} transfers\n[Dex](https://dexscreener.com/solana/{mint}) | [Photon](https://photon-sol.tinyastro.io/en/lp/{mint})\nTX: https://solscan.io/tx/{sells[0]['sig']}")
                    await app.bot.send_message(CHAT_ID, msg, parse_mode="Markdown", disable_web_page_preview=True)
                    SELL_BUFFER.pop(mint,None)
        except: await asyncio.sleep(5)

async def dead_loop(app):
    while True:
        await asyncio.sleep(30)
        try:
            for addr in list(SOL_WALLETS.keys()):
                inf=SOL_WALLETS[addr]
                if inf['parent']=="ROOT": continue
                if time.time()-inf['added_at']<300: continue
                bal=await asyncio.to_thread(get_bal, addr)
                toks=await asyncio.to_thread(get_tokens, addr)
                if bal<=0.001 and len(toks)==0:
                    del SOL_WALLETS[addr]; DELETED.add(addr)
        except: pass

async def post_init(app):
    # FIX CONFLICT - delete webhook
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        print("Webhook deleted - conflict fixed")
    except: pass
    asyncio.create_task(forward_loop(app))
    asyncio.create_task(buy_loop(app))
    asyncio.create_task(cluster_alert_loop(app))
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
    app.add_handler(CommandHandler("listwallets", listwallets))
    app.add_handler(CommandHandler("list", listwallets))
    app.add_handler(CommandHandler("pnl", pnl))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear_all", clear_all))
    app.add_handler(CommandHandler("clear", clear_all))
    app.add_error_handler(error_handler)
    print("start bot V25 conflict fix")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
