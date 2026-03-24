"""
TON TE AI — DeepSeek function calling for TON operations.

Parses user intent, calls tools (ton_tools + mcp_client), returns natural language.
"""
import asyncio
import json
import logging
import re
from typing import Any, Optional

from openai import OpenAI, AsyncOpenAI

from config import DEEPSEEK_API_KEY
from ton_tools import (
    TON_ADDR_RE,
    get_balance,
    get_transactions,
    get_address_info,
    get_jettons,
    get_jetton_info,
    get_nft_item_info,
    get_nfts,
    resolve_dns,
    get_swap_quote,
    get_trending_tokens,
    search_token,
)
from mcp_client import (
    mcp_get_swap_quote,
    mcp_send_ton,
    mcp_send_jetton,
    is_mcp_available,
)

log = logging.getLogger("tonpal.ai")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
async_client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

LANG_NAMES = {"zh": "簡體中文", "yue": "廣東話", "ru": "俄語", "ja": "日本語", "ko": "한국어", "th": "泰文"}

# UI language → English description for translation-only mode
TARGET_LANG_FOR_TRANSLATE = {
    "en": "English",
    "zh": "Simplified Chinese",
    "yue": "Cantonese (Traditional Chinese, Hong Kong spoken style)",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "th": "Thai",
}


async def translate_to_ui_language(text: str, user_data: dict) -> str:
    """Translate arbitrary text into the user's current UI language (no tools, for translate mode)."""
    if not DEEPSEEK_API_KEY:
        return "DeepSeek API key not configured."
    lang = user_data.get("lang", "en")
    target = TARGET_LANG_FOR_TRANSLATE.get(lang, "English")
    prompt = (
        f"Translate the following into {target}. "
        "Output ONLY the translation. No quotes, no explanation, no preamble."
    )
    try:
        resp = await async_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text[:4000]},
            ],
            temperature=0,
            max_tokens=2000,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or text
    except Exception as e:
        log.exception("translate_to_ui_language: %s", e)
        return f"Translation error: {e}"


async def _translate_to_lang(text: str, lang: str) -> str:
    """Translate token/NFT/wallet info to user's language — concise."""
    if lang not in LANG_NAMES or lang == "en":
        return text
    try:
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": (
                    "Translate concisely to the target language. "
                    "Keep numbers, addresses (EQ/UQ), URLs unchanged. "
                    "Remove filler, be brief. Output only the translation."
                )},
                {"role": "user", "content": f"Translate to {LANG_NAMES[lang]}:\n\n{text[:2000]}"},
            ],
            temperature=0,
            max_tokens=600,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out if out else text
    except Exception as e:
        log.warning("translate failed: %s", e)
        return text

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "Get TON balance for any address. Use when user asks for balance, how much TON, etc.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "TON address (EQ or UQ)"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transactions",
            "description": "Get recent transactions for an address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "string", "description": "TON address"},
                    "limit": {"type": "integer", "description": "Number of txs (default 5)", "default": 5},
                },
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_address_info",
            "description": "Get address info: balance and state (active/uninitialized/frozen).",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "TON address"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_jettons",
            "description": "Get jetton (token) balances for an address. USDT, STON, etc.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "TON address"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nfts",
            "description": "Get NFT items held by an address.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "TON address"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_jetton_info",
            "description": "Get info about a jetton (token) by its contract address: name, symbol, supply, holders, etc. Use when user sends an address that might be a token contract, or asks about a specific token.",
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "Jetton master contract address"}},
                "required": ["address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_dns",
            "description": "Resolve TON DNS domain (e.g. alice.ton) to wallet address.",
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string", "description": "Domain like alice.ton or foundation.ton"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_swap_quote",
            "description": "Get swap quote via STON.fi DEX. Supports: TON, USDT, STON, NOT, DOGS, or any jetton address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_token": {"type": "string", "description": "Token symbol (TON, USDT, STON, NOT, DOGS) or jetton address"},
                    "to_token": {"type": "string", "description": "Token symbol or jetton address"},
                    "amount": {"type": "string", "description": "Amount in human-readable form, e.g. 10 or 1.5"},
                },
                "required": ["from_token", "to_token", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trending_tokens",
            "description": "Get trending/popular tokens on TON. Shows top tokens by popularity with prices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of tokens to show (default 10, max 20)", "default": 10},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_token",
            "description": "Search for tokens by name or symbol. Use when user asks 'find token X' or 'search for X'. Returns matching tokens with prices and addresses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Token name or symbol to search for, e.g. 'USDT', 'NOT', 'dogs'"},
                },
                "required": ["query"],
            },
        },
    },
]

