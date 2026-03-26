#!/usr/bin/env python3
"""
TON TE AI — AI-powered TON blockchain assistant for Telegram

Natural language TON operations: balance, transactions, swap quotes,
NFT lookup, DNS resolve, send TON/jettons.

Usage: python3 bot.py
"""
import asyncio
import html
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import edge_tts
import speech_recognition as sr
from pydub import AudioSegment

_START_DEBOUNCE: dict[int, float] = {}
# 同一訪客通知管理員嘅最短間隔（秒），避免瘋狂撳掣洗爆私訊
_ADMIN_VISITOR_NOTIFY_LAST: dict[int, float] = {}
_ADMIN_VISITOR_NOTIFY_COOLDOWN_SEC = 45.0

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent, InlineQueryResultCachedPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    TypeHandler,
    filters,
    ContextTypes,
)

import httpx

from config import TELEGRAM_BOT_TOKEN, CANTONESE_TTS_API_KEY, GROQ_API_KEY, USDT_JETTON, REDPACKET_OWNER_IDS, BOT_ADMIN_IDS
from ai import stream_process_message, translate_to_ui_language
from tts_cantonese_ai import synthesize_cantonese_ai
from ton_tools import (
    get_trending_tokens, get_transactions, TON_ADDR_RE,
    nft_inline_search, nft_best_preview, nft_price_display,
)
from redpacket import (
    init_db as init_redpacket_db,
    create_packet,
    get_packet,
    get_claim_count,
    get_reserved_claim_count,
    has_claimed,
    allocate_claim,
    release_failed_claim,
    set_claim_tx_result,
    nanoton_to_ton,
    raw_to_usdt,
    get_deep_link,
    register_group_message,
    get_group_messages,
    clear_group_messages,
    get_saved_address,
    save_address,
    get_admin_dashboard_stats,
    record_bot_user,
    list_recent_bot_users,
    list_recent_claims,
)
from mcp_client import mcp_send_ton, mcp_send_jetton, mcp_get_balance, is_mcp_available

_draft_http = None  # type: Optional[httpx.AsyncClient]


async def _send_draft(chat_id: int, draft_id: int, text: str):
    """Stream partial text via Telegram's sendMessageDraft (Bot API 9.5)."""
    global _draft_http
    try:
        if _draft_http is None:
            _draft_http = httpx.AsyncClient(timeout=5.0)
        await _draft_http.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessageDraft",
            json={"chat_id": chat_id, "draft_id": draft_id, "text": text[:4096]},
        )
    except Exception:
        pass

BANNER_PATH = Path(__file__).parent / "assets" / "banner.png"
_URL_RE = re.compile(r'(https?://[^\s<>")\]]+)')
_EXPLORER_ADDR_RE = re.compile(
    r'(?:tonviewer\.com|tonscan\.org|tonapi\.io|explorer\.toncoin\.org)'
    r'/(?:address/|account/)?((?:EQ|UQ)[A-Za-z0-9_-]{44,48})',
    re.IGNORECASE,
)
_IMG_TAG_RE = re.compile(r'\[IMG\](.*?)\[/IMG\]\n?')
_RP_CARD_FILE_ID = "AgACAgUAAxkDAAIDfmnCLX4AAep1KHgOU2cWyDUHia2o1QACJQ9rG3GrEFZ--Xd5trw4RQEAAwIAA3cAAzoE"
_REDPACKET_LINK_RE = re.compile(
    r'(?:https?://)?(?:t\.me|telegram\.me)/\w+\?start=(rp_[a-z0-9_]+)',
    re.IGNORECASE,
)

VOICE_MAP = {
    "zh":    "zh-CN-XiaoxiaoNeural",
    "yue":   "zh-HK-HiuGaaiNeural",
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "en":    "en-US-AvaMultilingualNeural",
    "ru":    "ru-RU-SvetlanaNeural",
    "ja":    "ja-JP-NanamiNeural",
    "ko":    "ko-KR-SunHiNeural",
    "ar":    "ar-SA-ZariyahNeural",
    "fr":    "fr-FR-DeniseNeural",
    "de":    "de-DE-KatjaNeural",
    "es":    "es-ES-ElviraNeural",
    "pt":    "pt-BR-FranciscaNeural",
    "it":    "it-IT-ElsaNeural",
    "hi":    "hi-IN-SwaraNeural",
    "th":    "th-TH-PremwadeeNeural",
    "vi":    "vi-VN-HoaiMyNeural",
    "tr":    "tr-TR-EmelNeural",
    "pl":    "pl-PL-AgnieszkaNeural",
    "nl":    "nl-NL-ColetteNeural",
    "sv":    "sv-SE-SofieNeural",
    "id":    "id-ID-GadisNeural",
    "fil":   "fil-PH-BlessicaNeural",
    "uk":    "uk-UA-PolinaNeural",
}
VOICE_FALLBACK = "en-US-AvaMultilingualNeural"

_STRIP_RE = re.compile(r'<[^>]+>|[━┣┗┃│─]+|\*+|https?://\S+|\[IMG\].*?\[/IMG\]')
_CANTONESE_CHARS = set("啲嘅嗰冇唔咁嘢㗎喺攞噉乜嚟咗俾嘥嗮揾啫嘞咩瞓")


def _detect_voice_lang(text: str) -> str:
    """Auto-detect language from response text using Unicode analysis."""
    sample = text[:500]

    has_cjk = False
    jp_score = 0
    kr_score = 0
    cyr_score = 0
    ar_score = 0
    thai_score = 0
    deva_score = 0
    viet_marks = 0
    canto_score = 0

    for ch in sample:
        cp = ord(ch)
        if 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF:
            jp_score += 3
        elif 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF:
            kr_score += 3
        elif 0x0E00 <= cp <= 0x0E7F:
            thai_score += 3
        elif 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F:
            ar_score += 3
        elif 0x0900 <= cp <= 0x097F:
            deva_score += 3
        elif 0x0400 <= cp <= 0x04FF:
            cyr_score += 3
        elif 0x4E00 <= cp <= 0x9FFF:
            has_cjk = True
        if ch in _CANTONESE_CHARS:
            canto_score += 5
        if ch in "ăâđêôơưẠ-ỹ":
            viet_marks += 3

    scores = {"ja": jp_score, "ko": kr_score, "th": thai_score,
              "ar": ar_score, "hi": deva_score, "ru": cyr_score}
    top = max(scores, key=scores.get)
    if scores[top] >= 6:
        return top

    if has_cjk:
        return "yue" if canto_score >= 5 else "zh"

    if viet_marks >= 6:
        return "vi"

    for ch in "àâçéèêëîïôùûüÿœæ":
        if ch in sample.lower():
            return "fr"
    if "ß" in sample or "ä" in sample.lower() or "ü" in sample.lower():
        return "de"
    if "ñ" in sample or "¿" in sample or "¡" in sample:
        return "es"
    if "ã" in sample.lower() or "õ" in sample.lower():
        return "pt"

    return "en"


def _voice_summary(text: str, limit: int = 200) -> str:
    """Extract a short spoken summary from bot reply (for TTS speed)."""
    clean = _STRIP_RE.sub("", text).strip()
    clean = re.sub(r'\s+', ' ', clean)
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    for sep in ("。", ".", "！", "!", "\n", "，", ",", " "):
        pos = cut.rfind(sep)
        if pos > limit // 3:
            return cut[: pos + 1].strip()
    return cut.strip() + "…"


def _cantonese_ai_mode(ui_lang: str, use_detection: bool, clean: str) -> Optional[str]:
    """若應使用 cantonese.ai（玲奈/Vin/JL），返 'yue'|'zh'|'en'；否則 None 用 edge。"""
    if not CANTONESE_TTS_API_KEY:
        return None
    if use_detection:
        det = _detect_voice_lang(clean)
        if det == "yue":
            return "yue"
        if det == "zh":
            return "zh"
        if det == "en":
            return "en"
        return None
    if ui_lang == "yue":
        return "yue"
    if ui_lang in ("zh", "zh-TW"):
        return "zh"
    if ui_lang == "en":
        return "en"
    return None


async def _text_to_voice(text: str, ui_lang: str = "en", use_detection: bool = True) -> Optional[str]:
    """Convert text to voice (OGG/MP3). 優先 cantonese.ai（玲奈粵 / Vin國 / JL英），否則 edge-tts。"""
    clean = _STRIP_RE.sub("", text).strip()
    clean = re.sub(r'\s+', ' ', clean)
    if len(clean) < 5:
        return None
    if len(clean) > 800:
        clean = clean[:800] + "..."

    mode = _cantonese_ai_mode(ui_lang, use_detection, clean)
    if mode:
        path = await synthesize_cantonese_ai(clean, mode)
        if path:
            log.info("TTS cantonese.ai mode=%s", mode)
            return path
        log.debug("cantonese.ai 失敗，fallback edge-tts")

    if use_detection:
        detected = _detect_voice_lang(clean)
        voice = VOICE_MAP.get(detected, VOICE_MAP.get(ui_lang, VOICE_FALLBACK))
    else:
        voice = VOICE_MAP.get(ui_lang, VOICE_FALLBACK)

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    try:
        comm = edge_tts.Communicate(clean, voice, rate="+8%")
        await comm.save(tmp.name)
        if os.path.getsize(tmp.name) < 100:
            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None


_STT_LANG_MAP = {
    "zh": "zh-TW", "yue": "zh-HK", "en": "en-US", "ru": "ru-RU",
    "ja": "ja-JP", "ko": "ko-KR", "th": "th-TH",
}
_STT_FALLBACK_ORDER = ["zh-HK", "zh-TW", "en-US", "ja-JP", "ko-KR", "ru-RU", "th-TH"]
_stt_recognizer = sr.Recognizer()

_GROQ_LANG_MAP = {
    "en": "en", "zh": "zh", "yue": "yue", "ru": "ru",
    "ja": "ja", "ko": "ko", "th": "th",
}


def _transcribe_groq_sync(audio_path: str, lang: str) -> Optional[str]:
    if not GROQ_API_KEY:
        return None
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        gl = _GROQ_LANG_MAP.get(lang, "en")
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()
        # UI 語言選英文時，用戶仍可能講廣東話／中文 — 唔好強制 language=en，交俾 Whisper 自動偵測
        kwargs = {
            "file": (os.path.basename(audio_path), audio_bytes),
            "model": "whisper-large-v3-turbo",
            "response_format": "text",
        }
        if lang != "en":
            kwargs["language"] = gl
        result = client.audio.transcriptions.create(**kwargs)
        text = (result if isinstance(result, str) else str(result)).strip()
        return text or None
    except Exception:
        return None


_fw_lock = threading.Lock()
_fw_model = None


def _get_faster_whisper_model():
    global _fw_model
    with _fw_lock:
        if _fw_model is None:
            from faster_whisper import WhisperModel
            mname = os.environ.get("WHISPER_MODEL", "small")
            _fw_model = WhisperModel(mname, device="cpu", compute_type="int8")
    return _fw_model


_FW_LANG_MAP = {
    "en": "en", "zh": "zh", "ru": "ru",
    "ja": "ja", "ko": "ko", "th": "th",
}

_FW_PROMPTS = {
    "yue": "以下係廣東話語音，可能包含TON、USDT、NFT等加密貨幣術語。",
    "zh": "以下是中文语音，可能包含TON、USDT、NFT等加密货币术语。",
    "en": "The following is about TON blockchain, crypto, USDT, NFT, jettons.",
}


