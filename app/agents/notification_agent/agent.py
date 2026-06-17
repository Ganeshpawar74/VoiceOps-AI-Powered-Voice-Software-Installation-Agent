"""
NotificationAgent — Delivers progress/completion notifications to users.

Root cause fixed:
  "Event loop is closed" error happens because:
  - The notification agent used asyncio.run() or awaited coroutines
    inside a Celery task running in --pool=solo (synchronous) mode.
  - Celery's solo pool runs everything synchronously; calling asyncio.run()
    inside a task that itself may be called from within an existing loop
    (e.g. FastAPI → Celery task chain) causes "Event loop is closed".

Fix:
  - NotificationAgent is now fully SYNCHRONOUS.
  - For any genuinely async delivery (WebSocket, SSE), we use
    asyncio.run() ONLY if no running loop exists, otherwise schedule
    via run_coroutine_threadsafe.
  - Redis pub/sub notification is synchronous and preferred for Celery tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Any

import redis  # sync redis client — NOT redis.asyncio

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification types
# ---------------------------------------------------------------------------

class NotificationLevel(str, Enum):
    INFO    = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR   = "error"


@dataclass
class Notification:
    user_id: str
    message: str
    level: NotificationLevel = NotificationLevel.INFO
    task_id: Optional[str] = None
    progress_pct: int = 0
    step: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "user_id":      self.user_id,
            "message":      self.message,
            "level":        self.level.value,
            "task_id":      self.task_id,
            "progress_pct": self.progress_pct,
            "step":         self.step,
            "timestamp":    self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ---------------------------------------------------------------------------
# Synchronous Redis pub/sub notifier (safe in Celery)
# ---------------------------------------------------------------------------

class RedisNotifier:
    """Publishes notifications to a Redis channel. Fully synchronous."""

    CHANNEL_PREFIX = "voiceops:notifications:"

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis = redis.from_url(redis_url, decode_responses=True)

    def publish(self, notification: Notification) -> bool:
        channel = f"{self.CHANNEL_PREFIX}{notification.user_id}"
        payload = notification.to_json()
        try:
            receivers = self._redis.publish(channel, payload)
            logger.info(
                "[Notification] Published to %s (%d receivers): %s",
                channel, receivers, notification.message,
            )
            return True
        except Exception as exc:
            logger.error("[Notification] Redis publish failed: %s", exc)
            return False

    def store_for_polling(self, notification: Notification, ttl_sec: int = 3600) -> bool:
        """Store notification in a Redis list for HTTP polling fallback."""
        key = f"voiceops:notif_queue:{notification.user_id}"
        try:
            self._redis.rpush(key, notification.to_json())
            self._redis.expire(key, ttl_sec)
            return True
        except Exception as exc:
            logger.error("[Notification] Redis store failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Safe async runner (avoids "Event loop is closed")
# ---------------------------------------------------------------------------

def _safe_async_run(coro):
    """
    Run an async coroutine safely regardless of whether we're inside an
    existing event loop (FastAPI) or not (Celery worker).
    """
    try:
        loop = asyncio.get_running_loop()
        # We're inside a running loop (FastAPI, etc.)
        # Schedule coroutine and return immediately (fire-and-forget)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            future.result(timeout=5)
        except Exception as exc:
            logger.error("[Notification] Async delivery error: %s", exc)
    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        try:
            asyncio.run(coro)
        except Exception as exc:
            logger.error("[Notification] asyncio.run error: %s", exc)


# ---------------------------------------------------------------------------
# Main NotificationAgent
# ---------------------------------------------------------------------------

class NotificationAgent:
    """
    Delivers notifications via Redis pub/sub (primary) and optional callbacks.

    Designed to be called from Celery tasks (synchronous) or FastAPI (async).
    Never raises — all errors are logged.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        extra_handlers: list[Callable[[Notification], None]] | None = None,
    ):
        self._redis_notifier = RedisNotifier(redis_url=redis_url)
        self._extra_handlers = extra_handlers or []

    def notify(
        self,
        user_id: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        task_id: Optional[str] = None,
        progress_pct: int = 0,
        step: Optional[str] = None,
    ) -> None:
        """Send a notification. Fully synchronous — safe inside Celery tasks."""
        notif = Notification(
            user_id=user_id,
            message=message,
            level=level,
            task_id=task_id,
            progress_pct=progress_pct,
            step=step,
        )

        logger.info("[Notification] user=%s msg=%s", user_id, message)

        # Primary: Redis pub/sub
        self._redis_notifier.publish(notif)
        # Fallback: store for HTTP polling
        self._redis_notifier.store_for_polling(notif)

        # Extra handlers (e.g. email, SMS) — synchronous only
        for handler in self._extra_handlers:
            try:
                handler(notif)
            except Exception as exc:
                logger.error("[Notification] Extra handler %s failed: %s", handler, exc)

    # Convenience helpers
    def notify_progress(self, user_id: str, task_id: str, step: str, pct: int, msg: str) -> None:
        self.notify(
            user_id, msg,
            level=NotificationLevel.INFO,
            task_id=task_id, progress_pct=pct, step=step,
        )

    def notify_success(self, user_id: str, task_id: str, software: str, version: Optional[str] = None) -> None:
        ver = f" v{version}" if version else ""
        self.notify(
            user_id,
            f"✓ {software}{ver} installed successfully!",
            level=NotificationLevel.SUCCESS,
            task_id=task_id, progress_pct=100, step="complete",
        )

    def notify_failure(self, user_id: str, task_id: str, software: str, error: str) -> None:
        self.notify(
            user_id,
            f"✗ Installation of {software} failed: {error}",
            level=NotificationLevel.ERROR,
            task_id=task_id, progress_pct=0, step="failed",
        )