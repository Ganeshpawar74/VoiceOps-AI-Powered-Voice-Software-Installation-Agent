"""
Agent 3 — Planner Agent (LangGraph Plan-and-Execute)

FIXES IN THIS VERSION:
  NEW FIX #PLANNER-LLM:
    Old behaviour: if software wasn't in winget_packages dict, immediately
    fell through to browser_steps (Playwright) — which then failed with
    NotImplementedError on Windows/Celery.

    Fix: Added _resolve_pkg_id_via_llm() — when the local registry doesn't
    have the package ID, ask Mistral for the winget ID. If the LLM returns
    a plausible package ID, use pkg_manager path. This keeps the vast majority
    of requests on the fast, reliable winget path and only falls through to
    browser for truly exotic software.

  NEW FIX #PLANNER-STORE:
    Microsoft Store apps (ChatGPT = 9NT1R1C2HH7J) cannot be installed via
    `winget install --id <id>` using the normal flow — they need
    `winget install --id <id> --source msstore`.
    The planner now detects Store IDs (all-caps alphanumeric) and passes
    install_method=winget_store in the plan params.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.config.settings import get_settings
from app.models.schemas import ExecutionPlan, ExecutionStep, Intent, IntentOutput, OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()


def _is_store_id(pkg_id: str) -> bool:
    """Microsoft Store IDs are uppercase alphanumeric, 12–14 chars, no dots."""
    return bool(re.fullmatch(r"[A-Z0-9]{9,16}", pkg_id))


def _get_local_pkg_id(software: str, os_: OperatingSystem) -> Optional[str]:
    """Look up package ID in the settings registry (no API call)."""
    lookup = {
        OperatingSystem.WINDOWS: settings.registry.winget_packages,
        OperatingSystem.MACOS:   settings.registry.brew_packages,
        OperatingSystem.LINUX:   settings.registry.apt_packages,
    }.get(os_, {})

    # Exact match
    if software in lookup:
        return lookup[software]

    # Case-insensitive match
    sw_lower = software.lower()
    for key, val in lookup.items():
        if key.lower() == sw_lower:
            return val

    # Linux snap fallback
    if os_ == OperatingSystem.LINUX:
        snap = settings.registry.snap_packages
        if software in snap:
            return snap[software]
        for key, val in snap.items():
            if key.lower() == sw_lower:
                return val

    return None


def _resolve_pkg_id_via_llm(software: str, os_: OperatingSystem) -> Optional[str]:
    """
    FIX #PLANNER-LLM: Ask Mistral for the package manager ID.
    Blocking — run in thread.
    """
    api_key = settings.llm.mistral_api_key
    if not api_key:
        return None
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)

        mgr = {
            OperatingSystem.WINDOWS: "winget",
            OperatingSystem.MACOS:   "Homebrew cask",
            OperatingSystem.LINUX:   "apt",
        }.get(os_, "winget")

        prompt = (
            f"What is the exact {mgr} package ID for '{software}'?\n"
            f"Reply with ONLY the package ID string (e.g. 'Google.Chrome' for winget, "
            f"'google-chrome' for brew, 'google-chrome-stable' for apt).\n"
            f"If you are not certain, reply UNKNOWN."
        )
        response = client.chat.complete(
            model=settings.llm.intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=40,
        )
        result = response.choices[0].message.content.strip().strip('"').strip("'")
        if result.upper() == "UNKNOWN" or len(result) < 2:
            return None
        logger.info("[PlannerAgent] LLM pkg id for %r (%s): %r", software, mgr, result)
        return result
    except Exception as exc:
        logger.warning("[PlannerAgent] LLM pkg id lookup failed: %s", exc)
        return None


async def _resolve_pkg_id(software: str, os_: OperatingSystem) -> Optional[str]:
    """Resolve package ID: local registry first, then LLM."""
    pkg_id = _get_local_pkg_id(software, os_)
    if pkg_id:
        return pkg_id
    # LLM fallback (async-safe via thread)
    return await asyncio.to_thread(_resolve_pkg_id_via_llm, software, os_)


def _browser_steps(intent: IntentOutput) -> list[ExecutionStep]:
    sw, os_ = intent.software_canonical, intent.operating_system
    return [
        ExecutionStep(
            name="search_official_source",
            description=f"Find official download page for {sw}",
            agent="browser_agent", tool="web_search",
            params={"query": f"{sw} official download {os_.value}"},
        ),
        ExecutionStep(
            name="find_download_link",
            description="Extract installer link from official page",
            agent="browser_agent", tool="navigate_and_extract",
            params={"os": os_.value, "arch": "x64"},
            depends_on=["search_official_source"],
        ),
    ]


def _download_steps() -> list[ExecutionStep]:
    return [
        ExecutionStep(
            name="download_installer",
            description="Download installer file",
            agent="download_agent", tool="download_file",
            params={}, depends_on=["find_download_link"],
        ),
        ExecutionStep(
            name="verify_installer",
            description="SHA-256 + signature verification",
            agent="download_agent", tool="verify_file",
            params={}, depends_on=["download_installer"],
        ),
    ]


def _install_steps(intent: IntentOutput) -> list[ExecutionStep]:
    tool = {
        "windows": "run_exe_or_msi",
        "macos":   "run_dmg_pkg",
        "linux":   "run_deb_rpm",
    }.get(intent.operating_system.value, "run_exe_or_msi")
    return [
        ExecutionStep(
            name="run_installer",
            description="Silent installation",
            agent="install_agent", tool=tool,
            params={"software": intent.software_canonical, "silent": True},
            depends_on=["verify_installer"],
        ),
        ExecutionStep(
            name="notify_user",
            description="Notify user of completion",
            agent="notification_agent", tool="notify",
            params={"software": intent.software_canonical},
            depends_on=["run_installer"],
        ),
    ]


# ── LangGraph ──────────────────────────────────────────────────────────────

class PlannerState(TypedDict):
    intent: IntentOutput
    steps: list[dict[str, Any]]
    estimated_seconds: int
    done: bool


async def _node_generate(state: PlannerState) -> PlannerState:
    intent = state["intent"]
    sw, os_ = intent.software_canonical, intent.operating_system

    logger.info("[PlannerAgent] intent=%s sw=%s os=%s", intent.intent, sw, os_)

    steps: list[ExecutionStep] = []
    est = 0

    if intent.intent in (Intent.INSTALL_SOFTWARE, Intent.DOWNLOAD_ONLY):
        # Try package manager path first (fast, reliable, no browser needed)
        pkg_id = await _resolve_pkg_id(sw, os_)

        if pkg_id:
            mgr = {
                OperatingSystem.WINDOWS: "winget",
                OperatingSystem.MACOS:   "brew",
                OperatingSystem.LINUX:   "apt",
            }.get(os_, "winget")

            # Detect Microsoft Store IDs
            install_method = "winget_store" if _is_store_id(pkg_id) else mgr

            steps = [
                ExecutionStep(
                    name="pkg_manager_install",
                    description=f"Install {sw} via {mgr} ({pkg_id})",
                    agent="install_agent",
                    tool=mgr,
                    params={
                        "package_id": pkg_id,
                        "software": sw,
                        "install_method": install_method,
                    },
                )
            ]
            est = 60 if intent.intent == Intent.INSTALL_SOFTWARE else 30

        else:
            # Browser + download + install path
            steps = _browser_steps(intent) + _download_steps()
            est = 120
            if intent.intent == Intent.INSTALL_SOFTWARE:
                steps += _install_steps(intent)
                est = 300

    state["steps"] = [s.model_dump(mode='json') for s in steps]
    state["estimated_seconds"] = est
    return state


def _node_validate(state: PlannerState) -> PlannerState:
    if not state["steps"]:
        logger.warning(
            "[PlannerAgent] No steps for intent=%s software=%s",
            state["intent"].intent, state["intent"].software_canonical,
        )
    state["done"] = True
    return state


def _build_graph() -> StateGraph:
    g = StateGraph(PlannerState)
    g.add_node("generate", _node_generate)
    g.add_node("validate", _node_validate)
    g.set_entry_point("generate")
    g.add_edge("generate", "validate")
    g.add_edge("validate", END)
    return g


_compiled = _build_graph().compile()


class PlannerAgent:
    """Agent 3 — Planner. LangGraph Plan-and-Execute."""

    async def plan(self, intent: IntentOutput) -> ExecutionPlan:
        logger.info(
            "[PlannerAgent] intent=%s sw=%s os=%s",
            intent.intent, intent.software_canonical, intent.operating_system,
        )
        init: PlannerState = {
            "intent": intent,
            "steps": [],
            "estimated_seconds": 0,
            "done": False,
        }
        final = await _compiled.ainvoke(init)
        return ExecutionPlan(
            intent=intent,
            steps=[ExecutionStep(**s) for s in final["steps"]],
            estimated_duration_sec=final["estimated_seconds"],
        )
