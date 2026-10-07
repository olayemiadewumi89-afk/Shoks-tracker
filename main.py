import os, asyncio, threading, time, requests, json
from flask import Flask
from datetime import datetime, timedelta
from collections import defaultdict
from web3 import Web3

SOL_WALLETS = [
"Beqv6dzTcjV2eodo8RRXCiCcnSYrS1vkQKhfqwHXqeit",
"5t8FA8z7SFiEpjau2nTBYKN8jd5CGo8Ttcgt1m73C7d",
"6xmMW5JPSEfeRuNdkxixHWsm4Sf57SdXybA3BdwZzrM1",
"2Ysos2B2S6rb9FBNYUB7Yrk54Xv2nNbr8eBHs1sX8M6m",
"Agt534WQKGXuVt9UFamcgqH9djySF9PmxqNS5caiMCAV",
"Ab25e8CyZ6119HSY6r4wdKpBPxY3UBLWfrZ7PEBvHD4F",
"4ugDhHJ8XDXAeABmrNmGffFaLbJb9BkPyiFGVSV9ocwo",
"9BMzTpSo4URse1oN666pmexhdjpU1vA5p7LtroCFQdLU",
"498g1rVnFcnjBjpfw1xyqA1WvgQXUU8RWuELjxkjAayQ",
"8f39XhhZoRD8sYb6K5K9N7iSHXkL7BDmFFQNDF3TtsEr",
"83b2LMf12aLec92kur3duRA8VvUkLaXUtML31aCcZNCM",
"7aoGoRSexZu1DE4vC24CVo4SqWrBDeoJ34qLRGUsL9ha",
"2yXwy5Dsa1XtEXcsrkFVRJeyuWD3qKkMN3pP3p5VTW3V",
"7iPPqPyrqcmfenRs4xZ72ab4pyuUofXB5YaQB83WJmT9",
"H2QSGECp13sFLJgdTsDtayX3dk18Dm6sQMSQKcew7Xzk",
"DCeH3aCsstGUSxQqS72VBZwTydoor1nQ6dWaxrgGQk39",
"6My97BBVoJz3j7mQWFVz5pykFxMsCs44gjhaqmczcAND",
"D9tPQeij7vSTZwkxzxZibso4GFuRW8aBMpCg5QhCSfVL",
"59mWSDjx5VFQz15FGJio8BJzupKorK5SVHSbaJaZGAxC",
]

EVM_WALLETS = [
"0xfd87eda88be6c372453b721da63d58ad1a5b2d94",
"0x2a17e1e796dd7bd3b27efe2cda72d4b909baaacf",
"0x4bc1782fafb967834e0e75947ba15113e48fc70e",
"0x5a08728a6704d99468d8919ba0adc2e1d2733083",
"0xae97104fde93a82797afec819ac0e09b8d544907",
"0xabf122e41ce873a3c6a71fbf1c6419ca5df07f17",
"0xd1c77a04b87393e98a1220532e72e8f7d0a31c5a",
"0x0c175c6a0065ee05f871a68783d2de432a1e6cbe",
"0x696d1265c8fc4f14797abebfae3c43ebfa9d8e28",
"0x0121525f755c9e7bbc525bba6672716ab46ced57",
"0x27d6a5a1d9a5e89bfce0239f08d9c95cf2256ebb",
"0xb8f305f27ccc406373de0082cc06cb1d065504ea",
"0xb02208d1b27811480cf6171bccc2b7af9513cddb",
"0x06de9c48b1e639ed5c13ec8fbd4080a38e39f2d1",
"0x1890e719822bc704c4f117aa4109401c2bab6f79",
"0xd4cf04bc9d7c80b49c6c30a633f7b9bd5370b4d6",
"0x7f6e4e1e59be05dc717c8e5429154a7408609ee2",
]

WALLETS_FILE = "wallets.json"
def load_wallets():
    global SOL_WALLETS, EVM_WALLETS
    try:
        if os.path.exists(WALLETS_FILE):
            with open(WALLETS_FILE, "r") as f:
                data=json.load(f)
                SOL_WALLETS=data.get("sol",SOL_WALLETS)
                EVM_WALLETS=data.get("evm",EVM_WALLETS)
    except: pass
