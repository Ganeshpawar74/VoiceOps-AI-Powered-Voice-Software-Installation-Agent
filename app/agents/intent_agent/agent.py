"""
Agent 2 — Intent Agent

FIXES IN THIS VERSION:
  All prior fixes retained.

  NEW FIX #LLM-CORRECT (Root cause of "Pinch Tall Chat GPD APK" failing):
    When Whisper mis-transcribes a voice command, the old code applied a tiny
    static correction map (get home → chrome, etc.) then passed the garbled text
    to the LLM for intent extraction. The LLM correctly returned UNKNOWN because
    "Pinch Tall, Chat, GPD, APK" is not a recognizable install command.
    Fix: Added _llm_correct_transcription() which asks Mistral:
      "This is a voice transcript that may have STT errors. What software name
       and action was the user most likely trying to request?"
    This is the ONLY call that's allowed to guess/hallucinate — it's explicitly
    prompting for phonetic reconstruction, not fact extraction. The result is
    then fed back into the normal intent pipeline.

  NEW FIX #FUZZY (Phonetic similarity for common mis-hearings):
    Added a fuzzy phonetic map for common mis-transcription patterns that
    recur in the logs: "chat gpd", "chatgbt", "chad gpt", "pinch tall" (install),
    "whole load" (download), "be escort" (vs code), etc.
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
# Phonetic mis-transcription correction
# ──────────────────────────────────────────────

# Keys are lowercase substrings / patterns that appear in mis-transcribed text.
# Values are what they should be replaced with.
# Ordered from most-specific to least-specific.
_TRANSCRIPTION_FIXES: list[tuple[str, str]] = [
    # ChatGPT — many mis-hearings from the logs
    ("chat gpd",       "chatgpt"),
    ("chat g p t",     "chatgpt"),
    ("chatgbt",        "chatgpt"),
    ("chat gbt",       "chatgpt"),
    ("chad gpt",       "chatgpt"),
    ("chat jpt",       "chatgpt"),
    ("pinch tall",     "install"),      # "install" mis-heard as "pinch tall"
    ("the whole load", "download"),     # "download" mis-heard as "the whole load"
    ("whole load",     "download"),
    # VS Code mis-hearings
    ("be escort",      "vs code"),
    ("vs coat",        "vs code"),
    ("vs cold",        "vs code"),
    ("visual studio coat", "visual studio code"),
    # Python
    ("install pie thon", "install python"),
    ("install pie",    "install python"),
    # Chrome
    ("get home",       "install chrome"),
    ("get chrome",     "install chrome"),
    ("get crome",      "install chrome"),
    ("install home",   "install chrome"),
    # GitHub
    ("get hub",        "github"),
    ("git hub",        "github"),
    ("gate hub",       "github"),
    ("give hub",       "github"),
    # Node
    ("no js",          "node.js"),
    # Zoom
    ("install sue",    "install zoom"),
]


def _apply_phonetic_fixes(text: str) -> str:
    """Apply static phonetic correction map. Case-insensitive substring replace."""
    lower = text.lower()
    for wrong, right in _TRANSCRIPTION_FIXES:
        if wrong in lower:
            corrected = lower.replace(wrong, right)
            logger.info("[IntentAgent] Phonetic fix: %r → %r", text, corrected)
            return corrected
    return text


def _llm_correct_transcription(transcript: str) -> str:
    """
    FIX #LLM-CORRECT:
    Ask Mistral to reconstruct what the user MOST LIKELY said given a garbled
    STT transcript. This is the ONLY place we allow the LLM to 'guess'.

    Returns a cleaned query string (e.g. "install chatgpt") or the original
    if the LLM can't figure it out.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return transcript
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)

        aliases_sample = list(settings.registry.software_aliases.keys())[:20]
        alias_hint = ", ".join(f'"{a}"' for a in aliases_sample)

        prompt = (
            f"A voice assistant received this speech-to-text transcript:\n"
            f'  "{transcript}"\n\n'
            f"This may contain STT errors. The user was trying to install or download software.\n"
            f"Known software names include: {alias_hint}, and many others.\n\n"
            f"What did the user MOST LIKELY say? Reconstruct the most probable command.\n"
            f"Reply with ONLY the corrected plain-text command — no quotes, no explanation.\n"
            f"If you cannot determine what software was meant, reply: UNKNOWN"
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=40,
        )
        result = response.choices[0].message.content.strip().strip('"').strip("'")
        if result.upper() == "UNKNOWN" or len(result) < 2:
            return transcript
        logger.info(
            "[IntentAgent] LLM transcript correction: %r → %r", transcript, result
        )
        return result
    except Exception as exc:
        logger.warning("[IntentAgent] LLM transcript correction failed: %s", exc)
        return transcript


