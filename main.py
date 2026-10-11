import os, asyncio, threading, time, requests, json, re
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict, deque, Counter

# ========= CONFIG =========
WALLETS_FILE = "wallets.json"
GROUPS_FILE = "groups.json"
SEEN_FILE = "seen.json"
MEMORY_FILE = "cluster_memory.json"

SOL_WALLETS = []
GROUPS = {}
cluster_memory = defaultdict(list)
seen_sigs = set()
recent_children = defaultdict(list) # funder -> [(child, amt, time, is_token, mint)]
sniper_marked = {} # child -> {funder, amt, time, group}
tg_queue = deque()
funder_cache = {}
last_tx_time = datetime.now()

# RPC Rotation - Public free RPCs
SOLANA_PUB_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://solana.api.onchainkit.io"
]
rpc_idx = 0
last_rpc_call = 0

def get_public_rpc():
    global rpc_idx, last_rpc_call
    now = time.time()
    if now - last_rpc_call < 0.35:
        time.sleep(0.35 - (now - last_rpc_call))
    last_rpc_call = time.time()
    url = SOLANA_PUB_RPCS[rpc_idx % len(SOLANA_PUB_RPCS)]
    rpc_idx += 1
    return url

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Flask keepalive for Render + UptimeRobot
app = Flask(__name__)
@app.route('/')
def home():
    return "Shoks V5.1 PREP SNIPER 15s - FULL", 200

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 10000)))

# ========= TG QUEUE =========
def send_tg_worker():
    while True:
        try:
            if tg_queue:
                text = tg_queue.popleft()
                if not BOT_TOKEN or not CHAT_ID:
                    print(text[:800], flush=True)
                else:
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True},
                            timeout=15
                        )
                    except Exception as e:
                        print(f"tg err {e}", flush=True)
                time.sleep(0.7)
            else:
                time.sleep(0.2)
        except:
            time.sleep(1)

threading.Thread(target=send_tg_worker, daemon=True).start()

def send_tg(text):
    if len(tg_queue) > 50:
        tg_queue.clear()
    tg_queue.append(text)

def short(a):
    return f"{a[:4]}...{a[-4:]}" if len(a) > 8 else a

# ========= STORAGE =========
def load_all():
    global SOL_WALLETS, GROUPS, seen_sigs
    try:
        if os.path.exists(WALLETS_FILE):
            d = json.load(open(WALLETS_FILE))
            SOL_WALLETS = d.get("sol", [])
            # support old format where groups were inside wallets file
            if "groups" in d and d["groups"]:
                GROUPS.update(d["groups"])
    except: pass
    try:
        if os.path.exists(GROUPS_FILE):
            g = json.load(open(GROUPS_FILE))
            GROUPS.update(g)
    except: pass
    try:
        if os.path.exists(SEEN_FILE):
            seen_sigs = set(json.load(open(SEEN_FILE)))
    except: pass

def save_wallets():
    try:
        json.dump({"sol": SOL_WALLETS, "groups": GROUPS}, open(WALLETS_FILE, "w"))
    except: pass

def save_groups():
    try:
        json.dump(GROUPS, open(GROUPS_FILE, "w"))
    except: pass

def save_seen():
    try:
        if len(seen_sigs) > 1500:
            # keep last 1000
            trimmed = list(seen_sigs)[-1000:]
            json.dump(trimmed, open(SEEN_FILE, "w"))
        else:
            json.dump(list(seen_sigs), open(SEEN_FILE, "w"))
    except: pass

load_all()

# ========= DEXSCREENER =========
BLACKLIST_MINTS = [
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "So11111111111111111111111111111111111111112"
]