def save_wallets():
    try:
        with open(WALLETS_FILE,"w") as f:
            json.dump({"sol":SOL_WALLETS,"evm":EVM_WALLETS},f)
    except: pass
load_wallets()

HELIUS_KEY = "3ac60377-a024-4177-8ef4-b8c36a692a57"

RPCS_FALLBACK = {
    "ETH": ["https://ethereum-rpc.publicnode.com","https://eth.llamarpc.com","https://rpc.ankr.com/eth"],
    "BSC": ["https://bsc-rpc.publicnode.com","https://bsc.llamarpc.com","https://rpc.ankr.com/bsc"],
    "BASE": ["https://base-rpc.publicnode.com","https://base.llamarpc.com","https://rpc.ankr.com/base"],
    "ARB": ["https://arbitrum-one-rpc.publicnode.com","https://arbitrum.llamarpc.com"],
    "POLY": ["https://polygon-bor-rpc.publicnode.com","https://polygon.llamarpc.com"]
}

def get_w3_with_fallback(chain):
    for url in RPCS_FALLBACK.get(chain, []):
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={'timeout':6}))
            if w3.is_connected():
                return w3
        except: continue
    return None

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)
@app.route('/')
def home(): return "Shok Tracker V3.3 Fixed 7d",200
@app.route('/health')
def health(): return "OK",200
def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv("PORT",10000)))

cluster_memory = defaultdict(list)
holdings = defaultdict(dict)

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(text[:1200]); return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id":CHAT_ID,"text":text,"parse_mode":"Markdown","disable_web_page_preview":True}, timeout=15)
    except Exception as e:
        print(f"TG err {e}")

def set_bot_commands():
    if not BOT_TOKEN: return
    cmds=[
        {"command":"start","description":"Status"},
        {"command":"overlap","description":"Overlap 7d + LIVE - fixed"},
        {"command":"history","description":"Current holders"},
        {"command":"listwallets","description":"List"},
    ]
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands", json={"commands":cmds}, timeout=10)
    except: pass

def get_token_info_quick(token_address, chain_hint="base"):
    chain_map={"ETH":"ethereum","BSC":"bsc","BASE":"base","ARB":"arbitrum","POLY":"polygon","SOL":"solana"}
    ds_chain=chain_map.get(chain_hint, chain_hint.lower())
    try:
        r=requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=5).json()
        pairs=r.get("pairs",[])
        if pairs:
            best=sorted(pairs, key=lambda x: x.get("liquidity",{}).get("usd",0), reverse=True)[0]
            name=best.get("baseToken",{}).get("symbol", token_address[:6])
            fdv=best.get("fdv") or best.get("marketCap") or 0
            if fdv>=1_000_000: mcap=f"${fdv/1_000_000:.2f}M"
            elif fdv>=1000: mcap=f"${fdv/1000:.1f}k"
            else: mcap=f"${fdv:.0f}" if fdv else "N/A"
            price=float(best.get("priceUsd",0) or 0)
            pair_addr=best.get("pairAddress",token_address)
            chain_id=best.get("chainId",ds_chain)
            dex_link=f"https://dexscreener.com/{chain_id}/{pair_addr}"
            return name, mcap, dex_link, price, chain_id.upper()
    except: pass
    return token_address[:6], "N/A", f"https://dexscreener.com/{ds_chain}/{token_address}", 0, chain_hint

def short(a):
    if not a: return "?"
    return f"{a[:4]}...{a[-4:]}"

def get_helius_holdings(wallet):
    try:
        r = requests.get(f"https://api.helius.xyz/v0/addresses/{wallet}/balances?api-key={HELIUS_KEY}", timeout=10).json()
        return r.get("tokens", [])
    except:
        return []

def get_evm_holdings_blockscout(wallet, base_url):
    try:
        url = f"{base_url}/api/v2/addresses/{wallet}/token-balances"
        r = requests.get(url, timeout=8).json()
        if isinstance(r, list):
            return r
        return []
    except:
        return []