def _transcribe_faster_whisper_sync(wav_path: str, lang: str) -> Optional[str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None
    # lang=en：可能講粵語，唔強制英文；用粵語 prompt 幫手
    wl = None if lang == "en" else _FW_LANG_MAP.get(lang)
    prompt = _FW_PROMPTS["yue"] if lang == "en" else _FW_PROMPTS.get(lang, _FW_PROMPTS.get("en"))
    try:
        model = _get_faster_whisper_model()
        segments, info = model.transcribe(
            wav_path,
            language=wl,
            beam_size=5,
            vad_filter=True,
            initial_prompt=prompt,
        )
        text = "".join(s.text for s in segments).strip()
        return text or None
    except Exception:
        return None

_CRYPTO_FIXES = {
    "ten": "TON", "ton": "TON", "tonne": "TON", "tongue": "TON",
    "town": "TON", "done": "TON", "dawn": "TON", "torn": "TON",
    "u s d t": "USDT", "usdd": "USDT",
    "not coin": "Notcoin", "dogs": "DOGS", "stone": "STON",
    "jet ton": "jetton", "jet tonne": "jetton",
    "nft": "NFT", "n f t": "NFT",
}


def _fix_crypto_terms(text: str) -> str:
    """Post-process STT output to fix common crypto misheard words."""
    result = text
    result = re.sub(r"\bp\s*o\s*n\b", "TON", result, flags=re.IGNORECASE)
    result = re.sub(r"\bp\s*o\s*n\s*t\b", "TON", result, flags=re.IGNORECASE)
    for wrong, right in _CRYPTO_FIXES.items():
        result = re.sub(re.escape(wrong), right, result, flags=re.IGNORECASE)
    return result


async def _voice_to_text(ogg_path: str, lang: str = "en") -> Optional[str]:
    """Convert voice → text. Order: Groq API → faster-whisper (local) → Google (single lang only)."""
    t0 = time.time()
    wav_path = ogg_path.rsplit(".", 1)[0] + ".wav"
    try:
        if ogg_path.endswith(".mp3"):
            audio = AudioSegment.from_mp3(ogg_path)
        else:
            audio = AudioSegment.from_ogg(ogg_path)
        audio.export(wav_path, format="wav")
        log.info("[STT] audio converted %.1fs", time.time() - t0)

        # Tier 1: Groq Whisper API
        t1 = time.time()
        groq_text = await asyncio.to_thread(_transcribe_groq_sync, ogg_path, lang)
        if groq_text and groq_text.strip():
            log.info("[STT] Groq OK %.1fs: %s", time.time() - t1, groq_text.strip()[:80])
            return _fix_crypto_terms(groq_text.strip())
        log.info("[STT] Groq skip/fail %.1fs", time.time() - t1)

        # Tier 2: faster-whisper local
        t2 = time.time()
        fw_text = await asyncio.to_thread(_transcribe_faster_whisper_sync, wav_path, lang)
        if fw_text and fw_text.strip():
            log.info("[STT] faster-whisper OK %.1fs: %s", time.time() - t2, fw_text.strip()[:80])
            return _fix_crypto_terms(fw_text.strip())
        log.info("[STT] faster-whisper fail %.1fs", time.time() - t2)

        # Tier 3: Google — single language only, no loop
        t3 = time.time()
        try:
            with sr.AudioFile(wav_path) as source:
                audio_data = _stt_recognizer.record(source)
            primary = _STT_LANG_MAP.get(lang, "en-US")
            text = await asyncio.to_thread(
                _stt_recognizer.recognize_google, audio_data, language=primary
            )
            if text and text.strip():
                log.info("[STT] Google OK %.1fs: %s", time.time() - t3, text.strip()[:80])
                return _fix_crypto_terms(text.strip())
        except (sr.UnknownValueError, sr.RequestError):
            pass
        log.info("[STT] Google fail %.1fs", time.time() - t3)

        log.warning("[STT] ALL tiers failed, total %.1fs", time.time() - t0)
        return None
    except Exception as e:
        log.exception("[STT] error: %s (%.1fs)", e, time.time() - t0)
        return None
    finally:
        for p in (ogg_path, wav_path):
            try:
                os.unlink(p)
            except OSError:
                pass


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tonpal")

_E = {
    "ton":    '<tg-emoji emoji-id="5462902520215002477">💎</tg-emoji>',
    "coin":   '<tg-emoji emoji-id="5382164415019768638">🪙</tg-emoji>',
    "chart":  '<tg-emoji emoji-id="5203993413346680064">📊</tg-emoji>',
    "rocket": '<tg-emoji emoji-id="5188481279963715781">🚀</tg-emoji>',
    "money":  '<tg-emoji emoji-id="5417924076503062111">💰</tg-emoji>',
    "link":   '<tg-emoji emoji-id="5440410042773824003">🔗</tg-emoji>',
    "lock":   '<tg-emoji emoji-id="5429405838345265327">🔓</tg-emoji>',
    "send":   '<tg-emoji emoji-id="5463424023734014980">🛫</tg-emoji>',
    "swap":   '<tg-emoji emoji-id="5377336227533969892">💱</tg-emoji>',
    "chat":   '<tg-emoji emoji-id="5235570365094188078">💬</tg-emoji>',
    "trophy": '<tg-emoji emoji-id="5188344996356448758">🏆</tg-emoji>',
    "wallet": '<tg-emoji emoji-id="5215420556089776398">👛</tg-emoji>',
    "key":    '<tg-emoji emoji-id="5307843983102204243">🔑</tg-emoji>',
    "target": '<tg-emoji emoji-id="5461009483314517035">🎯</tg-emoji>',
    "up":     '<tg-emoji emoji-id="5298614648138919107">📈</tg-emoji>',
    "card":   '<tg-emoji emoji-id="5472250091332993630">💳</tg-emoji>',
}

LANG_TEXTS = {
    "en": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'Your AI assistant for the <b>TON</b> blockchain.\n\n'
            f'<blockquote>{_E["target"]} <b>What I can do:</b>\n'
            f'{_E["chart"]} Scan any token — price, LP, safety\n'
            f'{_E["rocket"]} Trending tokens on TON\n'
            f'{_E["wallet"]} Balance &amp; transactions\n'
            f'{_E["swap"]} Swap quotes (TON ↔ USDT)\n'
            f'{_E["link"]} DNS, jettons, NFTs\n'
            f'{_E["send"]} Send TON / jettons (MCP)\n'
            f'{_E["money"]} <b>Red packets</b> — share in any chat (inline), real on-chain payouts\n'
            f'{_E["chat"]} <b>Voice</b>: speak → STT → AI → TTS reply</blockquote>\n'
            f'Tap <b>How to Use</b> for a <b>demo recording script</b>.\n'
            f'<i>Built by TE (TechEscrow)</i>'
        ),
        "btn_howto": "❓ How to Use",
        "btn_scan": "🔍 Scan Token",
        "btn_trending": "🔥 Trending",
        "btn_balance": "💎 Balance",
        "btn_tx": "📜 Transactions",
        "btn_dns": "🔗 DNS Resolve",
        "btn_jettons": "🪙 Jettons",
        "btn_nfts": "🖼 NFTs",
        "btn_swap": "💱 Swap Quote",
        "btn_search": "🔎 Search Token",
        "btn_lang": "🌐 Language",
        "btn_translate": "📝 Translate",
        "btn_back": "◀️ Back",
        "ask_scan": f'{_E["chart"]}  <b>Token Scanner</b>\n\n<blockquote>Send me a token contract address\n<code>EQ...</code> or <code>UQ...</code>\n\nI\'ll scan: price, liquidity, holders,\nsafety risk, DEX pools &amp; more.</blockquote>',
        "ask_search": f'{_E["target"]}  <b>Search Token</b>\n\n<blockquote>Type a token name or symbol\ne.g. <i>USDT</i>, <i>NOT</i>, <i>DOGS</i></blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>Balance Check</b>\n\n<blockquote>Send me a TON address\n<code>EQ...</code> or <code>UQ...</code></blockquote>',
        "ask_tx": f'{_E["money"]}  <b>Transaction History</b>\n\n<blockquote>Send me a TON address\nI\'ll show the recent transactions.</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>DNS Resolve</b>\n\n<blockquote>Send me a <code>.ton</code> domain\ne.g. <code>foundation.ton</code></blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>Jetton Balances</b>\n\n<blockquote>Send me a TON address\nI\'ll list all jettons (USDT, STON, etc.).</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT Collection</b>\n\n<blockquote>Send me a TON address\nI\'ll show the NFTs held.</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>Swap Quote</b>\n\n<blockquote>Tell me what to swap, e.g.\n<i>"How much USDT for 10 TON?"</i></blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>Language</b>\n\nChoose your language:',
        "lang_set": f'{_E["trophy"]} Language set to <b>English</b>',
        "ask_translate": f'{_E["chat"]}  <b>Translate mode</b>\n\n<blockquote>Send <b>voice</b> or <b>text</b> in any language.\nI will translate it into your <b>interface language</b> (English).\n\n<i>TON queries are off in this mode.</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>Why TON TE AI?</b>\n\n'
            f'<blockquote>Over <b>150 million</b> people in China\'s top 5 provinces + Hong Kong alone — fewer than <b>500,000</b> have ever heard of TON.\n\n'
            f'Across Japan, Korea, Thailand, the Middle East — most TON tools only speak English or Russian.\n\n'
            f'Billions worldwide are left out: different languages, low literacy, or visual impairment.</blockquote>\n\n'
            f'{_E["rocket"]} <b>Our mission:</b> TON for <b>everyone</b> — any race, any country, any ability.\n\n'
            f'<blockquote><b>Everyday use:</b>\n'
            f'• Tap a button, or <b>type</b> in your language, or <b>send voice</b>\n'
            f'• <b>Translate mode</b> for pure translation (no TON queries)\n'
            f'• <b>Red packet</b> (admin): private chat → 🧧 → <code>amount count</code>; or in any group: <code>@YourBot 1 5</code> → pick result → card + <b>Open</b> link → claim in DM with TON address</blockquote>\n\n'
            f'{_E["trophy"]} <b>Suggested demo video script (~2–3 min)</b>\n'
            f'<blockquote>1) <b>/start</b> — show home buttons &amp; languages\n'
            f'2) Ask by <b>voice</b>: e.g. TON price — show text + voice reply\n'
            f'3) <b>Scan token</b> or <b>Trending</b> — one quick example\n'
            f'4) In a <b>group</b>: <code>@BotUsername 0.01 3</code> — send red-packet card → tap <b>Open Red Packet</b> → bot DM → paste <code>UQ…</code> / <code>EQ…</code> → show success\n'
            f'5) (Optional) <b>Translate mode</b> — short clip</blockquote>'
        ),
        "voice_not_recognized": "⚠️ Could not recognize your voice. Please try again or type your message.",
        "btn_redpacket": "🧧 Red Packet",
        "ask_redpacket": f'{_E["money"]}  <b>Red Packet</b>\n\n<blockquote><b>Private (admin):</b> reply here:\n<code>amount count [lucky|fixed] [usdt]</code>\ne.g. <code>1 5</code>, <code>10 3 usdt</code>\n\n<b>Any group:</b> type <code>@BotUsername 1 5</code> → choose the photo card → <b>Open Red Packet</b> opens the bot → send TON address to claim. First claim saves your address for next time.</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>Red Packet</b>\n\nTo claim, send your TON wallet address (<code>EQ...</code> or <code>UQ...</code>)",
        "redpacket_success": "✅ Claimed {amount} successfully!",
        "redpacket_failed": "❌ Failed to send: {err}",
        "redpacket_expired": "⏰ This red packet has expired.",
        "redpacket_no_slots": "😔 No shares left.",
        "redpacket_already_claimed": "You have already claimed from this packet.",
        "redpacket_mcp_required": "⚠️ Send feature requires MCP server. Red packets unavailable.",
        "redpacket_created": "🧧 Red packet created!\n\nShare this link:\n{link}\n\n{total} {asset} × {count} shares",
        "redpacket_group_card": "🧧 <b>Red Packet</b>\n\n{total} {asset} × {count} shares\n{claimed}/{count} claimed",
        "redpacket_all_claimed": "🧧 <b>Red Packet</b>\n\n{total} {asset} × {count} shares\n✅ All claimed!",
        "redpacket_btn_open": "🧧 Open",
        "redpacket_claim_prompt_short": "Check PM — send your TON address there",
        "redpacket_inline_title": "🧧 Send Red Packet",
        "redpacket_inline_desc": "Share a TON red packet in this chat",
    },
    "zh": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'你的 <b>TON</b> 區塊鏈 AI 助手\n\n'
            f'<blockquote>{_E["target"]} <b>我能做什麼：</b>\n'
            f'{_E["chart"]} 掃描代幣 — 價格、LP、安全\n'
            f'{_E["rocket"]} TON 熱門代幣\n'
            f'{_E["wallet"]} 餘額與交易記錄\n'
            f'{_E["swap"]} 兌換報價 (TON ↔ USDT)\n'
            f'{_E["link"]} 域名、代幣、NFT\n'
            f'{_E["send"]} 發送 TON / 代幣（MCP）\n'
            f'{_E["money"]} <b>紅包</b> — 任意群組 inline 分享，鏈上真實到賬\n'
            f'{_E["chat"]} <b>語音</b>：說話 → 識別 → AI → 語音回覆</blockquote>\n'
            f'點 <b>使用指南</b> 有<b>錄影演示腳本</b>。\n'
            f'<i>由 TE (TechEscrow) 打造</i>'
        ),
        "btn_howto": "❓ 使用指南",
        "btn_scan": "🔍 掃描代幣",
        "btn_trending": "🔥 熱門",
        "btn_balance": "💎 查餘額",
        "btn_tx": "📜 交易記錄",
        "btn_dns": "🔗 域名解析",
        "btn_jettons": "🪙 代幣",
        "btn_nfts": "🖼 NFT",
        "btn_swap": "💱 兌換報價",
        "btn_search": "🔎 搜索代幣",
        "btn_lang": "🌐 語言",
        "btn_translate": "📝 翻譯",
        "btn_back": "◀️ 返回",
        "ask_scan": f'{_E["chart"]}  <b>代幣掃描</b>\n\n<blockquote>發一個代幣合約地址\n<code>EQ...</code> 或 <code>UQ...</code>\n\n我幫你掃描：價格、流動性、持幣人、\n安全風險、DEX 池等。</blockquote>',
        "ask_search": f'{_E["target"]}  <b>搜索代幣</b>\n\n<blockquote>輸入代幣名稱或符號\n例如 <i>USDT</i>、<i>NOT</i>、<i>DOGS</i></blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>查詢餘額</b>\n\n<blockquote>發一個 TON 地址\n<code>EQ...</code> 或 <code>UQ...</code></blockquote>',
        "ask_tx": f'{_E["money"]}  <b>交易記錄</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>域名解析</b>\n\n<blockquote>發一個 <code>.ton</code> 域名\n例如 <code>foundation.ton</code></blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>代幣列表</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT 列表</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>兌換報價</b>\n\n<blockquote>話我知你想換咩，例如\n<i>\"10 TON 可以換幾多 USDT？\"</i></blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>語言</b>\n\n選擇語言：',
        "lang_set": f'{_E["trophy"]} 語言已設為 <b>國語</b>',
        "ask_translate": f'{_E["chat"]}  <b>翻譯模式</b>\n\n<blockquote>發<b>語音</b>或<b>文字</b>（任何語言）。\n我會譯成你的<b>介面語言</b>（簡體中文）。\n\n<i>此模式下不做 TON 查詢。</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>為什麼選 TON TE AI？</b>\n\n'
            f'<blockquote>中國五大省加香港約 <b>1.5 億</b>人口，知道 TON 的不超過 <b>50 萬</b>。\n\n'
            f'日本、韓國、東南亞 — 大部分 TON 工具只有英語和俄語。\n\n'
            f'全球數十億人被排除在外：語言不通、識字率低、甚至視障人士。</blockquote>\n\n'
            f'{_E["rocket"]} <b>我們的使命：</b>讓<b>所有人</b>都能用 TON。\n\n'
            f'<blockquote><b>日常使用：</b>\n'
            f'• 點按鈕，或中文<b>打字</b>，或發<b>語音</b>\n'
            f'• <b>翻譯模式</b>：只做翻譯（不做 TON 查詢）\n'
            f'• <b>紅包</b>（管理員）：私聊 🧧 → 回覆 <code>金額 份數</code>；任意群組 <code>@機械人 1 5</code> → 選封面卡 → <b>開紅包</b> 鏈接 → 私訊發 TON 地址領取</blockquote>\n\n'
            f'{_E["trophy"]} <b>建議錄影腳本（約 2–3 分鐘）</b>\n'
            f'<blockquote>1) <b>/start</b> — 展示首頁按鈕與語言\n'
            f'2) <b>語音</b>問 TON 價格 — 展示文字 + 語音回覆\n'
            f'3) <b>掃描代幣</b> 或 <b>熱門</b> — 快速演示一個\n'
            f'4) <b>群組</b>：<code>@機械人 0.01 3</code> — 發紅包卡 → 點 <b>Open Red Packet</b> → 機械人私訊 → 貼 <code>UQ…</code> — 顯示領取成功\n'
            f'5)（可選）<b>翻譯模式</b> 短鏡頭</blockquote>'
        ),
        "voice_not_recognized": "⚠️ 無法識別語音，請重試或直接輸入文字。",
        "btn_redpacket": "🧧 紅包",
        "ask_redpacket": f'{_E["money"]}  <b>紅包</b>\n\n<blockquote><b>私聊（管理員）：</b>在此回覆\n<code>金額 份數 [lucky|fixed] [usdt]</code>\n例：<code>1 5</code>、<code>10 3 usdt</code>\n\n<b>任意群組：</b>輸入 <code>@機械人用戶名 1 5</code> → 選封面圖卡片 → <b>Open Red Packet</b> 打開機械人 → 發 TON 地址領取。首次領取會記住地址。</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>紅包</b>\n\n請發送你的 TON 錢包地址（<code>EQ...</code> 或 <code>UQ...</code>）領取",
        "redpacket_success": "✅ 領取成功！收到 {amount}",
        "redpacket_failed": "❌ 發送失敗：{err}",
        "redpacket_expired": "⏰ 此紅包已過期。",
        "redpacket_no_slots": "😔 已搶完。",
        "redpacket_already_claimed": "你已經領過這個紅包了。",
        "redpacket_mcp_required": "⚠️ 發送功能需要 MCP 服務，紅包暫不可用。",
        "redpacket_created": "🧧 紅包已建立！\n\n分享連結：\n{link}\n\n{total} {asset} × {count} 份",
        "redpacket_group_card": "🧧 <b>紅包</b>\n\n{total} {asset} × {count} 份\n已搶 {claimed}/{count}",
        "redpacket_all_claimed": "🧧 <b>紅包</b>\n\n{total} {asset} × {count} 份\n✅ 已搶完！",
        "redpacket_btn_open": "🧧 開紅包",
        "redpacket_claim_prompt_short": "請到私訊輸入TON地址領取",
        "redpacket_inline_title": "🧧 發紅包",
        "redpacket_inline_desc": "分享TON紅包",
    },
    "ru": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'Ваш AI-ассистент для блокчейна <b>TON</b>\n\n'
            f'<blockquote>{_E["target"]} <b>Что я умею:</b>\n'
            f'{_E["chart"]} Сканировать токены — цена, LP, безопасность\n'
            f'{_E["rocket"]} Трендовые токены на TON\n'
            f'{_E["wallet"]} Баланс и транзакции\n'
            f'{_E["swap"]} Котировки обмена (TON ↔ USDT)\n'
            f'{_E["link"]} DNS, жетоны, NFT\n'
            f'{_E["send"]} Отправка TON / жетонов (MCP)\n'
            f'{_E["money"]} <b>Красные конверты</b> — inline в чатах, выплаты в сети\n'
            f'{_E["chat"]} <b>Голос</b>: речь → STT → AI → голосовой ответ</blockquote>\n'
            f'«Как пользоваться» — сценарий для демо-видео.\n'
            f'<i>Создано TE (TechEscrow)</i>'
        ),
        "btn_howto": "❓ Как?",
        "btn_scan": "🔍 Сканер", "btn_trending": "🔥 Тренды", "btn_balance": "💎 Баланс",
        "btn_tx": "📜 Транзакции", "btn_dns": "🔗 DNS", "btn_jettons": "🪙 Жетоны",
        "btn_nfts": "🖼 NFT", "btn_swap": "💱 Обмен", "btn_search": "🔎 Поиск",
        "btn_lang": "🌐 Язык", "btn_translate": "📝 Перевод", "btn_back": "◀️ Назад",
        "ask_scan": f'{_E["chart"]}  <b>Сканер токенов</b>\n\n<blockquote>Отправьте адрес контракта\n<code>EQ...</code> или <code>UQ...</code></blockquote>',
        "ask_search": f'{_E["target"]}  <b>Поиск</b>\n\n<blockquote>Введите название или символ токена</blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>Баланс</b>\n\n<blockquote>Отправьте TON-адрес</blockquote>',
        "ask_tx": f'{_E["money"]}  <b>Транзакции</b>\n\n<blockquote>Отправьте TON-адрес</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>DNS</b>\n\n<blockquote>Отправьте <code>.ton</code> домен</blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>Жетоны</b>\n\n<blockquote>Отправьте TON-адрес</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT</b>\n\n<blockquote>Отправьте TON-адрес</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>Обмен</b>\n\n<blockquote><i>\"Сколько USDT за 10 TON?\"</i></blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>Язык</b>\n\nВыберите язык:',
        "lang_set": f'{_E["trophy"]} Язык: <b>Русский</b>',
        "ask_translate": f'{_E["chat"]}  <b>Режим перевода</b>\n\n<blockquote>Голос или текст на любом языке —\nпереведу на <b>язык интерфейса</b> (русский).\n\n<i>Запросы TON в этом режиме отключены.</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>Зачем TON TE AI?</b>\n\n'
            f'<blockquote>Более <b>150 млн</b> человек в пяти крупнейших провинциях Китая и Гонконге — о TON слышали менее <b>500 тыс.</b>\n\n'
            f'В Японии, Корее, Таиланде и на Ближнем Востоке большинство TON-инструментов только на английском или русском.\n\n'
            f'Миллиарды остаются в стороне: разные языки, низкая грамотность, нарушения зрения.</blockquote>\n\n'
            f'{_E["rocket"]} <b>Наша миссия:</b> TON для <b>всех</b> — любая национальность, страна, возможности.\n\n'
            f'<blockquote><b>Будни:</b>\n'
            f'• Кнопка, <b>текст</b> на своём языке или <b>голос</b>\n'
            f'• <b>Режим перевода</b> — только перевод (без TON-запросов)\n'
            f'• <b>Красный конверт</b> (админ): в ЛС → 🧧 → ответ <code>сумма кол-во</code>; в любом чате <code>@BotUsername 1 5</code> → карточка → <b>Open Red Packet</b> → в ЛС отправить TON-адрес</blockquote>\n\n'
            f'{_E["trophy"]} <b>Сценарий демо-видео (~2–3 мин)</b>\n'
            f'<blockquote>1) <b>/start</b> — кнопки и языки\n'
            f'2) <b>Голосом</b> спросить цену TON — текст + голосовой ответ\n'
            f'3) <b>Сканер</b> или <b>Тренды</b> — один быстрый пример\n'
            f'4) В <b>группе</b>: <code>@BotUsername 0.01 3</code> — карточка → <b>Open Red Packet</b> → бот в ЛС → <code>UQ…</code> / <code>EQ…</code> → успех\n'
            f'5) (Опц.) <b>Режим перевода</b> — короткий кадр</blockquote>'
        ),
        "voice_not_recognized": "⚠️ Не удалось распознать голос.",
        "btn_redpacket": "🧧 Красный конверт",
        "ask_redpacket": f'{_E["money"]}  <b>Красный конверт</b>\n\n<blockquote><b>Личка (админ):</b> ответьте здесь:\n<code>сумма кол-во [lucky|fixed] [usdt]</code>\nнапр. <code>1 5</code>, <code>10 3 usdt</code>\n\n<b>Любая группа:</b> <code>@username_бота 1 5</code> → выберите карточку → <b>Open Red Packet</b> откроет бота → отправьте TON-адрес. Первый claim сохранит адрес.</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>Красный конверт</b>\n\nОтправьте TON-адрес (<code>EQ...</code> или <code>UQ...</code>)",
        "redpacket_success": "✅ Получено {amount}!",
        "redpacket_failed": "❌ Ошибка: {err}",
        "redpacket_expired": "⏰ Срок истёк.",
        "redpacket_no_slots": "😔 Всё роздано.",
        "redpacket_already_claimed": "Вы уже получили.",
        "redpacket_mcp_required": "⚠️ Требуется MCP сервер.",
        "redpacket_created": "🧧 Создано!\n\nСсылка:\n{link}\n\n{total} {asset} × {count}",
        "redpacket_group_card": "🧧 <b>Конверт</b>\n\n{total} {asset} × {count}\n{claimed}/{count}",
        "redpacket_all_claimed": "🧧 <b>Конверт</b>\n\n{total} {asset} × {count}\n✅ Всё роздано!",
        "redpacket_btn_open": "🧧 Открыть",
        "redpacket_claim_prompt_short": "Проверьте ЛС — отправьте TON-адрес",
        "redpacket_inline_title": "🧧 Конверт",
        "redpacket_inline_desc": "Поделиться TON красным конвертом",
    },
    "yue": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'你嘅 <b>TON</b> 區塊鏈 AI 助手\n\n'
            f'<blockquote>{_E["target"]} <b>我可以做咩：</b>\n'
            f'{_E["chart"]} 掃描代幣 — 價格、LP、安全\n'
            f'{_E["rocket"]} TON 熱門代幣\n'
            f'{_E["wallet"]} 餘額同交易記錄\n'
            f'{_E["swap"]} 兌換報價 (TON ↔ USDT)\n'
            f'{_E["link"]} 域名、代幣、NFT\n'
            f'{_E["send"]} 發 TON / 代幣（MCP）\n'
            f'{_E["money"]} <b>紅包</b> — 任何群組 inline 分享，鏈上真錢派\n'
            f'{_E["chat"]} <b>語音</b>：講嘢 → 識別 → AI → 語音覆你</blockquote>\n'
            f'撳 <b>點用</b> 有<b>錄影腳本</b>建議。\n'
            f'<i>由 TE (TechEscrow) 打造</i>'
        ),
        "btn_howto": "❓ 點用",
        "btn_scan": "🔍 掃描代幣", "btn_trending": "🔥 熱門", "btn_balance": "💎 查餘額",
        "btn_tx": "📜 交易記錄", "btn_dns": "🔗 域名解析", "btn_jettons": "🪙 代幣",
        "btn_nfts": "🖼 NFT", "btn_swap": "💱 兌換報價", "btn_search": "🔎 搜索代幣",
        "btn_lang": "🌐 語言", "btn_translate": "📝 跟住講", "btn_back": "◀️ 返回",
        "ask_scan": f'{_E["chart"]}  <b>代幣掃描</b>\n\n<blockquote>發一個代幣合約地址\n<code>EQ...</code> 或 <code>UQ...</code></blockquote>',
        "ask_search": f'{_E["target"]}  <b>搜索代幣</b>\n\n<blockquote>輸入代幣名稱或符號\n例如 <i>USDT</i>、<i>NOT</i>、<i>DOGS</i></blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>查餘額</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_tx": f'{_E["money"]}  <b>交易記錄</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>域名解析</b>\n\n<blockquote>發一個 <code>.ton</code> 域名</blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>代幣列表</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT 列表</b>\n\n<blockquote>發一個 TON 地址</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>兌換報價</b>\n\n<blockquote>例如「10 TON 可以換幾多 USDT？」</blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>語言</b>\n\n選擇語言：',
        "lang_set": f'{_E["trophy"]} 語言已設為 <b>廣東話</b>',
        "ask_translate": f'{_E["chat"]}  <b>跟住講／翻譯</b>\n\n<blockquote>發<b>語音</b>或者<b>打字</b>（任何語言）。\n我會譯做你嘅<b>介面語言</b>（廣東話）。\n\n<i>呢個模式唔做 TON 查詢。</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>點解要用 TON TE AI？</b>\n\n'
            f'<blockquote>中國五大省加香港約 <b>1.5 億</b>人，識 TON 嘅唔超過 <b>50 萬</b>。\n\n'
            f'日本、韓國、東南亞 — 大部分 TON 工具淨係有英文同俄文。\n\n'
            f'全球幾十億人用唔到：語言唔通、唔識字、甚至失明。</blockquote>\n\n'
            f'{_E["rocket"]} <b>我哋嘅使命：</b>TON 屬於<b>所有人</b> — 唔理種族、國家、識唔識字。\n\n'
            f'<blockquote><b>日常點用：</b>\n'
            f'• 撳掣、<b>打字</b>、或者發<b>語音</b>\n'
            f'• <b>跟住講／翻譯模式</b>：淨係翻譯（唔做 TON 查詢）\n'
            f'• <b>紅包</b>（老闆／admin）：私訊撳 🧧 → 回覆 <code>金額 份數</code>；群組打 <code>@機械人 1 5</code> → 揀封面卡 → <b>Open Red Packet</b> 入 bot → 發 TON 地址領</blockquote>\n\n'
            f'{_E["trophy"]} <b>錄影建議流程（約 2–3 分鐘）</b>\n'
            f'<blockquote>1) <b>/start</b> — 影主畫面同語言掣\n'
            f'2) <b>講廣東話</b>問「TON 幾多錢」— 睇字幕同語音回覆\n'
            f'3) <b>掃描代幣</b> 或 <b>熱門</b> — 快示範一個\n'
            f'4) <b>群組</b>：<code>@TON_TE_Ai_bot 0.01 3</code> — 發紅包卡 → 撳 <b>Open Red Packet</b> → 私訊貼 <code>UQ…</code> — 顯示領到\n'
            f'5)（可選）<b>翻譯模式</b> 幾秒</blockquote>'
        ),
        "voice_not_recognized": "⚠️ 聽唔到你講乜，試多次或者打字啦。",
        "btn_redpacket": "🧧 紅包",
        "ask_redpacket": f'{_E["money"]}  <b>紅包</b>\n\n<blockquote><b>私聊（admin）：</b>喺度回覆\n<code>金額 份數 [lucky|fixed] [usdt]</code>\n例：<code>1 5</code>、<code>10 3 usdt</code>\n\n<b>任何群組：</b>打 <code>@TON_TE_Ai_bot 1 5</code> → 揀張封面圖 → <b>Open Red Packet</b> 開 bot → 發 TON 地址領。第一次領會記低地址。</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>紅包</b>\n\n發你嘅 TON 錢包地址（<code>EQ...</code> 或 <code>UQ...</code>）嚟領",
        "redpacket_success": "✅ 領到 {amount}！",
        "redpacket_failed": "❌ 發送失敗：{err}",
        "redpacket_expired": "⏰ 紅包過期喇。",
        "redpacket_no_slots": "😔 搶完喇。",
        "redpacket_already_claimed": "你領過呢個紅包喇。",
        "redpacket_mcp_required": "⚠️ 要 MCP 先用到紅包。",
        "redpacket_created": "🧧 紅包開好喇！\n\n分享呢條連結：\n{link}\n\n{total} {asset} × {count} 份",
        "redpacket_group_card": "🧧 <b>紅包</b>\n\n{total} {asset} × {count} 份\n已搶 {claimed}/{count}",
        "redpacket_all_claimed": "🧧 <b>紅包</b>\n\n{total} {asset} × {count} 份\n✅ 搶完喇！",
        "redpacket_btn_open": "🧧 開紅包",
        "redpacket_claim_prompt_short": "去私訊發TON地址領取",
        "redpacket_inline_title": "🧧 發紅包",
        "redpacket_inline_desc": "喺呢度分享紅包",
    },
    "ja": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'<b>TON</b> ブロックチェーンのAIアシスタント\n\n'
            f'<blockquote>{_E["target"]} <b>できること：</b>\n'
            f'{_E["chart"]} トークンスキャン — 価格、LP、安全性\n'
            f'{_E["rocket"]} TONトレンドトークン\n'
            f'{_E["wallet"]} 残高・取引履歴\n'
            f'{_E["swap"]} スワップ見積 (TON ↔ USDT)\n'
            f'{_E["link"]} DNS、ジェットン、NFT\n'
            f'{_E["send"]} TON/ジェットン送金（MCP）\n'
            f'{_E["money"]} <b>お年玉</b> — インライン共有、オンチェーン送金\n'
            f'{_E["chat"]} <b>音声</b>：話す → STT → AI → 音声返信</blockquote>\n'
            f'使い方にデモ動画の流れあり。\n'
            f'<i>TE (TechEscrow) 製</i>'
        ),
        "btn_howto": "❓ 使い方",
        "btn_scan": "🔍 スキャン", "btn_trending": "🔥 トレンド", "btn_balance": "💎 残高",
        "btn_tx": "📜 取引", "btn_dns": "🔗 DNS", "btn_jettons": "🪙 ジェットン",
        "btn_nfts": "🖼 NFT", "btn_swap": "💱 スワップ", "btn_search": "🔎 検索",
        "btn_lang": "🌐 言語", "btn_translate": "📝 翻訳", "btn_back": "◀️ 戻る",
        "ask_scan": f'{_E["chart"]}  <b>トークンスキャン</b>\n\n<blockquote>アドレスを送信\n<code>EQ...</code> または <code>UQ...</code></blockquote>',
        "ask_search": f'{_E["target"]}  <b>検索</b>\n\n<blockquote>トークン名またはシンボルを入力</blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>残高</b>\n\n<blockquote>TONアドレスを送信</blockquote>',
        "ask_tx": f'{_E["money"]}  <b>取引履歴</b>\n\n<blockquote>TONアドレスを送信</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>DNS</b>\n\n<blockquote><code>.ton</code> ドメインを送信</blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>ジェットン</b>\n\n<blockquote>TONアドレスを送信</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT</b>\n\n<blockquote>TONアドレスを送信</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>スワップ</b>\n\n<blockquote>例: 「10 TONでいくらUSDT？」</blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>言語</b>\n\n言語を選択：',
        "lang_set": f'{_E["trophy"]} 言語: <b>日本語</b>',
        "ask_translate": f'{_E["chat"]}  <b>翻訳モード</b>\n\n<blockquote>音声・テキストどちらでも（言語自由）。\n<b>UI言語</b>（日本語）に翻訳します。\n\n<i>このモードではTON検索はしません。</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>なぜ TON TE AI？</b>\n\n'
            f'<blockquote>中国トップ5省と香港だけで約<b>1.5億</b>人 — TONを知るのは<b>50万人</b>未満。\n\n'
            f'日本・韓国・タイ・中東など、多くのツールは英語かロシア語のみ。\n\n'
            f'言語・リテラシー・視覚的障害で、数十億人が取り残されています。</blockquote>\n\n'
            f'{_E["rocket"]} <b>使命：</b>TONを<b>誰もが</b>使えるように — 国籍・能力を問わず。\n\n'
            f'<blockquote><b>日常の使い方：</b>\n'
            f'• ボタン、<b>テキスト</b>、または<b>音声</b>\n'
            f'• <b>翻訳モード</b> — 純粋な翻訳（TON検索オフ）\n'
            f'• <b>お年玉</b>（管理者）：DMで 🧧 → <code>金額 個数</code>；グループで <code>@BotUsername 1 5</code> → カード → <b>Open Red Packet</b> → DMでTONアドレス</blockquote>\n\n'
            f'{_E["trophy"]} <b>デモ動画の流れ（約2–3分）</b>\n'
            f'<blockquote>1) <b>/start</b> — ホームと言語\n'
            f'2) <b>音声</b>でTON価格など — テキスト＋音声返信\n'
            f'3) <b>スキャン</b> または <b>トレンド</b> — 1例\n'
            f'4) <b>グループ</b>: <code>@BotUsername 0.01 3</code> — カード送信 → <b>Open Red Packet</b> → ボットDM → <code>UQ…</code> 送信 → 成功表示\n'
            f'5)（任意）<b>翻訳モード</b> 短く</blockquote>'
        ),
        "voice_not_recognized": "⚠️ 音声を認識できませんでした。",
        "btn_redpacket": "🧧 赤い封筒",
        "ask_redpacket": f'{_E["money"]}  <b>赤い封筒</b>\n\n<blockquote><b>DM（管理者）：</b>ここに返信\n<code>金額 個数 [lucky|fixed] [usdt]</code>\n例：<code>1 5</code>、<code>10 3 usdt</code>\n\n<b>任意のグループ：</b> <code>@ボットユーザー名 1 5</code> → カバー画像を選ぶ → <b>Open Red Packet</b> でボットを開く → TONアドレスを送って受取。初回でアドレスを記憶。</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>赤い封筒</b>\n\nTONアドレス（<code>EQ...</code> または <code>UQ...</code>）を送信",
        "redpacket_success": "✅ {amount} を受け取りました！",
        "redpacket_failed": "❌ エラー：{err}",
        "redpacket_expired": "⏰ 期限切れです。",
        "redpacket_no_slots": "😔 配り終わりました。",
        "redpacket_already_claimed": "既に受け取り済みです。",
        "redpacket_mcp_required": "⚠️ MCPサーバーが必要です。",
        "redpacket_created": "🧧 作成しました！\n\nリンク：\n{link}\n\n{total} {asset} × {count}",
        "redpacket_group_card": "🧧 <b>赤い封筒</b>\n\n{total} {asset} × {count}\n{claimed}/{count} 受取済",
        "redpacket_all_claimed": "🧧 <b>赤い封筒</b>\n\n{total} {asset} × {count}\n✅ 配り終わりました",
        "redpacket_btn_open": "🧧 開ける",
        "redpacket_claim_prompt_short": "DMでTONアドレスを送信",
        "redpacket_inline_title": "🧧 赤い封筒",
        "redpacket_inline_desc": "TON赤い封筒を共有",
    },
    "ko": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'<b>TON</b> 블록체인 AI 어시스턴트\n\n'
            f'<blockquote>{_E["target"]} <b>기능:</b>\n'
            f'{_E["chart"]} 토큰 스캔 — 가격, LP, 안전\n'
            f'{_E["rocket"]} TON 트렌드\n'
            f'{_E["wallet"]} 잔액·거래\n'
            f'{_E["swap"]} 스왑 시세 (TON ↔ USDT)\n'
            f'{_E["link"]} DNS, 제튼, NFT\n'
            f'{_E["send"]} TON/제튼 전송 (MCP)\n'
            f'{_E["money"]} <b>세뱃돈</b> — 인라인 공유, 온체인 지급\n'
            f'{_E["chat"]} <b>음성</b>: 말하기 → STT → AI → 음성 답변</blockquote>\n'
            f'사용법에 데모 영상 흐름 안내.\n'
            f'<i>TE (TechEscrow) 제작</i>'
        ),
        "btn_howto": "❓ 사용법",
        "btn_scan": "🔍 스캔", "btn_trending": "🔥 트렌드", "btn_balance": "💎 잔액",
        "btn_tx": "📜 거래", "btn_dns": "🔗 DNS", "btn_jettons": "🪙 제튼",
        "btn_nfts": "🖼 NFT", "btn_swap": "💱 스왑", "btn_search": "🔎 검색",
        "btn_lang": "🌐 언어", "btn_translate": "📝 번역", "btn_back": "◀️ 뒤로",
        "ask_scan": f'{_E["chart"]}  <b>토큰 스캔</b>\n\n<blockquote>토큰 주소 전송\n<code>EQ...</code> 또는 <code>UQ...</code></blockquote>',
        "ask_search": f'{_E["target"]}  <b>검색</b>\n\n<blockquote>토큰명 또는 심볼 입력</blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>잔액</b>\n\n<blockquote>TON 주소 전송</blockquote>',
        "ask_tx": f'{_E["money"]}  <b>거래</b>\n\n<blockquote>TON 주소 전송</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>DNS</b>\n\n<blockquote><code>.ton</code> 도메인 전송</blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>제튼</b>\n\n<blockquote>TON 주소 전송</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT</b>\n\n<blockquote>TON 주소 전송</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>스왑</b>\n\n<blockquote>예: 「10 TON = USDT ?」</blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>언어</b>\n\n선택：',
        "lang_set": f'{_E["trophy"]} 언어: <b>한국어</b>',
        "ask_translate": f'{_E["chat"]}  <b>번역 모드</b>\n\n<blockquote>음성·텍스트 모두 가능 (언어 무관).\n<b>UI 언어</b>(한국어)로 번역합니다.\n\n<i>이 모드에서는 TON 조회를 하지 않습니다.</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>왜 TON TE AI?</b>\n\n'
            f'<blockquote>중국 상위 5개 성과 홍콩만 해도 약 <b>1.5억</b> 명 — TON을 아는 사람은 <b>50만</b> 명 미만.\n\n'
            f'일본, 한국, 태국, 중동 등에서 TON 도구는 대부분 영어·러시아어만 지원합니다.\n\n'
            f'언어, 문해력, 시각 장애로 수십억 명이 소외됩니다.</blockquote>\n\n'
            f'{_E["rocket"]} <b>사명:</b> TON을 <b>모든 사람</b>에게 — 국적·능력과 관계없이.\n\n'
            f'<blockquote><b>일상 사용:</b>\n'
            f'• 버튼, <b>텍스트</b>, 또는 <b>음성</b>\n'
            f'• <b>번역 모드</b> — 순수 번역 (TON 조회 없음)\n'
            f'• <b>세뱃돈</b> (관리자): DM에서 🧧 → <code>금액 개수</code>；그룹에서 <code>@BotUsername 1 5</code> → 카드 → <b>Open Red Packet</b> → DM에 TON 주소</blockquote>\n\n'
            f'{_E["trophy"]} <b>데모 영상 시나리오 (약 2–3분)</b>\n'
            f'<blockquote>1) <b>/start</b> — 홈 버튼·언어\n'
            f'2) <b>음성</b>으로 TON 가격 등 질문 — 텍스트+음성 답변\n'
            f'3) <b>스캔</b> 또는 <b>트렌드</b> — 한 가지 예시\n'
            f'4) <b>그룹</b>: <code>@BotUsername 0.01 3</code> — 카드 전송 → <b>Open Red Packet</b> → 봇 DM → <code>UQ…</code> / <code>EQ…</code> → 성공\n'
            f'5) (선택) <b>번역 모드</b> 짧게</blockquote>'
        ),
        "voice_not_recognized": "⚠️ 음성 인식 실패.",
        "btn_redpacket": "🧧 빨간 봉투",
        "ask_redpacket": f'{_E["money"]}  <b>빨간 봉투</b>\n\n<blockquote><b>DM (관리자):</b> 여기에 답장\n<code>금액 개수 [lucky|fixed] [usdt]</code>\n예: <code>1 5</code>, <code>10 3 usdt</code>\n\n<b>아무 그룹:</b> <code>@봇이름 1 5</code> → 커버 카드 선택 → <b>Open Red Packet</b>으로 봇 열기 → TON 주소 전송. 첫 수령 시 주소 저장.</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>빨간 봉투</b>\n\nTON 주소 (<code>EQ...</code> 또는 <code>UQ...</code>) 전송",
        "redpacket_success": "✅ {amount} 받았습니다!",
        "redpacket_failed": "❌ 오류: {err}",
        "redpacket_expired": "⏰ 만료됨",
        "redpacket_no_slots": "😔 다 나갔습니다",
        "redpacket_already_claimed": "이미 받으셨습니다",
        "redpacket_mcp_required": "⚠️ MCP 서버 필요",
        "redpacket_created": "🧧 생성!\n\n링크:\n{link}\n\n{total} {asset} × {count}",
        "redpacket_group_card": "🧧 <b>빨간 봉투</b>\n\n{total} {asset} × {count}\n{claimed}/{count}",
        "redpacket_all_claimed": "🧧 <b>빨간 봉투</b>\n\n{total} {asset} × {count}\n✅ 다 나갔습니다",
        "redpacket_btn_open": "🧧 열기",
        "redpacket_claim_prompt_short": "DM에서 TON 주소 전송",
        "redpacket_inline_title": "🧧 빨간 봉투",
        "redpacket_inline_desc": "TON 빨간 봉투 공유",
    },
    "th": {
        "welcome": (
            f'{_E["ton"]} <b>TON TE AI</b> {_E["ton"]}\n\n'
            f'ผู้ช่วย AI สำหรับบล็อกเชน <b>TON</b>\n\n'
            f'<blockquote>{_E["target"]} <b>สิ่งที่ทำได้:</b>\n'
            f'{_E["chart"]} สแกนโทเค็น — ราคา, LP, ความปลอดภัย\n'
            f'{_E["rocket"]} โทเค็นยอดนิยม\n'
            f'{_E["wallet"]} ยอดและธุรกรรม\n'
            f'{_E["swap"]} ราคาสวอป (TON ↔ USDT)\n'
            f'{_E["link"]} DNS, เจตตัน, NFT\n'
            f'{_E["send"]} ส่ง TON/เจตตัน (MCP)\n'
            f'{_E["money"]} <b>อั่งเปา</b> — แชร์ inline, จ่ายจริง on-chain\n'
            f'{_E["chat"]} <b>เสียง</b>: พูด → STT → AI → ตอบเสียง</blockquote>\n'
            f'วิธีใช้มีสคริปต์สาธิตวิดีโอ\n'
            f'<i>โดย TE (TechEscrow)</i>'
        ),
        "btn_howto": "❓ วิธีใช้",
        "btn_scan": "🔍 สแกน", "btn_trending": "🔥 เทรนด์", "btn_balance": "💎 ยอด",
        "btn_tx": "📜 ธุรกรรม", "btn_dns": "🔗 DNS", "btn_jettons": "🪙 เจตตัน",
        "btn_nfts": "🖼 NFT", "btn_swap": "💱 สวอป", "btn_search": "🔎 ค้นหา",
        "btn_lang": "🌐 ภาษา", "btn_translate": "📝 แปล", "btn_back": "◀️ กลับ",
        "ask_scan": f'{_E["chart"]}  <b>สแกน</b>\n\n<blockquote>ส่งที่อยู่โทเค็น\n<code>EQ...</code> หรือ <code>UQ...</code></blockquote>',
        "ask_search": f'{_E["target"]}  <b>ค้นหา</b>\n\n<blockquote>ใส่ชื่อหรือสัญลักษณ์โทเค็น</blockquote>',
        "ask_balance": f'{_E["wallet"]}  <b>ยอด</b>\n\n<blockquote>ส่งที่อยู่ TON</blockquote>',
        "ask_tx": f'{_E["money"]}  <b>ธุรกรรม</b>\n\n<blockquote>ส่งที่อยู่ TON</blockquote>',
        "ask_dns": f'{_E["link"]}  <b>DNS</b>\n\n<blockquote>ส่งโดเมน <code>.ton</code></blockquote>',
        "ask_jettons": f'{_E["coin"]}  <b>เจตตัน</b>\n\n<blockquote>ส่งที่อยู่ TON</blockquote>',
        "ask_nfts": f'{_E["card"]}  <b>NFT</b>\n\n<blockquote>ส่งที่อยู่ TON</blockquote>',
        "ask_swap": f'{_E["swap"]}  <b>สวอป</b>\n\n<blockquote>เช่น 「10 TON = USDT ?」</blockquote>',
        "lang_prompt": f'{_E["key"]}  <b>ภาษา</b>\n\nเลือก：',
        "lang_set": f'{_E["trophy"]} ภาษา: <b>ไทย</b>',
        "ask_translate": f'{_E["chat"]}  <b>โหมดแปล</b>\n\n<blockquote>ส่งเสียงหรือข้อความ (ทุกภาษา)\nแปลเป็น<b>ภาษาของ UI</b> (ไทย)\n\n<i>ไม่ทำงาน TON ในโหมดนี้</i></blockquote>',
        "ask_howto": (
            f'{_E["target"]}  <b>ทำไมต้อง TON TE AI?</b>\n\n'
            f'<blockquote>แค่ 5 มณฑลใหญ่ของจีน + ฮ่องกง มีคนราว <b>150 ล้าน</b> — แต่คนที่เคยได้ยิน TON น้อยกว่า <b>500,000</b>\n\n'
            f'ญี่ปุ่น เกาหลี ไทย ตะวันออกกลาง — เครื่องมือ TON หลายตัวมีแค่ EN/RU\n\n'
            f'หลายพันล้านคนถูกทิ้งไว้: ภาษา การอ่านเขียน หรือการมองเห็น</blockquote>\n\n'
            f'{_E["rocket"]} <b>พันธกิจ:</b> TON สำหรับ<b>ทุกคน</b> — ไม่ว่าเชื้อชาติ ประเทศ หรือความสามารถ\n\n'
            f'<blockquote><b>ใช้ในชีวิตประจำวัน:</b>\n'
            f'• กดปุ่ม <b>พิมพ์</b> หรือส่ง<b>เสียง</b>\n'
            f'• <b>โหมดแปล</b> — แปลอย่างเดียว (ไม่ค้น TON)\n'
            f'• <b>อั่งเปา</b> (แอดมิน): แชทส่วนตัว → 🧧 → ตอบ <code>จำนวน ชิ้น</code>；กลุ่มใดก็ได้ <code>@BotUsername 1 5</code> → การ์ด → <b>Open Red Packet</b> → ส่งที่อยู่ TON ใน DM</blockquote>\n\n'
            f'{_E["trophy"]} <b>สคริปต์วิดีโอเดโม (~2–3 นาที)</b>\n'
            f'<blockquote>1) <b>/start</b> — ปุ่มหน้าแรกและภาษา\n'
            f'2) ถามด้วย<b>เสียง</b> เช่น ราคา TON — ข้อความ + ตอบเสียง\n'
            f'3) <b>สแกน</b> หรือ <b>เทรนด์</b> — ตัวอย่างสั้นๆ\n'
            f'4) ใน<b>กลุ่ม</b>: <code>@BotUsername 0.01 3</code> — ส่งการ์ด → แตะ <b>Open Red Packet</b> → บอท DM → วาง <code>UQ…</code> / <code>EQ…</code> → สำเร็จ\n'
            f'5) (ทางเลือก) <b>โหมดแปล</b> สั้นๆ</blockquote>'
        ),
        "voice_not_recognized": "⚠️ ไม่สามารถจดจำเสียงได้",
        "btn_redpacket": "🧧 ซองอั่งเปา",
        "ask_redpacket": f'{_E["money"]}  <b>ซองอั่งเปา</b>\n\n<blockquote><b>แชทส่วนตัว (แอดมิน):</b> ตอบที่นี่\n<code>จำนวน ชิ้น [lucky|fixed] [usdt]</code>\nเช่น <code>1 5</code>, <code>10 3 usdt</code>\n\n<b>กลุ่มใดก็ได้:</b> พิมพ์ <code>@ชื่อบอท 1 5</code> → เลือกการ์ดรูปปก → <b>Open Red Packet</b> เปิดบอท → ส่งที่อยู่ TON เพื่อรับ ครั้งแรกจะจำที่อยู่</blockquote>',
        "redpacket_claim_prompt": "🧧 <b>ซองอั่งเปา</b>\n\nส่งที่อยู่ TON (<code>EQ...</code> หรือ <code>UQ...</code>)",
        "redpacket_success": "✅ ได้รับ {amount}!",
        "redpacket_failed": "❌ ผิดพลาด：{err}",
        "redpacket_expired": "⏰ หมดอายุ",
        "redpacket_no_slots": "😔 แจกจ่ายแล้ว",
        "redpacket_already_claimed": "รับแล้ว",
        "redpacket_mcp_required": "⚠️ ต้องมี MCP",
        "redpacket_created": "🧧 สร้างแล้ว!\n\nลิงก์:\n{link}\n\n{total} {asset} × {count}",
        "redpacket_group_card": "🧧 <b>ซองอั่งเปา</b>\n\n{total} {asset} × {count}\n{claimed}/{count}",
        "redpacket_all_claimed": "🧧 <b>ซองอั่งเปา</b>\n\n{total} {asset} × {count}\n✅ แจกจ่ายแล้ว",
        "redpacket_btn_open": "🧧 เปิด",
        "redpacket_claim_prompt_short": "ไปที่ DM ส่งที่อยู่ TON",
        "redpacket_inline_title": "🧧 ซองอั่งเปา",
        "redpacket_inline_desc": "แชร์ TON ซองอั่งเปา",
    },
}


