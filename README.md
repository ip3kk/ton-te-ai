# TON TE AI — Multilingual Voice-Enabled TON Assistant

A Telegram bot that makes the **TON blockchain accessible to everyone** — any language, any ability. Type, tap buttons, or **speak** in 7+ languages; the bot answers in text **and voice**. Share **on-chain red packets** in any group, search **NFTs inline**, scan tokens, check balances, swap, and more — all inside one Telegram chat.

Built by **TE (TechEscrow)** for the [Identity Hub AI Hackathon](https://identityhub.app/contests/ai-hackathon).

**Try it:** [@TON_TE_Ai_bot](https://t.me/TON_TE_Ai_bot)

---

## Why This Exists

Over **150 million** people across China's top provinces and Hong Kong alone — fewer than **500,000** have ever heard of TON. Across Japan, Korea, Thailand, the Middle East, most TON tools only speak English or Russian. Billions are left out by language, literacy, or disability.

**TON TE AI** bridges that gap: one Telegram bot, every language, voice-first.

---

## Features

### Conversational AI
- **Natural language** queries — "What's the balance of EQ...?", "How much USDT for 10 TON?"
- **DeepSeek** function-calling brain picks the right on-chain tool automatically
- Context-aware follow-ups within a conversation

### Voice (STT + TTS)
- **Voice input**: hold mic, speak in any supported language → Groq Whisper / faster-whisper transcribes → AI responds
- **Voice output**: every reply can be spoken back via **cantonese.ai** (Cantonese / Mandarin / English voices) or **Edge TTS** fallback
- Accessibility for visually impaired or low-literacy users

### 7+ Language UI
Full button menus, prompts, and voice in: **English, Simplified Chinese, Cantonese, Russian, Japanese, Korean, Thai**. Switch anytime via the Language button.

### On-Chain Red Packets
- Admin creates in DM or **any group via inline** (`@bot 1 5` = 1 TON × 5 shares)
- Photo card with **Open** button → claims in bot DM → real TON sent via **@ton/mcp**
- Lucky (random split) or fixed mode; TON or USDT

### Inline NFT Search
- Type `@bot durov` / `@bot foundation.ton` / `@bot 0:abc...` in any chat
- Returns NFT cards with preview images, prices, **Getgems** and **TONViewer** links
- DNS auctions link to **Fragment**
- Empty query shows featured Telegram Usernames collection

### TON Blockchain Tools
| Feature | How |
|---------|-----|
| Token scan (price, LP, holders, risk) | Button or paste address |
| Trending tokens | `/trending` or button |
| Balance & transactions | Paste any TON address |
| Jetton balances | Button |
| NFT list | Button |
| DNS resolve | `foundation.ton` etc. |
| Swap quotes | "10 TON to USDT?" |
| Send TON / jettons | Via MCP (admin) |

### Translate Mode
Dedicated mode: send voice or text in any language → translated into your chosen UI language. No TON queries — pure translation.

### Admin Dashboard
`/admin` (owner-only, no reply to others): your ID, total bot users, recent 20 visitors (ID, username, display name, touch count, last seen), red packet stats, hot wallet balance.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Runtime | Python 3.11+, python-telegram-bot |
| AI Brain | DeepSeek V3 (function calling, tool orchestration) |
| Voice → Text | Groq Whisper (primary), faster-whisper (fallback), Google STT (fallback) |
| Text → Voice | cantonese.ai API (Cantonese/Mandarin/English voices), Microsoft Edge TTS (fallback) |
| Chain Data | TonAPI (`tonapi.io/v2`), TonCenter API v2 |
| On-Chain Actions | @ton/mcp (Streamable HTTP) — send TON, send jettons, swap |
| Database | SQLite (red packets, claims, saved addresses, bot users) |

---

## Setup

### 1. Install

```bash
cd tonpal
pip install -r requirements.txt
```

### 2. Environment

Create `.env`:

```env
TELEGRAM_BOT_TOKEN=your_token
DEEPSEEK_API_KEY=your_key
```

Optional (recommended):

```env
GROQ_API_KEY=your_groq_key
CANTONESE_TTS_API_KEY=your_cantonese_ai_key
TONCENTER_API_KEY=your_key
MCP_SERVER_URL=http://localhost:3000
REDPACKET_OWNER_IDS=5396577745
```

### 3. Run

```bash
python bot.py
```

### 4. (Optional) MCP for send/swap/red packets

```bash
NETWORK=mainnet MNEMONIC="word1 word2 ..." npx @ton/mcp@alpha --http 3000
```

---

## Project Structure

```
tonpal/
├── bot.py              # Telegram handlers, inline, voice, red packets, admin
├── ai.py               # DeepSeek function calling + translate mode
├── ton_tools.py        # TonCenter + TonAPI + NFT inline search
├── mcp_client.py       # @ton/mcp HTTP client (send, swap, balance)
├── tts_cantonese_ai.py # cantonese.ai TTS wrapper
├── redpacket.py        # Red packet DB, creation, claims, bot_users tracking
├── config.py           # All env config
├── requirements.txt
└── data/               # SQLite DBs (auto-created)
```

---

## Hackathon

Built for **Identity Hub AI Hackathon** (March 2026).

- Uses [@ton/mcp](https://docs.ton.org/ecosystem/ai/mcp) for real on-chain transactions
- 7+ language voice interface — not just text translation, but full STT→AI→TTS pipeline
- Red packets demonstrate a real, shareable, on-chain social feature
- Inline NFT search turns every Telegram chat into an NFT discovery tool

---

## License

MIT
