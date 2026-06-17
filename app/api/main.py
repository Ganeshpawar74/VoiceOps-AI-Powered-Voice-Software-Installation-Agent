"""
VoiceOps FastAPI Application

"""

from __future__ import annotations
import traceback

import asyncio
import base64
import logging
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from dotenv import load_dotenv
load_dotenv()

from app.config.settings import get_settings
from app.models.schemas import (
    Task, TaskResponse, TaskStatus, TaskStatusResponse,
    TextCommandRequest, VoiceCommandRequest,
)
from app.services.task_store import TaskStore, init_db

logger   = logging.getLogger(__name__)
settings = get_settings()

import time as _time_mod
BUILD_TAG  = "main.py loaded from: " + __file__
BUILD_TIME = _time_mod.strftime("%Y-%m-%d %H:%M:%S", _time_mod.localtime())

# ── Prometheus ─────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "voiceops_requests_total", "Total API requests",
    ["method", "endpoint", "status"],
)
TASK_DURATION = Histogram(
    "voiceops_task_duration_seconds", "Task execution duration",
    ["task_type"],
)

# ── Dev mode ───────────────────────────────────────────────
_DEV_MODE_SECRETS = {
    "CHANGE_IN_PRODUCTION_USE_LONG_RANDOM_STRING",
    "DEV_MODE_SECRET_LOCAL_ONLY",
    "change_this_to_a_long_random_string_in_production",
    "",
}

def _is_dev_mode() -> bool:
    return settings.security.jwt_secret in _DEV_MODE_SECRETS


# ── Redis factory ──────────────────────────────────────────
def _make_redis() -> aioredis.Redis:
    return aioredis.from_url(
        str(settings.redis.url),
        decode_responses=True,
        socket_keepalive=settings.redis.socket_keepalive,
        socket_connect_timeout=settings.redis.socket_connect_timeout,
    )


# ── In-process workflow runners ────────────────────────────

async def _run_voice_workflow_inprocess(
    task_id: str, user_id: str, session_id: str, audio_base64: str,
) -> None:
    from app.workflows.main_workflow import run_voice_workflow
    store = TaskStore()
    task  = Task(
        task_id=task_id, user_id=user_id, session_id=session_id,
        query="(voice input — pending transcription)",
        status=TaskStatus.RUNNING,
    )
    await store.save(task)
    try:
        audio_bytes = base64.b64decode(audio_base64)
        final_state = await run_voice_workflow(task=task, audio_bytes=audio_bytes)
        task.status = TaskStatus.COMPLETED if not final_state.get("error") else TaskStatus.FAILED
        task.error  = final_state.get("error")
        task.result = _build_result(final_state)
    except Exception as exc:
        logger.error("[InProcess] Voice workflow failed: %s", exc, exc_info=True)
        task.status = TaskStatus.FAILED
        task.error  = str(exc)
    finally:
        await store.save(task)


async def _run_text_workflow_inprocess(
    task_id: str, user_id: str, session_id: str, query: str,
    os_hint: Optional[str] = None,
) -> None:
    from app.workflows.main_workflow import run_voice_workflow
    store = TaskStore()
    task  = Task(
        task_id=task_id, user_id=user_id, session_id=session_id,
        query=query, status=TaskStatus.RUNNING,
    )
    await store.save(task)
    try:
        final_state = await run_voice_workflow(task=task, text_query=query, os_hint=os_hint)
        task.status = TaskStatus.COMPLETED if not final_state.get("error") else TaskStatus.FAILED
        task.error  = final_state.get("error")
        task.result = _build_result(final_state)
    except Exception as exc:
        logger.error("[InProcess] Text workflow failed: %s", exc, exc_info=True)
        task.status = TaskStatus.FAILED
        task.error  = str(exc)
    finally:
        await store.save(task)


def _build_result(final_state: dict) -> dict:
    """Build result dict including new fields from verify/response/tts nodes."""
    return {
        "install":       final_state.get("install"),
        "download":      final_state.get("download"),
        "intent":        final_state.get("intent"),
        "speech":        final_state.get("speech"),
        "verify":        final_state.get("verify"),
        "response_text": final_state.get("response_text"),
        "tts_audio":     final_state.get("tts_audio"),
    }


# ── Celery availability check ──────────────────────────────
def _celery_available() -> bool:
    try:
        url  = urlparse(str(settings.redis.celery_broker))
        host = url.hostname or "localhost"
        port = url.port or 6379
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False