def _t(context, key: str) -> str:
    lang = (context.user_data or {}).get("lang", "en")
    texts = LANG_TEXTS.get(lang, LANG_TEXTS["en"])
    if key in texts:
        return texts[key]
    return LANG_TEXTS["en"].get(key, key)


def _heard_reply_caption(transcribed: str, reply: str, lang: str, *, translate_only: bool = False) -> str:
    """Banner caption: show what was heard + reply/translation (HTML). Telegram limit 1024."""
    l1 = {"en": "Heard", "zh": "聽到", "yue": "聽到", "ru": "Распознано", "ja": "聞き取り", "ko": "인식", "th": "รับรู้"}.get(lang, "Heard")
    if translate_only:
        l2 = {"en": "Translation", "zh": "译文", "yue": "翻譯", "ru": "Перевод", "ja": "翻訳", "ko": "번역", "th": "แปล"}.get(lang, "Translation")
    else:
        l2 = {"en": "Reply", "zh": "回覆", "yue": "回覆", "ru": "Ответ", "ja": "返答", "ko": "답변", "th": "ตอบ"}.get(lang, "Reply")
    t_raw = transcribed or ""
    r_raw = reply or ""
    while True:
        t = html.escape(t_raw)
        r = html.escape(r_raw)
        body = f"<b>🎙 {l1}</b>\n{t}\n\n<b>💬 {l2}</b>\n{r}"
        if len(body) <= 1024:
            return body
        if len(t_raw) <= 8 and len(r_raw) <= 8:
            return body[:1020] + "…"
        if len(t_raw) >= len(r_raw):
            t_raw = t_raw[:-80] if len(t_raw) > 80 else t_raw[:-1]
        else:
            r_raw = r_raw[:-80] if len(r_raw) > 80 else r_raw[:-1]


