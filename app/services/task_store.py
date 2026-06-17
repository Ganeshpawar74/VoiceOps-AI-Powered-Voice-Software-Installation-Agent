"""
TaskStore — dual persistence layer with in-memory fallback.

FIXES:
  BUG #1 CRITICAL: `await aioredis.from_url()` raises TypeError in redis>=5.x.
  from_url() is a sync factory in redis>=5. Removed the await.
  This was the direct cause of every HTTP 500 on POST /text/command.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import get_settings
from app.models.schemas import Task, TaskStatus

logger   = logging.getLogger(__name__)
settings = get_settings()


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"
    id           = Column(String(36), primary_key=True)
    user_id      = Column(String(64), nullable=False, index=True)
    session_id   = Column(String(64), nullable=False)
    query        = Column(Text, nullable=False)
    status       = Column(String(32), nullable=False, default="pending")
    progress     = Column(Integer, default=0)
    result_json  = Column(Text, nullable=True)
    error        = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


_engine      = create_async_engine(str(settings.database.url), echo=settings.database.echo)
_session_fac = async_sessionmaker(_engine, expire_on_commit=False)
_memory_store: dict[str, str] = {}


async def dispose_engine() -> None:
    await _engine.dispose()


async def init_db():
    try:
        async with _engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[TaskStore] Database initialized successfully")
    except Exception as exc:
        logger.warning("[TaskStore] PostgreSQL unavailable at startup: %s — using in-memory fallback", exc)


class TaskStore:
    REDIS_TTL    = settings.redis.ttl_session
    REDIS_PREFIX = "voiceops:task:"

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    def _get_redis(self) -> aioredis.Redis:
        # BUG #1 FIX: from_url() is NOT a coroutine in redis>=5.x — no await
        if self._redis is None:
            self._redis = aioredis.from_url(
                str(settings.redis.url), decode_responses=True
            )
        return self._redis

    async def save(self, task: Task) -> None:
        task.updated_at = datetime.utcnow()
        json_payload    = task.model_dump_json()

        try:
            r   = self._get_redis()
            key = f"{self.REDIS_PREFIX}{task.task_id}"
            await r.set(key, json_payload, ex=self.REDIS_TTL)
        except Exception as exc:
            logger.debug("[TaskStore] Redis save skipped: %s", exc)

        pg_ok = False
        try:
            async with _session_fac() as session:
                existing = await session.get(TaskRecord, task.task_id)
                if existing:
                    existing.status      = task.status.value
                    existing.progress    = task.progress_pct
                    existing.result_json = json.dumps(task.result) if task.result else None
                    existing.error       = task.error
                    existing.updated_at  = task.updated_at
                    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                        existing.completed_at = datetime.utcnow()
                else:
                    session.add(TaskRecord(
                        id=task.task_id, user_id=task.user_id,
                        session_id=task.session_id, query=task.query,
                        status=task.status.value, progress=task.progress_pct,
                        result_json=json.dumps(task.result) if task.result else None,
                        error=task.error, created_at=task.created_at,
                        updated_at=task.updated_at,
                    ))
                await session.commit()
            pg_ok = True
        except Exception as exc:
            logger.warning("[TaskStore] PostgreSQL save skipped: %s", exc)

        if not pg_ok:
            _memory_store[task.task_id] = json_payload

    async def get(self, task_id: str) -> Optional[Task]:
        try:
            r   = self._get_redis()
            raw = await r.get(f"{self.REDIS_PREFIX}{task_id}")
            if raw:
                return Task.model_validate_json(raw)
        except Exception as exc:
            logger.debug("[TaskStore] Redis get skipped: %s", exc)

        try:
            async with _session_fac() as session:
                rec = await session.get(TaskRecord, task_id)
                if rec is not None:
                    return Task(
                        task_id=rec.id, user_id=rec.user_id, session_id=rec.session_id,
                        query=rec.query, status=TaskStatus(rec.status),
                        progress_pct=rec.progress or 0,
                        result=json.loads(rec.result_json) if rec.result_json else None,
                        error=rec.error, created_at=rec.created_at, updated_at=rec.updated_at,
                    )
        except Exception as exc:
            logger.debug("[TaskStore] PostgreSQL get skipped: %s", exc)

        raw = _memory_store.get(task_id)
        if raw:
            return Task.model_validate_json(raw)
        return None

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[Task]:
        try:
            async with _session_fac() as session:
                stmt = (select(TaskRecord)
                    .where(TaskRecord.user_id == user_id)
                    .order_by(TaskRecord.created_at.desc())
                    .limit(limit))
                result = await session.execute(stmt)
                rows   = result.scalars().all()
                return [Task(
                    task_id=r.id, user_id=r.user_id, session_id=r.session_id,
                    query=r.query, status=TaskStatus(r.status),
                    progress_pct=r.progress or 0, error=r.error,
                    created_at=r.created_at, updated_at=r.updated_at,
                ) for r in rows]
        except Exception as exc:
            logger.debug("[TaskStore] PostgreSQL list skipped: %s", exc)

        tasks = []
        for raw in _memory_store.values():
            try:
                t = Task.model_validate_json(raw)
                if t.user_id == user_id:
                    tasks.append(t)
            except Exception:
                continue
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]