def get_token_info(token_address):
    if token_address in BLACKLIST_MINTS:
        return None
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=7).json()
        pairs = r.get("pairs", [])
        if not pairs:
            return None
        # filter dust liq < $100
        valid = [p for p in pairs if (p.get("liquidity", {}).get("usd", 0) or 0) >= 50]
        if not valid:
            valid = pairs
        best = sorted(valid, key=lambda x: x.get("liquidity", {}).get("usd", 0) or 0, reverse=True)[0]
        fdv = best.get("fdv") or best.get("marketCap") or 0
        if fdv >= 1_000_000:
            mcap_str = f"${fdv/1_000_000:.2f}M"
        elif fdv >= 1000:
            mcap_str = f"${fdv/1000:.0f}k"
        else:
            mcap_str = f"${fdv:.0f}"
        price = float(best.get("priceUsd", 0) or 0)
        if price == 0:
            return None
        age_min = 0
        try:
            pc = best.get("pairCreatedAt")
            if pc:
                age_min = int((time.time()*1000 - pc) / 60000)
        except: pass
        return {
            "name": best.get("baseToken", {}).get("symbol", token_address[:6]),
            "mcap_str": mcap_str,
            "mcap_val": fdv,
            "price": price,
            "dex_link": f"https://dexscreener.com/{best.get('chainId','solana')}/{best.get('pairAddress', token_address)}",
            "photon": f"https://photon-sol.tinyastro.io/en/lp/{token_address}",
            "age_min": age_min,
            "pair": best.get("pairAddress","")
        }
    except:
        return None

# ========= RPC HELPERS =========
def get_signatures(wallet, limit=10):
    try:
        rpc_url = get_public_rpc()
        payload = {"jsonrpc":"2.0","id":1,"method":"getSignaturesForAddress","params":[wallet,{"limit":limit}]}
        r = requests.post(rpc_url, json=payload, timeout=12)
        return r.json().get("result", []) or []
    except:
        return []

def get_tx_details(sig):
    # cache 10min
    if sig in funder_cache and (datetime.now() - funder_cache[sig][0]).total_seconds() < 600:
        return funder_cache[sig][1]
    sol_transfers = []
    token_transfers = [] # (from, to, mint, amt, is_buy)
    try:
        rpc_url = get_public_rpc()
        payload = {"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig, {"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}
        r = requests.post(rpc_url, json=payload, timeout=14)
        res = r.json().get("result")
        if not res:
            funder_cache[sig] = (datetime.now(), ([], []))
            return [], []
        # SOL transfers
        for instr in res.get("transaction", {}).get("message", {}).get("instructions", []):
            try:
                if instr.get("program") == "system" and instr.get("parsed", {}).get("type") == "transfer":
                    info = instr["parsed"]["info"]
                    sol_transfers.append((info["source"], info["destination"], info.get("lamports",0)/1e9))
            except: continue
        # Inner instructions also have transfers
        for inner in res.get("meta", {}).get("innerInstructions", []) or []:
            for instr in inner.get("instructions", []):
                try:
                    if instr.get("program") == "system" and instr.get("parsed", {}).get("type") == "transfer":
                        info = instr["parsed"]["info"]
                        sol_transfers.append((info["source"], info["destination"], info.get("lamports",0)/1e9))
                except: continue

        # Token balances diff
        meta = res.get("meta", {})
        pre = meta.get("preTokenBalances", []) or []
        post = meta.get("postTokenBalances", []) or []
        pre_map = {}
        for b in pre:
            pre_map[(b.get("owner",""), b.get("mint",""))] = float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
        for b in post:
            owner = b.get("owner",""); mint = b.get("mint","")
            if mint in BLACKLIST_MINTS:
                continue
            post_amt = float(b.get("uiTokenAmount",{}).get("uiAmount",0) or 0)
            pre_amt = pre_map.get((owner,mint), 0)
            diff = post_amt - pre_amt
            if abs(diff) > 0.000001 and diff > 0:
                token_transfers.append(("unknown", owner, mint, abs(diff), True))
    except Exception as e:
        # print(f"tx err {e}", flush=True)
        pass
    funder_cache[sig] = (datetime.now(), (sol_transfers, token_transfers))
    if len(funder_cache) > 700:
        funder_cache.clear()
    return sol_transfers, token_transfers

