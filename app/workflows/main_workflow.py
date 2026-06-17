"""
Master LangGraph Workflow — orchestrates all agents.

ARCHITECTURE:
  1. Speech Agent      → STT (Whisper / Sarvam)
  2. Intent Agent      → Mistral LLM
  3. Planner Agent     → LangGraph Plan-and-Execute
  4. Browser Agent     → Playwright
  5. Download Agent    → httpx + SHA-256
  6. Install Agent     → winget / brew / apt / installer file
  6b.Verify Agent      → post-install verification
  7. Response Agent    → Mistral LLM natural language response
  8. TTS Agent         → Sarvam TTS voice output
  9. Notification Agent→ Redis pub/sub

FIXES IN THIS VERSION:
  FIX-1–9: All prior fixes retained.
  FIX-10 (NEW): _MIN_SPEECH_CONFIDENCE lowered from 0.55 → 0.40.
          Whisper's no_speech_prob heuristic returns low confidence even for
          perfectly clear short commands. Speech agent now floors confidence
          at 0.65 when a non-empty transcript is returned, and this workflow
          threshold is set to 0.40 as an additional safety margin.
  FIX-11 (NEW): node_notify now logs the actual software canonical name from
          intent state so notification messages read "GitHub Desktop has been
          installed successfully" instead of "the software has been installed".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.config.settings import get_settings
from app.models.schemas import (
    AudioInput,
    BrowserResult,
    DownloadResult,
    ExecutionPlan,
    InstallResult,
    Intent,
    IntentOutput,
    Language,
    OperatingSystem,
    SpeechOutput,
    Task,
    TaskStatus,
)

logger   = logging.getLogger(__name__)
settings = get_settings()

# FIX: lowered from 0.55 → 0.40. After VAD parameter relaxation and the
# confidence floor added in speech_agent (FIX #9, #10), valid short commands
# reliably return confidence ≥ 0.65. Setting threshold to 0.40 keeps a safety
# net against genuinely garbled audio without false-rejecting real speech.
_MIN_SPEECH_CONFIDENCE = 0.40


# ──────────────────────────────────────────────
# Workflow state
# ──────────────────────────────────────────────

class WorkflowState(TypedDict):
    task: dict[str, Any]
    audio_bytes: Optional[bytes]
    text_query: Optional[str]
    os_hint: Optional[str]
    speech: Optional[dict[str, Any]]
    intent: Optional[dict[str, Any]]
    plan: Optional[dict[str, Any]]
    rag_context: Optional[str]
    browser: Optional[dict[str, Any]]
    use_package_manager: bool
    download: Optional[dict[str, Any]]
    install: Optional[dict[str, Any]]
    verify: Optional[dict[str, Any]]
    response_text: Optional[str]
    tts_audio: Optional[str]          # base64 WAV
    error: Optional[str]
    current_step: str
    retry_count: int
    awaiting_approval: bool


# ──────────────────────────────────────────────
# Agent singletons (stateless) — lazy-loaded
# ──────────────────────────────────────────────

_speech_agent   = None
_intent_agent   = None
_planner_agent  = None
_download_agent = None
_install_agent  = None
_verify_agent   = None
_tts_agent      = None
_monitor_agent  = None
_notify_agent   = None


def _get_agents():
    """Lazy-load agents once so import errors surface as task failures, not app crashes."""
    global _speech_agent, _intent_agent, _planner_agent, _download_agent
    global _install_agent, _verify_agent, _tts_agent, _monitor_agent, _notify_agent

    if _speech_agent is None:
        from app.agents.download_agent.agent import DownloadAgent
        from app.agents.install_agent.agent import InstallAgent
        from app.agents.intent_agent.agent import IntentAgent
        from app.agents.monitoring_agent.agent import MonitoringAgent, NotificationAgent
        from app.agents.planner_agent.agent import PlannerAgent
        from app.agents.speech_agent.agent import SpeechAgent
        from app.agents.verify_agent.agent import VerifyAgent
        from app.agents.tts_agent.agent import TTSAgent

        _speech_agent   = SpeechAgent()
        _intent_agent   = IntentAgent()
        _planner_agent  = PlannerAgent()
        _download_agent = DownloadAgent()
        _install_agent  = InstallAgent()
        _verify_agent   = VerifyAgent()
        _tts_agent      = TTSAgent()
        _monitor_agent  = MonitoringAgent()
        _notify_agent   = NotificationAgent()

    return (
        _speech_agent, _intent_agent, _planner_agent, _download_agent,
        _install_agent, _verify_agent, _tts_agent, _monitor_agent, _notify_agent,
    )


# ──────────────────────────────────────────────
# Node implementations
# ──────────────────────────────────────────────

async def node_speech(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "speech"
    try:
        speech_agent, *_ = _get_agents()
        if state.get("audio_bytes"):
            audio = AudioInput(
                audio_bytes=state["audio_bytes"],
                session_id=state["task"]["session_id"],
            )
            result: SpeechOutput = await speech_agent.process(audio)
        else:
            result = SpeechOutput(
                query=state["text_query"] or "",
                language=Language.EN,
                confidence=1.0,
                raw_transcript=state["text_query"] or "",
                session_id=state["task"]["session_id"],
                processing_time_ms=0,
            )
        state["speech"] = result.model_dump(mode='json')
        logger.info("[Workflow] Speech done: query=%r", result.query)

        if state.get("audio_bytes") and not result.query.strip():
            state["error"] = (
                "No speech detected in the recording. Please try again and "
                "speak clearly into the microphone."
            )
        elif state.get("audio_bytes") and result.confidence < _MIN_SPEECH_CONFIDENCE:
            state["error"] = (
                f"Wasn't able to clearly understand the recording "
                f"(heard: \"{result.query}\", confidence too low). Please try again."
            )
    except Exception as exc:
        state["error"] = f"Speech agent error: {exc}"
        logger.error("[Workflow] Speech failed: %s", exc, exc_info=True)
    return state


async def node_intent(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "intent"
    if state.get("error"):
        return state
    if not state.get("speech"):
        state["error"] = "Intent agent error: speech output missing"
        return state
    try:
        _, intent_agent, *_ = _get_agents()
        speech = SpeechOutput(**state["speech"])
        result: IntentOutput = await intent_agent.extract(speech)

        if state.get("os_hint"):
            try:
                result = result.model_copy(
                    update={"operating_system": OperatingSystem(state["os_hint"])}
                )
            except ValueError:
                pass

        state["intent"] = result.model_dump(mode='json')
        logger.info(
            "[Workflow] Intent done: %s / %s / os=%s",
            result.intent, result.software_canonical, result.operating_system,
        )
    except Exception as exc:
        state["error"] = f"Intent agent error: {exc}"
        logger.error("[Workflow] Intent failed: %s", exc, exc_info=True)
    return state


async def node_planner(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "planner"
    if state.get("error"):
        return state
    if not state.get("intent"):
        state["error"] = "Planner agent error: intent output missing"
        return state
    try:
        _, _, planner_agent, *_ = _get_agents()
        intent = IntentOutput(**state["intent"])

        if not intent.software_canonical and intent.intent == Intent.UNKNOWN:
            state["error"] = (
                f"Couldn't understand what to install from: "
                f"\"{intent.raw_query}\". Please try again with the software name."
            )
            return state

        plan: ExecutionPlan = await planner_agent.plan(intent)
        state["plan"] = plan.model_dump(mode='json')

        # RAG enrichment — only when enabled AND Qdrant is reachable
        if settings.features.rag_enabled:
            try:
                from app.rag.store import RAGStore
                rag = RAGStore()
                guide = await rag.get_install_guide(
                    intent.software_canonical, intent.operating_system.value
                )
                if guide:
                    state["rag_context"] = guide
                    logger.info("[Workflow] RAG context loaded (%d chars)", len(guide))
            except Exception as rag_exc:
                logger.warning("[Workflow] RAG lookup failed (non-fatal): %s", rag_exc)

        steps = plan.steps or []
        state["use_package_manager"] = (
            len(steps) == 1 and steps[0].name == "pkg_manager_install"
        )
        logger.info(
            "[Workflow] Plan done: %d steps, use_pkg_mgr=%s",
            len(steps), state["use_package_manager"],
        )
    except Exception as exc:
        state["error"] = f"Planner agent error: {exc}"
        logger.error("[Workflow] Planner failed: %s", exc, exc_info=True)
    return state


async def node_human_approval(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "human_approval"
    if state.get("error"):
        return state

    state["awaiting_approval"] = True
    task_id = state["task"]["task_id"]
    plan    = ExecutionPlan(**state["plan"])
    intent  = IntentOutput(**state["intent"])

    import json
    import redis.asyncio as aioredis
    from app.models.schemas import NotificationEvent

    # from_url is sync in redis>=5 — NO await
    r = aioredis.from_url(str(settings.redis.url), decode_responses=True)

    approval_event = NotificationEvent(
        task_id=task_id,
        event="approval_required",
        message=(
            f"About to install {intent.software_canonical} "
            f"on {intent.operating_system.value}. Approve?"
        ),
        data={
            "software": intent.software_canonical,
            "os":       intent.operating_system.value,
            "steps":    len(plan.steps),
        },
    )
    await r.publish(f"voiceops:progress:{task_id}", approval_event.model_dump_json())

    approval_channel = f"voiceops:approval:{task_id}"
    async with r.pubsub() as pubsub:
        await pubsub.subscribe(approval_channel)
        try:
            decision = await asyncio.wait_for(
                _wait_for_approval_signal(pubsub), timeout=300
            )
        except asyncio.TimeoutError:
            decision = "rejected"
        finally:
            await pubsub.unsubscribe(approval_channel)

    await r.aclose()

    if decision != "approved":
        state["error"] = "Installation cancelled: user did not approve"
    else:
        state["awaiting_approval"] = False
    return state


async def _wait_for_approval_signal(pubsub) -> str:
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True)
        if message and message["type"] == "message":
            signal = str(message["data"]).strip().lower()
            if signal in ("approved", "rejected"):
                return signal
        await asyncio.sleep(0.5)


async def node_browser(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "browser"
    if state.get("error"):
        return state
    if not state.get("intent"):
        state["error"] = "Browser agent error: intent missing"
        return state

    intent = IntentOutput(**state["intent"])

    if state.get("use_package_manager"):
        state["browser"] = BrowserResult(
            success=True,
            page_title="package-manager",
            navigation_path=["pkg_manager"],
        ).model_dump(mode='json')
        return state

    from app.agents.browser_agent.agent import BrowserAgent
    browser_agent = BrowserAgent()

    try:
        result: BrowserResult = await browser_agent.find_download_link(intent)
        state["browser"] = result.model_dump(mode='json')
        if not result.success or not result.selected_link:
            state["error"] = result.error or "No download link found"
    except Exception as exc:
        state["error"] = f"Browser agent error: {exc}"
        logger.error("[Workflow] Browser failed: %s", exc, exc_info=True)
    finally:
        await browser_agent.close()

    return state


async def node_download(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "download"
    if state.get("error"):
        return state

    try:
        _, _, _, download_agent, *_ = _get_agents()

        if state.get("use_package_manager"):
            # If download_to_desktop is enabled, let InstallAgent handle the
            # download itself (winget download → Desktop). Just mark as pkg_mgr.
            # Otherwise, skip download entirely (legacy winget silent install).
            state["download"] = DownloadResult(
                success=True,
                local_path="__use_package_manager__",
                download_duration_sec=0,
            ).model_dump(mode='json')
            return state

        if not state.get("browser"):
            state["error"] = "Download agent error: browser result missing"
            return state

        browser = BrowserResult(**state["browser"])
        if not browser.selected_link:
            state["error"] = "No download link available"
            return state

        result: DownloadResult = await download_agent.download_and_verify(
            browser.selected_link
        )
        state["download"] = result.model_dump(mode='json')
        if not result.success:
            state["error"] = result.error or "Download failed"
    except Exception as exc:
        state["error"] = f"Download agent error: {exc}"
        logger.error("[Workflow] Download failed: %s", exc, exc_info=True)
    return state


async def node_install(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "install"
    if state.get("error"):
        return state
    if not state.get("download") or not state.get("intent"):
        state["error"] = "Install agent error: download or intent missing"
        return state

    try:
        _, _, _, _, install_agent, *_ = _get_agents()
        download = DownloadResult(**state["download"])
        intent   = IntentOutput(**state["intent"])

        # Resolve pkg_id and install_method from plan params
        pkg_id: Optional[str] = None
        plan_install_method: Optional[str] = None
        plan_data = state.get("plan")
        if plan_data and plan_data.get("steps"):
            first_step = plan_data["steps"][0]
            pkg_id = first_step.get("params", {}).get("package_id")
            plan_install_method = first_step.get("params", {}).get("install_method")

        result: InstallResult = await install_agent.install(
            download=download,
            software=intent.software_canonical,
            os_target=intent.operating_system,
            package_id=pkg_id,
            use_package_manager=state.get("use_package_manager", False),
            rag_context=state.get("rag_context"),
            install_method_override=plan_install_method,
        )
        state["install"] = result.model_dump(mode='json')
        if not result.success:
            state["error"] = result.error or "Install failed"
    except Exception as exc:
        state["error"] = f"Install agent error: {exc}"
        logger.error("[Workflow] Install failed: %s", exc, exc_info=True)
    return state


async def node_verify(state: WorkflowState) -> WorkflowState:
    """Post-install verification node."""
    state["current_step"] = "verify"
    if not state.get("install") or not state.get("intent"):
        state["verify"] = {
            "verified": False,
            "method": "skipped",
            "version_found": None,
            "install_claimed_success": False,
            "note": "Skipped — install node did not produce a result",
        }
        return state

    try:
        _, _, _, _, _, verify_agent, *_ = _get_agents()
        install = InstallResult(**state["install"])
        intent  = IntentOutput(**state["intent"])

        pkg_id: Optional[str] = None
        plan_data = state.get("plan")
        if plan_data and plan_data.get("steps"):
            pkg_id = plan_data["steps"][0].get("params", {}).get("package_id")

        verify_result = await verify_agent.verify(
            software=intent.software_canonical,
            os_target=intent.operating_system,
            install_result=install,
            pkg_id=pkg_id,
        )
        state["verify"] = verify_result

        if install.success and not verify_result["verified"]:
            logger.warning(
                "[Workflow] Install claimed success but verify could not confirm: %s",
                verify_result["note"],
            )
    except Exception as exc:
        logger.warning("[Workflow] Verify failed (non-fatal): %s", exc)
        state["verify"] = {
            "verified": False,
            "method": "error",
            "version_found": None,
            "install_claimed_success": True,
            "note": str(exc),
        }
    return state


async def node_response(state: WorkflowState) -> WorkflowState:
    """Mistral generates a human-like natural language response."""
    state["current_step"] = "response"
    try:
        intent_data  = state.get("intent") or {}
        install_data = state.get("install") or {}
        verify_data  = state.get("verify") or {}
        error        = state.get("error")

        software = intent_data.get("software_canonical") or "the software"
        os_name  = intent_data.get("operating_system", "windows")

        if error:
            text = (
                f"I was unable to complete the installation of {software}. "
                f"The error was: {error}. Please try again or install manually."
            )
        elif install_data.get("success"):
            version = (
                verify_data.get("version_found")
                or install_data.get("version_installed")
            )
            method = install_data.get("install_method", "package manager")
            verified = verify_data.get("verified", False)
            verification_note = (
                f" Version {version} confirmed." if version
                else (" Installation verified on your system." if verified else "")
            )
            text = (
                f"{software} has been installed successfully on your {os_name} system "
                f"using {method}.{verification_note}"
            )
        else:
            err = install_data.get("error") or error or "unknown reason"
            text = (
                f"I was unable to install {software}. Reason: {err}. "
                "Please check the logs or try installing manually."
            )

        # Try to enrich with Mistral if API key is set (non-blocking)
        if settings.llm.mistral_api_key:
            try:
                text = await _enrich_response_with_llm(text, software, error)
            except Exception as llm_exc:
                logger.debug("[Workflow] LLM response enrichment skipped: %s", llm_exc)

        state["response_text"] = text
        logger.info("[Workflow] Response: %r", text[:100])
    except Exception as exc:
        logger.warning("[Workflow] Response generation failed: %s", exc)
        state["response_text"] = "Task completed."
    return state


async def _enrich_response_with_llm(template: str, software: str, error: Optional[str]) -> str:
    """Use Mistral to make the response more conversational."""
    from mistralai import Mistral
    client = Mistral(api_key=settings.llm.mistral_api_key)
    prompt = (
        f"You are a helpful voice assistant. Rephrase this installation status into "
        f"a clear, friendly, one-sentence voice response. "
        f"Be concise (under 30 words). Do not use markdown.\n\n"
        f"Status: {template}"
    )
    def _call():
        resp = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()

    return await asyncio.to_thread(_call)


async def node_tts(state: WorkflowState) -> WorkflowState:
    """Sarvam TTS converts response text to audio."""
    state["current_step"] = "tts"
    text = state.get("response_text") or "Task completed."
    try:
        _, _, _, _, _, _, tts_agent, *_ = _get_agents()
        audio_b64 = await tts_agent.synthesize(text)
        state["tts_audio"] = audio_b64
        if audio_b64:
            logger.info("[Workflow] TTS audio generated (%d chars)", len(audio_b64))
    except Exception as exc:
        logger.warning("[Workflow] TTS failed (non-fatal): %s", exc)
        state["tts_audio"] = None
    return state


async def node_notify(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "notify"
    try:
        *_, monitor_agent, notify_agent = _get_agents()
        task = Task(**state["task"])

        if state.get("intent"):
            task.intent_output = IntentOutput(**state["intent"])
        if state.get("install"):
            task.install_result = InstallResult(**state["install"])

        # FIX: extract actual software name for notification messages
        software_name = ""
        if state.get("intent"):
            software_name = state["intent"].get("software_canonical", "")

        if state.get("error"):
            task.status = TaskStatus.FAILED
            task.error  = state["error"]
            sw_label = f" for {software_name}" if software_name else ""
            logger.info(
                "[Notification] task=%s status=failed msg=Failed to install%s: %s",
                task.task_id, sw_label, state["error"],
            )
        else:
            task.status       = TaskStatus.COMPLETED
            task.progress_pct = 100
            # Use the actual software name from intent — not a hardcoded string
            logger.info(
                "[Notification] task=%s status=completed msg=%s has been installed successfully.",
                task.task_id,
                software_name or "Software",
            )

        install_data = state.get("install")
        if install_data:
            install_r = InstallResult(**install_data)
            if install_r.logs:
                await monitor_agent.capture_logs(task.task_id, install_r.logs)

        await notify_agent.notify(task)
    except Exception as exc:
        logger.error("[Workflow] Notify failed: %s", exc, exc_info=True)
    return state


# ──────────────────────────────────────────────
# Routing conditions
# ──────────────────────────────────────────────

def _route_after_speech(state: WorkflowState) -> str:
    return "notify" if state.get("error") else "intent"

def _route_after_intent(state: WorkflowState) -> str:
    return "notify" if state.get("error") else "planner"

def _route_after_planner(state: WorkflowState) -> str:
    if state.get("error"):
        return "notify"
    if settings.features.human_in_loop:
        return "human_approval"
    return "browser"

def _route_after_approval(state: WorkflowState) -> str:
    return "notify" if state.get("error") else "browser"

def _route_after_browser(state: WorkflowState) -> str:
    return "notify" if state.get("error") else "download"

def _route_after_download(state: WorkflowState) -> str:
    if state.get("error"):
        return "notify"
    intent_data = state.get("intent", {})
    if intent_data.get("intent") == Intent.DOWNLOAD_ONLY.value:
        return "response"
    return "install"

def _route_after_install(state: WorkflowState) -> str:
    return "verify"

def _route_after_verify(state: WorkflowState) -> str:
    return "response"

def _route_after_response(state: WorkflowState) -> str:
    return "tts"

def _route_after_tts(state: WorkflowState) -> str:
    return "notify"


# ──────────────────────────────────────────────
# Build the LangGraph — lazy singleton
# ──────────────────────────────────────────────

_workflow = None  # FIX-9: lazy build — not built at import time


def _build_workflow():
    g = StateGraph(WorkflowState)

    g.add_node("speech",         node_speech)
    g.add_node("intent",         node_intent)
    g.add_node("planner",        node_planner)
    g.add_node("human_approval", node_human_approval)
    g.add_node("browser",        node_browser)
    g.add_node("download",       node_download)
    g.add_node("install",        node_install)
    g.add_node("verify",         node_verify)
    g.add_node("response",       node_response)
    g.add_node("tts",            node_tts)
    g.add_node("notify",         node_notify)

    g.set_entry_point("speech")

    g.add_conditional_edges("speech", _route_after_speech, {
        "intent": "intent", "notify": "notify",
    })
    g.add_conditional_edges("intent", _route_after_intent, {
        "planner": "planner", "notify": "notify",
    })
    g.add_conditional_edges("planner", _route_after_planner, {
        "human_approval": "human_approval",
        "browser":        "browser",
        "notify":         "notify",
    })
    g.add_conditional_edges("human_approval", _route_after_approval, {
        "browser": "browser", "notify": "notify",
    })
    g.add_conditional_edges("browser", _route_after_browser, {
        "download": "download", "notify": "notify",
    })
    g.add_conditional_edges("download", _route_after_download, {
        "install":  "install",
        "response": "response",
        "notify":   "notify",
    })
    g.add_conditional_edges("install", _route_after_install, {
        "verify": "verify",
    })
    g.add_conditional_edges("verify", _route_after_verify, {
        "response": "response",
    })
    g.add_conditional_edges("response", _route_after_response, {
        "tts": "tts",
    })
    g.add_conditional_edges("tts", _route_after_tts, {
        "notify": "notify",
    })
    g.add_edge("notify", END)

    return g.compile()


def _get_workflow():
    """FIX-9: Return lazily built workflow — safe to call from async context."""
    global _workflow
    if _workflow is None:
        _workflow = _build_workflow()
    return _workflow


# ──────────────────────────────────────────────
# Public entry-point
# ──────────────────────────────────────────────

async def run_voice_workflow(
    task: Task,
    audio_bytes: Optional[bytes] = None,
    text_query: Optional[str] = None,
    os_hint: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run the full pipeline for a voice or text command.
    Returns the final WorkflowState dict.
    Callers can read: error, install, download, intent, speech,
                      verify, response_text, tts_audio.
    """
    initial: WorkflowState = {
        "task":                task.model_dump(mode='json'),
        "audio_bytes":         audio_bytes,
        "text_query":          text_query,
        "os_hint":             os_hint,
        "speech":              None,
        "intent":              None,
        "plan":                None,
        "rag_context":         None,
        "browser":             None,
        "use_package_manager": False,
        "download":            None,
        "install":             None,
        "verify":              None,
        "response_text":       None,
        "tts_audio":           None,
        "error":               None,
        "current_step":        "start",
        "retry_count":         0,
        "awaiting_approval":   False,
    }
    logger.info(
        "[Workflow] Starting task=%s query=%r os_hint=%s",
        task.task_id, text_query or "(voice)", os_hint,
    )
    workflow = _get_workflow()   # FIX-9: lazy build
    final = await workflow.ainvoke(initial)
    logger.info(
        "[Workflow] Finished task=%s error=%s verified=%s",
        task.task_id, final.get("error"),
        (final.get("verify") or {}).get("verified"),
    )
    return final