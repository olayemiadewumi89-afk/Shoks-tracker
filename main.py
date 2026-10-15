import asyncio, requests, time, os, threading, json
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
PREP_TRACKER = defaultdict(lambda: {"wallets":[], "times":[]})
SEEN_MINTS = set()

WALLET_FILE = "wallets.json"
LABEL_FILE = "labels.json"
SEEN_FILE = "seen_mints.json"

def save_all():
    try:
        with open(WALLET_FILE, 'w') as f: json.dump(SOL_WALLETS, f)
        with open(LABEL_FILE, 'w') as f: json.dump(LABELS, f)
        with open(SEEN_FILE, 'w') as f: json.dump(list(SEEN_MINTS)[-5000:], f)
    except: pass

def load_all():
    global SOL_WALLETS, LABELS, SEEN_MINTS
    try:
        if os.path.exists(WALLET_FILE):
            with open(WALLET_FILE,'r') as f: SOL_WALLETS.update(json.load(f))
    except: pass
    try:
        if os.path.exists(LABEL_FILE):
            with open(LABEL_FILE,'r') as f: LABELS.update(json.load(f))
    except: pass
    try:
        if os.path.exists(SEEN_FILE):
            with open(SEEN_FILE,'r') as f: SEEN_MINTS.update(set(json.load(f)))
    except: pass

app_flask = Flask(__name__)
@app_flask.route('/')
def home(): return "SHOKS V28.1 BUNDLED FIX LIVE"
def run_flask(): app_flask.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

def rpc(m,p):
    try:
        r = requests.post(RPC, json={"jsonrpc":"2.0","id":1,"method":m,"params":p}, timeout=12)
        if r.status_code == 429:
            time.sleep(2)
            return None
        return r.json().get('result')
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
        if not pair: return None, None, None, None
        return pair.get('baseToken',{}).get('symbol','UNKNOWN'), pair.get('fdv','?'), pair.get('volume',{}).get('h24','?'), pair.get('priceUsd','?')
    except: return None, None, None, None

async def find_funder(addr):
    sigs = await asyncio.to_thread(get_sigs, addr, 20)
    for s in sigs:
        tx = await asyncio.to_thread(get_tx, s['signature'])
        if not tx: continue
        instr=[]; instr.extend(tx['transaction']['message'].get('instructions',[]))
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
    load_all()
    if not LABELS:
        await update.message.reply_text(f"👋 SHOKS TRACKER V28.1\nTotal Wallets: {len(SOL_WALLETS)}\n/bundle <wallet>")
        return
    groups = defaultdict(list)
    for a,inf in SOL_WALLETS.items():
        cur=a; ci=inf; seen=set()
        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS and ci['parent'] not in seen:
            seen.add(cur); cur=ci['parent']; ci=SOL_WALLETS[cur]
        root = cur if ci['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=a
        groups[root].append(a)
    msg = f"📊 SHOKS V28.1 - LABOUR GROUPS\nGroups: {len(LABELS)} | Total: {len(SOL_WALLETS)}\n\n"
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
        msg = f"✅ No funder found (CEX/Binance) - good.\n\n"
    SOL_WALLETS[a] = {"parent":"ROOT","gen":0,"added_at":time.time()}
    toks = await asyncio.to_thread(get_tokens, a)
    for tok in toks:
        try: SEEN_MINTS.add(tok['account']['data']['parsed']['info']['mint'])
        except: pass
    SEEN_TX.clear()
    save_all()
    msg += f"Now tracking {a[:6]}..\nOld tokens ignored\n\n👉 /labelgroup {a[:6]} <name>"
    await update.message.reply_text(msg)

async def labelgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args)<2: await update.message.reply_text("Use: /labelgroup <wallet> <name>"); return
    addr = context.args[0]; name = " ".join(context.args[1:])
    LABELS[addr] = name
    save_all()
    await update.message.reply_text(f"🏷️ Team named: {name}\nWallet: {addr[:4]}..")

async def listwallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SOL_WALLETS: await update.message.reply_text("Empty. Use /bundle <addr>"); return
    msg = f"📋 {len(SOL_WALLETS)} WALLETS:\n"
    for i,(addr,inf) in enumerate(list(SOL_WALLETS.items())[:100]):
        msg+=f"{i+1}. {addr[:4]}.. g{inf['gen']} {LABELS.get(addr,'')}\n"
    await update.message.reply_text(msg)

async def listgroups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not LABELS: await update.message.reply_text("No labour groups yet."); return
    groups = defaultdict(list)
    for addr,inf in SOL_WALLETS.items():
        cur=addr; ci=inf; seen=set()
        while ci['parent']!="ROOT" and ci['parent'] in SOL_WALLETS and ci['parent'] not in seen:
            seen.add(cur); cur=ci['parent']; ci=SOL_WALLETS[cur]
        root = cur if ci['parent']=="ROOT" else inf['parent']
        if inf['parent']=="ROOT": root=addr
        groups[root].append(addr)
    msg = f"👥 LABOUR GROUPS ({len(LABELS)}):\n\n"
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
    await update.message.reply_text(f"⚙️ V28.1 BUNDLED FIX\nWallets: {len(SOL_WALLETS)}\nLabour Groups: {len(LABELS)}\nSeen TX: {len(SEEN_TX)}\nSeen Mints: {len(SEEN_MINTS)}\nCHAT: {CHAT_ID}")

