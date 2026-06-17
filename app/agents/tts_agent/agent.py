"""
Agent 8 — TTS Agent (NEW — per architecture diagram)

Converts the final text response into audio using Sarvam AI TTS.
Returns base64-encoded WAV bytes so the API can stream it to the browser.

If TTS is disabled (FEATURE_TTS_ENABLED=false) or the Sarvam key is missing,
returns None gracefully — the caller falls back to text-only response.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from app.config.settings import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()


class TTSAgent:
    """
    Agent 8 — Text-to-Speech via Sarvam AI.

    Input : plain text string (the final response message).
    Output: base64-encoded audio bytes (WAV) | None if TTS disabled/fails.
    """

    async def synthesize(self, text: str) -> Optional[str]:
        """
        Returns base64-encoded audio string or None.
        Never raises — TTS failure is non-fatal.
        """
        if not settings.features.tts_enabled:
            logger.debug("[TTSAgent] TTS disabled via FEATURE_TTS_ENABLED=false")
            return None

        if settings.tts.provider == "none":
            return None

        # Resolve API key: TTS key → STT key (both use Sarvam)
        api_key = settings.tts.sarvam_api_key or settings.stt.sarvam_api_key
        if not api_key:
            logger.warning(
                "[TTSAgent] No Sarvam API key set (TTS_SARVAM_API_KEY or STT_SARVAM_API_KEY). "
                "Skipping TTS."
            )
            return None

        url = f"{settings.tts.sarvam_base_url}/text-to-speech"
        payload = {
            "inputs":               [text[:500]],   # Sarvam limit per call
            "target_language_code": settings.tts.target_language_code,
            "speaker":              settings.tts.speaker,
            "model":                settings.tts.model,
            "enable_preprocessing": True,
        }
        headers = {
            "api-subscription-key": api_key,
            "Content-Type":         "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # Sarvam returns {"audios": ["<base64_wav>", ...]}
            audios = data.get("audios") or []
            if not audios:
                logger.warning("[TTSAgent] Sarvam returned empty audios list")
                return None

            audio_b64 = audios[0]
            logger.info("[TTSAgent] TTS OK — %d chars base64 audio", len(audio_b64))
            return audio_b64

        except Exception as exc:
            logger.warning("[TTSAgent] TTS synthesis failed (non-fatal): %s", exc)
            return None