if is_mcp_available():
    TOOLS.extend([
        {
            "type": "function",
            "function": {
                "name": "send_ton",
                "description": "Send TON to an address. Requires user confirmation. Use only when user explicitly wants to send.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to_address": {"type": "string", "description": "Recipient TON address"},
                        "amount": {"type": "string", "description": "Amount in TON e.g. 1.5"},
                        "comment": {"type": "string", "description": "Optional comment"},
                    },
                    "required": ["to_address", "amount"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "send_jetton",
                "description": "Send jetton (e.g. USDT) to an address. Requires user confirmation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to_address": {"type": "string", "description": "Recipient address"},
                        "jetton_address": {"type": "string", "description": "Jetton master contract address"},
                        "amount": {"type": "string", "description": "Amount in human-readable form"},
                    },
                    "required": ["to_address", "jetton_address", "amount"],
                },
            },
        },
    ])

SYSTEM_PROMPT = """You are TON TE AI — a fast TON blockchain assistant in Telegram. Built by TE (TechEscrow). Be concise. No filler. Answer TON questions from knowledge below — NO tool call needed for general questions.

== TON CORE ==
- The Open Network (TON): L1 blockchain by Nikolai Durov (Telegram co-founder), started 2018. Originally "Telegram Open Network", raised $1.7B for "Gram" token. SEC sued 2019, Telegram settled 2020 ($18.5M penalty + $1.2B returned). Community forked and continued as TON Foundation. Telegram re-partnered Sep 2023. TON = exclusive blockchain for all Telegram Mini Apps since Jan 2025.
- Architecture: Masterchain (single source of truth, stores config/validator stakes) + Workchains (currently only BaseChain/workchain 0, supports up to 2^32) + Shardchains (parallel tx processing). Dynamic sharding: auto-split under load, auto-merge when idle.
- TVM (TON Virtual Machine): Stack-based VM. Data types: integers, cells, slices, builders, tuples, continuations, null. Feb 2025 upgrade: new crypto opcodes, higher gas limits.
- Consensus: BFT Proof-of-Stake. ~339 active validators. Block time ~3-5s.
- Performance: Record 104,715 TPS (peak 111,498) Oct 2023, audited by CertiK. 512 shards. Theoretical: millions TPS.

== TOKENOMICS ==
- Total supply: ~5.1B TON. Circulating: ~2.5B (~49%). ~2.6B locked in vesting, ~37M unlock monthly.
- Inflation: 2% annual gross (validator rewards). Net ~0.55% after burns. 50% of all tx fees burned.
- Staking: ~25% circulating staked. Validator APY 3-6%. Min 300K TON to run validator.
- Funding: $944M total raised. $400M round Mar 2025 (Sequoia, Ribbit Capital, Benchmark).

== WALLETS ==
- Tonkeeper: Most popular (5-10M+ users). Non-custodial, open-source. iOS/Android/browser. Built-in swaps, staking, NFTs, TON Connect.
- MyTonWallet: Power-user. Non-custodial. Multi-account, Ledger support, multi-chain (TON+TRON), desktop/mobile/web/extension.
- Telegram Wallet / TON Space: Custodial (Telegram holds keys). No seed phrase. Direct bank card purchases. Stars integration.
- OpenMask: Browser extension. Non-custodial. Lightweight dApp interaction.
- Tonhub: Non-custodial mobile. Staking pool integration + TON Connect.
- Addresses: EQ = bounceable, UQ = non-bounceable. Base64url encoded.

== DEFI ==
- TVL: ~$61M. Stablecoin market cap on TON: ~$966M (82% USDT).
- STON.fi: Dominant DEX (~70% volume). $6.5B+ lifetime volume, 30M+ txs, 30K+ pairs. AMM model.
- DeDust: #2 DEX. Gas-efficient AMM. ~$34M TVL. Native token: SCALE.
- EVAA Finance: First lending protocol. $118M+ peak TVL, 300K+ wallets. Lend/borrow TON, USDT, tsTON, DOGS, NOT.
- Liquid staking: tsTON (Tonstakers) — reward-bearing LST. stTON (bemo) — same model. hTON (Hipo) — open-source + HPO governance token. LSTs yield 0.5-1.5% higher than direct staking.
- Yield farming: LP on STON.fi/DeDust (80% fees to LPs). EVAA looping. SCALE staking (~30% APY). STON staking for GEMSTON.

== MAJOR JETTONS ==
- USDT: Tether stablecoin. 1:1 USD. Dominates TON (82% of stablecoins).
- NOT (Notcoin): Viral tap-to-earn Mini App game. Massive airdrop to millions.
- DOGS: Community token. Telegram sticker culture. Airdrop based on Telegram account age.
- STON: STON.fi governance. Stake → earn GEMSTON.
- SCALE: DeDust ecosystem. Max 21M supply, ~16.7M circulating. 20% DeDust fees to SCALE stakers.
- EVAA: Lending protocol governance. 22% airdrop/LP allocation.
- STORM: Storm Trade derivatives platform token.
- USDe: Ethena synthetic dollar. Up to 18% APY staked as tsUSDe. Launched May 2025.
- XAUt: Tether tokenized gold. 1:1 physical gold backed. LayerZero OFT standard.
- BOLT, GRAM, FISH, KINGY, CATS: Community/meme tokens.

== NFTs ==
- Standard: TEP-62 (NFT), TEP-66 (royalties), TEP-85 (SBT/soulbound).
- Getgems: Main marketplace. Mint/buy/sell, near-zero gas. 5% commission. Supports compressed NFTs.
- TON Diamonds: Premium collection. Notable: Plush Pepes, Durov's Caps, Ion Gems.
- Telegram Gifts as NFTs: Export to TON blockchain via Fragment/wallet. $312M+ total volume, 541K+ wallets.

== TON DNS ==
- .ton domains = NFTs. Min 4 chars, max 126. Letters, digits, hyphens.
- Price: ~$1/char/year. Buy at: dns.ton.org, Getgems, Tonkeeper.
- Renewal: Annual. 90-day grace period. Supported by Tonkeeper, MyTonWallet, Tonscan.

== BRIDGES ==
- Orbit Bridge: Decentralized. Multi-sig BFT. Bridges ETH/BSC/MATIC assets to TON. Bridged assets get 'o' prefix (oUSDT, oETH).
- LayerZero: Cross-chain messaging (2025). USDT transfers via OFT standard.

== INFRASTRUCTURE ==
- TON Proxy: Decentralized proxy via ADNL protocol. Mandatory encryption.
- TON Sites: Decentralized websites using ADNL+RLDP instead of IP/TCP. Access via .ton domains.
- TON Storage: Decentralized file storage (torrent-like). Bag of files with Merkle verification. Storage providers earn TON.
- TON Connect: Standard wallet↔dApp protocol. 30+ wallets. Mandatory for Telegram Mini Apps.

== DEVELOPMENT ==
- Tolk: Current official language (2025+). Replaced FunC.
- FunC: Legacy. Compiler no longer maintained.
- Tact: Modern, TypeScript-like. ~33% of mainnet contracts. Audited by Trail of Bits.
- Blueprint: Primary dev env. npm create ton@latest. Build→test→deploy. TypeScript wrappers.
- Actor model: Each contract = independent account. Async message communication. State in cells.

== TELEGRAM INTEGRATION ==
- Mini Apps: HTML5+JS inside Telegram. 500M+ MAU. Zero install. TON exclusive since Jan 2025.
- Fragment: TON marketplace for usernames, anonymous numbers, gifts. Fund Telegram Ads (min 20 TON). 5% fee.
- Stars: In-app currency. Creators accept Stars. Convert to Toncoin. Use for Ads at discount.
- Gifts: Digital collectibles. Exportable as NFTs. $312M+ trading volume.
- Ads: Via Fragment. Funded with Toncoin. Creators earn 50% ad revenue.

== STAKING ==
- Validator: Min 300K TON. ~339 validators. Run node + mytonctrl.
- Nominator pools: Aggregate funds. Delegate without running node. In Tonkeeper "Earn" tab.
- Liquid staking: tsTON, stTON, hTON. No mandatory lock-up. Use in DeFi.
- APY: 3-6%. Liquid staking +0.5-1.5% from DeFi composability.
- Unstaking: 1-3 day wait. Min stake: ~1 TON for pools (+~1.2 TON fees).

== GOVERNANCE ==
- TON Foundation: Non-profit overseeing ecosystem.
- Society DAO (Nov 2024): 4 founders — TON Core, TON Society, Wallet in Telegram, TON Studio.
- TON Society: Independent org (Aug 2024). Grants, community coordination.

== LAUNCHPADS ==
- Tonstarter: First/largest. 150+ VC network, 20K+ investors. IDO bot.
- TON Raffles: Jetton & NFT launchpads, token swap, staking.
- Others: PizzaTon, TAND3M, Parachute, TonPump, Purr.fund, MULTIX.

== ANALYTICS ==
- Tonviewer (tonviewer.com): #1 explorer. Full analytics, contract code, tx traces.
- Tonscan (tonscan.org): Fast general explorer. Mainnet+testnet.
- ton.app: Ecosystem directory. Lists all dApps/wallets/explorers.
- DefiLlama: TON DeFi TVL tracking (~$61M), protocol rankings.

== 2025-2026 ==
- Cocoon (Nov 2025): Durov's decentralized AI compute on TON. TEE encrypted processing. GPU owners earn TON. Powers Telegram AI summaries. 125K+ jobs/month.
- TON Pay SDK (Feb 2026): Unified payments for TON apps. Checkout+settlement+reporting. Near-instant on-chain.
- x402 Protocol: HTTP 402 micropayments. AI agents pay for API/compute autonomously. 75M+ monthly txs.
- $400M funding (Mar 2025): Sequoia, Ribbit, Benchmark.
- TVM upgrade (Feb 2025): New opcodes, higher gas limits.
- STON.fi/DeDust on TradingView. $100M DeFi fund by TVM Ventures.

== RULES ==
1. EQ/UQ address → first call get_jetton_info. If NOT_A_JETTON → call get_balance.
2. General TON questions → answer from knowledge. NO tool call.
3. send_ton/send_jetton → NEVER first request. Ask confirmation first.
4. Swap: symbols TON, USDT, STON, NOT, DOGS.
5. LANGUAGE: If the user message includes instructions such as "(reply in English only)" or "(The UI language is English" or "(UI language is English", you MUST follow them (e.g. user spoke Cantonese but UI is English — reply in English). Otherwise reply in the same language the user writes in. The UI hint overrides when present.
6. SHORT answers. Present tool results as-is.
7. Trending → get_trending_tokens. Search → search_token.
8. PRICE queries: "TON幾錢/TON price/TON стоимость" → call get_swap_quote(from_token="TON", to_token="USDT", amount="1"). Show "1 TON ≈ $X.XX".
9. If input seems garbled from voice recognition, do your BEST to interpret the intent. E.g. "查下TON而家幾錢" = TON price. NEVER say "I can't understand" — try the most likely interpretation.
10. FORMATTING: NEVER use ** or * for bold/italic. Use plain text only. No markdown formatting. Responses will be read aloud by TTS — asterisks sound terrible when spoken.
"""


