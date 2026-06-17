"""
Test suite for VoiceOps agents.
Run: pytest tests/ -v
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.schemas import (
    AudioInput,
    Intent,
    IntentOutput,
    Language,
    OperatingSystem,
    SpeechOutput,
    Task,
    TaskStatus,
)


# ──────────────────────────────────────────────
# Intent Agent Tests
# ──────────────────────────────────────────────

class TestIntentAgent:
    @pytest.fixture
    def agent(self):
        from app.agents.intent_agent.agent import IntentAgent
        return IntentAgent()

    @pytest.fixture
    def speech(self):
        return SpeechOutput(
            query="Install VS Code",
            language=Language.EN,
            confidence=0.98,
            raw_transcript="Install VS Code",
            session_id="test-session",
            processing_time_ms=120,
        )

    @pytest.mark.asyncio
    async def test_install_vscode_en(self, agent, speech):
        result = await agent.extract(speech)
        assert result.intent == Intent.INSTALL_SOFTWARE
        assert "Visual Studio Code" in result.software_canonical
        assert result.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_download_only(self, agent):
        speech = SpeechOutput(
            query="Download Python 3.12",
            language=Language.EN,
            confidence=0.95,
            raw_transcript="Download Python 3.12",
            session_id="s2",
            processing_time_ms=100,
        )
        result = await agent.extract(speech)
        assert result.intent == Intent.DOWNLOAD_ONLY
        assert "Python" in result.software_canonical

    @pytest.mark.asyncio
    async def test_windows_os_detection(self, agent):
        speech = SpeechOutput(
            query="Install Docker for Windows",
            language=Language.EN,
            confidence=0.95,
            raw_transcript="Install Docker for Windows",
            session_id="s3",
            processing_time_ms=100,
        )
        result = await agent.extract(speech)
        assert result.operating_system == OperatingSystem.WINDOWS

    @pytest.mark.asyncio
    async def test_hinglish_query(self, agent):
        speech = SpeechOutput(
            query="VS Code install karo",
            language=Language.HI_EN,
            confidence=0.87,
            raw_transcript="VS Code install karo",
            session_id="s4",
            processing_time_ms=150,
        )
        result = await agent.extract(speech)
        # Should pick up VS Code even in Hinglish
        assert "Visual Studio Code" in result.software_canonical or "vs code" in result.software_name.lower()


# ──────────────────────────────────────────────
# Planner Agent Tests
# ──────────────────────────────────────────────

class TestPlannerAgent:
    @pytest.fixture
    def agent(self):
        from app.agents.planner_agent.agent import PlannerAgent
        return PlannerAgent()

    @pytest.fixture
    def vscode_intent(self):
        return IntentOutput(
            intent=Intent.INSTALL_SOFTWARE,
            software_name="VS Code",
            software_canonical="Visual Studio Code",
            operating_system=OperatingSystem.WINDOWS,
            confidence=0.95,
            raw_query="Install VS Code",
        )

    @pytest.mark.asyncio
    async def test_plan_has_steps(self, agent, vscode_intent):
        plan = await agent.plan(vscode_intent)
        assert len(plan.steps) > 0

    @pytest.mark.asyncio
    async def test_winget_plan_for_vscode(self, agent, vscode_intent):
        plan = await agent.plan(vscode_intent)
        # VS Code is in winget list — should produce pkg_manager step
        step_names = [s.name for s in plan.steps]
        assert "pkg_manager_install" in step_names

    @pytest.mark.asyncio
    async def test_linux_apt_plan(self, agent):
        intent = IntentOutput(
            intent=Intent.INSTALL_SOFTWARE,
            software_name="git",
            software_canonical="Git",
            operating_system=OperatingSystem.LINUX,
            confidence=0.95,
            raw_query="Install git",
        )
        plan = await agent.plan(intent)
        step_names = [s.name for s in plan.steps]
        assert "pkg_manager_install" in step_names
        # Verify it uses apt
        pkg_step = next(s for s in plan.steps if s.name == "pkg_manager_install")
        assert pkg_step.tool == "apt"


# ──────────────────────────────────────────────
# Download Agent Tests
# ──────────────────────────────────────────────

class TestDownloadAgent:
    @pytest.fixture
    def agent(self):
        from app.agents.download_agent.agent import DownloadAgent
        return DownloadAgent()

    @pytest.fixture
    def trusted_link(self):
        from app.models.schemas import DownloadLink
        return DownloadLink(
            url="https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user",
            source_domain="code.visualstudio.com",
            is_official=True,
            file_name="vscode_setup.exe",
        )

    @pytest.fixture
    def untrusted_link(self):
        from app.models.schemas import DownloadLink
        return DownloadLink(
            url="http://sketchy-site.ru/vscode.exe",
            source_domain="sketchy-site.ru",
            is_official=False,
            file_name="vscode.exe",
        )

    @pytest.mark.asyncio
    async def test_rejects_non_https(self, agent, untrusted_link):
        result = await agent.download_and_verify(untrusted_link)
        assert result.success is False
        assert "HTTPS" in result.error or "trusted" in result.error.lower()

    @pytest.mark.asyncio
    async def test_rejects_unofficial_source(self, agent):
        from app.models.schemas import DownloadLink
        link = DownloadLink(
            url="https://sketchy-site.ru/vscode.exe",
            source_domain="sketchy-site.ru",
            is_official=False,
            file_name="vscode.exe",
        )
        result = await agent.download_and_verify(link)
        assert result.success is False


# ──────────────────────────────────────────────
# Browser Agent Tests
# ──────────────────────────────────────────────

class TestBrowserAgent:
    @pytest.fixture
    def intent(self):
        return IntentOutput(
            intent=Intent.INSTALL_SOFTWARE,
            software_name="VS Code",
            software_canonical="Visual Studio Code",
            operating_system=OperatingSystem.WINDOWS,
            confidence=0.95,
            raw_query="Install VS Code",
        )

    @pytest.mark.asyncio
    async def test_is_trusted_domain(self):
        from app.agents.browser_agent.agent import _is_trusted
        assert _is_trusted("https://code.visualstudio.com/download/vscode.exe")
        assert _is_trusted("https://python.org/downloads/python-3.12.exe")
        assert not _is_trusted("https://totallylegit.ru/vscode.exe")

    @pytest.mark.asyncio
    async def test_link_scoring(self):
        from app.agents.browser_agent.agent import _score_link
        score_win  = _score_link("https://example.com/vscode-1.89.exe", OperatingSystem.WINDOWS)
        score_mac  = _score_link("https://example.com/vscode.dmg", OperatingSystem.WINDOWS)
        assert score_win > score_mac


# ──────────────────────────────────────────────
# Task Store Tests
# ──────────────────────────────────────────────

class TestTaskStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self):
        from app.services.task_store import TaskStore
        import uuid

        store   = TaskStore()
        task_id = str(uuid.uuid4())
        task    = Task(
            task_id=task_id,
            user_id="user-123",
            session_id="sess-abc",
            query="Install Python",
            status=TaskStatus.PENDING,
        )

        with (
            patch.object(store, '_get_redis', new_callable=AsyncMock) as mock_redis,
        ):
            mock_r = AsyncMock()
            mock_redis.return_value = mock_r
            mock_r.set = AsyncMock()
            mock_r.get = AsyncMock(return_value=task.model_dump_json())

            from app.services import task_store as ts_module
            with patch.object(ts_module, '_session_fac') as mock_sf:
                mock_session = AsyncMock()
                mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_sf.return_value.__aexit__ = AsyncMock(return_value=None)
                mock_session.get = AsyncMock(return_value=None)
                mock_session.add = MagicMock()
                mock_session.commit = AsyncMock()

                await store.save(task)
                result = await store.get(task_id)
                assert result is not None
                assert result.task_id == task_id