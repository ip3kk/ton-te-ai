"""
cantonese.ai TTS — 玲奈（粵）/ Vin（國）/ JL（英）聲線，與 RpA 專案一致。
需環境變數 CANTONESE_TTS_API_KEY；失敗時由 bot 層 fallback edge-tts。
文檔: https://docs.cantonese.ai/text-to-speech
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import httpx

from config import (
    CANTONESE_SPEED_EN,
    CANTONESE_SPEED_YUE,
    CANTONESE_SPEED_ZH,
    CANTONESE_TTS_API_KEY,
    CANTONESE_VOICE_EN,
    CANTONESE_VOICE_YUE,
    CANTONESE_VOICE_ZH,
)

log = logging.getLogger("tonpal.tts")

CANTONESE_AI_URL = "https://cantonese.ai/api/tts"


def _mp3_to_ogg_opus(mp3_path: str, add_ambient_noise: bool) -> Optional[str]:
    """MP3 → OGG Opus（Telegram voice 有波形）。廣東話可加微弱底噪，似 RpA。"""
    ogg_path = mp3_path.replace(".mp3", ".ogg")
    try:
        if add_ambient_noise:
            af = (
                "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=mono[voice];"
                "anoisesrc=d=60:c=pink:a=0.012[noise];"
                "[voice][noise]amix=inputs=2:duration=first:weights=1 0.18[out]"
            )
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp3_path,
                    "-filter_complex", af, "-map", "[out]",
                    "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                    "-application", "voip", ogg_path,
                ],
                capture_output=True,
                timeout=120,
            )
            if r.returncode != 0:
                r = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", mp3_path,
                        "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                        "-application", "voip", ogg_path,
                    ],
                    capture_output=True,
                    timeout=120,
                )
        else:
            r = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", mp3_path,
                    "-c:a", "libopus", "-b:a", "64k", "-vbr", "on",
                    "-application", "voip", ogg_path,
                ],
                capture_output=True,
                timeout=120,
            )
        if r.returncode == 0 and os.path.isfile(ogg_path) and os.path.getsize(ogg_path) > 100:
            try:
                os.unlink(mp3_path)
            except OSError:
                pass
            return ogg_path
    except FileNotFoundError:
        log.debug("ffmpeg 未安裝，沿用 MP3")
    except Exception as e:
        log.warning("OGG 轉換失敗: %s", e)
    return None


async def synthesize_cantonese_ai(
    text: str,
    lang_mode: str,
) -> Optional[str]:
    """
    用 cantonese.ai 生成語音檔路徑（.ogg 優先，否則 .mp3）。
    lang_mode: "yue" | "zh" | "en"
    """
    if not CANTONESE_TTS_API_KEY or not text.strip():
        return None

    if lang_mode == "yue":
        voice_id = CANTONESE_VOICE_YUE
        api_lang = "cantonese"
        speed = CANTONESE_SPEED_YUE
        should_enhance = False
        add_noise = True
    elif lang_mode == "zh":
        voice_id = CANTONESE_VOICE_ZH
        api_lang = "mandarin"
        speed = CANTONESE_SPEED_ZH
        should_enhance = True
        add_noise = False
    elif lang_mode == "en":
        voice_id = CANTONESE_VOICE_EN
        api_lang = "english"
        speed = CANTONESE_SPEED_EN
        should_enhance = True
        add_noise = False
    else:
        return None

    payload = {
        "api_key": CANTONESE_TTS_API_KEY,
        "text": text[:5000],
        "language": api_lang,
        "output_extension": "mp3",
        "speed": speed,
        "pitch": 0,
        "voice_id": voice_id,
        "frame_rate": "44100",
        "should_enhance": should_enhance,
    }

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    mp3_path = tmp.name

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(CANTONESE_AI_URL, json=payload)
        if r.status_code != 200 or not r.content or len(r.content) < 100:
            log.warning("cantonese.ai HTTP %s: %s", r.status_code, (r.text or "")[:200])
            try:
                os.unlink(mp3_path)
            except OSError:
                pass
            return None

        with open(mp3_path, "wb") as f:
            f.write(r.content)

        if shutil.which("ffmpeg"):
            ogg = _mp3_to_ogg_opus(mp3_path, add_ambient_noise=add_noise)
            if ogg:
                return ogg
        return mp3_path
    except Exception as e:
        log.warning("cantonese.ai 請求失敗: %s", e)
        try:
            os.unlink(mp3_path)
        except OSError:
            pass
        return None
