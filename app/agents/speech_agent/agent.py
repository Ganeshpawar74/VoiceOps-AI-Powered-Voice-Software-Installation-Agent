"""
Agent 1 — Speech Agent

"""
from __future__ import annotations
import asyncio, io, logging, time, wave
from typing import Optional
import httpx
import numpy as np
from app.config.settings import get_settings
from app.models.schemas import AudioInput, Language, SpeechOutput

logger   = logging.getLogger(__name__)
settings = get_settings()

_TARGET_SR   = 16_000
_MAX_BYTES   = 25 * 1024 * 1024
_MIN_SAMPLES = int(0.3 * _TARGET_SR)   # FIX #11: reject clips shorter than 300ms

_VAD_PARAMETERS = {
    "threshold":                0.30,   # less aggressive speech/silence boundary
    "min_speech_duration_ms":   100,    # keep segments ≥ 100 ms
    "min_silence_duration_ms":  300,    # merge gaps < 300 ms (default is 2000!)
    "speech_pad_ms":            400,    # pad around speech (default 400)
}


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    if orig_sr == target_sr:
        return audio
    try:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(target_sr), int(orig_sr))
        return resample_poly(audio, target_sr // g, orig_sr // g).astype(np.float32)
    except ImportError:
        pass
    duration   = len(audio) / orig_sr
    new_length = int(duration * target_sr)
    old_idx    = np.linspace(0, len(audio) - 1, new_length)
    return np.interp(old_idx, np.arange(len(audio)), audio).astype(np.float32)


def _legacy_bytes_to_numpy(audio_bytes: bytes, source_sr: int = 16_000) -> np.ndarray:
    """
    Pre-BUG#8 behaviour — only correct for a WAV file or raw headerless PCM16.
    Kept as last-resort fallback when decode_audio fails.
    """
    if audio_bytes[:4] == b"RIFF":
        try:
            buf = io.BytesIO(audio_bytes)
            with wave.open(buf, "rb") as wf:
                channels  = wf.getnchannels()
                framerate = wf.getframerate()
                raw_pcm   = wf.readframes(wf.getnframes())
            audio = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                audio = audio.reshape(-1, channels).mean(axis=1)
            if framerate != _TARGET_SR:
                audio = _resample(audio, framerate, _TARGET_SR)
            return audio
        except Exception as exc:
            logger.warning("[SpeechAgent] WAV parse failed (%s) — raw PCM fallback", exc)

    # BUG #7 FIX: np.frombuffer with dtype=int16 requires even-length bytes.
    if len(audio_bytes) % 2 != 0:
        logger.warning(
            "[SpeechAgent] Odd-length audio buffer (%d bytes) — trimming 1 byte",
            len(audio_bytes),
        )
        audio_bytes = audio_bytes[:-1]

    audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if source_sr != _TARGET_SR:
        audio = _resample(audio, source_sr, _TARGET_SR)
    return audio


def _bytes_to_numpy(audio_bytes: bytes, source_sr: int = 16_000) -> np.ndarray:
    """
    BUG #8 FIX: auto-detect real container/codec (webm/opus, ogg/opus, mp4/aac,
    mp3, wav, flac, ...) instead of assuming raw PCM.
    """
    try:
        from faster_whisper.audio import decode_audio
        audio = decode_audio(io.BytesIO(audio_bytes), sampling_rate=_TARGET_SR)
        if audio.size == 0:
            raise ValueError("decoded audio is empty")
        return audio
    except Exception as exc:
        logger.warning(
            "[SpeechAgent] Container auto-detect failed (%s) — falling back to "
            "legacy WAV/raw-PCM handling. If this keeps happening, the client is "
            "sending bytes ffmpeg/PyAV cannot parse as audio at all.",
            exc,
        )
        return _legacy_bytes_to_numpy(audio_bytes, source_sr)


_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        # BUG #5 FIX: keyword args, not a dict as 2nd positional arg
        _whisper_model = WhisperModel(
            settings.stt.whisper_model_size,
            device=settings.stt.whisper_device,
            compute_type=settings.stt.whisper_compute_type,
        )
        logger.info("[SpeechAgent] Whisper model=%s loaded", settings.stt.whisper_model_size)
    return _whisper_model


def _transcribe_whisper(audio: np.ndarray) -> tuple[str, float, str]:
    """
    FIX #9: Pass relaxed vad_parameters so short clips aren't entirely stripped.
    FIX #10: Floor confidence at 0.65 when a non-empty transcript is returned,
             because no_speech_prob is unreliable for short voice commands.
    FIX #11: Guard against micro-clips shorter than 300ms.
    """
    # FIX #11: guard micro-clips
    if len(audio) < _MIN_SAMPLES:
        logger.warning(
            "[SpeechAgent] Audio too short (%d samples, %.2fs) — skipping transcription",
            len(audio), len(audio) / _TARGET_SR,
        )
        return "", 0.0, "en"

    duration_s = len(audio) / _TARGET_SR
    logger.info("[SpeechAgent] Transcribing %.2fs of audio", duration_s)

    model = _get_whisper_model()
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,
        vad_parameters=_VAD_PARAMETERS,   # FIX #9: relaxed VAD
        condition_on_previous_text=False,
        word_timestamps=False,
    )
    segments_list = list(segments)
    if not segments_list:
        logger.warning("[SpeechAgent] Whisper returned no segments after VAD filtering")
        return "", 0.0, info.language or "en"

    transcript  = " ".join(s.text.strip() for s in segments_list).strip()
    confidences = [max(0.0, 1.0 - s.no_speech_prob) for s in segments_list]
    confidence  = float(np.mean(confidences))

    # FIX #10: if we actually got text, don't discard due to low no_speech_prob
    if transcript and confidence < 0.65:
        logger.info(
            "[SpeechAgent] Bumping confidence %.2f → 0.65 because transcript is non-empty: %r",
            confidence, transcript,
        )
        confidence = 0.65

    return transcript, confidence, info.language or "en"