# ── Dispatch helpers ───────────────────────────────────────
async def _dispatch_voice(task_id, user_id, session_id, audio_base64) -> None:
    broker_up = await asyncio.to_thread(_celery_available)
    if broker_up:
        try:
            from app.services.tasks import run_voice_install_task
            await asyncio.to_thread(
                lambda: run_voice_install_task.delay(
                    task_id=task_id, user_id=user_id,
                    session_id=session_id, audio_base64=audio_base64,
                )
            )
            logger.info("[Dispatch] Voice task %s queued via Celery", task_id)
            return
        except Exception as exc:
            logger.warning("[Dispatch] Celery voice send failed (%s) — in-process", exc)

    logger.info("[Dispatch] Running voice task %s in-process (no Redis)", task_id)
    asyncio.create_task(_run_voice_workflow_inprocess(task_id, user_id, session_id, audio_base64))


async def _dispatch_text(task_id, user_id, session_id, query, os_hint) -> None:
    broker_up = await asyncio.to_thread(_celery_available)
    if broker_up:
        try:
            from app.services.tasks import run_text_install_task
            await asyncio.to_thread(
                lambda: run_text_install_task.delay(
                    task_id=task_id, user_id=user_id,
                    session_id=session_id, query=query, os_hint=os_hint,
                )
            )
            logger.info("[Dispatch] Text task %s queued via Celery", task_id)
            return
        except Exception as exc:
            logger.warning("[Dispatch] Celery text send failed (%s) — in-process", exc)

    logger.info("[Dispatch] Running text task %s in-process (no Redis)", task_id)
    asyncio.create_task(_run_text_workflow_inprocess(task_id, user_id, session_id, query, os_hint))


# ── Lifespan ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=settings.observability.log_level)
    logger.info("Starting VoiceOps — initialising DB and Redis pool")
    logger.info("Auth mode: %s", "DEV (no JWT)" if _is_dev_mode() else "PRODUCTION (JWT required)")
    logger.info("BUILD: %s  (started %s)", BUILD_TAG, BUILD_TIME)
    await init_db()
    app.state.redis = _make_redis()
    yield
    logger.info("VoiceOps shutting down")
    try:
        await app.state.redis.aclose()
    except Exception:
        pass


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global exception handler (always JSON) ──────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("[500] Unhandled exception on %s %s:\n%s",
                 request.method, request.url.path, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__, "traceback": tb},
    )


# ── Prometheus middleware ───────────────────────────────────
@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    t0       = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - t0
    endpoint = request.url.path
    REQUEST_COUNT.labels(
        method=request.method, endpoint=endpoint, status=str(response.status_code),
    ).inc()
    if endpoint.endswith("/voice/command") or endpoint.endswith("/text/command"):
        TASK_DURATION.labels(task_type="voice" if "voice" in endpoint else "text").observe(duration)
    return response


# ── Dependencies ───────────────────────────────────────────
def get_task_store() -> TaskStore:
    return TaskStore()

def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis

