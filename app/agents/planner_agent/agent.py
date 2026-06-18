"""
Agent 3 — Planner Agent (LangGraph Plan-and-Execute)  (REWRITTEN)

"""
from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents.resolver_agent.agent import ResolvedPackage, SoftwareResolverAgent
from app.config.settings import get_settings
from app.models.schemas import ExecutionPlan, ExecutionStep, Intent, IntentOutput, OperatingSystem

logger   = logging.getLogger(__name__)
settings = get_settings()

_resolver = SoftwareResolverAgent()


def _is_store_id(pkg_id: str) -> bool:
    """Microsoft Store IDs are uppercase alphanumeric, 9-16 chars, no dots."""
    return bool(re.fullmatch(r"[A-Z0-9]{9,16}", pkg_id))


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


def _pkg_manager_install_step(resolved: ResolvedPackage, sw: str, os_: OperatingSystem) -> ExecutionStep:
    return ExecutionStep(
        name="pkg_manager_install",
        description=f"Install {sw} via {resolved.install_method} ({resolved.package_id})",
        agent="install_agent",
        tool=resolved.install_method or "winget",
        params={
            "package_id": resolved.package_id,
            "software": resolved.display_name or sw,
            "install_method": resolved.install_method,
        },
    )


def _pkg_manager_download_only_step(resolved: ResolvedPackage, sw: str, os_: OperatingSystem) -> ExecutionStep:
    """
    FIX: download_only intent now produces a REAL executable step — download
    the installer file to the user's Desktop via the package manager's
    native download command, without running/installing it. Previously this
    intent produced no step at all and the workflow fabricated a "success"
    message with nothing actually happening on disk.
    """
    return ExecutionStep(
        name="pkg_manager_download_only",
        description=f"Download installer for {sw} to Desktop via {resolved.install_method} (no install)",
        agent="install_agent",
        tool="download_only",
        params={
            "package_id": resolved.package_id,
            "software": resolved.display_name or sw,
            "install_method": resolved.install_method,
        },
    )


# ── LangGraph ──────────────────────────────────────────────────────────────

class PlannerState(TypedDict):
    intent: IntentOutput
    steps: list[dict[str, Any]]
    estimated_seconds: int
    done: bool
    resolution_note: str


async def _node_generate(state: PlannerState) -> PlannerState:
    intent = state["intent"]
    sw, os_ = intent.software_canonical, intent.operating_system

    logger.info("[PlannerAgent] intent=%s sw=%s os=%s", intent.intent, sw, os_)

    steps: list[ExecutionStep] = []
    est = 0
    note = ""

    if intent.intent in (Intent.INSTALL_SOFTWARE, Intent.DOWNLOAD_ONLY):
        # Resolve against the LIVE package manager — never a hardcoded dict.
        resolved = await _resolver.resolve(sw, os_)
        note = resolved.note

        if resolved.found and resolved.package_id:
            if intent.intent == Intent.DOWNLOAD_ONLY:
                steps = [_pkg_manager_download_only_step(resolved, sw, os_)]
                est = 30
            else:
                steps = [_pkg_manager_install_step(resolved, sw, os_)]
                est = 60
        else:
            # Genuinely not findable via any package manager — fall back to
            # browser-based discovery of the vendor's own download page.
            logger.info(
                "[PlannerAgent] No package-manager match for %r — falling back to browser path (%s)",
                sw, resolved.note,
            )
            steps = _browser_steps(intent) + _download_steps()
            est = 120
            if intent.intent == Intent.INSTALL_SOFTWARE:
                steps += _install_steps(intent)
                est = 300

    state["steps"] = [s.model_dump(mode='json') for s in steps]
    state["estimated_seconds"] = est
    state["resolution_note"] = note
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
            "resolution_note": "",
        }
        final = await _compiled.ainvoke(init)
        return ExecutionPlan(
            intent=intent,
            steps=[ExecutionStep(**s) for s in final["steps"]],
            estimated_duration_sec=final["estimated_seconds"],
        )