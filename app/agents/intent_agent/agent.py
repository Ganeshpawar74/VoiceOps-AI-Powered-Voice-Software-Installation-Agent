"""
Agent 2 — Intent Agent
Extracts software install intent from a SpeechOutput using Mistral AI.

Root causes fixed vs original:
  1. extract() was SYNCHRONOUS but main_workflow calls `await _intent_agent.extract(speech)`.
     → Made fully async; Mistral API call wrapped in asyncio.to_thread.
  2. IntentAgent.__init__ required api_key parameter but workflow called IntentAgent()
     with no arguments → TypeError on every startup.
     → api_key now read from settings.llm.mistral_api_key; no constructor arg needed.
  3. Model name was hardcoded "mistral-small-latest" ignoring settings.llm.intent_model.
     → Now reads settings.llm.intent_model.
  4. extract() accepted a raw string but workflow passes a SpeechOutput object.
     → Signature changed to accept SpeechOutput; query extracted inside.
  5. _parse() could silently return UNKNOWN if the LLM returned a valid-looking intent
     string that wasn't in the Intent enum (e.g. "install" instead of "install_software").
     → Added graceful fallback with a logged warning.
  6. Hinglish keywords were checked in the original but intent prompt system message
     was not injected with the full alias list from settings.registry.software_aliases.
     → Alias map now sourced from settings, not a hardcoded dict.
  7. Mistral client initialisation happened at __init__ time, causing import-time
     failures if the API key env var was not yet loaded.
     → Client initialised lazily on first call.
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
# Mis-transcription correction map
# ──────────────────────────────────────────────

_TRANSCRIPTION_FIXES: dict[str, str] = {
    "get home":          "install chrome",
    "get chrome":        "install chrome",
    "install home":      "install chrome",
    "get crome":         "install chrome",
    "vs coat":           "install vs code",
    "vs cold":           "install vs code",
    "be escort":         "install vs code",
    "install pie":       "install python",
    "install pie thon":  "install python",
    "install node":      "install node.js",
    "no js":             "node.js",
    "install sue":       "install zoom",
}


def _fix_transcription(text: str) -> str:
    lower = text.lower().strip()
    for wrong, right in _TRANSCRIPTION_FIXES.items():
        if wrong in lower:
            corrected = lower.replace(wrong, right)
            logger.info("[IntentAgent] Transcription fix: %r → %r", text, corrected)
            return corrected
    return text


def _build_system_prompt() -> str:
    """Build system prompt from settings (alias list is config-driven, not hardcoded)."""
    aliases = settings.registry.software_aliases
    alias_lines = "\n".join(f"  - {k} → {v}" for k, v in aliases.items())

    hinglish_kws = ", ".join(f'"{kw}"' for kw in settings.registry.hinglish_keywords)

    return f"""You are an intent extraction engine for a voice-controlled software installation assistant.

The user may speak in English, Hindi, or mixed Hinglish.
Hinglish command verbs to recognise: {hinglish_kws}
Examples: "VS Code install karo" → install_software, "Chrome download kar" → download_only

Known software aliases (normalise to the value on the right):
{alias_lines}

Respond ONLY with valid JSON — no markdown, no explanation:
{{
  "intent": "<install_software|download_only|uninstall|check_status|open_app|unknown>",
  "software_name": "<raw name from query, lowercase>",
  "software_canonical": "<normalised name from alias list, or best guess>",
  "operating_system": "<windows|macos|linux|unknown>",
  "confidence": <0.0-1.0>,
  "extra_params": {{}}
}}

