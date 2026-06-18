"""
Master LangGraph Workflow — orchestrates all agents.  (REWRITTEN)

ARCHITECTURE:
  1. Speech Agent          → STT (Whisper / Sarvam)
  2. Intent Agent          → LLM-based intent + raw software-name extraction
  3. Planner Agent         → resolves software via live package-manager search
                              (SoftwareResolverAgent) and builds an execution plan
  4. Browser Agent         → Playwright (only when no package-manager match exists)
  5. Download Agent        → httpx + SHA-256 (only for browser-discovered installers)
  6. Install Agent         → winget / brew / apt / installer file — OR, for
                              download_only intent, downloads the installer to
                              Desktop WITHOUT running it
  6b. Verify Agent         → post-install verification (install path only)
  7. Response Agent        → composes the final natural-language response from
                              what ACTUALLY happened — never a canned success string
  8. TTS Agent              → Sarvam TTS voice output
  9. Notification Agent     → Redis pub/sub

WHY THIS WAS REWRITTEN (root causes fixed):

  FIX-DOWNLOAD-ONLY (the reported bug — "download chatgpt" did nothing):
    The previous node_download(), whenever use_package_manager was true,
    immediately wrote a fake DownloadResult with
    local_path="__use_package_manager__" and routed straight to "response"
    for download_only intent — meaning NOTHING was ever executed and
    node_response synthesized a "has been installed successfully" sentence
    unconditionally. Now: download_only intent calls
    install_agent.download_only(), which runs `winget download` for real
    and leaves an actual installer file on the user's Desktop. install_only
    intent still flows through node_install as before.

  FIX-EVENT-LOOP: Agents that hold a Redis client bound to the event loop
    they were constructed in (MonitoringAgent, NotificationAgent) were
    previously cached as module-level singletons in `_get_agents()`, while
    each Celery task runs inside its OWN fresh event loop via
    `asyncio.run()`. The second task onward reused a Redis connection bound
    to a now-closed loop -> "Event loop is closed". Fix: agents that hold
    any loop-bound resource are now constructed FRESH per workflow
    invocation (see `_build_fresh_agents()`); only fully stateless agents
    remain cached.

  FIX-NO-FAKE-SUCCESS: node_response no longer has an unconditional
    "X has been installed/downloaded successfully" branch. It only reports
    success when the corresponding result object's `success` field is
    actually True, and for the install path additionally checks the verify
    result before saying "verified" language.
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
# Agent construction
#
# FIX-EVENT-LOOP: Stateless agents (no cached event-loop-bound resources)
# are safe to keep as process-wide singletons for performance. Agents that
# lazily cache a Redis client bound to "whichever loop was running when
# first called" (MonitoringAgent, NotificationAgent) are rebuilt fresh on
# every workflow invocation so they always bind to the CURRENT loop.
# ──────────────────────────────────────────────

_speech_agent   = None
_intent_agent   = None
_planner_agent  = None
_download_agent = None
_install_agent  = None
_verify_agent   = None
_tts_agent      = None


def _get_stateless_agents():
    """Lazy-load the agents that hold no loop-bound resources."""
    global _speech_agent, _intent_agent, _planner_agent, _download_agent
    global _install_agent, _verify_agent, _tts_agent

    if _speech_agent is None:
        from app.agents.download_agent.agent import DownloadAgent
        from app.agents.install_agent.agent import InstallAgent
        from app.agents.intent_agent.agent import IntentAgent
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

    return (
        _speech_agent, _intent_agent, _planner_agent,
        _download_agent, _install_agent, _verify_agent, _tts_agent,
    )


def _build_fresh_loop_bound_agents():
    """
    FIX-EVENT-LOOP: construct MonitoringAgent/NotificationAgent fresh for
    THIS workflow run, bound to whatever event loop is currently running.
    Cheap to construct (just sets self._redis = None internally; the actual
    aioredis client is created lazily on first use inside the live loop).
    """
    from app.agents.monitoring_agent.agent import MonitoringAgent, NotificationAgent
    return MonitoringAgent(), NotificationAgent()


# ──────────────────────────────────────────────
# Node implementations
# ──────────────────────────────────────────────

async def node_speech(state: WorkflowState) -> WorkflowState:
    state["current_step"] = "speech"
    try:
        speech_agent, *_ = _get_stateless_agents()
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
        _, intent_agent, *_ = _get_stateless_agents()
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
        _, _, planner_agent, *_ = _get_stateless_agents()
        intent = IntentOutput(**state["intent"])

        if not intent.software_canonical and intent.intent == Intent.UNKNOWN:
            state["error"] = (
                f"Couldn't understand what to install from: "
                f"\"{intent.raw_query}\". Please try again with the software name."
            )
            return state

        plan: ExecutionPlan = await planner_agent.plan(intent)
        state["plan"] = plan.model_dump(mode='json')

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
        state["use_package_manager"] = bool(steps) and steps[0].name in (
            "pkg_manager_install", "pkg_manager_download_only",
        )

        if not steps:
            state["error"] = (
                f"Couldn't find an installable package for "
                f"\"{intent.software_canonical}\" on {intent.operating_system.value}, "
                f"and no official download page could be located either."
            )
            return state

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

    import redis.asyncio as aioredis
    from app.models.schemas import NotificationEvent

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
    """
    FIX-DOWNLOAD-ONLY: this node now branches explicitly on intent.

    - download_only + package-manager-resolved software: executes a REAL
      download (winget download / brew fetch) straight to the user's
      Desktop via InstallAgent.download_only(). The result is the actual
      DownloadResult — no install ever runs, and the response node will
      only claim success if this DownloadResult.success is True.
    - install_software + package-manager-resolved software: still defers
      the actual download+install to node_install's single winget/brew/apt
      call (download-then-install is one atomic package-manager operation),
      so this node just passes through a marker as before.
    - Anything resolved via the browser path: unchanged real download via
      DownloadAgent.
    """
    state["current_step"] = "download"
    if state.get("error"):
        return state

    try:
        _, _, _, download_agent, install_agent, *_ = _get_stateless_agents()
        intent = IntentOutput(**state["intent"]) if state.get("intent") else None

        if state.get("use_package_manager"):
            plan_data = state.get("plan") or {}
            steps = plan_data.get("steps") or []
            first_step = steps[0] if steps else {}
            params = first_step.get("params", {})
            pkg_id = params.get("package_id")
            install_method = params.get("install_method")

            if intent and intent.intent == Intent.DOWNLOAD_ONLY:
                result: DownloadResult = await install_agent.download_only(
                    software=intent.software_canonical,
                    os_target=intent.operating_system,
                    package_id=pkg_id,
                    install_method=install_method,
                )
                state["download"] = result.model_dump(mode='json')
                if not result.success:
                    state["error"] = result.error or "Download failed"
                return state

            # install_software path: defer to node_install's atomic
            # download+install package-manager call.
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
        _, _, _, _, install_agent, *_ = _get_stateless_agents()
        download = DownloadResult(**state["download"])
        intent   = IntentOutput(**state["intent"])

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
    """Post-install verification node — install path only."""
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
        _, _, _, _, _, verify_agent, *_ = _get_stateless_agents()
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
    """
    Composes the final natural-language response from what ACTUALLY
    happened. FIX-NO-FAKE-SUCCESS: there is no unconditional success
    template here — every success branch is gated on the relevant result
    object's own `success` field.
    """
    state["current_step"] = "response"
    try:
        intent_data  = state.get("intent") or {}
        download_data = state.get("download") or {}
        install_data = state.get("install") or {}
        verify_data  = state.get("verify") or {}
        error        = state.get("error")

        software = intent_data.get("software_canonical") or "the software"
        os_name  = intent_data.get("operating_system", "windows")
        is_download_only = intent_data.get("intent") == Intent.DOWNLOAD_ONLY.value

        if error:
            verb = "download" if is_download_only else "install"
            text = (
                f"I was unable to {verb} {software}. "
                f"The error was: {error}. Please try again or do it manually."
            )

        elif is_download_only:
            if download_data.get("success") and download_data.get("local_path"):
                file_name = download_data.get("file_name") or "the installer"
                text = (
                    f"{software} installer ({file_name}) has been downloaded to your "
                    f"Desktop. It has NOT been installed — run it yourself when you're ready."
                )
            else:
                err = download_data.get("error") or "the download did not complete"
                text = f"I couldn't download {software}. Reason: {err}."

        elif install_data.get("success"):
            version = (
                verify_data.get("version_found")
                or install_data.get("version_installed")
            )
            method = install_data.get("install_method", "package manager")
            verified = bool(verify_data.get("verified", False))

            if verified:
                verification_note = (
                    f" Verified on your system" + (f" — version {version}." if version else ".")
                )
            else:
                # FIX-NO-FAKE-SUCCESS: install reported success, but our own
                # verify step could not independently confirm it. Say so
                # plainly rather than declaring an unqualified success.
                verification_note = (
                    " The installer reported success, but I could not "
                    "independently confirm the install on your system yet — "
                    "it may need a moment, or a restart of your terminal/PATH."
                )

            text = (
                f"{software} has been installed on your {os_name} system "
                f"using {method}.{verification_note}"
            )
        else:
            err = install_data.get("error") or error or "unknown reason"
            text = (
                f"I was unable to install {software}. Reason: {err}. "
                "Please check the logs or try installing manually."
            )

        if settings.llm.mistral_api_key:
            try:
                text = await _enrich_response_with_llm(text, software, error)
            except Exception as llm_exc:
                logger.debug("[Workflow] LLM response enrichment skipped: %s", llm_exc)

        state["response_text"] = text
        logger.info("[Workflow] Response: %r", text[:160])
    except Exception as exc:
        logger.warning("[Workflow] Response generation failed: %s", exc)
        state["response_text"] = (
            "Something went wrong while preparing the response, but no success "
            "should be assumed — please check the task status."
        )
    return state


async def _enrich_response_with_llm(template: str, software: str, error: Optional[str]) -> str:
    """
    Uses Mistral to make the response more conversational. Explicitly
    instructed to preserve the factual content (success/failure, whether
    anything was actually installed vs only downloaded) — it may only
    restyle the wording, never invent additional claims of success.
    """
    from mistralai import Mistral
    client = Mistral(api_key=settings.llm.mistral_api_key)
    prompt = (
        f"You are a helpful voice assistant. Rephrase this status update into "
        f"a clear, friendly, one-sentence voice response. Preserve all factual "
        f"content exactly (what succeeded, what failed, what was/wasn't "
        f"installed vs only downloaded) — do not add any claim of success that "
        f"isn't already in the text below. Be concise (under 30 words). No markdown.\n\n"
        f"Status: {template}"
    )
    def _call():
        resp = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=80,
        )
        return resp.choices[0].message.content.strip()

    return await asyncio.to_thread(_call)


async def node_tts(state: WorkflowState) -> WorkflowState:
    """Sarvam TTS converts response text to audio."""
    state["current_step"] = "tts"
    text = state.get("response_text") or "Task completed."
    try:
        *_, tts_agent = _get_stateless_agents()
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
        # FIX-EVENT-LOOP: build fresh, bound to the loop that's running NOW
        monitor_agent, notify_agent = _build_fresh_loop_bound_agents()
        task = Task(**state["task"])

        if state.get("intent"):
            task.intent_output = IntentOutput(**state["intent"])
        if state.get("install"):
            task.install_result = InstallResult(**state["install"])

        software_name = ""
        if state.get("intent"):
            software_name = state["intent"].get("software_canonical", "")

        if state.get("error"):
            task.status = TaskStatus.FAILED
            task.error  = state["error"]
            sw_label = f" for {software_name}" if software_name else ""
            logger.info(
                "[Notification] task=%s status=failed msg=Failed%s: %s",
                task.task_id, sw_label, state["error"],
            )
        else:
            task.status       = TaskStatus.COMPLETED
            task.progress_pct = 100
            logger.info(
                "[Notification] task=%s status=completed sw=%s",
                task.task_id, software_name or "(unknown)",
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
    """
    FIX-DOWNLOAD-ONLY: download_only intent now terminates at "response"
    straight after a REAL download (no install ever runs for this intent —
    that's the whole point of "download" vs "install"). install_software
    intent continues to node_install as before.
    """
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

_workflow = None


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
    workflow = _get_workflow()
    final = await workflow.ainvoke(initial)
    logger.info(
        "[Workflow] Finished task=%s error=%s verified=%s",
        task.task_id, final.get("error"),
        (final.get("verify") or {}).get("verified"),
    )
    return final