def _main_buttons(context, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(_t(context, "btn_howto"), callback_data="act_howto"),
            InlineKeyboardButton(_t(context, "btn_translate"), callback_data="act_translate"),
            InlineKeyboardButton(_t(context, "btn_lang"), callback_data="act_lang"),
        ],
        [
            InlineKeyboardButton(_t(context, "btn_scan"), callback_data="act_scan"),
            InlineKeyboardButton(_t(context, "btn_trending"), callback_data="act_trending"),
        ],
        [
            InlineKeyboardButton(_t(context, "btn_balance"), callback_data="act_balance"),
            InlineKeyboardButton(_t(context, "btn_tx"), callback_data="act_tx"),
        ],
        [
            InlineKeyboardButton(_t(context, "btn_swap"), callback_data="act_swap"),
            InlineKeyboardButton(_t(context, "btn_search"), callback_data="act_search"),
        ],
        [
            InlineKeyboardButton(_t(context, "btn_nfts"), callback_data="act_nfts"),
            InlineKeyboardButton(_t(context, "btn_dns"), callback_data="act_dns"),
        ],
        [
            InlineKeyboardButton(_t(context, "btn_redpacket"), callback_data="act_redpacket"),
        ],
    ]
    if user_id is not None and user_id in BOT_ADMIN_IDS:
        rows.append([InlineKeyboardButton("🔐 後台", callback_data="act_admin")])
    return InlineKeyboardMarkup(rows)