# ========= BUNDLE =========
def handle_bundle(wallet):
    wallet = wallet.strip()
    if len(wallet) < 30:
        send_tg(f"❌ Invalid wallet")
        return
    send_tg(f"🔍 Scanning `{short(wallet)}` for parent...")
    try:
        sigs = get_signatures(wallet, 15)
        funders = []
        for item in sigs[:12]:
            sig = item.get("signature")
            if not sig: continue
            sols,_ = get_tx_details(sig)
            for frm,to,amt in sols:
                if to == wallet and amt >= 0.03:
                    if frm not in [f[0] for f in funders]:
                        funders.append((frm, amt, sig))

        if not funders:
            # no funder found, add wallet alone
            if wallet not in SOL_WALLETS:
                SOL_WALLETS.append(wallet)
                GROUPS[wallet] = short(wallet)
                save_wallets(); save_groups()
            send_tg(f"⚠️ No parent found for `{short(wallet)}` — added solo as new group")
            return

        main_funder = funders[0][0]
        group_name = short(main_funder)

        GROUPS[wallet] = group_name
        GROUPS[main_funder] = group_name
        if main_funder not in SOL_WALLETS:
            SOL_WALLETS.append(main_funder)

        # Find siblings (other children of same funder)
        added = 0
        sib_sigs = get_signatures(main_funder, 20)
        for item in sib_sigs[:15]:
            sig = item.get("signature")
            if not sig: continue
            sols,_ = get_tx_details(sig)
            for frm,to,amt in sols:
                if frm == main_funder and to!= wallet and amt >= 0.03 and to not in SOL_WALLETS and added < 7:
                    SOL_WALLETS.append(to)
                    GROUPS[to] = group_name
                    added += 1
                    recent_children[main_funder].append((to, amt, datetime.now(), False, None))

        save_wallets(); save_groups()
        send_tg(f"📦 Bundled `{short(wallet)}`\nParent: `{short(main_funder)}`\nGroup: {group_name}\n✅ +{added} sibs | Total: {len(SOL_WALLETS)} wallets")

    except Exception as e:
        send_tg(f"❌ Bundle error: {e}")

# ========= ALERT LOGIC 1-5w =========
def alert_prep(funder):
    now = datetime.now()
    # clean older than 5 min
    recent = [x for x in recent_children[funder] if (now - x[2]).total_seconds() <= 300]
    recent_children[funder] = recent
    if not recent:
        return

    sol_only = [x for x in recent if not x[3]]
    token_only = [x for x in recent if x[3]]

    group = GROUPS.get(funder, short(funder))

    # TOKEN distribution = SALE prep (1-5w token)
    if len(token_only) >= 1:
        mint = token_only[0][4] if len(token_only[0]) > 4 else "token"
        info = get_token_info(mint) if mint!= "token" and mint else None
        name = info["name"] if info else short(mint) if mint!="token" else "TOKEN"
        mcap = info["mcap_str"] if info else ""
        child_list = "\n".join([f"• `{short(c[0])}` +{c[1]:.1f}" for c in token_only[:4]])
        send_tg(f"🚨 DIST {len(token_only)}w 5m\nGroup {group} | `{short(funder)}`\nToken: {name} {mcap}\n{child_list}\n[Solscan](https://solscan.io/account/{funder})")
        return

    # SOL funding = BUY prep (1-5w sol)
    if len(sol_only) >= 1:
        biggest = max(sol_only, key=lambda x: x[1]) if sol_only else None
        biggest_amt = biggest[1] if biggest else 0
        biggest_child = biggest[0] if biggest else ""
        # Mark sniper if >=0.4 SOL or biggest among <=3
        if biggest_amt >= 0.35 or (len(sol_only) <= 3 and biggest_amt >= 0.08):
            sniper_marked[biggest_child] = {"funder": funder, "amt": biggest_amt, "time": now, "group": group}

        childs_str = " ".join([f"`{short(c[0])}` {c[1]:.2f}S" for c in sol_only[:4]])
        sniper_str = f"\n🎯 Sniper `{short(biggest_child)}` {biggest_amt:.2f} SOL loaded" if biggest_amt >= 0.3 else ""
        # only alert if new burst
        send_tg(f"⚠️ PREP {len(sol_only)}w 5m\nG:{group} P:`{short(funder)}`\n{childs_str}{sniper_str}\n[Solscan](https://solscan.io/account/{funder})")