async def _run_tool(name: str, args: dict) -> str:
    """Execute tool and return result string."""
    name = name.replace("-", "_")
    try:
        if name == "get_balance":
            out, ok = await get_balance(args["address"])
            return out if ok else f"Error: {out}"
        if name == "get_transactions":
            limit = args.get("limit", 5)
            out, ok = await get_transactions(args["address"], limit=int(limit))
            return out if ok else f"Error: {out}"
        if name == "get_address_info":
            out, ok = await get_address_info(args["address"])
            return out if ok else f"Error: {out}"
        if name == "get_jettons":
            out, ok = await get_jettons(args["address"])
            return out if ok else f"Error: {out}"
        if name == "get_nfts":
            out, ok = await get_nfts(args["address"])
            return out if ok else f"Error: {out}"
        if name == "get_jetton_info":
            out, ok = await get_jetton_info(args["address"])
            if not ok and out == "NOT_A_JETTON":
                return "This address is not a jetton contract. It might be a wallet or another type of contract."
            return out if ok else f"Error: {out}"
        if name == "resolve_dns":
            out, ok = await resolve_dns(args["domain"])
            return out if ok else f"Error: {out}"
        if name == "get_swap_quote":
            ft = args.get("from_token", "TON")
            tt = args.get("to_token", "TON")
            amt = str(args.get("amount", "1"))
            out, ok = await get_swap_quote(ft, tt, amt)
            return out if ok else f"Error: {out}"
        if name == "get_trending_tokens":
            limit = args.get("limit", 10)
            out, ok = await get_trending_tokens(limit=int(limit))
            return out if ok else f"Error: {out}"
        if name == "search_token":
            q = args.get("query", "")
            out, ok = await search_token(q)
            return out if ok else f"Error: {out}"
        if name == "send_ton":
            out, ok = await mcp_send_ton(
                args["to_address"],
                str(args["amount"]),
                args.get("comment", ""),
            )
            return out if ok else f"Error: {out}"
        if name == "send_jetton":
            out, ok = await mcp_send_jetton(
                args["to_address"],
                args["jetton_address"],
                str(args["amount"]),
                args.get("comment", ""),
            )
            return out if ok else f"Error: {out}"
    except Exception as e:
        log.exception("Tool %s failed: %s", name, e)
        return f"Error: {e}"
    return f"Unknown tool: {name}"


