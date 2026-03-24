"""
TonPal — TON read operations via TonCenter and TonAPI.

Provides: balance, transactions, address info, jettons, NFTs.
"""
import re
from typing import Any, Optional

import aiohttp

from config import TONCENTER_BASE, TONAPI_BASE, TONCENTER_API_KEY

TON_ADDR_RE = re.compile(r"(?:EQ|UQ)[a-zA-Z0-9_-]{44,48}")


def _toncenter_url(path: str, params: Optional[dict] = None) -> str:
    base = TONCENTER_BASE.rstrip("/")
    params = dict(params or {})
    if TONCENTER_API_KEY:
        params["api_key"] = TONCENTER_API_KEY
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}/{path}?{qs}" if qs else f"{base}/{path}"


async def _get(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()


async def get_balance(address: str) -> tuple[str, bool]:
    """Get TON balance. Returns (human-readable text, success)."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid TON address format", False
    data = await _get(_toncenter_url("getAddressBalance", {"address": address}))
    if data.get("ok"):
        nanoton = int(data.get("result", 0))
        ton = nanoton / 1e9
        return f"{ton:.4f} TON", True
    return "Failed to fetch balance", False


async def get_balance_raw(address: str) -> tuple[float, bool]:
    """Get balance as float. Returns (ton_amount, success)."""
    if not TON_ADDR_RE.fullmatch(address):
        return 0.0, False
    data = await _get(_toncenter_url("getAddressBalance", {"address": address}))
    if data.get("ok"):
        nanoton = int(data.get("result", 0))
        return nanoton / 1e9, True
    return 0.0, False


async def get_transactions(address: str, limit: int = 5) -> tuple[str, bool]:
    """Get recent transactions. Returns (formatted text, success)."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid TON address format", False
    data = await _get(_toncenter_url("getTransactions", {"address": address, "limit": str(limit)}))
    if not data.get("ok"):
        return "Failed to fetch transactions", False
    txs = data.get("result", [])
    if not txs:
        return "No transactions found", True

    import datetime
    lines = [f"━━━ 📜 Transactions ━━━\n"]
    for i, tx in enumerate(txs[:limit], 1):
        utime = tx.get("utime", 0)
        dt = datetime.datetime.fromtimestamp(utime).strftime("%m-%d %H:%M") if utime else "?"
        fee = int(tx.get("fee", 0)) / 1e9

        in_msg = tx.get("in_msg") or {}
        out_msgs = tx.get("out_msgs") or []
        in_val = int(in_msg.get("value") or 0) / 1e9
        in_src = in_msg.get("source", "")

        if in_val > 0.001 and in_src:
            short_from = in_src[:12] + "..." + in_src[-4:] if len(in_src) > 16 else in_src
            comment = (in_msg.get("message") or "").strip()
            if comment and not comment.startswith("c2ln") and len(comment) < 50:
                lines.append(f"{i}. 📥 +{in_val:.4f} TON\n   From: {short_from}\n   📝 {comment}\n   🕐 {dt}")
            else:
                lines.append(f"{i}. 📥 +{in_val:.4f} TON\n   From: {short_from}\n   🕐 {dt}")
        elif out_msgs:
            total_out = 0
            dest = ""
            for om in out_msgs:
                ov = int(om.get("value") or 0) / 1e9
                total_out += ov
                if not dest:
                    dest = om.get("destination", "")
            short_to = dest[:12] + "..." + dest[-4:] if len(dest) > 16 else dest
            comment = ""
            for om in out_msgs:
                c = (om.get("message") or "").strip()
                if c and not c.startswith("c2ln") and len(c) < 50:
                    comment = c
                    break
            line = f"{i}. 📤 -{total_out:.4f} TON"
            if short_to:
                line += f"\n   To: {short_to}"
            if comment:
                line += f"\n   📝 {comment}"
            line += f"\n   🕐 {dt}  Fee: {fee:.4f}"
            lines.append(line)
        else:
            lines.append(f"{i}. ⚙️ Internal\n   🕐 {dt}  Fee: {fee:.4f}")

    return "\n".join(lines), True


async def get_address_info(address: str) -> tuple[str, bool]:
    """Get address info (balance + state). Returns (formatted text, success)."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid TON address format", False
    data = await _get(_toncenter_url("getAddressInformation", {"address": address}))
    if not data.get("ok"):
        return "Failed to fetch address info", False
    r = data.get("result", {})
    balance = int(r.get("balance", 0))
    ton = balance / 1e9
    state = r.get("state", "unknown")
    state_txt = {"active": "✅ Active", "uninitialized": "⏸ Uninitialized", "frozen": "❄️ Frozen"}.get(state, state)
    return f"Balance: {ton:.4f} TON\nState: {state_txt}", True


async def get_jettons(address: str) -> tuple[str, bool]:
    """Get jetton balances for address. Uses TonAPI."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid TON address format", False
    url = f"{TONAPI_BASE.rstrip('/')}/accounts/{address}/jettons"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return "Failed to fetch jettons", False
                data = await resp.json()
    except Exception as e:
        return f"Error: {e}", False
    balances = data.get("balances", [])
    if not balances:
        return "No jettons found", True
    lines = []
    for b in balances[:15]:
        jetton = b.get("jetton", {})
        symbol = jetton.get("symbol", "?")
        name = jetton.get("name", "")
        raw = b.get("balance", "0")
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        decimals = jetton.get("decimals", 9)
        amount = raw / (10 ** decimals)
        lines.append(f"• {symbol} ({name}): {amount:,.4f}")
    return "\n".join(lines), True


async def get_nfts(address: str) -> tuple[str, bool]:
    """Get NFT items for address. Uses TonAPI."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid TON address format", False
    url = f"{TONAPI_BASE.rstrip('/')}/accounts/{address}/nfts"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return "Failed to fetch NFTs", False
                data = await resp.json()
    except Exception as e:
        return f"Error: {e}", False
    nfts = data.get("nft_items", [])
    if not nfts:
        return "No NFTs found", True
    lines = []
    for n in nfts[:15]:
        meta = n.get("metadata", {}) or {}
        name = meta.get("name") or n.get("name") or "Unknown"
        addr = n.get("address", "?")[:20] + "..."
        lines.append(f"• {name} ({addr})")
    return "\n".join(lines), True


async def get_nft_item_info(address: str) -> tuple[str, bool, Optional[str]]:
    """Check if address is an NFT item and return info. Returns (text, is_nft, image_url)."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid address format", False, None

    acct_url = f"{TONAPI_BASE.rstrip('/')}/accounts/{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(acct_url) as resp:
                if resp.status != 200:
                    return "NOT_NFT", False, None
                acct = await resp.json()
    except Exception:
        return "NOT_NFT", False, None

    interfaces = acct.get("interfaces", [])
    is_nft = any(x in interfaces for x in ["nft_item", "nft_collection", "teleitem"])
    if not is_nft:
        return "NOT_NFT", False, None

    is_collection = "nft_collection" in interfaces
    image_url = None

    if is_collection:
        col_url = f"{TONAPI_BASE.rstrip('/')}/nfts/collections/{address}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(col_url) as resp:
                    if resp.status == 200:
                        col = await resp.json()
                        meta = col.get("metadata", {}) or {}
                        name = meta.get("name", "Unknown Collection")
                        desc = meta.get("description", "")
                        image_url = meta.get("image")
                        count = col.get("next_item_index", "?")
                        owner = col.get("owner", {})
                        owner_name = owner.get("name", "")
                        owner_addr = owner.get("address", "?")[:20] + "..."

                        lines = [
                            f"━━━ NFT Collection ━━━",
                            f"📛 {name}",
                        ]
                        if desc:
                            lines.append(f"📝 {desc[:200]}")
                        lines.append(f"📦 Items: {count}")
                        if owner_name:
                            lines.append(f"👤 Owner: {owner_name}")
                        else:
                            lines.append(f"👤 Owner: {owner_addr}")
                        lines.append(f"\n📋 Contract: {address}")
                        lines.append(f"🔗 Tonviewer: https://tonviewer.com/{address}")
                        lines.append(f"🔗 Getgems: https://getgems.io/collection/{address}")
                        return "\n".join(lines), True, image_url
        except Exception:
            pass
        return f"NFT Collection at {address}", True, None

    nft_url = f"{TONAPI_BASE.rstrip('/')}/nfts/{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(nft_url) as resp:
                if resp.status != 200:
                    return f"NFT item at {address} (details unavailable)", True, None
                nft = await resp.json()
    except Exception:
        return f"NFT item at {address}", True, None

    meta = nft.get("metadata", {}) or {}
    name = meta.get("name", "Unknown NFT")
    desc = meta.get("description", "")
    image_url = meta.get("image")

    previews = nft.get("previews", [])
    if previews and not image_url:
        for p in reversed(previews):
            if p.get("url"):
                image_url = p["url"]
                break

    collection = nft.get("collection", {}) or {}
    col_name = collection.get("name", "")
    col_addr = collection.get("address", "")

    owner = nft.get("owner", {}) or {}
    owner_name = owner.get("name", "")
    owner_addr = owner.get("address", "?")[:20] + "..."

    verified = nft.get("verified", False)
    approved = nft.get("approved_by", [])
    dns = nft.get("dns", "")

    lines = [f"━━━ NFT Item ━━━", f"📛 {name}"]
    if col_name:
        lines.append(f"📂 Collection: {col_name}")
    if dns:
        lines.append(f"🌐 DNS: {dns}")
    if desc:
        lines.append(f"📝 {desc[:200]}")
    if owner_name:
        lines.append(f"👤 Owner: {owner_name}")
    else:
        lines.append(f"👤 Owner: {owner_addr}")
    if verified:
        lines.append(f"✅ Verified")
    if approved:
        lines.append(f"🏷 Approved by: {', '.join(approved)}")
    lines.append(f"\n📋 Address: {address}")
    lines.append(f"🔗 Tonviewer: https://tonviewer.com/{address}")
    if col_addr:
        raw = col_addr
        if ":" in raw:
            raw = _raw_to_eq(raw)
        lines.append(f"🔗 Getgems: https://getgems.io/collection/{raw}")

    return "\n".join(lines), True, image_url


async def get_jetton_info(address: str) -> tuple[str, bool]:
    """Full token scanner: info + price + LP + safety + holders. BuyBot/Scanner-style."""
    if not TON_ADDR_RE.fullmatch(address):
        return "Invalid address format", False

    url = f"{TONAPI_BASE.rstrip('/')}/jettons/{address}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return "NOT_A_JETTON", False
                if resp.status != 200:
                    return "Failed to fetch jetton info", False
                data = await resp.json()
    except Exception as e:
        return f"Error: {e}", False

    meta = data.get("metadata", {})
    name = meta.get("name", "Unknown")
    symbol = meta.get("symbol", "?")
    decimals = int(meta.get("decimals", "9"))
    desc = meta.get("description", "")
    total_raw = data.get("total_supply", "0")
    try:
        total_supply = int(total_raw) / (10 ** decimals)
    except (ValueError, TypeError):
        total_supply = 0
    holders = data.get("holders_count", "?")
    mintable = data.get("mintable", False)
    verification = data.get("verification", "none")
    admin = data.get("admin", {})
    admin_name = admin.get("name") or ""

    price_usd = None
    is_blacklisted = False
    is_taxable = False
    is_community = False
    tags = []
    try:
        ston_url = f"https://api.ston.fi/v1/assets/{address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(ston_url) as resp:
                if resp.status == 200:
                    ston = await resp.json()
                    asset = ston.get("asset", {})
                    p = asset.get("third_party_price_usd") or asset.get("dex_price_usd")
                    if p:
                        price_usd = float(p)
                    is_blacklisted = asset.get("blacklisted", False)
                    is_taxable = asset.get("taxable", False)
                    is_community = asset.get("community", False)
                    tags = asset.get("tags", [])
    except Exception:
        pass

    lp_tvl_usd = None
    volume_24h_usd = None
    lp_fee = None
    pool_count = 0
    try:
        pools_url = "https://api.ston.fi/v1/pools/query"
        payload = {"search_terms": [address], "limit": 5}
        async with aiohttp.ClientSession() as session:
            async with session.post(pools_url, json=payload) as resp:
                if resp.status == 200:
                    pools_data = await resp.json()
                    pools = pools_data.get("pool_list", [])
                    pool_count = len(pools)
                    total_tvl = 0.0
                    total_vol = 0.0
                    for pool in pools:
                        tvl_str = pool.get("lp_total_supply_usd", "0")
                        vol_str = pool.get("volume_24h_usd", "0")
                        try:
                            total_tvl += float(tvl_str) if tvl_str else 0
                        except (ValueError, TypeError):
                            pass
                        try:
                            total_vol += float(vol_str) if vol_str else 0
                        except (ValueError, TypeError):
                            pass
                        if not lp_fee and pool.get("lp_fee"):
                            lp_fee = pool.get("lp_fee")
                    if total_tvl > 0:
                        lp_tvl_usd = total_tvl
                    if total_vol > 0:
                        volume_24h_usd = total_vol
    except Exception:
        pass

    top_holders_text = ""
    top10_pct = 0.0
    try:
        h_url = f"{TONAPI_BASE.rstrip('/')}/jettons/{address}/holders?limit=10"
        async with aiohttp.ClientSession() as session:
            async with session.get(h_url) as resp:
                if resp.status == 200:
                    h_data = await resp.json()
                    addrs = h_data.get("addresses", [])
                    if addrs and total_supply > 0:
                        lines_h = []
                        for i, h in enumerate(addrs[:10], 1):
                            bal = int(h.get("balance", 0)) / (10 ** decimals)
                            pct = (bal / total_supply) * 100 if total_supply else 0
                            top10_pct += pct
                            owner = h.get("owner", {})
                            owner_name = owner.get("name", "")
                            if owner_name:
                                lines_h.append(f"  {i}. {owner_name} — {pct:.2f}%")
                            else:
                                oa = owner.get("address", "?")[:16] + "..."
                                lines_h.append(f"  {i}. {oa} — {pct:.2f}%")
                        top_holders_text = "\n".join(lines_h)
    except Exception:
        pass

    verify_icon = {"whitelist": "✅", "blacklist": "🚫", "none": "⚪"}.get(verification, "⚪")
    mint_icon = "🟢" if mintable else "🔴"

    lines = [f"━━━ {name} ({symbol}) ━━━"]

    if price_usd is not None and price_usd > 0:
        lines.append(f"💲 Price: ${price_usd:.8f}" if price_usd < 0.01 else f"💲 Price: ${price_usd:.4f}")
        if total_supply > 0:
            fdv = price_usd * total_supply
            lines.append(f"📊 FDV: {_fmt_usd(fdv)}")

    if lp_tvl_usd is not None:
        lines.append(f"💧 Liquidity (TVL): {_fmt_usd(lp_tvl_usd)}")
    if volume_24h_usd is not None:
        lines.append(f"📈 Volume 24h: {_fmt_usd(volume_24h_usd)}")
    if pool_count > 0:
        lines.append(f"🏊 DEX Pools: {pool_count}")
    if lp_fee:
        try:
            fee_pct = float(lp_fee) * 100
            lines.append(f"💸 LP Fee: {fee_pct:.2f}%")
        except (ValueError, TypeError):
            pass

    lines.append("")
    lines.append(f"📦 Supply: {total_supply:,.0f}" if total_supply > 0 else f"📦 Supply: {total_raw}")
    lines.append(f"👥 Holders: {holders}")
    lines.append(f"{mint_icon} Mintable: {'Yes' if mintable else 'No'}")
    lines.append(f"{verify_icon} Verification: {verification}")

    lines.append("")
    safety_score = 0
    safety_items = []
    if is_blacklisted:
        safety_items.append("🚫 BLACKLISTED on STON.fi")
    else:
        safety_score += 1
    if is_taxable:
        safety_items.append("⚠️ Taxable (transfer fees)")
    else:
        safety_score += 1
    if not mintable:
        safety_score += 1
        safety_items.append("🔒 Not mintable")
    else:
        safety_items.append("⚠️ Mintable (admin can mint more)")
    if verification == "whitelist":
        safety_score += 1
        safety_items.append("✅ Whitelisted")
    elif verification == "blacklist":
        safety_items.append("🚫 Blacklisted")
    if lp_tvl_usd and lp_tvl_usd > 10000:
        safety_score += 1
        safety_items.append("💧 Sufficient liquidity")
    elif lp_tvl_usd and lp_tvl_usd < 1000:
        safety_items.append("⚠️ Low liquidity")
    if top10_pct > 80:
        safety_items.append(f"⚠️ Top 10 hold {top10_pct:.1f}% (concentrated)")
    elif top10_pct > 0:
        safety_items.append(f"📊 Top 10 hold {top10_pct:.1f}%")

    risk_label = "🟢 Low" if safety_score >= 4 else ("🟡 Medium" if safety_score >= 2 else "🔴 High")
    lines.append(f"🛡 Risk: {risk_label}")
    for item in safety_items:
        lines.append(f"  {item}")

    if desc:
        lines.append(f"\n📝 {desc[:150]}")
    if admin_name:
        lines.append(f"👤 Admin: {admin_name}")
    if tags:
        lines.append(f"🏷 Tags: {', '.join(tags[:5])}")

    if top_holders_text:
        lines.append(f"\n📊 Top Holders:")
        lines.append(top_holders_text)

    lines.append(f"\n📋 Contract: {address}")
    lines.append(f"🔗 Tonviewer: https://tonviewer.com/{address}")
    lines.append(f"🔗 STON.fi: https://app.ston.fi/swap?chartToken={address}")

    return "\n".join(lines), True


def _fmt_usd(val: float) -> str:
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:,.2f}B"
    if val >= 1_000_000:
        return f"${val / 1_000_000:,.2f}M"
    if val >= 1_000:
        return f"${val / 1_000:,.2f}K"
    return f"${val:,.2f}"