def live_scan_overlap():
    token_to_wallets = defaultdict(list)

    # SOL LIVE - no filter
    for sol_w in SOL_WALLETS:
        try:
            tokens = get_helius_holdings(sol_w)
            for t in tokens:
                mint = t.get("mint")
                amt = t.get("amount",0)
                if not mint or amt==0: continue
                # skip tiny dust < $0.5 equivalent but keep amount > 0
                token_to_wallets[f"SOL:{mint}"].append({
                    "mint": mint, "wallet": sol_w,
                    "amount": amt, "decimals": t.get("decimals",6),
                    "chain": "SOL", "source": "live"
                })
        except: continue

    # EVM LIVE
    EVM_SCN = {
        "ETH": "https://eth.blockscout.com",
        "BASE": "https://base.blockscout.com",
        "BSC": "https://bsc.blockscout.com",
        "ARB": "https://arbitrum.blockscout.com",
        "POLY": "https://polygon.blockscout.com"
    }
    for evm_w in EVM_WALLETS:
        for chain, base_url in EVM_SCN.items():
            try:
                bals = get_evm_holdings_blockscout(evm_w, base_url)
                for h in bals[:200]:
                    tok = h.get("token",{})
                    t_addr = tok.get("address_hash") or (tok.get("address",{}).get("hash") if isinstance(tok.get("address"), dict) else tok.get("address"))
                    if not t_addr: continue
                    if t_addr.lower() == "0x0000000000000000000000000000000000000000": continue
                    try: val = int(h.get("value",0))
                    except: continue
                    if val == 0: continue
                    dec = int(tok.get("decimals",18) or 18)
                    if dec == 0: continue
                    # skip NFT 1
                    if val == 1 and dec == 0: continue
                    real = val / (10**dec) if dec else 0
                    if real < 0.000001: continue
                    token_to_wallets[f"{chain}:{t_addr.lower()}"].append({
                        "mint": t_addr, "wallet": evm_w,
                        "amount": val, "decimals": dec,
                        "chain": chain, "source": "live"
                    })
            except: continue

    # 7d HISTORY from cluster_memory
    now = datetime.now()
    for token, events in cluster_memory.items():
        recent = [e for e in events if (now - e[1]).days <= 7]
        if len(recent) < 2: continue
        uniq_w = set([w.lower() for w,_,_ in recent])
        if len(uniq_w) < 2: continue
        for w, ts, ch in recent:
            key = f"{ch}:{token.lower()}" if ch!= "SOL" else f"SOL:{token}"
            # if already live, skip
            if any(x["wallet"].lower()==w.lower() for x in token_to_wallets.get(key,[])):
                continue
            token_to_wallets[key].append({
                "mint": token, "wallet": w, "amount": 0,
                "decimals": 18, "chain": ch, "source": "history", "time": ts
            })

    # FINAL - ONLY skip if BOTH mcap N/A AND price 0 AND only 2 holders with 0 amount
    final = {}
    for key, holders in token_to_wallets.items():
        uniq_map = {}
        for h in holders:
            uniq_map[h["wallet"].lower()] = h
        if len(uniq_map) < 2: continue
        chain = list(uniq_map.values())[0]["chain"]
        mint = list(uniq_map.values())[0]["mint"]
        name, mcap, dex_link, price, _ = get_token_info_quick(mint, chain)

        # FIX: don't kill N/A coins - keep them if at least 2 wallets still hold
        live_count = len([x for x in uniq_map.values() if x.get("source")=="live"])
        if mcap == "N/A" and price == 0 and live_count < 2:
            # keep only if history has 3+ wallets (strong signal)
            if len(uniq_map) < 3:
                continue

        final[key] = (chain, mint, name, mcap, dex_link, price, list(uniq_map.values()))

    if not final:
        return "📊 *No overlaps in last 7d*\n\nLive scan: 0 shared holdings.\nHistory: 0 clusters in 7d.\n\nNote: History builds after deployment - let it run 12h for buys."

    msg = f"🔍 *OVERLAP FOUND - {len(final)} coin(s) - 7d + LIVE (FIXED)*\n\n"
    count=0
    for key, (chain, mint, name, mcap, dex_link, price, holders_list) in sorted(final.items(), key=lambda x: len(x[1][6]), reverse=True)[:15]:
        scan_map = {"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com","SOL":"solscan.io"}
        explorer_token = f"https://solscan.io/token/{mint}" if chain=="SOL" else f"https://{scan_map.get(chain,'basescan.org')}/token/{mint}"
        msg += f"*{count+1}) {name} - {mcap}*\n"
        msg += f"Chain: {chain}\n"
        msg += f"Token: `{short(mint)}` - [View]({explorer_token})\n"
        live_h = [h for h in holders_list if h.get("source")=="live"]
        hist_h = [h for h in holders_list if h.get("source")=="history"]
        if live_h:
            msg += f"Still Holding ({len(live_h)}):\n"
            for h in live_h[:6]:
                w=h["wallet"]
                raw=h["amount"]; dec=h["decimals"]
                real=raw/(10**dec) if dec else raw
                usd=real*price if price>0 else 0
                scan_url = f"https://solscan.io/account/{w}" if chain=="SOL" else f"https://{scan_map.get(chain,'basescan.org')}/address/{w}"
                if usd>=0.5:
                    msg += f"• `{short(w)}` - ${usd:,.0f} - [Scan]({scan_url})\n"
                else:
                    msg += f"• `{short(w)}` - {real:.4f} - [Scan]({scan_url})\n"
        if hist_h:
            msg += f"Bought 7d ({len(hist_h)}):\n"
            for h in hist_h[:6]:
                w=h["wallet"]
                scan_url = f"https://solscan.io/account/{w}" if chain=="SOL" else f"https://{scan_map.get(chain,'basescan.org')}/address/{w}"
                msg += f"• `{short(w)}` - bought - [Scan]({scan_url})\n"
        msg += f"📈 [Chart]({dex_link})\n\n"
        count+=1
        if len(msg) > 3800: break
    return msg[:4000]

