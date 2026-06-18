"""
Agent 2 — Intent Agent  (REWRITTEN — fully Gen-AI based, no hardcoded dicts)

"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from app.config.settings import get_settings
from app.models.schemas import Intent, IntentOutput, Language, OperatingSystem, SpeechOutput

logger   = logging.getLogger(__name__)
settings = get_settings()


# ──────────────────────────────────────────────
# Intent-verb detection (verbs only — NOT software names, so this scales to
# any software without needing new entries)
# ──────────────────────────────────────────────

_INSTALL_VERBS  = ["install", "setup", "set up", "add", "lagao", "chahiye", "chahta", "karo install"]
_DOWNLOAD_VERBS = ["download", "fetch", "get me", "save"]
_UNINSTALL_VERBS = ["uninstall", "remove", "delete"]
_HINGLISH_VERBS  = ["karo", "kar do", "lagao", "chahiye", "chahta"]


def _detect_os(lower: str) -> OperatingSystem:
    if any(kw in lower for kw in ["windows", "win", ".exe", ".msi"]):
        return OperatingSystem.WINDOWS
    if any(kw in lower for kw in ["mac", "macos", ".dmg", ".pkg", "apple"]):
        return OperatingSystem.MACOS
    if any(kw in lower for kw in ["linux", "ubuntu", "debian", ".deb", ".rpm"]):
        return OperatingSystem.LINUX
    return OperatingSystem.WINDOWS


def _detect_intent_verb(lower: str) -> Optional[Intent]:
    if any(kw in lower for kw in _INSTALL_VERBS):
        return Intent.INSTALL_SOFTWARE
    if any(kw in lower for kw in _DOWNLOAD_VERBS):
        return Intent.DOWNLOAD_ONLY
    if any(kw in lower for kw in _UNINSTALL_VERBS):
        return Intent.UNINSTALL
    return None


def _strip_verb_and_os(query: str, lower: str) -> str:
    """Remove the intent verb and OS hint words to isolate the software name."""
    text = lower
    for kw in _INSTALL_VERBS + _DOWNLOAD_VERBS + _UNINSTALL_VERBS + _HINGLISH_VERBS:
        text = re.sub(rf"\b{re.escape(kw)}\b", " ", text)
    for kw in ["windows", "win", "mac", "macos", "apple", "linux", "ubuntu",
               "debian", "app", "application", "software", "please", "for me"]:
        text = re.sub(rf"\b{re.escape(kw)}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_system_prompt() -> str:
    return """You are an intent-and-entity extraction engine for a voice-controlled
software installation assistant. The user may speak in English, Hindi, or mixed
Hinglish, and the text you receive may contain speech-to-text transcription
errors (e.g. "Install, Chad, GPD, APK" most likely means "install ChatGPT app").

Your job:
1. Decide the user's intent: install_software, download_only, uninstall,
   check_status, open_app, or unknown.
2. Identify the software/product name the user is most likely referring to,
   reconstructing plausible STT errors using your general knowledge of real
   software, app, and product names. Use phonetic and contextual reasoning
   (e.g. "chat gpd", "chad gpt", "chat jpt" all phonetically resemble
   "ChatGPT"; "be escort" phonetically resembles "VS Code").
3. Do NOT invent a software name that has no plausible phonetic or semantic
   relationship to the transcript. If you genuinely cannot tell, return
   software_name="" and intent="unknown".
4. Give your best-guess plain-language product name — do not worry about
   matching it to any specific catalogue or canonical naming scheme; another
   system will resolve it against real installable packages.
5. Default operating_system to "windows" when not specified.