async def clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SOL_WALLETS.clear(); SEEN_TX.clear(); DELETED.clear(); BUY_BUFFER.clear(); SELL_BUFFER.clear(); PREP_TRACKER.clear(); SEEN_MINTS.clear(); LAST_ALERT.clear()
    save_all()
    await update.message.reply_text("🗑️ Cleared all")

async def error_handler(update, context):
    print(f"Error: {context.error}")

async def forward_loop(app):
    global CHAT_ID
    while True:
        try:
            if not SOL_WALLETS or not CHAT_ID: await asyncio.sleep(3); continue
            for addr in list(SOL_WALLETS.keys()):
                sigs = await asyncio.to_thread(get_sigs, addr, 4)
                for s in sigs[:2]:
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
                                PREP_TRACKER[addr]["wallets"].append(dest)
                                PREP_TRACKER[addr]["times"].append(time.time())
                                recent_w=[]; recent_t=[]
                                for w,t in zip(PREP_TRACKER[addr]["wallets"], PREP_TRACKER[addr]["times"]):
                                    if time.time()-t<120:
                                        recent_w.append(w); recent_t.append(t)
                                PREP_TRACKER[addr]["wallets"]=recent_w
                                PREP_TRACKER[addr]["times"]=recent_t
                                save_all()
                        if ins.get('program') in ("spl-token","spl-associated-token-account") and parsed.get('type') in ("transfer","transferChecked"):
                            info=parsed['info']; src_ata=info.get('source'); dst_ata=info.get('destination'); mint=info.get('mint')
                            if not mint and src_ata: m,_=await asyncio.to_thread(get_ata_info, src_ata); mint=m
                            if mint and dst_ata:
                                _,owner=await asyncio.to_thread(get_ata_info, dst_ata)
                                if owner: SELL_BUFFER[mint].append({"from":addr,"to":owner,"sig":sig,"time":time.time()})
                await asyncio.sleep(1.2)
            for parent_addr in list(PREP_TRACKER.keys()):
                data = PREP_TRACKER[parent_addr]
                if not data["wallets"]: continue
                oldest = min(data["times"]) if data["times"] else 0
                if time.time()-oldest>30 or len(data["wallets"])>=2:
                    count = len(data["wallets"])
                    wallets_preview = ", ".join([w[:4]+".." for w in data["wallets"][:5]])
                    try:
                        await app.bot.send_message(CHAT_ID, f"🚨 BUY PREP ALERT (BUNDLED)\n{parent_addr[:4]}.. sent SOL to {count} wallets -> buying soon\nWallets: {wallets_preview}\nGroup: {LABELS.get(parent_addr,'')}\nhttps://solscan.io/account/{parent_addr}")
                    except: pass
                    PREP_TRACKER[parent_addr]={"wallets":[],"times":[]}
            await asyncio.sleep(8)
        except Exception as e:
            print(f"forward error {e}")
            await asyncio.sleep(8)

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
                        if mint in SEEN_MINTS: continue
                        if mint in LAST_ALERT and time.time()-LAST_ALERT[mint]<600: continue
                        name,mcap,vol,price = await asyncio.to_thread(dex_info, mint)
                        if not name or name=="UNKNOWN": continue
                        if not price or price=="?": continue
                        try: price_f = float(price)
                        except: continue
                        if price_f==0: continue
                        usd_val = price_f * amt
                        if usd_val < 5: continue
                        if not any(b['addr']==addr for b in BUY_BUFFER[mint]):
                            BUY_BUFFER[mint].append({"addr":addr,"time":time.time(),"gen":SOL_WALLETS[addr]['gen'],"amt":amt,"usd":usd_val})
                            SEEN_MINTS.add(mint)
                            save_all()
                    except: continue
                await asyncio.sleep(0.8)
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
                    if not name or name=="UNKNOWN": BUY_BUFFER.pop(mint,None); continue
                    total_usd = sum([b['usd'] for b in buys])
                    if total_usd < 10: BUY_BUFFER.pop(mint,None); continue
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
        except: await asyncio.sleep(5)

async def dead_loop(app):
    while True:
        await asyncio.sleep(3600)
        try:
            total_pending = sum([len(v["wallets"]) for v in PREP_TRACKER.values()])
            if total_pending > 3:
                print(f"Skipping dead check - factory funding {total_pending}")
                continue
            for addr in list(SOL_WALLETS.keys()):
                inf=SOL_WALLETS[addr]
                if inf['parent']=="ROOT": continue
                if time.time()-inf['added_at']<10800: continue
                bal=await asyncio.to_thread(get_bal, addr)
                await asyncio.sleep(0.5)
                if bal>0.002: continue
                toks=await asyncio.to_thread(get_tokens, addr)
                await asyncio.sleep(0.5)
                if bal<=0.001 and len(toks)==0:
                    del SOL_WALLETS[addr]; DELETED.add(addr)
                    save_all()
        except: pass

async def post_init(app):
    load_all()
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
    except: pass
    asyncio.create_task(forward_loop(app))
    asyncio.create_task(buy_loop(app))
    asyncio.create_task(cluster_alert_loop(app))
    asyncio.create_task(dead_loop(app))

def main():
    load_all()
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
    print(f"start bot V28.1 - wallets {len(SOL_WALLETS)}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__=="__main__":
    main()
