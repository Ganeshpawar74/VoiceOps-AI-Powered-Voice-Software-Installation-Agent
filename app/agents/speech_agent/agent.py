"""
Agent 1 — Speech Agent
FIXES:
  BUG #5: WhisperModel(model, {dict}) → WhisperModel(model, device=, compute_type=)
  BUG #6: _resample() body was missing (file truncated in original)
  BUG #7: buffer size must be a multiple of element size
          → odd-length raw PCM bytes trimmed before np.frombuffer(..., dtype=np.int16)
  BUG #8 (ROOT CAUSE of "No speech detected in the recording" on every request):
          _bytes_to_numpy() only ever recognised two formats: a WAV container
          (RIFF magic) or headerless raw 16-bit PCM. Browsers' MediaRecorder API
          does NOT produce either of those by default — it produces a compressed
          webm/opus (or ogg/opus, mp4/aac) container. Those bytes don't start with
          "RIFF", so they fell straight into the raw-PCM branch and got
          reinterpreted as if every 2 bytes were one int16 audio sample. That turns
          a compressed file into pure digital noise. faster-whisper's VAD correctly
          identifies that noise as "not speech" and strips the entire clip, so the
          transcript is always empty and you always get "No speech detected" —
          regardless of what was actually said into the mic. (The odd-length-byte
          warning was a symptom of this, not the cause: compressed containers have
          no reason to be an even number of bytes, raw PCM16 always would be.)
          FIX: decode with faster-whisper's own PyAV-based decoder
          (`faster_whisper.audio.decode_audio`), which sniffs the *real*
          container/codec and returns correct 16 kHz mono float32 PCM. PyAV ships
          its own FFmpeg libraries, so no system ffmpeg install is required, and
          `av` is already a hard dependency of faster-whisper — nothing new to
          install. The old WAV/raw-PCM logic is kept only as a last-resort
          fallback for genuinely headerless raw PCM input.
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

_TARGET_SR = 16_000
_MAX_BYTES = 25 * 1024 * 1024


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    # BUG #6 FIX: body was missing
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
    Pre-BUG#8 behaviour. Only correct for an actual WAV file or genuinely
    headerless raw PCM16. Kept purely as a last-resort fallback for when real
    container sniffing (decode_audio, below) fails outright — e.g. the client
    really is sending bare raw PCM with no container/header at all, which
    PyAV/ffmpeg can't identify on its own.
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
    BUG #8 FIX: auto-detect the real container/codec (webm/opus, ogg/opus,
    mp4/aac, mp3, wav, flac, ...) instead of assuming raw PCM. This is what
    makes recordings from a browser's MediaRecorder (webm/opus by default)
    actually transcribe instead of being read as noise and rejected as
    "no speech detected".
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
    model = _get_whisper_model()
    segments, info = model.transcribe(audio, beam_size=5, vad_filter=True,
                                      condition_on_previous_text=False)
    segments_list = list(segments)
    if not segments_list:
        return "", 0.0, "en"
    transcript  = " ".join(s.text.strip() for s in segments_list).strip()
    confidences = [max(0.0, 1.0 - s.no_speech_prob) for s in segments_list]
    confidence  = float(np.mean(confidences))
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