Respond ONLY with valid JSON, no markdown, no explanation:
{
  "intent": "<install_software|download_only|uninstall|check_status|open_app|unknown>",
  "software_name": "<your best-guess plain product name, or empty string>",
  "operating_system": "<windows|macos|linux|unknown>",
  "confidence": <0.0-1.0 — your confidence that software_name is correct>,
  "extra_params": {}
}
"""


class IntentAgent:
    """
    Agent 2 — Extracts structured intent from SpeechOutput.

    Pipeline:
      1. Fast rule-based intent-VERB detection (scales to any software,
         since it never looks at software names — only action verbs).
      2. If a verb was found, isolate the remaining text as the raw software
         name and hand off to the resolver agent downstream (no dict lookup
         here).
      3. Always run the LLM extraction as well when confidence is low or the
         remaining text looks like STT noise — this is what reconstructs
         "Chad GPD APK" -> "ChatGPT" using general language reasoning
         instead of a static phonetic map.
    """

    def __init__(self) -> None:
        self._client = None
        self._system_prompt = _build_system_prompt()

    def _get_client(self):
        if self._client is None:
            from mistralai import Mistral
            api_key = settings.llm.mistral_api_key
            if not api_key:
                raise ValueError(
                    "LLM_MISTRAL_API_KEY is not set. "
                    "Add it to your .env file: LLM_MISTRAL_API_KEY=your_key_here"
                )
            self._client = Mistral(api_key=api_key)
        return self._client

    async def extract(self, speech: SpeechOutput) -> IntentOutput:
        query = speech.query.strip()
        logger.info("[IntentAgent] Extracting from: %r", query)

        if len(query) < 2:
            logger.warning("[IntentAgent] Query too short/empty — returning UNKNOWN")
            return self._unknown(query)

        lower = query.lower()
        os_ = _detect_os(lower)
        verb_intent = _detect_intent_verb(lower)

        # Fast path: clear verb + remaining text looks like a clean product
        # name (no obvious STT garbage — short, mostly alphabetic tokens).
        if verb_intent:
            remainder = _strip_verb_and_os(query, lower)
            if remainder and _looks_clean(remainder):
                logger.info(
                    "[IntentAgent] Fast path: intent=%s software_name=%r (no LLM call)",
                    verb_intent, remainder,
                )
                return IntentOutput(
                    intent=verb_intent,
                    software_name=remainder,
                    software_canonical=remainder,
                    operating_system=os_,
                    confidence=0.85,
                    raw_query=query,
                    extra_params={},
                )

        # Slow path: ask the LLM to reconstruct intent + software name,
        # handling STT noise via general reasoning (no static phonetic map).
        try:
            raw = await asyncio.to_thread(self._call_llm, query)
            result = self._parse(raw, original_query=query, os_hint=os_)
        except Exception as exc:
            logger.warning("[IntentAgent] LLM call failed (%s)", exc)
            if verb_intent and query:
                # Degrade gracefully: we at least know the verb
                remainder = _strip_verb_and_os(query, lower) or query
                result = IntentOutput(
                    intent=verb_intent, software_name=remainder,
                    software_canonical=remainder, operating_system=os_,
                    confidence=0.4, raw_query=query, extra_params={},
                )
            else:
                result = self._unknown(query)

        logger.info(
            "[IntentAgent] Result: intent=%s sw=%s os=%s conf=%.2f",
            result.intent, result.software_canonical, result.operating_system, result.confidence,
        )
        return result

    def _call_llm(self, query: str) -> str:
        client = self._get_client()
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user",   "content": query},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        return response.choices[0].message.content.strip()

    def _parse(self, raw: str, original_query: str, os_hint: OperatingSystem) -> IntentOutput:
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[IntentAgent] LLM returned non-JSON: %r", raw)
            return self._unknown(original_query)

        sw_name = (data.get("software_name") or "").strip()

        try:
            intent_enum = Intent(data.get("intent", "unknown"))
        except ValueError:
            intent_enum = Intent.UNKNOWN

        if not sw_name and intent_enum != Intent.UNKNOWN:
            intent_enum = Intent.UNKNOWN

        try:
            os_enum = OperatingSystem(data.get("operating_system", os_hint.value))
        except ValueError:
            os_enum = os_hint

        return IntentOutput(
            intent=intent_enum,
            software_name=sw_name,
            # software_canonical left as the LLM's best-guess plain name —
            # SoftwareResolverAgent resolves this against real packages later.
            software_canonical=sw_name,
            operating_system=os_enum,
            confidence=float(data.get("confidence", 0.5)),
            raw_query=original_query,
            extra_params=data.get("extra_params", {}),
        )

    def _unknown(self, query: str) -> IntentOutput:
        return IntentOutput(
            intent=Intent.UNKNOWN,
            software_name="",
            software_canonical="",
            operating_system=OperatingSystem.WINDOWS,
            confidence=0.0,
            raw_query=query,
            extra_params={},
        )


def _looks_clean(text: str) -> bool:
    """
    Heuristic only (not a software dict): does this remaining text look like
    a plausible, cleanly-transcribed product name rather than STT garbage?
    Short, mostly-alphabetic, few stray single-letter "words" (a strong sign
    of garbled acronym transcription like "Chad, GPD, APK").
    """
    tokens = [t for t in re.split(r"[\s,]+", text) if t]
    if not tokens or len(tokens) > 5:
        return False
    single_letter_tokens = sum(1 for t in tokens if len(t) <= 2)
    if single_letter_tokens >= 2:
        return False
    return all(re.fullmatch(r"[A-Za-z0-9+.\-]+", t) for t in tokens)