_assets_cache = {"data": [], "ts": 0}
_CACHE_TTL = 300


async def _fetch_all_assets() -> list:
    """Fetch all assets from STON.fi with 5-min cache."""
    import time as _t
    now = _t.time()
    if _assets_cache["data"] and now - _assets_cache["ts"] < _CACHE_TTL:
        return _assets_cache["data"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.ston.fi/v1/assets") as resp:
                if resp.status != 200:
                    return _assets_cache["data"]
                data = await resp.json()
        _assets_cache["data"] = data.get("asset_list", [])
        _assets_cache["ts"] = now
    except Exception:
        pass
    return _assets_cache["data"]


def _format_price(price) -> str:
    if not price:
        return ""
    try:
        p = float(price)
        if p == 0:
            return ""
        return f"${p:.8f}" if p < 0.01 else f"${p:.4f}"
    except (ValueError, TypeError):
        return ""


async def get_trending_tokens(limit: int = 10) -> tuple[str, bool]:
    """Get trending/popular tokens on TON via STON.fi."""
    assets = await _fetch_all_assets()
    if not assets:
        return "Failed to fetch tokens", False

    defaults = [a for a in assets if a.get("default_symbol")]
    defaults.sort(key=lambda a: a.get("popularity_index", 0), reverse=True)

    if not defaults:
        return "No trending tokens found", True

    lines = ["━━━ 🔥 Trending Tokens on TON ━━━\n"]
    for i, a in enumerate(defaults[:limit], 1):
        sym = a.get("symbol", "?")
        name = a.get("display_name", "") or sym
        addr = a.get("contract_address", "?")
        price_str = _format_price(a.get("dex_price_usd") or a.get("third_party_price_usd"))
        bl = a.get("blacklisted", False)
        tax = a.get("taxable", False)

        flags = ""
        if bl:
            flags += " 🚫"
        if tax:
            flags += " 💰"

        line = f"{i}. {name} ({sym}){flags}"
        if price_str:
            line += f" — {price_str}"
        short_addr = addr[:20] + "..." if len(addr) > 20 else addr
        line += f"\n   {short_addr}"
        lines.append(line)

    return "\n".join(lines), True


async def search_token(query: str) -> tuple[str, bool]:
    """Search for tokens by name or symbol on STON.fi (case-insensitive)."""
    if not query or len(query.strip()) < 1:
        return "Search query too short", False
    q = query.strip().lower()
    assets = await _fetch_all_assets()
    if not assets:
        return "Failed to fetch tokens", False

    matches = []
    for a in assets:
        sym = (a.get("symbol") or "").lower()
        name = (a.get("display_name") or "").lower()
        if q == sym:
            matches.insert(0, a)
        elif q in sym or q in name:
            matches.append(a)

    has_price = [a for a in matches if a.get("dex_price_usd") or a.get("third_party_price_usd")]
    no_price = [a for a in matches if not (a.get("dex_price_usd") or a.get("third_party_price_usd"))]
    matches = has_price + no_price

    if not matches:
        return f"No tokens found for '{query}'", True

    lines = [f"━━━ 🔎 Search: {query} ━━━\n"]
    for i, a in enumerate(matches[:10], 1):
        sym = a.get("symbol", "?")
        name = a.get("display_name", "") or sym
        addr = a.get("contract_address", "?")
        price_str = _format_price(a.get("dex_price_usd") or a.get("third_party_price_usd"))

        line = f"{i}. {name} ({sym})"
        if price_str:
            line += f" — {price_str}"
        line += f"\n   {addr}"
        lines.append(line)

    lines.append(f"\n📊 {len(matches)} tokens found. Send a contract address to scan in detail.")
    return "\n".join(lines), True


def _raw_to_eq(raw: str) -> str:
    """Convert raw address (0:hex or -1:hex) to EQ format."""
    if raw.startswith("EQ") or raw.startswith("UQ"):
        return raw
    parts = raw.split(":")
    if len(parts) != 2:
        return raw
    wc = int(parts[0])
    h = bytes.fromhex(parts[1])
    if len(h) != 32:
        return raw
    # Bounceable tag 0x11, workchain as byte. Base64 result is full address (no extra EQ prefix).
    wc_byte = 0xFF if wc == -1 else (wc & 0xFF)
    payload = bytes([0x11, wc_byte]) + h
    import base64
    b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return b64


async def resolve_dns(domain: str) -> tuple[str, bool]:
    """Resolve TON DNS domain to address. Uses TonAPI."""
    domain = domain.strip().lower()
    if not domain or " " in domain:
        return "Invalid domain", False
    if not domain.endswith(".ton"):
        domain = f"{domain}.ton"
    url = f"{TONAPI_BASE.rstrip('/')}/dns/{domain}/resolve"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return "Domain not found or failed to resolve", False
                data = await resp.json()
    except Exception as e:
        return f"Error: {e}", False
    wallet = data.get("wallet", {})
    addr = wallet.get("address") or data.get("address")
    if addr:
        if not (addr.startswith("EQ") or addr.startswith("UQ")):
            addr = _raw_to_eq(addr)
        return addr, True
    return "No address found for domain", False


async def get_transactions_raw(address: str, limit: int = 5) -> tuple[list[dict], bool]:
    """Get raw transaction list for AI processing."""
    if not TON_ADDR_RE.fullmatch(address):
        return [], False
    data = await _get(_toncenter_url("getTransactions", {"address": address, "limit": str(limit)}))
    if not data.get("ok"):
        return [], False
    return data.get("result", []), True


# Native TON address for STON.fi
_TON_NATIVE = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"

KNOWN_TOKENS = {
    "TON": _TON_NATIVE,
    "USDT": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
    "STON": "EQBlqsm144Dq6SjbPI4jjZvA1hqTIP3CvHovbIfW_t-SCALE",
    "SCALE": "EQBlqsm144Dq6SjbPI4jjZvA1hqTIP3CvHovbIfW_t-SCALE",
    "NOT": "EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT",
    "DOGS": "EQCvxJy4eG8hyHBFsZ7DUdtNk3Jl27RkXcblbdpWcWoVMECL",
}

TOKEN_DECIMALS = {
    _TON_NATIVE: 9,
    "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs": 6,  # USDT
    "EQBlqsm144Dq6SjbPI4jjZvA1hqTIP3CvHovbIfW_t-SCALE": 9,   # STON
    "EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT": 9,   # NOT
}


def _resolve_token(name_or_addr: str) -> str:
    """Resolve token symbol or address to contract address."""
    up = name_or_addr.strip().upper()
    if up in KNOWN_TOKENS:
        return KNOWN_TOKENS[up]
    if name_or_addr.startswith("EQ") or name_or_addr.startswith("UQ"):
        return name_or_addr
    return name_or_addr


def _token_symbol(addr: str) -> str:
    """Reverse-lookup symbol from address."""
    for sym, a in KNOWN_TOKENS.items():
        if a == addr:
            return sym
    return addr[:12] + "..."


async def get_swap_quote(from_token: str, to_token: str, amount: str) -> tuple[str, bool]:
    """Get swap quote from STON.fi. Returns (formatted text, success)."""
    offer_addr = _resolve_token(from_token)
    ask_addr = _resolve_token(to_token)

    from_decimals = TOKEN_DECIMALS.get(offer_addr, 9)
    try:
        human_amount = float(amount)
        units = str(int(human_amount * (10 ** from_decimals)))
    except (ValueError, TypeError):
        return "Invalid amount", False

    url = (
        f"https://api.ston.fi/v1/swap/simulate"
        f"?offer_address={offer_addr}"
        f"&ask_address={ask_addr}"
        f"&units={units}"
        f"&slippage_tolerance=0.01"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return f"Swap quote failed (HTTP {resp.status}): {body[:100]}", False
                data = await resp.json()
    except Exception as e:
        return f"Swap quote error: {e}", False

    ask_units = data.get("ask_units", "0")
    to_decimals = TOKEN_DECIMALS.get(ask_addr, 9)
    out_amount = int(ask_units) / (10 ** to_decimals)
    swap_rate = data.get("swap_rate", "?")
    price_impact = data.get("price_impact", "0")
    fee_pct = data.get("fee_percent", "0")

    from_sym = _token_symbol(offer_addr)
    to_sym = _token_symbol(ask_addr)

    result = (
        f"{human_amount} {from_sym} → {out_amount:,.4f} {to_sym}\n"
        f"Rate: 1 {from_sym} = {swap_rate} {to_sym}\n"
        f"Price impact: {float(price_impact)*100:.4f}%\n"
        f"Fee: {float(fee_pct)*100:.2f}%\n"
        f"(via STON.fi)"
    )
    return result, True


# ---------------------------------------------------------------------------
# NFT Inline Search — tonapi.io (free, no auth)
# ---------------------------------------------------------------------------

KNOWN_NFT_COLLECTIONS = {
    "usernames": "EQCA14o1-VWhS2efqoh_9M1b_A9DtKTuoqfmkn83AbJzwnPi",
    "numbers": "EQAOQdwdw8kGftJCSFgOErM1mBjYPe4DBPq8-AhF6vr9si5N",
    "dns": "EQC3dNlesgVD8YbAazcauIrXBPfiVhMMr5YYk2in0Mtsz0Bz",
}


async def nft_fetch_item(address: str) -> Optional[dict]:
    """Fetch a single NFT item by raw (0:hex) or friendly address."""
    url = f"{TONAPI_BASE.rstrip('/')}/nfts/{address}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    return await r.json()
    except Exception:
        pass
    return None


async def nft_fetch_collection_items(collection_addr: str, limit: int = 15) -> list[dict]:
    """Fetch NFT items from a collection."""
    url = f"{TONAPI_BASE.rstrip('/')}/nfts/collections/{collection_addr}/items?limit={limit}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("nft_items", [])
    except Exception:
        pass
    return []


async def nft_resolve_dns(domain: str) -> Optional[dict]:
    """Resolve a .ton domain via tonapi DNS and return the NFT item dict."""
    url = f"{TONAPI_BASE.rstrip('/')}/dns/{domain}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    item = data.get("item")
                    if item and item.get("address"):
                        return item
    except Exception:
        pass
    return None


async def nft_fetch_auctions(limit: int = 20) -> list[dict]:
    """Fetch active DNS auctions from tonapi."""
    url = f"{TONAPI_BASE.rstrip('/')}/dns/auctions"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("data", [])[:limit]
    except Exception:
        pass
    return []


def nft_best_preview(previews: list[dict], target: str = "500x500") -> Optional[str]:
    """Pick the best preview URL from tonapi previews list."""
    by_res = {p.get("resolution"): p.get("url") for p in previews if p.get("url")}
    for res in [target, "1500x1500", "500x500", "100x100", "5x5"]:
        if res in by_res:
            return by_res[res]
    return previews[0].get("url") if previews else None


def nft_price_display(sale: dict) -> str:
    """Format sale price for display."""
    price_info = sale.get("price", {})
    value = int(price_info.get("value", 0))
    decimals = int(price_info.get("decimals", 9))
    token = price_info.get("token_name", "TON")
    amount = value / (10 ** decimals)
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M {token}"
    if amount >= 1_000:
        return f"{amount / 1_000:.1f}K {token}"
    if amount == int(amount):
        return f"{int(amount)} {token}"
    return f"{amount:.2f} {token}"


async def nft_inline_search(query: str) -> list[dict]:
    """
    Search for NFTs given a query string. Returns list of raw tonapi NFT dicts.
    Strategy:
      - empty → featured from Telegram Usernames collection
      - starts with 0: or EQ/UQ → direct address lookup
      - ends with .ton / .t.me → DNS resolve
      - plain text → try as .t.me, then .ton, then filter collection items
    """
    import asyncio as _aio
    query = query.strip()
    results: list[dict] = []

    if not query:
        items = await nft_fetch_collection_items(KNOWN_NFT_COLLECTIONS["usernames"], 20)
        on_sale = [it for it in items if it.get("sale")]
        not_sale = [it for it in items if not it.get("sale")]
        return (on_sale[:8] + not_sale[:4])[:12]

    if query.startswith("0:") or query.startswith("EQ") or query.startswith("UQ"):
        nft = await nft_fetch_item(query)
        if nft:
            results.append(nft)
        return results

    if query.endswith(".ton") or query.endswith(".t.me"):
        item = await nft_resolve_dns(query)
        if item:
            addr = item.get("address", "")
            full = await nft_fetch_item(addr) if addr else None
            results.append(full or item)
        return results

    tme, ton = await _aio.gather(
        nft_resolve_dns(f"{query}.t.me"),
        nft_resolve_dns(f"{query}.ton"),
    )
    if tme:
        addr = tme.get("address", "")
        full = await nft_fetch_item(addr) if addr else None
        results.append(full or tme)
    if ton:
        addr = ton.get("address", "")
        full = await nft_fetch_item(addr) if addr else None
        results.append(full or ton)

    if not results:
        items = await nft_fetch_collection_items(KNOWN_NFT_COLLECTIONS["usernames"], 50)
        q_lower = query.lower()
        for it in items:
            name = (it.get("metadata", {}).get("name", "") or "").lower()
            if q_lower in name:
                results.append(it)
                if len(results) >= 10:
                    break

    auctions = await nft_fetch_auctions(50)
    q_lower = query.lower()
    matching = [a for a in auctions if q_lower in a.get("domain", "").lower()]
    for a in matching[:max(0, 12 - len(results))]:
        results.append({"_auction": True, **a})

    return results[:20]
