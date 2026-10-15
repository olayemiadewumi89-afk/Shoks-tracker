import os, asyncio, time, aiohttp
from collections import deque
from telegram import Bot

# YOUR EXACT ENV NAMES FROM SCREENSHOT
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ALCHEMY_KEY = os.getenv("ALCHEMY_API_KEY")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

FACTORY = [] # empty - add via /add
PREP_FILTER = 50
SWAP_FILTER = 50
PREP_POLL = 30
SWAP_POLL = 10
CLUSTER_WINDOW = 120
SOL_PRICE = 160

bot = Bot(token=BOT_TOKEN)
WATCHED = {}
SEEN_TX = set()
SWAP_HISTORY = deque(maxlen=1000)

# Use Helius if key is Helius, or Alchemy if Alchemy - auto detect
def get_rpc_url(addr):
    if ALCHEMY_KEY and "helius" in ALCHEMY_KEY.lower() or len(ALCHEMY_KEY)>40:
        return f"https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={ALCHEMY_KEY}"
    else:
        # Fallback to Helius format with your key
        return f"https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={ALCHEMY_KEY}"

async def get_sol_price():
    global SOL_PRICE
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://price.jup.ag/v4/price?ids=SOL") as r:
                j = await r.json()
                SOL_PRICE = j['data']['SOL']['price']
    except: SOL_PRICE = 160

async def fetch_txs(address):
    try:
        url = get_rpc_url(address)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                return await r.json()
    except: return []

def short(a): return f"{a[:4]}..{a[-4:]}"

async def send(msg):
    try: await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e: print(e)

async def prep_loop():
    print("PREP 30s started")
    while True:
        try:
            for fac in FACTORY:
                txs = await fetch_txs(fac)
                for tx in txs[:20]:
                    sig = tx.get('signature')
                    if not sig or sig in SEEN_TX: continue
                    for tr in tx.get('nativeTransfers',[]):
                        sol = tr.get('amount',0)/1e9
                        usd = sol*SOL_PRICE
                        if usd < PREP_FILTER: continue
                        to_u = tr.get('toUserAccount')
                        from_u = tr.get('fromUserAccount')
                        if to_u and to_u not in WATCHED and to_u not in FACTORY:
                            WATCHED[to_u]=999 # silent recursive, no alert
                        await send(f"PREP: {short(from_u)} -> {short(to_u)} | {sol:.2f} SOL (${usd:.0f}) | [Tx](https://solscan.io/tx/{sig})")
                        SEEN_TX.add(sig)
        except Exception as e: print(e)
        await asyncio.sleep(PREP_POLL)

async def swap_loop():
    print("SWAP 10s started")
    while True:
        try:
            await get_sol_price()
            workers = list(WATCHED.keys()) + FACTORY
            for w in workers[:50]:
                txs = await fetch_txs(w)
                for tx in txs[:10]:
                    sig = tx.get('signature')
                    if not sig or sig in SEEN_TX: continue
                    if tx.get('type')!='SWAP': continue
                    for tt in tx.get('tokenTransfers',[]):
                        if tt.get('toUserAccount')==w:
                            spent = sum([n['amount'] for n in tx.get('nativeTransfers',[]) if n['fromUserAccount']==w])/1e9
                            usd = spent*SOL_PRICE
                            if usd < SWAP_FILTER: continue
                            mint = tt.get('mint')
                            name = tt.get('tokenSymbol') or short(mint)
                            now=time.time()
                            SWAP_HISTORY.append((now,mint,w,usd,sig))
                            recent=[s for s in SWAP_HISTORY if now-s[0]<=CLUSTER_WINDOW and s[1]==mint]
                            total=sum(s[3] for s in recent)
                            cl=f"\nCluster: {len(recent)} wallets, ${total:.0f} in 120s" if len(recent)>1 else ""
                            await send(f"MAIN BUY: {name}\nMint: `{mint}`\nBuyer: {short(w)} (${usd:.0f}){cl}\n[Chart](https://dexscreener.com/solana/{mint}) | [Tx](https://solscan.io/tx/{sig})")
                            SEEN_TX.add(sig)
                            break
        except Exception as e: print(e)
        await asyncio.sleep(SWAP_POLL)

from telegram.ext import Application, CommandHandler
async def cmd_status(u,c): await u.message.reply_text(f"V29 RUNNING\nPrep 30s Swap 10s\nWatched: {len(WATCHED)}\nFactories: {len(FACTORY)}\nFilter >$50")
async def cmd_add(u,c):
    if not c.args: return await u.message.reply_text("Use: /add <factory_address>")
    FACTORY.append(c.args[0]); await u.message.reply_text(f"Added {short(c.args[0])}")
async def cmd_remove(u,c):
    if not c.args: return await u.message.reply_text("Use: /remove <address>")
    a=c.args[0]
    if a in FACTORY: FACTORY.remove(a)
    if a in WATCHED: del WATCHED[a]
    await u.message.reply_text(f"Removed {short(a)}")
async def cmd_clear(u,c):
    FACTORY.clear(); WATCHED.clear()
    await u.message.reply_text("Cleared all. Fresh start.")
async def cmd_list(u,c):
    if not FACTORY and not WATCHED: return await u.message.reply_text("Empty - use /add")
    t="\n".join([f"F: {short(x)}" for x in FACTORY] + [f"W: {short(x)}" for x in list(WATCHED.keys())[:30]])
    await u.message.reply_text(t)

async def main():
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_status))
    app.add_handler(CommandHandler("status",cmd_status))
    app.add_handler(CommandHandler("add",cmd_add))
    app.add_handler(CommandHandler("remove",cmd_remove))
    app.add_handler(CommandHandler("clear_all",cmd_clear))
    app.add_handler(CommandHandler("list",cmd_list))
    await app.initialize(); await app.start()
    print("V29 FRESH START READY")
    await asyncio.gather(prep_loop(), swap_loop())

if __name__=="__main__": asyncio.run(main())