# ========= MAIN TRACKING =========
async def track_sol_polling():
    print("SOL polling started every 6s", flush=True)
    while True:
        try:
            # scan last 90 wallets to stay light on free
            for w in SOL_WALLETS[-90:]:
                sigs = get_signatures(w, 3)
                for item in sigs[:2]:
                    sig = item.get("signature")
                    if not sig or sig in seen_sigs:
                        continue
                    sols, tokens = get_tx_details(sig)
                    for _, owner, mint, amt, is_buy in tokens:
                        if owner == w and is_buy and amt > 0:
                            info = get_token_info(mint)
                            if not info:
                                continue
                            if amt * info["price"] < 8: # $8 min buy filter (keep $10 as you wanted ~)
                                continue
                            seen_sigs.add(sig)
                            save_seen()
                            global last_tx_time
                            last_tx_time = datetime.now()

                            is_sniper = w in sniper_marked
                            is_low = info["mcap_val"] < 300_000
                            is_new = info["age_min"] < 180 # <3h
                            group = GROUPS.get(w, "?")
                            mcap_str = info["mcap_str"]

                            # EARLY BUY FORMAT - Short clickable
                            if is_low and is_new:
                                # If sniper or not, early is early
                                sniper_tag = "SNIPER" if is_sniper else "EARLY"
                                send_tg(
                                    f"🎯 EARLY @{mcap_str}\n"
                                    f"🪙 {info['name']} | {mcap_str} | {info['age_min']}m\n"
                                    f"👤 `{short(w)}` G:{group} {sniper_tag}\n"
                                    f"CA: `{mint}`\n"
                                    f"[DS]({info['dex_link']}) [PH]({info['photon']}) [TX](https://solscan.io/tx/{sig})"
                                )
                            else:
                                # Normal buy
                                tag = " SNIPER" if is_sniper else ""
                                send_tg(
                                    f"💰 BUY {mcap_str}\n"
                                    f"{info['name']} | `{short(w)}` G:{group}{tag} | ${amt*info['price']:.0f}\n"
                                    f"CA: `{mint[:10]}..`\n"
                                    f"[DS]({info['dex_link']}) [PH]({info['photon']}) [TX](https://solscan.io/tx/{sig})"
                                )
                            # also alert sells if you want - keeping
        except Exception as e:
            print(f"poll err {e}", flush=True)
        await asyncio.sleep(6)

def track_funders_15s():
    print("Funder tracker 15s started", flush=True)
    while True:
        time.sleep(15)
        try:
            # build funder list: all parents + all groups leaders
            funders = set()
            for w,g in GROUPS.items():
                if short(w) == g:
                    funders.add(w)
            # add wallets that had recent children
            for k in recent_children.keys():
                funders.add(k)
            if not funders:
                funders = set(SOL_WALLETS[:20])

            for funder in list(funders)[:35]:
                try:
                    sigs = get_signatures(funder, 8)
                    for item in sigs[:5]:
                        sig = item.get("signature")
                        if not sig or sig in seen_sigs:
                            continue
                        sols, tokens = get_tx_details(sig)
                        # SOL child creation
                        for frm,to,amt in sols:
                            if frm == funder and amt >= 0.02 and to not in SOL_WALLETS:
                                SOL_WALLETS.append(to)
                                GROUPS[to] = GROUPS.get(funder, short(funder))
                                save_wallets(); save_groups()
                                recent_children[funder].append((to, amt, datetime.now(), False, None))
                                seen_sigs.add(sig); save_seen()
                                alert_prep(funder)
                        # TOKEN distribution
                        for _,to2,mint,amt2,_ in tokens:
                            if to2 not in SOL_WALLETS and amt2 > 0 and len(mint) > 20:
                                # only track if from funder context (token sent)
                                SOL_WALLETS.append(to2)
                                GROUPS[to2] = GROUPS.get(funder, short(funder))
                                save_wallets(); save_groups()
                                recent_children[funder].append((to2, amt2, datetime.now(), True, mint))
                                seen_sigs.add(sig); save_seen()
                                alert_prep(funder)
                except:
                    continue
        except Exception as e:
            print(f"funder err {e}", flush=True)