def build_overlap_report():
    return live_scan_overlap()

def handle_command(text):
    t=text.strip()
    low=t.lower()
    if low.startswith("/start"):
        send_tg(f"🚀 *Shok Tracker V3.3 FIXED 7d ACTIVE*\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM\n✅ /overlap now shows N/A coins too + no timeout\nPress / for commands")
        set_bot_commands()
    elif low.startswith("/overlap"):
        send_tg("⏳ Scanning 19 SOL + 17 EVM (5 chains) + 7d history... (takes 60s)")
        # run in thread so Telegram doesn't timeout
        def run_overlap():
            report = build_overlap_report()
            send_tg(report)
        threading.Thread(target=run_overlap, daemon=True).start()
    elif low.startswith("/history"):
        if not holdings:
            send_tg("📜 No clusters yet - waiting for buys...")
            return
        msg="📜 *Current Holders (2+ wallets)*\n\n"
        shown=0
        for token, wallets_dict in holdings.items():
            if len(wallets_dict)<2: continue
            sample=list(wallets_dict.values())[0]
            chain=sample.get("chain","?")
            name2, mcap, dex_link, _, _ = get_token_info_quick(token, chain)
            msg+=f"🪙 *{name2}* ({mcap}) Chain:{chain}\n"
            for w_addr, data in wallets_dict.items():
                usd=data.get("amount_usd",0)
                msg+=f"• `${usd:.0f}` - `{w_addr[:6]}...{w_addr[-4:]}`\n"
            msg+=f"📊 [Chart]({dex_link})\n\n"
            shown+=1
            if shown>=6: break
        if shown==0: msg+="No current 2+ holders."
        send_tg(msg[:4000])
    elif low.startswith("/listwallets"):
        sol="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(SOL_WALLETS)])
        evm="\n".join([f"{i+1}. `{w}`" for i,w in enumerate(EVM_WALLETS)])
        send_tg((f"*SOL ({len(SOL_WALLETS)}):*\n{sol}\n\n*EVM ({len(EVM_WALLETS)}):*\n{evm}")[:4000])