async def _transcribe_sarvam(audio_bytes: bytes, session_id: str) -> tuple[str, float, str]:
    url     = f"{settings.stt.sarvam_base_url}/speech-to-text"
    headers = {"api-subscription-key": settings.stt.sarvam_api_key}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers,
            files={"file": (f"{session_id}.wav", audio_bytes, "audio/wav")},
            data={"language_code": "hi-en", "model": "saarika:v1"})
        resp.raise_for_status()
        data = resp.json()
    return data.get("transcript", "").strip(), 0.9, data.get("language_code", "hi-en")


class SpeechAgent:
    async def process(self, audio: AudioInput) -> SpeechOutput:
        t0 = time.perf_counter()
        if len(audio.audio_bytes) > _MAX_BYTES:
            raise ValueError(f"Audio too large: {len(audio.audio_bytes)//1024//1024} MB")

        if settings.stt.provider == "sarvam":
            if not settings.stt.sarvam_api_key:
                raise ValueError("STT_SARVAM_API_KEY required when STT_PROVIDER=sarvam")
            transcript, confidence, lang_code = await _transcribe_sarvam(
                audio.audio_bytes, audio.session_id)
        else:
            audio_np = await asyncio.to_thread(_bytes_to_numpy, audio.audio_bytes, audio.sample_rate)
            try:
                import noisereduce as nr
                audio_np = await asyncio.to_thread(nr.reduce_noise, y=audio_np, sr=_TARGET_SR)
            except ImportError:
                pass
            transcript, confidence, lang_code = await asyncio.to_thread(_transcribe_whisper, audio_np)

        lang_map = {"en": Language.EN, "hi": Language.HI, "hi-en": Language.HI_EN}
        language = lang_map.get(lang_code[:5].lower(), Language.EN)
        duration_ms = (time.perf_counter() - t0) * 1000
        logger.info("[SpeechAgent] transcript=%r conf=%.2f lang=%s %.0fms",
                    transcript, confidence, language.value, duration_ms)
        return SpeechOutput(query=transcript, language=language, confidence=confidence,
                            raw_transcript=transcript, session_id=audio.session_id,
                            processing_time_ms=duration_ms)