def _extract_address_from_context(text: str, user_data: dict) -> Optional[str]:
    """Extract TON address from message or user's remembered address."""
    m = TON_ADDR_RE.search(text)
    if m:
        return m.group(0)
    return user_data.get("last_address")


def _trim_history(history: list[dict], max_messages: int = 10) -> list[dict]:
    if len(history) <= max_messages:
        return history
    return history[-max_messages:]


async def _auto_scan_address(text: str) -> tuple:
    """If message is mostly a TON address, auto-scan it.
    Returns (text_result, image_url_or_None) or (None, None).
    """
    m = TON_ADDR_RE.search(text)
    if not m:
        return None, None
    addr = m.group(0)
    non_addr = text.replace(addr, "", 1).strip()
    non_addr = re.sub(r"https?://\S+", "", non_addr)
    non_addr = re.sub(r"\(reply in \w+\)|\(UI preference:[^)]+\)", "", non_addr).strip()
    if len(non_addr) > 200:
        return None, None

    out, ok = await get_jetton_info(addr)
    if ok:
        return out, None

    if out == "NOT_A_JETTON":
        nft_out, is_nft, nft_image = await get_nft_item_info(addr)
        if is_nft:
            return nft_out, nft_image

        info_out, info_ok = await get_address_info(addr)
        bal_out, bal_ok = await get_balance(addr)
        jettons_out, jettons_ok = await get_jettons(addr)

        parts = [f"Wallet: {addr}"]
        if info_ok:
            parts.append(info_out)
        if bal_ok:
            parts.append(f"\n💎 TON Balance: {bal_out}")
        if jettons_ok and jettons_out != "No jettons found":
            parts.append(f"\n🪙 Jettons:\n{jettons_out}")
        if len(parts) > 1:
            return "\n".join(parts), None
    return None, None


