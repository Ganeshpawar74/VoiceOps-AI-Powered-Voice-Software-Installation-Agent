"""
Agent 7 — MonitoringAgent  /  Agent 8 — NotificationAgent  (REWRITTEN)

"""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from typing import Callable, Optional
import redis.asyncio as aioredis
from app.config.settings import get_settings
from app.models.schemas import InstallResult, NotificationEvent, Task, TaskStatus

logger   = logging.getLogger(__name__)
settings = get_settings()


class MonitoringAgent:
    PROGRESS_CHANNEL = "voiceops:progress:{task_id}"

    async def publish_progress(self, task_id: str, pct: int, message: str) -> None:
        try:
            r = aioredis.from_url(str(settings.redis.url), decode_responses=True)
            try:
                event = NotificationEvent(task_id=task_id, event="progress",
                                          message=message, data={"progress_pct": pct})
                await r.publish(self.PROGRESS_CHANNEL.format(task_id=task_id), event.model_dump_json())
            finally:
                await r.aclose()
        except Exception as exc:
            logger.debug("[Monitoring] publish_progress skipped: %s", exc)

    async def monitor(self, task: Task, install_task: asyncio.Task,
                      on_progress: Optional[Callable[[int, str], None]] = None) -> InstallResult:
        milestones = [(10, "Starting..."), (30, "Running installer..."),
                      (60, "Configuring..."), (85, "Finalizing...")]
        idx = 0
        while not install_task.done():
            if idx < len(milestones):
                pct, msg = milestones[idx]
                await self.publish_progress(task.task_id, pct, msg)
                if on_progress:
                    on_progress(pct, msg)
                idx += 1
            await asyncio.sleep(5)
        result = install_task.result()
        pct = 100 if result.success else 0
        msg = "Done!" if result.success else f"Failed: {result.error}"
        await self.publish_progress(task.task_id, pct, msg)
        return result

    async def capture_logs(self, task_id: str, logs: list[str]) -> None:
        if not logs:
            return
        try:
            r = aioredis.from_url(str(settings.redis.url), decode_responses=True)
            try:
                key = f"voiceops:logs:{task_id}"
                await r.rpush(key, *logs)
                await r.expire(key, settings.redis.ttl_session)
            finally:
                await r.aclose()
        except Exception as exc:
            logger.debug("[Monitoring] capture_logs skipped: %s", exc)


class NotificationAgent:
    def _format_message(self, task: Task) -> str:
        sw = task.intent_output.software_canonical if task.intent_output else "the software"
        if task.status == TaskStatus.COMPLETED:
            return f"{sw}: task completed."
        elif task.status == TaskStatus.FAILED:
            return f"{sw}: task failed — {task.error or 'unknown error'}"
        return f"Task {task.task_id}: {task.status.value}"

    async def notify(self, task: Task) -> None:
        message = self._format_message(task)
        logger.info("[Notification] task=%s status=%s msg=%s",
                    task.task_id, task.status.value, message)
        event = NotificationEvent(
            task_id=task.task_id,
            event="completed" if task.status == TaskStatus.COMPLETED else "failed",
            message=message,
            data={"status": task.status.value, "error": task.error,
                  "progress": 100 if task.status == TaskStatus.COMPLETED else 0},
        )
        try:
            r = aioredis.from_url(str(settings.redis.url), decode_responses=True)
            try:
                await r.publish(f"voiceops:progress:{task.task_id}", event.model_dump_json())
                await r.publish(f"voiceops:notify:{task.user_id}", event.model_dump_json())
            finally:
                await r.aclose()
        except Exception as exc:
            logger.warning("[Notification] Redis publish skipped: %s", exc)