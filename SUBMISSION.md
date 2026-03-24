# Hackathon Submission Text (Copy-Paste Ready)

## Title
TON TE AI — Multilingual Voice-Enabled TON Assistant

## Track
User-Facing AI Agents

## GitHub Repo
https://github.com/user/tonpal
(Replace with your actual GitHub repo URL)

## Demo Bot
https://t.me/TON_TE_Ai_bot

## Demo video (optional — no phone screen recording needed)

**Option A — Product page (recommended, clean):** Open `tonpal/demo/index.html` in Chrome/Safari → fullscreen (F11) → record 45–90s with QuickTime / macOS screenshot bar → scroll slowly through hero → features → phone mockup → tech tags. That file already states all selling points + correct bot handle.

**Option B — Live bot:** Record 1–2 min of you using https://t.me/TON_TE_Ai_bot (or skip video and rely on judges clicking the bot).

**What judges should do:** Open the bot → `/start` → tap **How to Use** — the bot text lists the full demo script.

---

## Description (paste this into submission form)

TON TE AI is a Telegram bot that makes the TON blockchain accessible to everyone — any language, any ability. Over 150 million people in China's top provinces alone have never heard of TON. Most TON tools only speak English or Russian. Billions are left out by language, literacy, or disability.

**What it does:**
- Full TON toolkit via buttons, text, or voice — scan tokens (price, holders, risk score), trending, balance, transactions, jettons, NFTs, DNS resolve, swap quotes
- 7+ language UI: English, Chinese, Cantonese, Russian, Japanese, Korean, Thai — switch anytime
- Voice input (Groq Whisper STT) → AI brain (DeepSeek with function calling) → voice output (cantonese.ai TTS with native Cantonese/Mandarin/English voices)
- On-chain red packets: create in DM or any group via inline (`@bot 1 5`), photo card with Open button, real TON payouts via @ton/mcp
- Inline NFT search: type `@bot durov` or `@bot foundation.ton` in any chat → NFT cards with Getgems/TONViewer/Fragment links
- Translate mode: voice or text in any language → translated to your UI language
- Admin dashboard: `/admin` shows bot users, red packet stats, wallet balance

**Tech stack:**
- Python + python-telegram-bot
- DeepSeek V3 (NLP brain with function calling + tool orchestration)
- Groq Whisper (fast STT), cantonese.ai (TTS), Edge TTS (fallback)
- TonAPI + TonCenter (chain data)
- @ton/mcp (on-chain send, swap, red packet payouts)
- SQLite (packets, claims, user tracking)

**Why it matters for TON:**
One bot, every language, voice-first. We bring TON to billions who can't use English-only tools — including visually impaired users who can speak to the bot and hear responses. Red packets turn TON into a social, shareable experience in any Telegram group.
