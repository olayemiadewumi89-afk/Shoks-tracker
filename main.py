import os, json, asyncio, time, requests
from datetime import datetime, timedelta
from pyrogram import Client, filters
from collections import defaultdict

# --- CONFIG ---
API_ID = int(os.getenv("API_ID", "12345"))
API_HASH = os.getenv("API_HASH", "your_api_hash")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
HELIUS_KEY = os.getenv("HELIUS_KEY", "")
ALCHEMY_BASE = os.getenv("ALCHEMY_BASE", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

MIN_USD = 20.0
REBUY_WINDOW_HOURS = 24
WALLETS_FILE = "wallets.json"
SUBS_FILE = "subscribers.json"

# --- YOUR CURRENT WALLETS (fallback) ---
DEFAULT_SOL = [
 "Beqv6d9C42M3Q3N3u8jN6X6j7K8L9M0N1P2Q3R4S5T6",
 # paste your 18 SOL wallets here - I truncated for example
]
DEFAULT_BASE = [
 "0x1234567890123456789012345678901234567890",
 # paste your 16 BASE wallets here
]

# Use your real lists - if you had them in old file, copy them here
SOL_WALLETS = [
"5t8F8Z9gHjKlQrStUvWxYzAbCdEfGhIjKlMnOpQrStUv",
"Beqv6d9C42M3Q3N3u8jN6X6j7K8L9M0N1P2Q3R4S5T6",
# ADD ALL 18 HERE
]
BASE_WALLETS = [
"0x1111111111111111111111111111111111111111",
"0x2222222222222222222222222222222222222222",
# ADD ALL 16 HERE
]

# --- STORAGE ---
def load_json(file, default):
    if os.path.exists(file):
        try: return json.load(open(file))
        except: return default
    return default

def save_json(file, data):
    json.dump(data, open(file, "w"))

# Load saved wallets if exist
saved = load_json(WALLETS_FILE, {})
if saved:
    SOL_WALLETS = saved.get("sol", SOL_WALLETS)
    BASE_WALLETS = saved.get("base", BASE_WALLETS)

subscribers = set(load_json(SUBS_FILE, []))
buy_history = defaultdict(list) # mint -> list of {wallet, usd, time}

# --- BOT ---
bot = Client("shoks_tracker", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def is_admin(chat_id):
    global ADMIN_ID
    if ADMIN_ID == 0:
        if subscribers:
            ADMIN_ID = list(subscribers)[0]
        else:
            ADMIN_ID = chat_id
        print(f"ADMIN SET TO {ADMIN_ID}")
    return chat_id == ADMIN_ID

async def broadcast(text):
    for chat_id in list(subscribers):
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
        except:
            pass

@bot.on_message(filters.command("start"))
async def start_cmd(client, message):
    chat_id = message.chat.id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_json(SUBS_FILE, list(subscribers))
    is_ad = await is_admin(chat_id)
    admin_note = "\n\n🔧 **Admin Commands**:\n/addsol <addr>\n/addbase <addr>\n/removesol <part>\n/removebase <part>\n/listwallets" if is_ad else ""
    await message.reply(
        f"🔥 **Shoks Tracker ACTIVE**\n\n"
        f"SOL: {len(SOL_WALLETS)} wallets\n"
        f"BASE: {len(BASE_WALLETS)} wallets\n"
        f"Min: ${MIN_USD}+ | Re-buy window: {REBUY_WINDOW_HOURS}h\n"
        f"You will get alerts here.{admin_note}\n\n"
        f"`LIVE - {len(subscribers)} users`"
    )

@bot.on_message(filters.command("addsol"))
async def addsol_cmd(client, message):
    if not await is_admin(message.chat.id):
        return await message.reply("❌ Admin only - set ADMIN_ID in Render")
    try:
        addr = message.text.split()[1].strip()
        if addr not in SOL_WALLETS:
            SOL_WALLETS.append(addr)
            save_json(WALLETS_FILE, {"sol": SOL_WALLETS, "base": BASE_WALLETS})
            await message.reply(f"✅ Added SOL:\n`{addr}`\nTotal SOL: {len(SOL_WALLETS)}")
        else:
            await message.reply("⚠️ Already tracked")
    except:
        await message.reply("Usage: /addsol <wallet_address>")

@bot.on_message(filters.command("addbase"))
async def addbase_cmd(client, message):
    if not await is_admin(message.chat.id): return
    try:
        addr = message.text.split()[1].strip()
        if addr not in BASE_WALLETS:
            BASE_WALLETS.append(addr)
            save_json(WALLETS_FILE, {"sol": SOL_WALLETS, "base": BASE_WALLETS})
            await message.reply(f"✅ Added BASE:\n`{addr}`\nTotal BASE: {len(BASE_WALLETS)}")
        else:
            await message.reply("⚠️ Already tracked")
    except:
        await message.reply("Usage: /addbase <0x_address>")

@bot.on_message(filters.command("listwallets"))
async def list_cmd(client, message):
    if not await is_admin(message.chat.id): return
    sol_txt = "\n".join([f"{w[:6]}...{w[-4:]}" for w in SOL_WALLETS[-15:]])
    base_txt = "\n".join([f"{w[:6]}...{w[-4:]}" for w in BASE_WALLETS[-15:]])
    await message.reply(f"**SOL ({len(SOL_WALLETS)}):**\n{sol_txt}\n\n**BASE ({len(BASE_WALLETS)}):**\n{base_txt}\n\nTotal: {len(SOL_WALLETS)+len(BASE_WALLETS)}")

@bot.on_message(filters.command("removesol"))
async def remsol_cmd(client, message):
    if not await is_admin(message.chat.id): return
    try:
        q = message.text.split()[1]
        found = [w for w in SOL_WALLETS if q.lower() in w.lower()]
        if found:
            SOL_WALLETS.remove(found[0])
            save_json(WALLETS_FILE, {"sol": SOL_WALLETS, "base": BASE_WALLETS})
            await message.reply(f"🗑️ Removed SOL: {found[0][:10]}...")
        else:
            await message.reply("Not found")
    except:
        await message.reply("Usage: /removesol <part_of_address>")

@bot.on_message(filters.command("removebase"))
async def rembase_cmd(client, message):
    if not await is_admin(message.chat.id): return
    try:
        q = message.text.split()[1]
        found = [w for w in BASE_WALLETS if q.lower() in w.lower()]
        if found:
            BASE_WALLETS.remove(found[0])
            save_json(WALLETS_FILE, {"sol": SOL_WALLETS, "base": BASE_WALLETS})
            await message.reply(f"🗑️ Removed BASE: {found[0][:10]}...")
        else:
            await message.reply("Not found")
    except:
        await message.reply("Usage: /removebase <part>")

# --- YOUR EXISTING TRACKING LOGIC ---
# Keep your helius / alchemy polling loops here
# Example structure:
async def check_sol_wallets():
    while True:
        try:
            # your existing logic that calls Helius
            # when you detect buy > MIN_USD:
            # if mint not in buy_history: send single ⚡ alert
            # else: send 🔁🔁 RE-BUY alert with history
            pass
        except Exception as e:
            print(e)
        await asyncio.sleep(5)

# --- START ---
async def main():
    await bot.start()
    print(f"Bot started - {len(SOL_WALLETS)} SOL + {len(BASE_WALLETS)} BASE - {len(subscribers)} users")
    # start your tracking tasks here
    # asyncio.create_task(check_sol_wallets())
    await asyncio.Event().wait()

if __name__ == "__main__":
    bot.run(main())