def _back_button(context) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(_t(context, "btn_back"), callback_data="act_home")],
    ])


async def _update_group_cards(context, packet_id: str, packet: dict, bot_username: str):
    """Update all tracked group/inline messages for a red packet after a claim."""
    claimed = get_claim_count(packet_id)
    reserved = get_reserved_claim_count(packet_id)
    count = packet["share_count"]
    asset = packet.get("asset_type") or "TON"
    total = packet["total_amount_real"]
    fully_claimed = claimed >= count
    slots_open = reserved < count

    if fully_claimed:
        card_text = _t(context, "redpacket_all_claimed").format(total=total, asset=asset, count=count)
        caption = f"🧧 <b>Red Packet</b>  ·  {total} {asset} × {count}\n✅ All claimed!\n\nPowered by <b>TON TE AI</b>"
        markup = InlineKeyboardMarkup([])
    else:
        card_text = _t(context, "redpacket_group_card").format(total=total, asset=asset, count=count, claimed=claimed)
        caption = f"🧧 <b>Red Packet</b>  ·  {total} {asset} × {count}\n{claimed}/{count} claimed\n\nPowered by <b>TON TE AI</b>"
        if slots_open:
            claim_url = f"https://t.me/{bot_username}?start={packet_id}"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("🧧 Open Red Packet", url=claim_url)]])
        else:
            markup = InlineKeyboardMarkup([])

    for m in get_group_messages(packet_id):
        try:
            if m["inline_message_id"]:
                await context.bot.edit_message_caption(
                    inline_message_id=m["inline_message_id"],
                    caption=caption, parse_mode="HTML", reply_markup=markup,
                )
            elif m["chat_id"] and m["message_id"]:
                await context.bot.edit_message_text(
                    chat_id=m["chat_id"], message_id=m["message_id"],
                    text=card_text, parse_mode="HTML", reply_markup=markup,
                )
        except Exception as e:
            log.debug("Failed to update group msg: %s", e)

    if fully_claimed:
        clear_group_messages(packet_id)


def _urls_to_buttons(text: str) -> tuple:
    """Extract URLs from text, replace URL lines with inline buttons.
    Returns (clean_text, list_of_InlineKeyboardButton or empty list).
    """
    lines = text.split("\n")
    clean_lines = []
    buttons = []

    for line in lines:
        m = _URL_RE.search(line)
        if not m:
            clean_lines.append(line)
            continue
        url = m.group(1)
        label = _URL_RE.sub("", line).strip()
        label = label.rstrip(":").strip()
        for ch in "🔗📋🔎":
            label = label.replace(ch, "")
        label = label.strip()
        if not label or len(label) < 2:
            domain = urlparse(url).netloc.replace("www.", "")
            label = domain
        buttons.append(InlineKeyboardButton(label, url=url))

    clean_text = "\n".join(clean_lines).rstrip()
    while "\n\n\n" in clean_text:
        clean_text = clean_text.replace("\n\n\n", "\n\n")

    return clean_text, buttons


def _build_reply_markup(url_buttons: list, extra_rows: Optional[list] = None) -> Optional[InlineKeyboardMarkup]:
    """Build InlineKeyboardMarkup from URL buttons (2 per row) + optional extra rows."""
    rows = []
    for i in range(0, len(url_buttons), 2):
        rows.append(url_buttons[i : i + 2])
    if extra_rows:
        rows.extend(extra_rows)
    return InlineKeyboardMarkup(rows) if rows else None


def _voice_buttons_row(msg_id: int, lang: str = "en") -> list:
    label = {"zh": "🔊 讀出", "yue": "🔊 讀出", "ru": "🔊 Озвучить", "ja": "🔊 読み上げ", "ko": "🔊 읽기", "th": "🔊 อ่าน"}.get(lang, "🔊 Read")
    return [InlineKeyboardButton(label, callback_data=f"voice_{msg_id}")]


def _result_markup(context, msg_id: int, url_buttons: list = None) -> InlineKeyboardMarkup:
    """Standard result markup: url buttons + voice + home."""
    lang = (context.user_data or {}).get("lang", "en")
    voice_label = {"zh": "🔊 讀出", "yue": "🔊 讀出", "ru": "🔊 Озвучить", "ja": "🔊 読み上げ", "ko": "🔊 읽기", "th": "🔊 อ่าน"}.get(lang, "🔊 Read")
    home_label = {"zh": "🏠 主頁", "yue": "🏠 主頁", "ru": "🏠 Главная", "ja": "🏠 ホーム", "ko": "🏠 홈", "th": "🏠 หน้าหลัก"}.get(lang, "🏠 Home")
    bottom = [
        InlineKeyboardButton(voice_label, callback_data=f"voice_{msg_id}"),
        InlineKeyboardButton(home_label, callback_data="act_home"),
    ]
    return _build_reply_markup(url_buttons or [], extra_rows=[bottom])


def _lang_buttons(context) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇨🇳 國語", callback_data="lang_zh"),
            InlineKeyboardButton("🇭🇰 粵語", callback_data="lang_yue"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru"),
            InlineKeyboardButton("🇯🇵 日本語", callback_data="lang_ja"),
            InlineKeyboardButton("🇰🇷 한국어", callback_data="lang_ko"),
            InlineKeyboardButton("🇹🇭 ไทย", callback_data="lang_th"),
        ],
        [InlineKeyboardButton(_t(context, "btn_back"), callback_data="act_home")],
    ])