def _build_system_prompt() -> str:
    aliases = settings.registry.software_aliases
    alias_sample_keys = list(aliases.keys())[:8]
    alias_lines = "\n".join(f"  - {k} → {aliases[k]}" for k in alias_sample_keys)
    hinglish_kws = ", ".join(f'"{kw}"' for kw in settings.registry.hinglish_keywords)

    return f"""You are an intent extraction engine for a voice-controlled software installation assistant.

The user may speak in English, Hindi, or mixed Hinglish.
Hinglish command verbs to recognise: {hinglish_kws}
Examples (use generic placeholders — do NOT substitute real software from examples):
  "SomeApp install karo" → install_software, software_name="someapp"
  "AnotherApp download kar" → download_only, software_name="anotherapp"

Sample alias mappings (normalise software names to canonical form):
{alias_lines}
... (more aliases available in the system)

CRITICAL RULES:
1. Extract the software name ONLY from the user's actual query — never invent one.
2. If you cannot identify a software name from the query, return software_name="" and intent="unknown".
3. Never return a software name that does not appear in the user's query.
4. The query is the ground truth — do not hallucinate based on examples.
5. "chatgpt", "chat gpt", "chatgbt" all mean the ChatGPT desktop app.

Respond ONLY with valid JSON — no markdown, no explanation:
{{
  "intent": "<install_software|download_only|uninstall|check_status|open_app|unknown>",
  "software_name": "<raw name from query, lowercase — empty string if not found>",
  "software_canonical": "<normalised name from alias list, or best guess, or empty>",
  "operating_system": "<windows|macos|linux|unknown>",
  "confidence": <0.0-1.0>,
  "extra_params": {{}}
}}

Rules:
- "install karo / install kar / install karna / lagao" → install_software
- "download karo / download kar" → download_only
- If software unrecognised but intent is clear, return intent with best-guess name
- Default OS to "windows" when not specified
- If truly ambiguous or no software mentioned, return intent=unknown, software_name=""
"""


# ──────────────────────────────────────────────
# Intent Agent
# ──────────────────────────────────────────────

class IntentAgent:
    """
    Agent 2 — Extracts structured intent from SpeechOutput.

    Pipeline:
      1. Phonetic static fix map
      2. Rule-based (alias lookup — instant, no API)
      3. If rule fails: LLM transcript correction → retry rule
      4. If still fails: LLM intent extraction on corrected transcript
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

        # Hard-gate: empty query → UNKNOWN immediately, no LLM call
        if len(query) < 2:
            logger.warning("[IntentAgent] Query too short/empty — returning UNKNOWN")
            return self._unknown(query)

        # Step 1: Static phonetic correction
        cleaned = _apply_phonetic_fixes(query)

        # Step 2: Rule-based fast path
        rule_result = self._rule_based(cleaned)
        if rule_result and rule_result.confidence >= 0.85:
            logger.info(
                "[IntentAgent] Rule-based match: %s / %s (conf=%.2f)",
                rule_result.intent, rule_result.software_canonical, rule_result.confidence,
            )
            return rule_result

        # Step 3: LLM transcript correction (handles STT gibberish like "Pinch Tall Chat GPD APK")
        corrected = await asyncio.to_thread(_llm_correct_transcription, cleaned)
        if corrected != cleaned:
            # Re-try rule-based on corrected transcript
            rule_result2 = self._rule_based(corrected)
            if rule_result2 and rule_result2.confidence >= 0.85:
                logger.info(
                    "[IntentAgent] Rule-based match after LLM correction: %s / %s",
                    rule_result2.intent, rule_result2.software_canonical,
                )
                return rule_result2
            cleaned = corrected  # use corrected for LLM intent extraction below

        # Step 4: LLM intent extraction on (possibly corrected) transcript
        try:
            raw = await asyncio.to_thread(self._call_llm, cleaned)
            result = self._parse(raw, original_query=query)
        except Exception as exc:
            logger.warning("[IntentAgent] LLM call failed (%s) — using rule fallback", exc)
            result = rule_result or self._unknown(query)

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

    def _parse(self, raw: str, original_query: str) -> IntentOutput:
        raw = re.sub(r"```(?:json)?|```", "", raw).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[IntentAgent] LLM returned non-JSON: %r", raw)
            return self._unknown(original_query)

        sw_raw       = data.get("software_name", "").lower().strip()
        aliases      = settings.registry.software_aliases
        sw_canonical = (
            aliases.get(sw_raw)
            or aliases.get(data.get("software_canonical", "").lower().strip())
            or data.get("software_canonical", sw_raw)
        )

        # Sanity check: if LLM returned a software name not in the query,
        # only discard if we got a non-empty name in the corrected transcript.
        # (After LLM correction the original query may differ from cleaned.)
        query_lower = original_query.lower()
        if sw_raw and sw_raw not in query_lower:
            canonical_lower = (sw_canonical or "").lower()
            canonical_words = set(canonical_lower.split())
            query_words     = set(query_lower.split())
            if not canonical_words.intersection(query_words):
                # Only discard if confidence is very low — the LLM-corrected
                # transcript path legitimately returns software not in original.
                if float(data.get("confidence", 0)) < 0.5:
                    logger.warning(
                        "[IntentAgent] Low-confidence result discarded: sw=%r not in query %r",
                        sw_raw, original_query,
                    )
                    return self._unknown(original_query)

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
        """Fast rule-based intent extraction — no API call."""
        import re as re_
        lower = query.lower()

        if any(kw in lower for kw in ["install", "setup", "set up", "add",
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
            os_ = OperatingSystem.WINDOWS

        # Software matching — longest alias first
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
            return None

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