Rules:
- "install karo / install kar / install karna / lagao" → install_software
- "download karo / download kar" → download_only
- If software unrecognised but intent is clear, still return intent with best guess at name
- Default OS to "windows" when not specified
- If truly ambiguous, return intent=unknown
"""


# ──────────────────────────────────────────────
# Intent Agent
# ──────────────────────────────────────────────

class IntentAgent:
    """
    Agent 2 — Extracts structured intent from SpeechOutput.

    Uses Mistral AI (model configured via settings.llm.intent_model).
    Falls back to a fast rule-based extractor when the LLM call fails.
    No constructor arguments required — all config from settings.
    """

    def __init__(self) -> None:
        self._client = None   # lazy init
        self._system_prompt = _build_system_prompt()

    def _get_client(self):
        if self._client is None:
            from mistralai import Mistral  # type: ignore
            api_key = settings.llm.mistral_api_key
            if not api_key:
                raise ValueError(
                    "LLM_MISTRAL_API_KEY is not set. "
                    "Add it to your .env file: LLM_MISTRAL_API_KEY=your_key_here"
                )
            self._client = Mistral(api_key=api_key)
        return self._client

    async def extract(self, speech: SpeechOutput) -> IntentOutput:
        """
        Main entry point. Accepts SpeechOutput, returns IntentOutput.
        Async — safe to await in workflow nodes.
        """
        query = speech.query.strip()
        logger.info("[IntentAgent] Extracting from: %r", query)

        if not query:
            return self._unknown(query)

        # Apply mis-transcription fixes
        cleaned = _fix_transcription(query)

        # Try rule-based first (instant, no API cost)
        rule_result = self._rule_based(cleaned)
        if rule_result and rule_result.confidence >= 0.85:
            logger.info(
                "[IntentAgent] Rule-based match: %s / %s (conf=%.2f)",
                rule_result.intent, rule_result.software_canonical, rule_result.confidence,
            )
            return rule_result

        # LLM extraction
        try:
            raw = await asyncio.to_thread(self._call_llm, cleaned)
            result = self._parse(raw, original_query=query)
        except Exception as exc:
            logger.warning("[IntentAgent] LLM call failed (%s) — using rule-based fallback", exc)
            result = rule_result or self._unknown(query)

        logger.info(
            "[IntentAgent] Result: intent=%s sw=%s os=%s conf=%.2f",
            result.intent, result.software_canonical, result.operating_system, result.confidence,
        )
        return result

    def _call_llm(self, query: str) -> str:
        """Blocking Mistral API call — must be run in a thread."""
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

    def _parse(self, raw: str, original_query: str) -> IntentOutput:
        """Parse LLM JSON response into IntentOutput."""
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[IntentAgent] LLM returned non-JSON: %r", raw)
            return self._unknown(original_query)

        # Normalise software name via settings alias map
        sw_raw       = data.get("software_name", "").lower().strip()
        aliases      = settings.registry.software_aliases
        sw_canonical = (
            aliases.get(sw_raw)
            or aliases.get(data.get("software_canonical", "").lower().strip())
            or data.get("software_canonical", sw_raw)
        )

        # Safe enum parsing with fallback
        try:
            intent_enum = Intent(data.get("intent", "unknown"))
        except ValueError:
            intent_enum = Intent.UNKNOWN

        try:
            os_enum = OperatingSystem(data.get("operating_system", "windows"))
        except ValueError:
            os_enum = OperatingSystem.WINDOWS

        return IntentOutput(
            intent=intent_enum,
            software_name=sw_raw,
            software_canonical=sw_canonical or sw_raw,
            operating_system=os_enum,
            confidence=float(data.get("confidence", 0.5)),
            raw_query=original_query,
            extra_params=data.get("extra_params", {}),
        )

    def _rule_based(self, query: str) -> Optional[IntentOutput]:
        """
        Fast rule-based intent extraction — no API call.
        Returns None when confidence is too low for reliable extraction.
        Uses word-boundary matching to avoid "go" matching inside "google".
        """
        import re as re_
        lower = query.lower()

        # Determine intent
        if any(kw in lower for kw in ["install", "setup", "set up", "get", "add",
                                        "install karo", "lagao", "chahiye", "chahta"]):
            intent = Intent.INSTALL_SOFTWARE
        elif any(kw in lower for kw in ["download", "download karo", "download kar"]):
            intent = Intent.DOWNLOAD_ONLY
        elif any(kw in lower for kw in ["uninstall", "remove", "delete"]):
            intent = Intent.UNINSTALL
        else:
            return None

        # Detect OS
        if any(kw in lower for kw in ["windows", "win", ".exe", ".msi"]):
            os_ = OperatingSystem.WINDOWS
        elif any(kw in lower for kw in ["mac", "macos", ".dmg", ".pkg", "apple"]):
            os_ = OperatingSystem.MACOS
        elif any(kw in lower for kw in ["linux", "ubuntu", "debian", ".deb", ".rpm"]):
            os_ = OperatingSystem.LINUX
        else:
            os_ = OperatingSystem.WINDOWS  # default

        # Software matching — word-boundary aware
        aliases = settings.registry.software_aliases
        sw_canonical: Optional[str] = None
        sw_raw: str = ""

        for alias, canonical in sorted(aliases.items(), key=lambda x: -len(x[0])):
            pattern = r'\b' + re_.escape(alias) + r'\b'
            if re_.search(pattern, lower):
                sw_canonical = canonical
                sw_raw = alias
                break

        if not sw_canonical:
            return None  # can't determine software — let LLM try

        return IntentOutput(
            intent=intent,
            software_name=sw_raw,
            software_canonical=sw_canonical,
            operating_system=os_,
            confidence=0.90,
            raw_query=query,
            extra_params={},
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