# ========= COMMANDS =========
def handle_command(text):
    t = text.strip()
    low = t.lower()

    if low.startswith("/start"):
        mins = int((datetime.now() - last_tx_time).total_seconds() / 60)
        uniq_groups = len(set(GROUPS.values()))
        send_tg(
            f"🚀 Shoks V5.1 FULL 15s\n"
            f"{len(SOL_WALLETS)} wallets | {uniq_groups} groups\n"
            f"Last tx {mins}m ago\n"
            f"Buy>${8} | Prep 1-5w | Early<300k\n"
            f"Cmds:\n"
            f"/bundle <addr>\n"
            f"/list - all wallets\n"
            f"/listgroups - group counts\n"
            f"/remove <addr>\n"
            f"/clear - clear all\n"
            f"/status\n"
            f"/test"
        )

    elif low.startswith("/bundle"):
        parts = t.split()
        if len(parts) < 2:
            send_tg("Usage: /bundle <wallet_address>")
        else:
            threading.Thread(target=handle_bundle, args=(parts[1],), daemon=True).start()

    elif low.startswith("/listgroups"):
        c = Counter(GROUPS.values())
        if not c:
            send_tg("No groups yet")
        else:
            msg = "📦 Groups:\n" + "\n".join([f"{g}: {cnt}w" for g,cnt in c.most_common(20)])
            send_tg(msg + f"\nTotal {len(SOL_WALLETS)}")

    elif low.startswith("/list"):
        if not SOL_WALLETS:
            send_tg("List empty")
        else:
            # chunk to avoid TG limit
            msg = f"📋 {len(SOL_WALLETS)} wallets:\n"
            for w in SOL_WALLETS[-40:]:
                g = GROUPS.get(w, "?")
                msg += f"`{short(w)}` G:{g}\n"
            send_tg(msg)

    elif low.startswith("/remove"):
        parts = t.split()
        if len(parts) < 2:
            send_tg("Usage: /remove <wallet>")
        else:
            addr = parts[1].strip()
            if addr in SOL_WALLETS:
                SOL_WALLETS.remove(addr)
                if addr in GROUPS: del GROUPS[addr]
                save_wallets(); save_groups()
                send_tg(f"🗑️ Removed `{short(addr)}`")
            else:
                send_tg(f"Not found `{short(addr)}`")

    elif low.startswith("/clear"):
        SOL_WALLETS.clear(); GROUPS.clear(); recent_children.clear(); sniper_marked.clear()
        save_wallets(); save_groups()
        send_tg("🧹 Cleared all wallets & groups")

    elif low.startswith("/status"):
        uniq = len(set(GROUPS.values()))
        mins = int((datetime.now() - last_tx_time).total_seconds() / 60)
        send_tg(f"📊 Status V5.1\nWallets: {len(SOL_WALLETS)}\nGroups: {uniq}\nSniper marked: {len(sniper_marked)}\nLast tx {mins}m\nSeen sigs: {len(seen_sigs)}")

    elif low.startswith("/test"):
        send_tg(
            "✅ V5.1 15s OK\n"
            "🎯 EARLY @$104k\n"
            "🪙 TEST | $104k | 1m old\n"
            "👤 `8xK2...pL9` G:Alpha SNIPER\n"
            "CA: `So1111111111111111111111111111111111111112`\n"
            "[DS](https://dexscreener.com/solana/test) [PH](https://photon-sol.tinyastro.io/en/lp/test) [TX](https://solscan.io/tx/test)"
        )

# ========= POLL TG COMMANDS =========
def poll_commands():
    off = 0
    while True:
        try:
            if not BOT_TOKEN:
                time.sleep(5); continue
            r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=20", timeout=25).json()
            for u in r.get("result", []):
                off = u["update_id"] + 1
                msg = u.get("message", {})
                txt = msg.get("text", "")
                if txt.startswith("/"):
                    handle_command(txt)
        except:
            time.sleep(3)

# ========= MAIN =========
async def main_loop():
    threading.Thread(target=track_funders_15s, daemon=True).start()
    send_tg("🚀 V5.1 FULL DEPLOYED\n15s child | 1-5w PREP | Sniper >0.35 SOL | Early <300k | Buy>$8")
    await track_sol_polling()

def start_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_loop())
    except Exception as e:
        print(f"CRASH {e}", flush=True)
        time.sleep(5)
        start_bot()

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=poll_commands, daemon=True).start()
    time.sleep(1)
    start_bot()
