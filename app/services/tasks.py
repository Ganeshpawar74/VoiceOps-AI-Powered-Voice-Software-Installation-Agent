"""
Celery app + task definitions for background installation jobs.
FastAPI enqueues tasks here; workers pull from Redis queue.

FIXES:
  FIX-1: _run_async() now uses asyncio.run() instead of deprecated
          asyncio.get_event_loop() — prevents deadlocks on Python 3.12 + Windows.
  FIX-2: SQLAlchemy async engine pool is disposed after each task so the next
          task's fresh event loop starts with clean connections.
  FIX-3 (NEW): settings.celery_max_retries and settings.celery_retry_backoff
          are now defined in AppSettings (settings.py FIX-5). Previously these
          caused AttributeError at import time, which made every attempt to check
          Celery availability crash and returned HTTP 500 on all API commands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from celery import Celery
from celery.utils.log import get_task_logger

from app.config.settings import get_settings

settings = get_settings()
logger   = get_task_logger(__name__)

# ──────────────────────────────────────────────
# Celery app
# ──────────────────────────────────────────────

celery_app = Celery(
    "voiceops",
    broker=settings.redis.celery_broker,
    backend=settings.redis.celery_backend,
)

celery_app.conf.update(
    task_serializer            = "json",
    result_serializer          = "json",
    accept_content             = ["json"],
    timezone                   = "UTC",
    enable_utc                 = True,
    task_soft_time_limit       = settings.celery_task_soft_time_limit,
    task_time_limit            = settings.celery_task_time_limit,
    task_acks_late             = True,   # re-queue on worker crash
    worker_prefetch_multiplier = 1,      # one install task per worker (heavy I/O)
    task_routes                = {
        "voiceops.tasks.run_voice_install": {"queue": "installs"},
        "voiceops.tasks.run_text_install":  {"queue": "installs"},
    },
    beat_schedule = {},
)


# ──────────────────────────────────────────────
# Async bridge — runs a coroutine from a sync Celery task
# ──────────────────────────────────────────────

def _run_async(coro):
    """
    Bridge between Celery's synchronous task execution and async workflow code.

    Uses asyncio.run() which creates a brand-new event loop per Celery task.
    This is correct because Celery worker processes are synchronous — there is
    no pre-existing running loop to conflict with.

    The SQLAlchemy async engine pool is disposed inside the same loop before it
    tears down, so the next task starts with clean connections.
    """
    async def _wrapper():
        try:
            return await coro
        finally:
            from app.services.task_store import dispose_engine
            await dispose_engine()
    return asyncio.run(_wrapper())


# ──────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="voiceops.tasks.run_voice_install",
    max_retries=settings.celery_max_retries,        # FIX-3: field now exists
    default_retry_delay=settings.celery_retry_backoff,  # FIX-3: field now exists
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def run_voice_install_task(
    self,
    task_id: str,
    user_id: str,
    session_id: str,
    audio_base64: str,
):
    """
    Celery task: decode audio, run the full voice workflow, persist result.
    Retries automatically on transient failures (network, timeout).
    """
    import base64
    from app.models.schemas import Task, TaskStatus
    from app.services.task_store import TaskStore

    async def _run():
        from app.workflows.main_workflow import run_voice_workflow

        audio_bytes = base64.b64decode(audio_base64)
        task = Task(
            task_id=task_id,
            user_id=user_id,
            session_id=session_id,
            query="(voice input)",
            status=TaskStatus.RUNNING,
        )

        store = TaskStore()
        await store.save(task)

        try:
            final_state = await run_voice_workflow(
                task=task,
                audio_bytes=audio_bytes,
            )
            task.status = (
                TaskStatus.COMPLETED
                if not final_state.get("error")
                else TaskStatus.FAILED
            )
            task.error  = final_state.get("error")
            task.result = {
                "install":       final_state.get("install"),
                "download":      final_state.get("download"),
                "intent":        final_state.get("intent"),
                "speech":        final_state.get("speech"),
                "verify":        final_state.get("verify"),
                "response_text": final_state.get("response_text"),
                "tts_audio":     final_state.get("tts_audio"),
            }
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            raise
        finally:
            await store.save(task)

        return task.model_dump()

    return _run_async(_run())


@celery_app.task(
    bind=True,
    name="voiceops.tasks.run_text_install",
    max_retries=settings.celery_max_retries,        # FIX-3: field now exists
    default_retry_delay=settings.celery_retry_backoff,  # FIX-3: field now exists
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def run_text_install_task(
    self,
    task_id: str,
    user_id: str,
    session_id: str,
    query: str,
    os_hint: Optional[str] = None,
):
    """
    Celery task: run the text workflow, persist result.
    os_hint is forwarded to the workflow as a string (OperatingSystem.value).
    """
    from app.models.schemas import Task, TaskStatus
    from app.services.task_store import TaskStore

    async def _run():
        from app.workflows.main_workflow import run_voice_workflow

        task = Task(
            task_id=task_id,
            user_id=user_id,
            session_id=session_id,
            query=query,
            status=TaskStatus.RUNNING,
        )

        store = TaskStore()
        await store.save(task)

        try:
            final_state = await run_voice_workflow(
                task=task,
                text_query=query,
                os_hint=os_hint,
            )
            task.status = (
                TaskStatus.COMPLETED
                if not final_state.get("error")
                else TaskStatus.FAILED
            )
            task.error  = final_state.get("error")
            task.result = {
                "install":       final_state.get("install"),
                "download":      final_state.get("download"),
                "speech":        final_state.get("speech"),
                "intent":        final_state.get("intent"),
                "verify":        final_state.get("verify"),
                "response_text": final_state.get("response_text"),
                "tts_audio":     final_state.get("tts_audio"),
            }
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error  = str(exc)
            raise
        finally:
            await store.save(task)

        return task.model_dump()

    return _run_async(_run())