async def stream_process_message(user_text: str, user_data: dict):
    """Async generator: yields ("text", partial), ("done", final), or ("image", url, text)."""
    if not DEEPSEEK_API_KEY:
        yield ("done", "DeepSeek API key not configured.")
        return

    scan_result, scan_image = await _auto_scan_address(user_text)
    if scan_result:
        addr_m = TON_ADDR_RE.search(user_text)
        if addr_m:
            user_data["last_address"] = addr_m.group(0)
        lang = user_data.get("lang", "en")

        if scan_image:
            if lang != "en" and lang in LANG_NAMES:
                scan_result = await _translate_to_lang(scan_result, lang)
            _save_history(user_data, user_text, scan_result)
            yield ("image", scan_image, scan_result)
            return

        if lang == "en" or lang not in LANG_NAMES:
            _save_history(user_data, user_text, scan_result)
            yield ("done", scan_result)
            return

        accumulated = ""
        try:
            stream = await async_client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": (
                        "Translate concisely. Keep numbers, addresses (EQ/UQ), URLs unchanged. "
                        "Be brief. Output only the translation."
                    )},
                    {"role": "user", "content": f"Translate to {LANG_NAMES[lang]}:\n\n{scan_result[:2000]}"},
                ],
                temperature=0,
                max_tokens=600,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    accumulated += chunk.choices[0].delta.content
                    yield ("text", accumulated)
        except Exception as e:
            log.warning("stream translate failed: %s", e)
            accumulated = scan_result

        final = accumulated.strip() or scan_result
        _save_history(user_data, user_text, final)
        yield ("done", final)
        return

    history = user_data.get("chat_history", [])
    history.append({"role": "user", "content": user_text})
    history = _trim_history(history)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    for _ in range(3):
        try:
            resp = await asyncio.to_thread(
                client.chat.completions.create,
                model="deepseek-chat",
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
                max_tokens=800,
            )
        except Exception as e:
            log.exception("DeepSeek error: %s", e)
            yield ("done", f"Sorry: {e}")
            return

        msg = resp.choices[0].message
        if not msg.tool_calls:
            reply = (msg.content or "").strip() or "Send me a TON address or ask anything about TON."
            user_data["chat_history"] = _trim_history(
                history + [{"role": "assistant", "content": reply}]
            )
            yield ("done", reply)
            return

        messages.append(msg)
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = await _run_tool(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            addr_val = args.get("address")
            if addr_val and TON_ADDR_RE.fullmatch(str(addr_val)):
                user_data["last_address"] = addr_val

    accumulated = ""
    try:
        stream = await async_client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0,
            max_tokens=800,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                accumulated += chunk.choices[0].delta.content
                yield ("text", accumulated)
    except Exception as e:
        yield ("done", f"Error: {e}")
        return

    final = accumulated.strip() or "Send me a TON address or ask anything about TON."
    user_data["chat_history"] = _trim_history(
        history + [{"role": "assistant", "content": final}]
    )
    yield ("done", final)


def _save_history(user_data: dict, user_text: str, reply: str):
    history = user_data.get("chat_history", [])
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    user_data["chat_history"] = _trim_history(history)
