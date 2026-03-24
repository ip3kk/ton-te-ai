"""
TonPal — TON AI Wallet Agent
Configuration and constants.
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# DeepSeek
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()

# Optional: Groq Whisper API (fast STT). If empty, bot uses faster-whisper then Google.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()

# cantonese.ai TTS（玲奈粵語 / Vin國語 / JL英語）— 與 RpA 相同 voice_id
# 文檔: https://docs.cantonese.ai/text-to-speech
CANTONESE_TTS_API_KEY = os.environ.get("CANTONESE_TTS_API_KEY", "").strip()
CANTONESE_VOICE_YUE = os.environ.get(
    "CANTONESE_VOICE_YUE", "b78c011c-56dd-4a7e-bbd0-63c2e34a56c5"
)  # 玲奈
CANTONESE_VOICE_ZH = os.environ.get(
    "CANTONESE_VOICE_ZH", "ad0a1c96-80ae-487d-b631-596fbd0e0f12"
)  # Vin 國語
CANTONESE_VOICE_EN = os.environ.get(
    "CANTONESE_VOICE_EN", "e6ab5b3f-6a2f-42a1-8aa0-6e8f74d3e70a"
)  # JL 英語
try:
    CANTONESE_SPEED_YUE = float(os.environ.get("CANTONESE_SPEED_YUE", "1.15"))
except ValueError:
    CANTONESE_SPEED_YUE = 1.15
try:
    CANTONESE_SPEED_ZH = float(os.environ.get("CANTONESE_SPEED_ZH", "1.0"))
except ValueError:
    CANTONESE_SPEED_ZH = 1.0
try:
    CANTONESE_SPEED_EN = float(os.environ.get("CANTONESE_SPEED_EN", "1.0"))
except ValueError:
    CANTONESE_SPEED_EN = 1.0

# TON
TON_NETWORK = os.environ.get("TON_NETWORK", "mainnet")  # mainnet | testnet
TONCENTER_API_KEY = os.environ.get("TONCENTER_API_KEY", "")
TONCENTER_BASE = "https://testnet.toncenter.com/api/v2" if TON_NETWORK == "testnet" else "https://toncenter.com/api/v2"
TONAPI_BASE = "https://testnet.tonapi.io/v2" if TON_NETWORK == "testnet" else "https://tonapi.io/v2"

# MCP server (optional — for send, swap, NFT, DNS)
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3000")

# Paths
ROOT = Path(__file__).parent.resolve()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Red packet: only these user IDs can CREATE packets (demo mode)
REDPACKET_OWNER_IDS = {int(x) for x in os.environ.get("REDPACKET_OWNER_IDS", "5396577745").split(",") if x.strip()}
# Admin dashboard (/admin). If unset, same as red packet owners.
_admin_raw = os.environ.get("BOT_ADMIN_IDS", "").strip()
BOT_ADMIN_IDS = (
    {int(x) for x in _admin_raw.split(",") if x.strip()}
    if _admin_raw
    else set(REDPACKET_OWNER_IDS)
)

# Known jettons (symbol -> master address)
USDT_JETTON = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
STON_JETTON = "EQBlqsm144Dq6SjbPI4jjZvA1hqTIP3CvHovbIfW_t-SCALE"