async def track_chain(chain):
    seen=set()
    scan={"ETH":"etherscan.io","BSC":"bscscan.com","BASE":"basescan.org","ARB":"arbiscan.io","POLY":"polygonscan.com"}[chain]
    while True:
        try:
            w3=get_w3_with_fallback(chain)
            if not w3:
                await asyncio.sleep(10); continue
            bn=w3.eth.block_number
            for b in range(max(0,bn-2), bn+1):
                if b in seen: continue
                seen.add(b)
                if len(seen)>150: seen=set(list(seen)[-80:])
                try:
                    block=w3.eth.get_block(b, full_transactions=True)
                    for tx in block.transactions:
                        frm=tx.get('from')
                        if not frm: continue
                        if frm.lower() not in [x.lower() for x in EVM_WALLETS]: continue
                        h=tx.hash.hex() if hasattr(tx.hash,'hex') else tx['hash'].hex()
                        try:
                            receipt=w3.eth.get_transaction_receipt(h)
                            logs=receipt.get('logs',[])
                            for log in logs:
                                if len(log.get('topics',[]))!=3: continue
                                if log['topics'][0].hex()!= 'ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef': continue
                                from_addr='0x'+log['topics'][1].hex()[-40:]
                                to_addr_topic='0x'+log['topics'][2].hex()[-40:]
                                token_contract=log['address']
                                amount_raw=int(log['data'],16)
                                name, mcap, dex_link, price, _ = get_token_info_quick(token_contract, chain)
                                usd_val = (amount_raw/1e18*price) if price>0 else 0
                                if from_addr.lower() == frm.lower():
                                    if usd_val!=0 and usd_val <500: continue
                                    if token_contract in holdings and frm in holdings[token_contract]:
                                        holdings[token_contract].pop(frm, None)
                                    usd_str = f"${usd_val:.0f}" if usd_val>0 else "unknown"
                                    send_tg(f"🚨 *SELL ALERT* 🚨\n🪙 {name} ({mcap})\n💸 {usd_str} sold by `{frm[:6]}...{frm[-4:]}`\n🔗 {chain}\n👤 `{frm}`\n📄 `{token_contract}`\n📊 [Chart]({dex_link}) | 🔍 [Tx](https://{scan}/tx/{h})")
                                else:
                                    if to_addr_topic.lower() == frm.lower():
                                        if usd_val>0 and usd_val<1: continue
                                        cluster_memory[token_contract].append((frm, datetime.now(), chain))
                                        cluster_memory[token_contract]=cluster_memory[token_contract][-200:]
                                        holdings[token_contract][frm]={"amount_usd": usd_val if usd_val>0 else 100, "chain": chain, "token_name": name, "mcap": mcap}
                                        usd_str = f"${usd_val:.0f}" if usd_val>0 else ""
                                        send_tg(f"💰 *{chain} BUY*\n🪙 {name} ({mcap})\n👤 `{frm[:6]}...{frm[-4:]}` | {usd_str}\n📄 `{token_contract}`\n📊 [Chart]({dex_link}) | 🔍 [Tx](https://{scan}/tx/{h})")
                        except: pass
                except: pass
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[{chain}] err {e}"); await asyncio.sleep(5)

import websockets
async def track_sol():
    uri=f"wss://atlas-mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                sub={"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions": SOL_WALLETS},{"commitment":"confirmed"}]}
                await ws.send(json.dumps(sub))
                async for msg in ws:
                    try:
                        data=json.loads(msg)
                        if "params" not in data: continue
                        sig=data["params"]["result"]["value"].get("signature","")
                        if sig:
                            send_tg(f"💰 *SOL BUY* Sig `{sig[:16]}..` [Tx](https://solscan.io/tx/{sig})")
                    except: pass
        except Exception as e:
            print(f"[SOL] WS err {e}"); await asyncio.sleep(5)

async def main_loop():
    send_tg(f"🚀 *Shok Tracker V3.3 FIXED ACTIVE*\nTracking {len(SOL_WALLETS)} SOL + {len(EVM_WALLETS)} EVM x 5 chains\n✅ Overlap fixed: shows N/A mcap coins + 7d history + no timeout")
    set_bot_commands()
    tasks=[track_chain(c) for c in RPCS_FALLBACK.keys()]
    tasks.append(track_sol())
    await asyncio.gather(*tasks)

def start_bot():
    loop=asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main_loop())

if __name__=="__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    def poll_cmd():
        off=0
        while True:
            try:
                if not BOT_TOKEN: time.sleep(5); continue
                r=requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={off}&timeout=10", timeout=15).json()
                for u in r.get("result",[]):
                    off=u["update_id"]+1
                    txt=u.get("message",{}).get("text","")
                    if txt.startswith("/"): handle_command(txt)
            except: time.sleep(4)
    threading.Thread(target=poll_cmd, daemon=True).start()
    start_bot()