async def _edit_caption_or_text(query, text: str, **kwargs):
    """Edit message caption if it's a photo message, otherwise edit text."""
    if query.message and query.message.photo:
        await query.edit_message_caption(caption=text, **kwargs)
    else:
        await query.edit_message_text(text=text, **kwargs)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else 0
    user_id = update.effective_user.id if update.effective_user else 0
    now = time.time()
    prev = _START_DEBOUNCE.get(chat_id, 0)
    if prev > now - 10:
        try:
            await update.message.delete()
        except Exception:
            pass
        return
    _START_DEBOUNCE[chat_id] = now

    try:
        await update.message.delete()
    except Exception:
        pass

    # Deep link: /start rp_xxx -> red packet claim flow
    msg_text = (update.message.text or "").strip()
    parts = msg_text.split(None, 1)
    if len(parts) >= 2 and parts[1].startswith("rp_"):
        packet_id = parts[1].split()[0].strip()[:64]
        p = get_packet(packet_id)
        bot_me = await context.bot.get_me()
        bot_uname = bot_me.username or "TON_TE_Ai_bot"
        if not p:
            text = _t(context, "redpacket_expired")
        elif has_claimed(packet_id, user_id):
            text = _t(context, "redpacket_already_claimed")
        elif get_reserved_claim_count(packet_id) >= p["share_count"]:
            text = _t(context, "redpacket_no_slots")
        else:
            saved_addr = get_saved_address(user_id)
            if saved_addr:
                result = allocate_claim(packet_id, user_id, saved_addr)
                if result:
                    claim_idx, amount_raw, asset_type = result
                    comment = f"rp:{packet_id}:{claim_idx}"
                    if asset_type == "USDT":
                        amount_display = raw_to_usdt(amount_raw)
                        mcp_out, ok = await mcp_send_jetton(saved_addr, USDT_JETTON, amount_raw, comment=comment)
                    else:
                        amount_display = nanoton_to_ton(amount_raw)
                        mcp_out, ok = await mcp_send_ton(saved_addr, amount_display, comment=comment)
                    if ok:
                        set_claim_tx_result(packet_id, claim_idx, mcp_out)
                        unit = "USDT" if asset_type == "USDT" else "TON"
                        text = _t(context, "redpacket_success").format(amount=f"{amount_display} {unit}")
                        await _update_group_cards(context, packet_id, p, bot_uname)
                    else:
                        release_failed_claim(packet_id, claim_idx)
                        text = _t(context, "redpacket_failed").format(err=mcp_out)
                        await _update_group_cards(context, packet_id, p, bot_uname)
                    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=_main_buttons(context, user_id))
                    return
                text = _t(context, "redpacket_no_slots")
            else:
                context.user_data["pending_action"] = "redpacket_claim"
                context.user_data["redpacket_packet_id"] = packet_id
                text = _t(context, "redpacket_claim_prompt")
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=_back_button(context))
        return

    old_banner = (context.user_data or {}).get("_banner_msg_id")
    if old_banner:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=old_banner)
        except Exception:
            pass

    context.user_data.pop("translate_mode", None)

    lang = (context.user_data or {}).get("lang", "en")
    text = LANG_TEXTS[lang]["welcome"]
    if BANNER_PATH.exists():
        with open(BANNER_PATH, "rb") as f:
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=InputFile(f),
                caption=text,
                parse_mode="HTML",
                reply_markup=_main_buttons(context, user_id),
            )
    else:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=_main_buttons(context, user_id),
        )
    context.user_data["_banner_msg_id"] = sent.message_id

    _WELCOME_VOICE = {
        "en": "Welcome to TON TE AI. I am your personal voice assistant for the TON blockchain. You can tap any button below to get started, type a question in your own language, or simply hold the microphone and speak to me. I support seven languages and I will always reply with voice. No typing required.",
        "yue": "歡迎使用 TON TE AI。我係你嘅 TON 區塊鏈語音助手。你可以撳下面任何一個掣開始，用你自己嘅語言打字問我，或者直接按住個咪講嘢。我支援七種語言，每次都會語音回覆你。唔使打字都完全用到。",
        "zh": "欢迎使用 TON TE AI。我是你的 TON 区块链语音助手。你可以点击下方任意按钮开始，用你的语言打字提问，或者按住麦克风直接说话。我支持七种语言，每次都会语音回复你。不需要打字也能使用。",
        "ru": "Добро пожаловать в TON TE AI. Я ваш голосовой ассистент для блокчейна TON. Нажмите любую кнопку ниже, напишите вопрос на своём языке или просто удерживайте микрофон и говорите. Я поддерживаю семь языков и всегда отвечаю голосом.",
        "ja": "TON TE AI へようこそ。私はTONブロックチェーンの音声アシスタントです。下のボタンをタップするか、お好きな言語で入力するか、マイクを長押しして話しかけてください。七つの言語に対応しており、毎回音声でお答えします。",
        "ko": "TON TE AI에 오신 것을 환영합니다. 저는 TON 블록체인 음성 어시스턴트입니다. 아래 버튼을 탭하거나, 원하는 언어로 입력하거나, 마이크를 길게 눌러 말씀하세요. 7개 언어를 지원하며 항상 음성으로 답변합니다.",
        "th": "ยินดีต้อนรับสู่ TON TE AI ฉันคือผู้ช่วยเสียงสำหรับบล็อกเชน TON คุณสามารถแตะปุ่มด้านล่าง พิมพ์คำถามในภาษาของคุณ หรือกดไมค์ค้างแล้วพูดได้เลย รองรับเจ็ดภาษา ตอบด้วยเสียงทุกครั้ง",
    }
    asyncio.create_task(_auto_voice_reply(
        context.bot, chat_id, _WELCOME_VOICE.get(lang, _WELCOME_VOICE["en"]), lang, delay=60, full=True,
    ))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""

    if data == "act_admin":
        u = query.from_user
        if not u or u.id not in BOT_ADMIN_IDS:
            try:
                await query.answer("無權限", show_alert=True)
            except Exception:
                pass
            return
        try:
            await query.answer()
        except Exception:
            pass
        if query.message:
            admin_uid = u.id
            await send_admin_dashboard(context, query.message, admin_user_id=admin_uid)
        return

    try:
        await query.answer()
    except Exception:
        pass

    if data.startswith("voice_"):
        parts = data.split("_", 1)
        if len(parts) >= 2:
            msg_id_str = parts[1]
            try:
                msg_id = int(msg_id_str)
            except ValueError:
                msg_id = None
            if msg_id is not None:
                voice_cache = (context.user_data or {}).get("_voice_cache", {})
                cached_text = voice_cache.get(msg_id)
                if cached_text:
                    v_sent = (context.user_data or {}).get("_voice_sent", {})
                    if v_sent.get(msg_id, 0) > time.time() - 15:
                        return
                    try:
                        _, url_buttons = _urls_to_buttons(cached_text)
                        markup = _build_reply_markup(url_buttons) if url_buttons else None
                        await context.bot.edit_message_reply_markup(
                            chat_id=query.message.chat_id,
                            message_id=query.message.message_id,
                            reply_markup=markup,
                        )
                    except Exception:
                        pass
                    v_lang = (context.user_data or {}).get("lang", "en")
                    short = _voice_summary(cached_text)
                    voice_path = await _text_to_voice(short, v_lang, use_detection=False)
                    if voice_path:
                        try:
                            with open(voice_path, "rb") as vf:
                                await query.message.reply_voice(voice=InputFile(vf))
                            v_sent = context.user_data.setdefault("_voice_sent", {})
                            v_sent[msg_id] = time.time()
                            while len(v_sent) > 10:
                                old = min(v_sent, key=v_sent.get)
                                del v_sent[old]
                        except Exception:
                            pass
                        try:
                            os.unlink(voice_path)
                        except OSError:
                            pass
        return

    # Legacy rp_open: callback (kept for backward compat with old cards)
    if data.startswith("rp_open:"):
        packet_id = data.split(":", 1)[1]
        bot_me = await context.bot.get_me()
        link = f"https://t.me/{bot_me.username}?start={packet_id}"
        await query.answer(f"Tap here to claim → {link}", show_alert=True, url=link)
        return

    # Back to home
    if data == "act_home":
        context.user_data.pop("translate_mode", None)
        lang = (context.user_data or {}).get("lang", "en")
        uid = query.from_user.id if query.from_user else None
        await _edit_caption_or_text(
            query, LANG_TEXTS[lang]["welcome"],
            parse_mode="HTML", reply_markup=_main_buttons(context, uid),
        )
        return

    # Language selection
    if data.startswith("lang_"):
        lang = data.split("_", 1)[1]
        context.user_data["lang"] = lang
        welcome = LANG_TEXTS[lang]["welcome"]
        lang_set = LANG_TEXTS[lang]["lang_set"]
        uid = query.from_user.id if query.from_user else None
        await _edit_caption_or_text(
            query, welcome + "\n\n" + lang_set,
            parse_mode="HTML", reply_markup=_main_buttons(context, uid),
        )
        return

    if data == "act_lang":
        await _edit_caption_or_text(
            query, _t(context, "lang_prompt"),
            parse_mode="HTML", reply_markup=_lang_buttons(context),
        )
        return

    if data == "act_howto":
        await _edit_caption_or_text(
            query, _t(context, "ask_howto"),
            parse_mode="HTML", reply_markup=_back_button(context),
        )
        return

    if data == "act_translate":
        context.user_data["translate_mode"] = True
        context.user_data.pop("pending_action", None)
        await _edit_caption_or_text(
            query, _t(context, "ask_translate"),
            parse_mode="HTML", reply_markup=_back_button(context),
        )
        return

    if data == "act_trending":
        context.user_data.pop("translate_mode", None)
        await _edit_caption_or_text(
            query, "⏳ Loading...",
            reply_markup=_back_button(context),
        )
        from ton_tools import _fetch_all_assets, _format_price
        assets = await _fetch_all_assets()
        defaults = [a for a in assets if a.get("default_symbol")]
        defaults.sort(key=lambda a: a.get("popularity_index", 0), reverse=True)
        tokens = defaults[1:21]
        rows = []
        for a in tokens:
            sym = a.get("symbol", "?")
            name = a.get("display_name", "") or sym
            price_str = _format_price(a.get("dex_price_usd") or a.get("third_party_price_usd"))
            label = f"{sym} — {price_str}" if price_str else sym
            addr = a.get("contract_address", "")
            rows.append([InlineKeyboardButton(label, callback_data=f"tscan:{addr[:50]}")])
        lang = (context.user_data or {}).get("lang", "en")
        home_label = {"zh": "🏠 主頁", "yue": "🏠 主頁", "ru": "🏠 Главная", "ja": "🏠 ホーム", "ko": "🏠 홈", "th": "🏠 หน้าหลัก"}.get(lang, "🏠 Home")
        rows.append([InlineKeyboardButton(home_label, callback_data="act_home")])
        title = {"zh": "🔥 TON 熱門代幣 — 點擊查看詳情", "yue": "🔥 TON 熱門代幣 — 撳入睇詳情", "ru": "🔥 Популярные токены TON", "ja": "🔥 TON トレンドトークン", "ko": "🔥 TON 인기 토큰", "th": "🔥 TON โทเค็นยอดนิยม"}.get(lang, "🔥 Trending TON Tokens — tap to scan")
        await _edit_caption_or_text(query, title, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("tscan:"):
        context.user_data.pop("translate_mode", None)
        addr = data.split(":", 1)[1]
        await _edit_caption_or_text(query, "⏳ Scanning...", reply_markup=_back_button(context))
        from ton_tools import get_jetton_info
        try:
            result, ok = await get_jetton_info(addr)
        except Exception as e:
            result = f"Error: {e}"
        result = result.replace("**", "").replace("*", "")
        clean_text, url_buttons = _urls_to_buttons(result)
        if len(clean_text) > 1024:
            clean_text = clean_text[:1021] + "..."
        banner_id = (context.user_data or {}).get("_banner_msg_id")
        mid = banner_id or (query.message.message_id if query.message else 0)
        markup = _result_markup(context, mid, url_buttons)
        voice_cache = (context.user_data or {}).setdefault("_voice_cache", {})
        voice_cache[mid] = clean_text
        await _edit_caption_or_text(query, clean_text, reply_markup=markup)
        return

    if data == "act_redpacket":
        context.user_data.pop("translate_mode", None)
        uid = update.effective_user.id if update.effective_user else 0
        if REDPACKET_OWNER_IDS and uid not in REDPACKET_OWNER_IDS:
            await _edit_caption_or_text(query, "🔒 Only admin can create red packets.", parse_mode="HTML", reply_markup=_back_button(context))
            return
        if not is_mcp_available():
            await _edit_caption_or_text(query, _t(context, "redpacket_mcp_required"), parse_mode="HTML", reply_markup=_back_button(context))
            return
        context.user_data["pending_action"] = "redpacket_create"
        await _edit_caption_or_text(
            query, _t(context, "ask_redpacket"),
            parse_mode="HTML", reply_markup=_back_button(context),
        )
        return

    if data.startswith("act_tx_addr:"):
        context.user_data.pop("translate_mode", None)
        addr = data.split(":", 1)[1]
        await _edit_caption_or_text(query, "⏳ Loading...", reply_markup=_back_button(context))
        try:
            result, ok = await get_transactions(addr, limit=10)
        except Exception as e:
            result = f"Error: {e}"
        result = result.replace("**", "").replace("*", "")
        clean_text, url_buttons = _urls_to_buttons(result)
        if len(clean_text) > 1024:
            clean_text = clean_text[:1021] + "..."
        banner_id = (context.user_data or {}).get("_banner_msg_id")
        mid = banner_id or (query.message.message_id if query.message else 0)
        markup = _result_markup(context, mid, url_buttons)
        voice_cache = (context.user_data or {}).setdefault("_voice_cache", {})
        voice_cache[mid] = clean_text
        await _edit_caption_or_text(query, clean_text, reply_markup=markup)
        return

    action_map = {
        "act_scan": "ask_scan",
        "act_search": "ask_search",
        "act_balance": "ask_balance",
        "act_tx": "ask_tx",
        "act_dns": "ask_dns",
        "act_nfts": "ask_nfts",
        "act_swap": "ask_swap",
    }
    key = action_map.get(data)
    if key:
        context.user_data.pop("translate_mode", None)
        context.user_data["pending_action"] = data
        await _edit_caption_or_text(
            query, _t(context, key),
            parse_mode="HTML", reply_markup=_back_button(context),
        )


async def _auto_voice_reply(bot, chat_id: int, text: str, lang: str, delay: int = 30, full: bool = False):
    """Generate TTS, send voice, auto-delete after delay. full=True skips truncation."""
    short = text if full else _voice_summary(text)
    voice_path = await _text_to_voice(short, lang, use_detection=False)
    if not voice_path:
        return
    try:
        with open(voice_path, "rb") as vf:
            data = vf.read()
        if voice_path.endswith(".mp3"):
            ogg_path = voice_path.replace(".mp3", "_v.ogg")
            r = await asyncio.to_thread(
                lambda: __import__("subprocess").run(
                    ["ffmpeg", "-y", "-i", voice_path, "-c:a", "libopus",
                     "-b:a", "64k", "-vbr", "on", "-application", "voip", ogg_path],
                    capture_output=True, timeout=30,
                )
            )
            if r.returncode == 0 and os.path.isfile(ogg_path) and os.path.getsize(ogg_path) > 100:
                with open(ogg_path, "rb") as of:
                    data = of.read()
                try:
                    os.unlink(ogg_path)
                except OSError:
                    pass
        import io as _io
        sent = await bot.send_voice(chat_id=chat_id, voice=InputFile(_io.BytesIO(data), filename="voice.ogg"))
        asyncio.create_task(_auto_delete_voice(bot, chat_id, sent.message_id, delay))
    except Exception as e:
        log.warning("_auto_voice_reply send failed: %s", e)
        if "forbidden" in str(e).lower():
            tip = {
                "en": "⚠️ Voice messages are blocked.\n\n<b>To enable voice replies:</b>\n<blockquote>Telegram → Settings → Privacy &amp; Security → Voice Messages → set to <b>Everybody</b></blockquote>",
                "yue": "⚠️ 語音訊息被封鎖。\n\n<b>開啟語音回覆：</b>\n<blockquote>Telegram → 設定 → 私隱與安全 → 語音訊息 → 設為<b>所有人</b></blockquote>",
                "zh": "⚠️ 语音消息被禁止。\n\n<b>开启语音回复：</b>\n<blockquote>Telegram → 设置 → 隐私与安全 → 语音消息 → 设为<b>所有人</b></blockquote>",
                "ru": "⚠️ Голосовые сообщения заблокированы.\n\n<b>Включить:</b>\n<blockquote>Telegram → Настройки → Конфиденциальность → Голосовые сообщения → <b>Все</b></blockquote>",
                "ja": "⚠️ 音声メッセージがブロックされています。\n\n<b>有効にする：</b>\n<blockquote>Telegram → 設定 → プライバシー → 音声メッセージ → <b>全員</b></blockquote>",
                "ko": "⚠️ 음성 메시지가 차단되었습니다.\n\n<b>활성화：</b>\n<blockquote>Telegram → 설정 → 개인정보 → 음성 메시지 → <b>모두</b></blockquote>",
                "th": "⚠️ ข้อความเสียงถูกบล็อก\n\n<b>เปิดใช้งาน:</b>\n<blockquote>Telegram → ตั้งค่า → ความเป็นส่วนตัว → ข้อความเสียง → <b>ทุกคน</b></blockquote>",
            }
            msg_text = tip.get(lang, tip["en"])
            try:
                hint = await bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
                asyncio.create_task(_auto_delete_voice(bot, chat_id, hint.message_id, 15))
            except Exception:
                pass
    finally:
        try:
            os.unlink(voice_path)
        except OSError:
            pass


async def _auto_delete_voice(bot, chat_id: int, msg_id: int, delay: int = 30):
    """Delete a voice message after delay seconds."""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice: delete user msg → edit banner in-place → auto voice reply → auto-delete voice."""
    msg = update.message
    if not msg:
        return

    lang = (context.user_data or {}).get("lang", "en")
    chat_id = msg.chat_id
    user_data = context.user_data
    banner_id = user_data.get("_banner_msg_id")

    # Delete user's voice message immediately for clean UI
    try:
        await msg.delete()
    except Exception:
        pass

    # Show "listening..." in banner
    if banner_id:
        listen_text = {"zh": "🎙 聆聽中…", "yue": "🎙 聽緊…", "ru": "🎙 Слушаю…", "ja": "🎙 聞いています…", "ko": "🎙 듣는 중…", "th": "🎙 กำลังฟัง…"}.get(lang, "🎙 Listening…")
        try:
            await context.bot.edit_message_caption(chat_id=chat_id, message_id=banner_id, caption=listen_text)
        except Exception:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=banner_id, text=listen_text)
            except Exception:
                pass

    await msg.chat.send_action("typing")

    if msg.voice:
        tg_file = await msg.voice.get_file()
        suffix = ".ogg"
    elif msg.audio:
        tg_file = await msg.audio.get_file()
        suffix = ".mp3"
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio"):
        tg_file = await msg.document.get_file()
        suffix = ".ogg"
    else:
        return

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.close()
    try:
        await tg_file.download_to_drive(tmp.name)
    except Exception:
        return

    transcribed = await _voice_to_text(tmp.name, lang)
    if not transcribed:
        fail_text = _t(context, "voice_not_recognized")
        uid = msg.from_user.id if msg.from_user else None
        if banner_id:
            try:
                await context.bot.edit_message_caption(chat_id=chat_id, message_id=banner_id, caption=fail_text, reply_markup=_main_buttons(context, uid))
            except Exception:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=banner_id, text=fail_text, reply_markup=_main_buttons(context, uid))
                except Exception:
                    pass
        return

    # Translate-only: no TON / AI streaming
    if user_data.get("translate_mode"):
        translated = await translate_to_ui_language(transcribed, user_data)
        caption = _heard_reply_caption(transcribed, translated, lang, translate_only=True)
        markup = _result_markup(context, banner_id, []) if banner_id else None
        if banner_id:
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=banner_id,
                    caption=caption, parse_mode="HTML", reply_markup=markup,
                )
            except Exception:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=banner_id,
                        text=caption, parse_mode="HTML", reply_markup=markup,
                    )
                except Exception:
                    pass
            user_data.setdefault("_voice_cache", {})[banner_id] = translated
        else:
            sent = await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
            user_data.setdefault("_voice_cache", {})[sent.message_id] = translated
            voice_row = _voice_buttons_row(sent.message_id, lang)
            new_markup = _build_reply_markup([], extra_rows=[voice_row])
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=sent.message_id, reply_markup=new_markup,
                )
            except Exception:
                pass
        asyncio.create_task(_auto_voice_reply(context.bot, chat_id, translated, lang, delay=45))
        return

    # Show transcription in banner (normal AI flow)
    if banner_id:
        try:
            await context.bot.edit_message_caption(chat_id=chat_id, message_id=banner_id, caption=f"🎙 {transcribed}\n\n⏳ …")
        except Exception:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=banner_id, text=f"🎙 {transcribed}\n\n⏳ …")
            except Exception:
                pass

    lang_hint = {
        "en": " (The UI language is English. Reply in English only, even if my speech was Cantonese or Chinese.)",
        "zh": " (reply in 简体中文)",
        "yue": " (reply in 廣東話)", "ru": " (reply in русский)",
        "ja": " (reply in 日本語)", "ko": " (reply in 한국어)",
        "th": " (reply in ภาษาไทย)",
    }
    ai_text = transcribed + lang_hint.get(lang, "")
    draft_id = int(time.time())
    last_draft_t = 0.0
    final_text = ""

    try:
        async for event in stream_process_message(ai_text, user_data):
            etype = event[0]
            if etype == "text":
                partial = event[1]
                now = time.time()
                if now - last_draft_t > 0.4:
                    await _send_draft(chat_id, draft_id, partial)
                    last_draft_t = now
            elif etype == "image":
                final_text = event[2]
            elif etype == "done":
                final_text = event[1]
    except Exception as e:
        log.exception("AI voice failed: %s", e)
        final_text = f"Sorry, something went wrong: {e}"

    if not final_text:
        final_text = "Send me a TON address or ask anything about TON."

    final_text = final_text.replace("**", "").replace("*", "")
    final_text = _IMG_TAG_RE.sub("", final_text)
    clean_text, url_buttons = _urls_to_buttons(final_text)

    # Update banner in-place (no new messages): heard + reply
    if banner_id:
        caption = _heard_reply_caption(transcribed, clean_text, lang, translate_only=False)
        markup = _result_markup(context, banner_id, url_buttons)
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id, message_id=banner_id,
                caption=caption, parse_mode="HTML", reply_markup=markup,
            )
        except Exception:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=banner_id,
                    text=caption, parse_mode="HTML", reply_markup=markup,
                )
            except Exception:
                pass
        voice_cache = user_data.setdefault("_voice_cache", {})
        voice_cache[banner_id] = clean_text
    else:
        cap = _heard_reply_caption(transcribed, clean_text, lang, translate_only=False)
        if len(cap) > 4000:
            cap = cap[:3997] + "…"
        sent = await context.bot.send_message(chat_id=chat_id, text=cap, parse_mode="HTML")
        voice_cache = user_data.setdefault("_voice_cache", {})
        voice_cache[sent.message_id] = clean_text
        voice_row = _voice_buttons_row(sent.message_id, lang)
        new_markup = _build_reply_markup(url_buttons, extra_rows=[voice_row])
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=sent.message_id, reply_markup=new_markup,
            )
        except Exception:
            pass

    # Auto send voice reply + auto-delete after 30s
    asyncio.create_task(_auto_voice_reply(context.bot, chat_id, clean_text, lang, delay=45))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.edited_message
    text = (msg.text or "").strip()
    if not text:
        return

    user_data = context.user_data
    lang = user_data.get("lang", "en")
    banner_id = user_data.get("_banner_msg_id")
    user_message = text
    chat_id = msg.chat_id
    reply_uid = msg.from_user.id if msg.from_user else None

    # P1: Group red packet card — when someone shares a deep link in a group
    chat_type = (msg.chat.type if msg.chat else None) or ""
    if chat_type in ("group", "supergroup"):
        m = _REDPACKET_LINK_RE.search(text)
        if m:
            packet_id = m.group(1)
            p = get_packet(packet_id)
            if p:
                claimed = get_claim_count(packet_id)
                reserved = get_reserved_claim_count(packet_id)
                total = p["total_amount_real"]
                count = p["share_count"]
                asset = p.get("asset_type") or "TON"
                bot_me = await context.bot.get_me()
                bot_uname = bot_me.username or "TON_TE_Ai_bot"
                mode_label = "🎲" if p.get("mode") == "lucky" else "="
                if claimed >= count:
                    cap = f"🧧 <b>Red Packet</b>  ·  {total} {asset} × {count} {mode_label}\n✅ All claimed!\n\nPowered by <b>TON TE AI</b>"
                    markup = InlineKeyboardMarkup([])
                else:
                    cap = f"🧧 <b>Red Packet</b>  ·  {total} {asset} × {count} {mode_label}\n{claimed}/{count} claimed\n\nPowered by <b>TON TE AI</b>"
                    if reserved < count:
                        claim_url = f"https://t.me/{bot_uname}?start={packet_id}"
                        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🧧 Open Red Packet", url=claim_url)]])
                    else:
                        markup = InlineKeyboardMarkup([])
                rp_img = Path(__file__).parent / "data" / "redpacket_card.png"
                if rp_img.exists():
                    with open(rp_img, "rb") as f:
                        reply = await msg.reply_photo(photo=InputFile(f), caption=cap, parse_mode="HTML", reply_markup=markup)
                else:
                    reply = await msg.reply_text(cap, parse_mode="HTML", reply_markup=markup)
                register_group_message(packet_id, chat_id, reply.message_id)
                return

    await msg.chat.send_action("typing")

    # Translate-only mode (before explorer/address rewrites and pending flows)
    if user_data.get("translate_mode"):
        user_data.pop("pending_action", None)
        translated = await translate_to_ui_language(text, user_data)
        caption = _heard_reply_caption(user_message, translated, lang, translate_only=True)
        _START_DEBOUNCE[chat_id] = time.time()
        try:
            await msg.delete()
        except Exception:
            pass
        markup = _result_markup(context, banner_id, []) if banner_id else None
        edited = False
        if banner_id:
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=banner_id,
                    caption=caption, parse_mode="HTML", reply_markup=markup,
                )
                edited = True
            except Exception:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=banner_id,
                        text=caption, parse_mode="HTML", reply_markup=markup,
                    )
                    edited = True
                except Exception:
                    pass
        if edited:
            user_data.setdefault("_voice_cache", {})[banner_id] = translated
            asyncio.create_task(_auto_voice_reply(context.bot, chat_id, translated, lang, delay=45))
            return
        sent = await context.bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML")
        user_data.setdefault("_voice_cache", {})[sent.message_id] = translated
        voice_row = _voice_buttons_row(sent.message_id, lang)
        new_markup = _build_reply_markup([], extra_rows=[voice_row])
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=sent.message_id, reply_markup=new_markup,
            )
        except Exception:
            pass
        asyncio.create_task(_auto_voice_reply(context.bot, chat_id, translated, lang, delay=45))
        return

    pending = user_data.pop("pending_action", None)

    # Red packet claim: user sends TON address
    if pending == "redpacket_claim":
        packet_id = user_data.pop("redpacket_packet_id", None)
        user_id_claim = msg.from_user.id if msg.from_user else 0
        addr = TON_ADDR_RE.search(text)
        addr_str = addr.group(0).strip() if addr else ""
        if not addr_str:
            # Check saved address
            saved = get_saved_address(user_id_claim) if user_id_claim else None
            if saved:
                addr_str = saved
        if not packet_id or not addr_str:
            out = _t(context, "redpacket_claim_prompt") if not addr_str else "Invalid TON address. Send EQ... or UQ..."
            await _edit_banner_or_send(context, chat_id, out, reply_markup=_back_button(context))
            if packet_id:
                user_data["pending_action"] = "redpacket_claim"
                user_data["redpacket_packet_id"] = packet_id
            return
        # Save address for future use
        if user_id_claim:
            save_address(user_id_claim, addr_str)
        result = allocate_claim(packet_id, user_id_claim, addr_str)
        if not result:
            out = _t(context, "redpacket_expired")
            if get_packet(packet_id):
                if has_claimed(packet_id, msg.from_user.id if msg.from_user else 0):
                    out = _t(context, "redpacket_already_claimed")
                elif get_reserved_claim_count(packet_id) >= (get_packet(packet_id) or {}).get("share_count", 0):
                    out = _t(context, "redpacket_no_slots")
            await _edit_banner_or_send(context, chat_id, out, reply_markup=_back_button(context))
            return
        claim_idx, amount_raw, asset_type = result
        comment = f"rp:{packet_id}:{claim_idx}"
        if asset_type == "USDT":
            amount_display = raw_to_usdt(amount_raw)
            mcp_out, ok = await mcp_send_jetton(
                addr_str, USDT_JETTON, amount_raw, comment=comment
            )
        else:
            amount_display = nanoton_to_ton(amount_raw)
            mcp_out, ok = await mcp_send_ton(addr_str, amount_display, comment=comment)
        if ok:
            set_claim_tx_result(packet_id, claim_idx, mcp_out)
            unit = "USDT" if asset_type == "USDT" else "TON"
            out = _t(context, "redpacket_success").format(amount=f"{amount_display} {unit}")
            p = get_packet(packet_id)
            if p:
                bot_me = await context.bot.get_me()
                await _update_group_cards(context, packet_id, p, bot_me.username or "TON_TE_Ai_bot")
        else:
            release_failed_claim(packet_id, claim_idx)
            out = _t(context, "redpacket_failed").format(err=mcp_out)
            p = get_packet(packet_id)
            if p:
                bot_me = await context.bot.get_me()
                await _update_group_cards(context, packet_id, p, bot_me.username or "TON_TE_Ai_bot")
        _START_DEBOUNCE[chat_id] = time.time()
        try:
            await msg.delete()
        except Exception:
            pass
        await _edit_banner_or_send(context, chat_id, out, reply_markup=_main_buttons(context, reply_uid))
        return

    # Red packet create: user sends "amount count [mode] [usdt]"
    if pending == "redpacket_create":
        parts = [p.strip() for p in text.split()]
        total_str = parts[0] if parts else "0"
        count_str = parts[1] if len(parts) > 1 else "1"
        mode = "lucky"
        asset_type = "TON"
        jetton_addr = None
        for i, p in enumerate(parts[2:], start=2):
            if p.lower() == "fixed":
                mode = "fixed"
            elif p.lower() == "lucky":
                mode = "lucky"
            elif p.lower() == "usdt":
                asset_type = "USDT"
                jetton_addr = USDT_JETTON
        try:
            share_count = int(count_str)
        except ValueError:
            share_count = 1
        packet_id, err = create_packet(
            creator_id=msg.from_user.id if msg.from_user else 0,
            total_ton=total_str,
            share_count=share_count,
            mode=mode,
            asset_type=asset_type,
            jetton_addr=jetton_addr,
        )
        if err:
            await _edit_banner_or_send(context, chat_id, f"❌ {err}", reply_markup=_back_button(context))
            user_data["pending_action"] = "redpacket_create"
            return
        bot_username = (await context.bot.get_me()).username or "tonteai_bot"
        link = get_deep_link(packet_id, bot_username)
        asset_label = "USDT" if asset_type == "USDT" else "TON"
        out = _t(context, "redpacket_created").format(link=link, total=total_str, asset=asset_label, count=share_count)
        _START_DEBOUNCE[chat_id] = time.time()
        try:
            await msg.delete()
        except Exception:
            pass
        await _edit_banner_or_send(context, chat_id, out, reply_markup=_main_buttons(context, reply_uid))
        return

    from ton_tools import TON_ADDR_RE as _ADDR_RE
    expl_m = _EXPLORER_ADDR_RE.search(text)
    if expl_m and not _ADDR_RE.search(text.replace(expl_m.group(1), "", 1)):
        text = expl_m.group(1)

    # Direct handler for transactions — bypass AI
    if pending == "act_tx":
        try:
            result, ok = await get_transactions(text.strip(), limit=10)
        except Exception as e:
            result = f"Error: {e}"
        final_text = result
    else:
        lang_hint = {
            "en": " (UI language is English. Reply in English only.)",
            "zh": " (UI preference: Chinese, but always match the user's actual language)",
            "yue": " (UI preference: Cantonese, but always match the user's actual language)",
            "ru": " (UI preference: Russian, but always match the user's actual language)",
            "ja": " (UI preference: Japanese, but always match the user's actual language)",
            "ko": " (UI preference: Korean, but always match the user's actual language)",
            "th": " (UI preference: Thai, but always match the user's actual language)",
        }
        ai_text = text + lang_hint.get(lang, "")

        draft_id = msg.message_id
        last_draft_t = 0.0
        final_text = ""

        try:
            async for event in stream_process_message(ai_text, user_data):
                etype = event[0]
                if etype == "text":
                    partial = event[1]
                    now = time.time()
                    if now - last_draft_t > 0.4:
                        await _send_draft(chat_id, draft_id, partial)
                        last_draft_t = now
                elif etype == "image":
                    final_text = event[2]
                elif etype == "done":
                    final_text = event[1]
        except Exception as e:
            log.exception("AI streaming failed: %s", e)
            final_text = f"Sorry, something went wrong: {e}"

    if not final_text:
        final_text = "Send me a TON address or ask anything about TON."

    final_text = final_text.replace("**", "").replace("*", "")

    img_match = _IMG_TAG_RE.search(final_text)
    image_url = None
    if img_match:
        image_url = img_match.group(1)
        final_text = _IMG_TAG_RE.sub("", final_text)

    clean_text, url_buttons = _urls_to_buttons(final_text)

    _START_DEBOUNCE[chat_id] = time.time()
    try:
        await msg.delete()
    except Exception:
        pass

    if banner_id:
        cap = _heard_reply_caption(user_message, clean_text, lang, translate_only=False)
        markup = _result_markup(context, banner_id, url_buttons)
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id, message_id=banner_id,
                caption=cap, parse_mode="HTML", reply_markup=markup,
            )
            voice_cache = user_data.setdefault("_voice_cache", {})
            voice_cache[banner_id] = clean_text
            while len(voice_cache) > 3:
                old = min(voice_cache.keys()); del voice_cache[old]
            return
        except Exception:
            log.debug("Failed to edit banner, falling back to new message")

    cap = _heard_reply_caption(user_message, clean_text, lang, translate_only=False)
    if len(cap) > 4000:
        cap = cap[:3997] + "…"
    markup = _build_reply_markup(url_buttons) if url_buttons else None
    sent = await context.bot.send_message(chat_id=chat_id, text=cap, parse_mode="HTML", reply_markup=markup)
    voice_cache = user_data.setdefault("_voice_cache", {})
    voice_cache[sent.message_id] = clean_text
    while len(voice_cache) > 3:
        old = min(voice_cache.keys()); del voice_cache[old]
    voice_row = _voice_buttons_row(sent.message_id, lang)
    new_markup = _build_reply_markup(url_buttons, extra_rows=[voice_row])
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=sent.message_id, reply_markup=new_markup,
        )
    except Exception:
        pass


async def _edit_banner_or_send(context, chat_id, text, url_buttons=None, reply_markup=None):
    """Edit banner in-place, or send new message if no banner."""
    user_data = context.user_data or {}
    banner_id = user_data.get("_banner_msg_id")
    if reply_markup is not None:
        markup = reply_markup
    else:
        markup = _result_markup(context, banner_id, url_buttons) if banner_id else _build_reply_markup(url_buttons or [])
    if banner_id:
        caption = text[:1021] + "..." if len(text) > 1024 else text
        try:
            await context.bot.edit_message_caption(chat_id=chat_id, message_id=banner_id, caption=caption, reply_markup=markup)
            return
        except Exception:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=banner_id, text=text[:4096], reply_markup=markup)
                return
            except Exception:
                pass
    await context.bot.send_message(chat_id=chat_id, text=text[:4096], reply_markup=markup)


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ton_tools import get_swap_quote
    if not update.message:
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    args = context.args or []
    token = (args[0].upper() if args else "TON")
    await update.message.chat.send_action("typing")
    try:
        result, ok = await get_swap_quote(token, "USDT", "1")
        out = f"{token}: {result}" if ok else f"Could not fetch price for {token}"
    except Exception as e:
        out = f"Error: {e}"
    await _edit_banner_or_send(context, update.effective_chat.id, out)


async def cmd_trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ton_tools import get_trending_tokens
    if not update.message:
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.message.chat.send_action("typing")
    try:
        result, ok = await get_trending_tokens(limit=10)
        out = result if ok else "Could not fetch trending tokens."
    except Exception as e:
        out = f"Error: {e}"
    out = out.replace("**", "").replace("*", "")
    await _edit_banner_or_send(context, update.effective_chat.id, out)


async def _notify_admins_visitor_ping(
    context: ContextTypes.DEFAULT_TYPE,
    visitor_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
) -> None:
    """Push a short DM to every admin when a non-admin uses the bot (cooldown in track_bot_user)."""
    un = f"@{username}" if username else "—"
    nm = " ".join(x for x in (first_name or "", last_name or "") if x).strip() or "—"
    text = (
        "👆 <b>有人用機械人</b>\n"
        f"<code>{visitor_id}</code> {html.escape(un)} · {html.escape(nm)}"
    )
    for aid in BOT_ADMIN_IDS:
        if aid == visitor_id:
            continue
        try:
            await context.bot.send_message(chat_id=aid, text=text, parse_mode="HTML")
        except Exception as e:
            log.warning("admin visitor notify failed for admin_id=%s: %s", aid, e)


async def track_bot_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log every Telegram user who touches the bot (for /admin list)."""
    u = update.effective_user
    if not u:
        return
    try:
        record_bot_user(u.id, u.username, u.first_name, u.last_name)
    except Exception as e:
        log.debug("track_bot_user: %s", e)
        return

    if u.id not in BOT_ADMIN_IDS:
        now = time.time()
        last = _ADMIN_VISITOR_NOTIFY_LAST.get(u.id, 0.0)
        if now - last >= _ADMIN_VISITOR_NOTIFY_COOLDOWN_SEC:
            _ADMIN_VISITOR_NOTIFY_LAST[u.id] = now
            if len(_ADMIN_VISITOR_NOTIFY_LAST) > 8000:
                _ADMIN_VISITOR_NOTIFY_LAST.clear()
            asyncio.create_task(
                _notify_admins_visitor_ping(
                    context, u.id, u.username, u.first_name, u.last_name
                )
            )


def _admin_format_claim_row(r: dict) -> str:
    uid = r["user_id"]
    addr = html.escape((r.get("to_address") or "").strip())
    pid_full = str(r.get("packet_id") or "")
    pid = html.escape(pid_full)
    idx = r.get("claim_idx", 0)
    ts = time.strftime("%m-%d %H:%M", time.gmtime(r["claimed_at"]))
    tx = r.get("tx_result") or ""
    tx_short = (tx[:100] + "…") if len(tx) > 100 else tx
    tx_disp = html.escape(tx_short) if tx else "—"
    vun = r.get("visitor_username")
    vlabel = f" @{html.escape(vun)}" if vun else ""
    vfn = (r.get("visitor_first_name") or "").strip()
    if vfn:
        vlabel = f"{vlabel} · {html.escape(vfn)}"
    amt_raw = (r.get("amount_real") or "").strip()
    asset = (r.get("packet_asset_type") or "TON").upper()
    try:
        if asset == "USDT" and amt_raw:
            amt_human = f"{raw_to_usdt(amt_raw)} USDT"
        elif amt_raw:
            amt_human = f"{nanoton_to_ton(amt_raw)} TON"
        else:
            amt_human = "—"
    except Exception:
        amt_human = amt_raw or "—"
    amt_disp = html.escape(amt_human)
    return (
        f"<code>{uid}</code>{vlabel}\n"
        f"  📦 <code>{pid}</code> #{idx} · {amt_disp}\n"
        f"  📍 <code>{addr}</code>\n"
        f"  ⛓ {tx_disp}\n"
        f"  🕐 {ts} UTC"
    )


async def send_admin_dashboard(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    admin_user_id: Optional[int] = None,
) -> None:
    """Push full admin stats + claims (used by /admin and 🔐 後台 button)."""
    admin_id = admin_user_id if admin_user_id is not None else (message.from_user.id if message.from_user else 0)
    s = get_admin_dashboard_stats()
    if is_mcp_available():
        bal_raw, ok = await mcp_get_balance()
        bal_line = html.escape(bal_raw) if ok else f"錯誤: {html.escape(str(bal_raw)[:300])}"
    else:
        bal_line = html.escape("未配置 MCP")

    recent_rows = list_recent_bot_users(20)
    lines = []
    for r in recent_rows:
        uid = r["user_id"]
        un = f"@{r['username']}" if r.get("username") else "—"
        nm = " ".join(x for x in (r.get("first_name") or "", r.get("last_name") or "") if x).strip() or "—"
        ts = time.strftime("%m-%d %H:%M", time.gmtime(r["last_seen"]))
        lines.append(
            f"<code>{uid}</code> {html.escape(un)} · {html.escape(nm)} · ×{r['touch_count']} · {ts}UTC"
        )
    recent_block = "\n".join(lines) if lines else "（未有記錄，有人撳過機械人後會出現）"

    text = (
        f"🔐 <b>後台</b> · 管理員 ID：<code>{admin_id}</code>\n"
        f"<i>（撳 <b>🔐 後台</b> 或 <code>/admin</code>）</i>\n\n"
        f"<b>撳過／用過機械人</b>：<b>{s['bot_users_n']}</b> 個 Telegram 帳號\n"
        f"<b>最近 20 個</b>（每行：<b>Telegram user_id</b> · @username · 名 · 互動次數 · 時間）：\n"
        f"{recent_block}\n\n"
        f"紅包：總 <b>{s['total_packets']}</b> · 未過期 <b>{s['active_packets']}</b> · 24h 新建 <b>{s['packets_24h']}</b>\n"
        f"領取：總 <b>{s['total_claims']}</b> · 24h <b>{s['claims_24h']}</b> · 已儲地址 <b>{s['saved_addresses']}</b>\n\n"
        f"<b>Hot wallet</b>\n{bal_line}"
    )
    await message.reply_text(text, parse_mode="HTML")

    claim_rows = list_recent_claims(25)
    if not claim_rows:
        await message.reply_text(
            "📋 <b>最近領取</b>：暫未有記錄。",
            parse_mode="HTML",
        )
        return
    header = (
        "📋 <b>最近領取</b>（<b>Telegram user_id</b> → 收款地址 · 紅包 id · 金額原始值 · tx）：\n\n"
    )
    cont_header = "📋 <b>最近領取（續）</b>\n\n"
    parts: list[str] = []
    current = header
    for r in claim_rows:
        row = _admin_format_claim_row(r) + "\n\n"
        if len(current) + len(row) > 4000:
            parts.append(current.rstrip())
            current = cont_header + row
        else:
            current += row
    parts.append(current.rstrip())
    for i, p in enumerate(parts):
        suffix = f" <i>({i + 1}/{len(parts)})</i>" if len(parts) > 1 else ""
        await message.reply_text(p + suffix, parse_mode="HTML")


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telegram-only admin dashboard; only BOT_ADMIN_IDS get a reply (others: silent)."""
    if not update.message:
        return
    u = update.effective_user
    if not u or u.id not in BOT_ADMIN_IDS:
        return
    await send_admin_dashboard(context, update.message, admin_user_id=u.id)


def _nft_to_inline_result(nft: dict, bot_username: str) -> InlineQueryResultPhoto | InlineQueryResultArticle | None:
    """Convert a tonapi NFT dict to a Telegram InlineQueryResult."""
    import hashlib

    if nft.get("_auction"):
        domain = nft.get("domain", "")
        if not domain:
            return None
        price_raw = int(nft.get("price", 0))
        price_ton = price_raw / 1e9
        bids = nft.get("bids", 0)
        price_str = f"{price_ton:,.0f} TON" if price_ton >= 1 else f"{price_ton:.2f} TON"
        is_tme = domain.endswith(".t.me")
        emoji = "@" if is_tme else "🌐"
        clean_name = domain.replace(".t.me", "") if is_tme else domain
        desc = f"💎 {price_str} · 🔨 {bids} bid{'s' if bids != 1 else ''}"
        frag_url = (
            f"https://fragment.com/username/{clean_name}" if is_tme
            else f"https://fragment.com/number/{clean_name}"
        )
        text = (
            f"{'👤' if is_tme else '🌐'} <b>{html.escape(domain)}</b>\n"
            f"💎 {price_str}  ·  🔨 {bids} bid{'s' if bids != 1 else ''}\n\n"
            f"🏷 Active Auction"
        )
        return InlineQueryResultArticle(
            id=hashlib.md5(domain.encode()).hexdigest()[:32],
            title=f"{emoji} {domain}",
            description=desc,
            input_message_content=InputTextMessageContent(text, parse_mode="HTML"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 View on Fragment", url=frag_url)],
            ]),
        )

    meta = nft.get("metadata", {}) or {}
    name = meta.get("name", "")
    if not name:
        return None

    addr = nft.get("address", "")
    sale = nft.get("sale")
    collection = nft.get("collection", {}) or {}
    owner = nft.get("owner", {}) or {}
    previews = nft.get("previews", [])

    caption_lines = [f"🖼 <b>{html.escape(name)}</b>"]
    col_name = collection.get("name", "")
    if col_name:
        caption_lines.append(f"📦 {html.escape(col_name)}")
    if sale:
        caption_lines.append(f"💎 {nft_price_display(sale)}")
        market = sale.get("market", {})
        if market.get("name"):
            caption_lines.append(f"🏪 {html.escape(market['name'])}")
    else:
        caption_lines.append("🔒 Not for sale")
    owner_name = owner.get("name", "")
    if owner_name:
        caption_lines.append(f"👤 {html.escape(owner_name)}")
    caption_lines.append(f"\n🤖 @{html.escape(bot_username)}")
    caption = "\n".join(caption_lines)

    getgems_url = f"https://getgems.io/nft/{addr}"
    tonviewer_url = f"https://tonviewer.com/{addr}"
    buttons = [
        [InlineKeyboardButton("🔍 Getgems", url=getgems_url),
         InlineKeyboardButton("🔗 TONViewer", url=tonviewer_url)],
    ]
    for b in (meta.get("buttons") or [])[:1]:
        if b.get("uri") and b.get("label"):
            buttons.append([InlineKeyboardButton(b["label"], url=b["uri"])])
    markup = InlineKeyboardMarkup(buttons)

    preview_url = nft_best_preview(previews, "500x500")
    thumb_url = nft_best_preview(previews, "100x100") or preview_url
    price_desc = nft_price_display(sale) if sale else "Not for sale"

    if preview_url:
        rid = hashlib.md5(addr.encode()).hexdigest()[:32]
        return InlineQueryResultPhoto(
            id=rid,
            photo_url=preview_url,
            thumbnail_url=thumb_url or preview_url,
            title=name,
            description=f"{price_desc}  ·  {col_name}" if col_name else price_desc,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        rid = hashlib.md5(addr.encode()).hexdigest()[:32]
        return InlineQueryResultArticle(
            id=rid,
            title=name,
            description=f"{price_desc}  ·  {col_name}" if col_name else price_desc,
            input_message_content=InputTextMessageContent(caption, parse_mode="HTML"),
            reply_markup=markup,
        )


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Inline mode:
      - Numbers → red packet: @BotName 1 5 = 1 TON × 5 shares
      - Text/address/domain → NFT search
      - Empty → featured NFTs + red packet help
    """
    query = update.inline_query
    if not query:
        return
    bot_me = await context.bot.get_me()
    uname = bot_me.username or "TON_TE_Ai_bot"

    def _help_article() -> InlineQueryResultArticle:
        hint = (
            f"🧧 <b>紅包用法</b>\n\n"
            f"喺輸入框打 <code>@{html.escape(uname)} 1 5</code>\n"
            f"= 1 TON 分 5 份\n\n"
            f"其他：<code>10 3 usdt</code>、<code>2 10 fixed</code>"
        )
        return InlineQueryResultArticle(
            id="help",
            title="🧧 紅包教學",
            description="打：金額 份數  例：1 5 = 1 TON × 5 shares",
            input_message_content=InputTextMessageContent(message_text=hint, parse_mode="HTML"),
        )

    def _nft_help_article() -> InlineQueryResultArticle:
        hint = (
            f"🔍 <b>NFT Search</b>\n\n"
            f"<code>@{html.escape(uname)} foundation.ton</code> — TON domain\n"
            f"<code>@{html.escape(uname)} durov</code> — Telegram username\n"
            f"<code>@{html.escape(uname)} 0:abc…</code> — NFT address\n\n"
            f"📦 Telegram Usernames · TON DNS · Anonymous Numbers"
        )
        return InlineQueryResultArticle(
            id="nft_help",
            title="🔍 Search TON NFTs",
            description="Type a domain, @username, or NFT address",
            input_message_content=InputTextMessageContent(message_text=hint, parse_mode="HTML"),
        )

    try:
        user_id = query.from_user.id if query.from_user else 0
        raw = (query.query or "").strip().replace("\u3000", " ").replace("\xa0", " ")
        query_text = " ".join(raw.split())
        parts = query_text.split()
        results = []

        is_redpacket = False
        if parts:
            try:
                float(parts[0].replace(",", "."))
                is_redpacket = True
            except (ValueError, IndexError):
                pass

        if is_redpacket:
            total_str = parts[0].replace(",", ".")
            total_val = float(total_str)
            share_count = 1
            mode = "lucky"
            asset_type = "TON"

            if total_val > 0:
                if len(parts) > 1:
                    try:
                        share_count = max(1, int(parts[1]))
                    except ValueError:
                        share_count = 1
                for p in parts[2:]:
                    if p.lower() == "fixed":
                        mode = "fixed"
                    elif p.lower() == "usdt":
                        asset_type = "USDT"

                if REDPACKET_OWNER_IDS and user_id not in REDPACKET_OWNER_IDS:
                    results.append(
                        InlineQueryResultArticle(
                            id="no_perm",
                            title="🔒 Only the admin can create red packets",
                            description="You can still claim red packets from others!",
                            input_message_content=InputTextMessageContent(
                                message_text="🔒 Only the admin can create red packets right now."
                            ),
                        )
                    )
                else:
                    asset_label = "USDT" if asset_type == "USDT" else "TON"
                    mode_label = "🎲" if mode == "lucky" else "="
                    jetton_addr = USDT_JETTON if asset_type == "USDT" else None
                    packet_id, create_err = create_packet(
                        creator_id=user_id,
                        total_ton=total_str,
                        share_count=share_count,
                        mode=mode,
                        asset_type=asset_type,
                        jetton_addr=jetton_addr,
                    )
                    if create_err or not packet_id:
                        results.append(
                            InlineQueryResultArticle(
                                id="create_err",
                                title=f"❌ {(create_err or 'Error')[:40]}",
                                description="Fix the amount or count",
                                input_message_content=InputTextMessageContent(message_text=f"❌ {create_err}"),
                            )
                        )
                    else:
                        claim_url = f"https://t.me/{uname}?start={packet_id}"
                        btn = InlineKeyboardButton("🧧 Open Red Packet", url=claim_url)
                        caption = (
                            f"🧧 <b>Red Packet</b>  ·  {total_str} {asset_label} × {share_count} {mode_label}\n"
                            f"0/{share_count} claimed\n\n"
                            f"Powered by <b>TON TE AI</b>"
                        )
                        results.append(
                            InlineQueryResultCachedPhoto(
                                id=packet_id,
                                photo_file_id=_RP_CARD_FILE_ID,
                                caption=caption,
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup([[btn]]),
                            )
                        )
            if not results:
                results.append(_help_article())
            await query.answer(results, cache_time=0, is_personal=True)
            return

        nft_results = await nft_inline_search(query_text)
        for nft in nft_results:
            r = _nft_to_inline_result(nft, uname)
            if r:
                results.append(r)

        if not query_text:
            results.insert(0, _nft_help_article())
            results.append(_help_article())

        if not results:
            results.append(InlineQueryResultArticle(
                id="nft_empty",
                title="No NFTs found",
                description="Try: foundation.ton, durov, or an NFT address",
                input_message_content=InputTextMessageContent(
                    "🔍 No TON NFTs found. Try:\n"
                    f"• <code>@{html.escape(uname)} foundation.ton</code>\n"
                    f"• <code>@{html.escape(uname)} durov</code>\n"
                    f"• <code>@{html.escape(uname)} 0:abc…</code>",
                    parse_mode="HTML",
                ),
            ))

        await query.answer(results, cache_time=30, is_personal=False)
    except Exception as e:
        log.exception("inline_query failed: %s", e)
        try:
            await query.answer([_nft_help_article()], cache_time=5, is_personal=False)
        except Exception:
            pass


async def handle_chosen_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track inline messages for group card updates after claims."""
    result = update.chosen_inline_result
    if not result:
        return
    packet_id = result.result_id
    if not packet_id.startswith("rp_"):
        return
    inline_msg_id = result.inline_message_id
    if inline_msg_id:
        register_group_message(packet_id, 0, 0, inline_message_id=inline_msg_id)


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in env or .env")
    init_redpacket_db()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update, track_bot_user), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_handler(ChosenInlineResultHandler(handle_chosen_inline))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("TON TE AI starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        import subprocess
        subprocess.Popen(
            ["defaults", "write", "NSGlobalDomain", "NSAppSleepDisabled", "-bool", "YES"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    main()