async def get_current_user(request: Request) -> str:
    if _is_dev_mode():
        return request.headers.get("X-User-ID", "anonymous")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Empty Bearer token")
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(
            token, settings.security.jwt_secret,
            algorithms=[settings.security.jwt_algorithm],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Token missing 'sub' claim")
        return user_id
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc


# ── Routes ─────────────────────────────────────────────────
@app.get("/api/health")
async def health(redis: aioredis.Redis = Depends(get_redis)):
    components: dict[str, str] = {}
    try:
        await redis.ping()
        components["redis"] = "ok"
    except Exception as exc:
        components["redis"] = f"error: {exc}"
    try:
        from app.services import task_store as _ts
        async with _ts._engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        components["database"] = "ok"
    except Exception as exc:
        components["database"] = f"unavailable: {exc}"
    # FIX: compute overall AFTER all components are populated
    overall = "ok" if all(v == "ok" for v in components.values()) else "degraded"
    return {
        "status": overall,
        "version": settings.app_version,
        "build": BUILD_TAG,
        "build_time": BUILD_TIME,
        "components": components,
        "auth_mode": "dev" if _is_dev_mode() else "production",
    }


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post(f"{settings.api_prefix}/voice/command", response_model=TaskResponse)
async def voice_command(
    req: VoiceCommandRequest,
    user_id: str = Depends(get_current_user),
    store: TaskStore = Depends(get_task_store),
):
    task_id       = str(uuid.uuid4())
    session_id    = req.session_id or str(uuid.uuid4())
    resolved_user = req.user_id or user_id

    task = Task(
        task_id=task_id, user_id=resolved_user, session_id=session_id,
        query="(voice input — pending transcription)",
        status=TaskStatus.PENDING,
    )
    await store.save(task)
    await _dispatch_voice(task_id, resolved_user, session_id, req.audio_base64)
    return TaskResponse(
        task_id=task_id, status=TaskStatus.PENDING,
        message="Voice command received and queued for processing.",
    )


@app.post(f"{settings.api_prefix}/text/command", response_model=TaskResponse)
async def text_command(
    req: TextCommandRequest,
    user_id: str = Depends(get_current_user),
    store: TaskStore = Depends(get_task_store),
):
    task_id       = str(uuid.uuid4())
    session_id    = req.session_id or str(uuid.uuid4())
    resolved_user = req.user_id or user_id

    task = Task(
        task_id=task_id, user_id=resolved_user, session_id=session_id,
        query=req.query, status=TaskStatus.PENDING,
    )
    await store.save(task)

    # BUG-API-1 FIX: req.os_hint is Optional[OperatingSystem] — guard before .value
    os_hint_str = req.os_hint.value if req.os_hint else None

    await _dispatch_text(
        task_id, resolved_user, session_id, req.query, os_hint_str,
    )
    return TaskResponse(
        task_id=task_id, status=TaskStatus.PENDING,
        message=f"Command queued: {req.query}",
    )


@app.get(f"{settings.api_prefix}/tasks/{{task_id}}", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: str,
    store: TaskStore = Depends(get_task_store),
):
    task = await store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskStatusResponse(
        task_id=task.task_id, status=task.status,
        progress_pct=task.progress_pct, current_step=task.current_step,
        result=task.result, error=task.error,
        created_at=task.created_at, updated_at=task.updated_at,
    )


@app.get(f"{settings.api_prefix}/tasks")
async def list_tasks(
    user_id: str = Depends(get_current_user),
    store: TaskStore = Depends(get_task_store),
):
    tasks = await store.list_for_user(user_id)
    return [
        {
            "task_id": t.task_id, "query": t.query,
            "status": t.status.value, "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]


@app.get(f"{settings.api_prefix}/tasks/{{task_id}}/stream")
async def task_progress_stream(
    task_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    async def _event_generator() -> AsyncGenerator[str, None]:
        channel = f"voiceops:progress:{task_id}"
        try:
            async with redis.pubsub() as pubsub:
                await pubsub.subscribe(channel)
                try:
                    while True:
                        try:
                            message = await asyncio.wait_for(
                                pubsub.get_message(ignore_subscribe_messages=True),
                                timeout=30.0,
                            )
                        except asyncio.TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                        except asyncio.CancelledError:
                            break
                        if message and message["type"] == "message":
                            yield f"data: {message['data']}\n\n"
                            try:
                                import json as _json
                                payload = _json.loads(message["data"])
                                pct = payload.get("data", {}).get("progress_pct")
                                if pct in (100, 0):
                                    break
                            except Exception:
                                pass
                finally:
                    try:
                        await pubsub.unsubscribe(channel)
                    except Exception:
                        pass
        except Exception as exc:
            yield f'data: {{"event":"error","message":"Stream unavailable: {exc}"}}\n\n'

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.websocket(f"{settings.api_prefix}/ws/{{user_id}}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    redis: aioredis.Redis = Depends(get_redis),
):
    await websocket.accept()
    channel = f"voiceops:notify:{user_id}"
    try:
        async with redis.pubsub() as pubsub:
            await pubsub.subscribe(channel)
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True), timeout=30.0,
                        )
                    except asyncio.TimeoutError:
                        await websocket.send_text('{"type":"ping"}')
                        continue
                    except asyncio.CancelledError:
                        break
                    if message and message["type"] == "message":
                        await websocket.send_text(message["data"])
            except WebSocketDisconnect:
                logger.info("[WS] user=%s disconnected", user_id)
            except Exception as exc:
                logger.error("[WS] user=%s error: %s", user_id, exc)
            finally:
                try:
                    await pubsub.unsubscribe(channel)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("[WS] Redis unavailable: %s", exc)
        await websocket.send_text('{"type":"error","message":"Real-time notifications unavailable"}')
        await websocket.close()