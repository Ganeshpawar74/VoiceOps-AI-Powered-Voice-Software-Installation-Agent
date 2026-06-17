"""
RAG Subsystem — Qdrant-backed retrieval for installation docs & troubleshooting.

Embedding provider: Mistral AI (mistral-embed, 1024-dim).
The embed() function calls the Mistral /v1/embeddings endpoint, not Ollama.
The original code called settings.llm.embedding_model and settings.llm.base_url
which did not exist in LLMSettings — those fields are now added in settings.py.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.config.settings import get_settings

logger   = logging.getLogger(__name__)
settings = get_settings()

COLLECTION = settings.vector_db.qdrant_collection
DIM        = settings.vector_db.embedding_dim


# ──────────────────────────────────────────────
# Embedding helper — Mistral AI embeddings API
# ──────────────────────────────────────────────

async def embed(text: str) -> list[float]:
    """
    Calls Mistral /v1/embeddings to get a 1024-dim vector.
    Falls back to a zero vector if the API key is missing (dev / test mode)
    so the rest of the RAG pipeline doesn't hard-crash during local dev.
    """
    if not settings.llm.mistral_api_key:
        logger.warning("[RAG] No Mistral API key — returning zero vector (dev mode)")
        return [0.0] * DIM

    headers = {
        "Authorization": f"Bearer {settings.llm.mistral_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm.embedding_model,   # "mistral-embed"
        "input": [text],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            # base_url is the property alias → "https://api.mistral.ai/v1"
            f"{settings.llm.base_url}/embeddings",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()

    data = resp.json()
    # Mistral response: {"data": [{"embedding": [...], "index": 0}], ...}
    return data["data"][0]["embedding"]


# ──────────────────────────────────────────────
# RAG Store
# ──────────────────────────────────────────────

class RAGStore:
    def __init__(self) -> None:
        self._client = AsyncQdrantClient(url=settings.vector_db.qdrant_url)

    async def ensure_collection(self) -> None:
        """Creates the Qdrant collection if it does not already exist."""
        existing = [c.name for c in (await self._client.get_collections()).collections]
        if COLLECTION not in existing:
            await self._client.create_collection(
                collection_name=COLLECTION,
                vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
            )
            logger.info("[RAG] Created Qdrant collection: %s (dim=%d)", COLLECTION, DIM)

    async def add_document(
        self,
        doc_id: str,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        """Embed text and upsert a point into Qdrant."""
        vector = await embed(text)
        await self._client.upsert(
            collection_name=COLLECTION,
            points=[
                PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={**metadata, "text": text},
                )
            ],
        )
        logger.debug("[RAG] Upserted doc_id=%s software=%s os=%s",
                     doc_id, metadata.get("software"), metadata.get("os"))

    async def search(
        self,
        query: str,
        software: Optional[str] = None,
        os_filter: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """
        Semantic search with optional payload filters.
        Returns list of {score, text, software, os, source, doc_type}.
        """
        vector = await embed(query)
        k = top_k or settings.vector_db.top_k

        filter_conds = []
        if software:
            filter_conds.append(
                FieldCondition(key="software", match=MatchValue(value=software))
            )
        if os_filter:
            filter_conds.append(
                FieldCondition(key="os", match=MatchValue(value=os_filter))
            )

        qdrant_filter = Filter(must=filter_conds) if filter_conds else None

        # FIX: qdrant-client >=1.10 removed AsyncQdrantClient.search() in
        # favor of query_points(). The old call here was crashing every
        # time with "'AsyncQdrantClient' object has no attribute 'search'",
        # which made every RAG lookup fail (caught as non-fatal upstream,
        # but it meant RAG context was *never* actually retrieved).
        # query_points() takes `query=` (not `query_vector=`) and returns a
        # QueryResponse object — the scored points are in `.points`, not
        # the bare list `search()` used to return directly.
        response = await self._client.query_points(
            collection_name=COLLECTION,
            query=vector,
            limit=k,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        results = response.points

        return [
            {
                "score":    r.score,
                "text":     r.payload.get("text", ""),
                "software": r.payload.get("software", ""),
                "os":       r.payload.get("os", ""),
                "source":   r.payload.get("source", ""),
                "doc_type": r.payload.get("doc_type", ""),
            }
            for r in results
        ]

    async def get_install_guide(
        self,
        software: str,
        os_target: str,
    ) -> Optional[str]:
        """
        Returns the best matching installation guide snippet for software + OS.
        Used by the planner node to enrich install step params before execution.
        Returns None when RAG is disabled or Qdrant has no matching doc.
        """
        results = await self.search(
            query=f"how to install {software} on {os_target}",
            software=software,
            os_filter=os_target,
            top_k=1,
        )
        if results and results[0]["score"] > 0.70:
            logger.info("[RAG] Hit for %s/%s score=%.2f", software, os_target, results[0]["score"])
            return results[0]["text"]
        return None


# ──────────────────────────────────────────────
# Seed script — run once to populate Qdrant
# ──────────────────────────────────────────────

SEED_DOCS = [
    {
        "id": "vscode-win-001",
        "text": (
            "Visual Studio Code on Windows can be installed silently with the flag "
            "/VERYSILENT /NORESTART. The installer is available at "
            "https://code.visualstudio.com/sha/download?build=stable&os=win32-x64-user"
        ),
        "meta": {"software": "Visual Studio Code", "os": "windows",
                 "doc_type": "install_guide", "source": "vscode-docs"},
    },
    {
        "id": "python-win-001",
        "text": (
            "Python 3.12 on Windows: download from python.org/downloads. "
            "Silent install: python-3.12.x-amd64.exe /quiet InstallAllUsers=1 PrependPath=1"
        ),
        "meta": {"software": "Python", "os": "windows",
                 "doc_type": "install_guide", "source": "python-docs"},
    },
    {
        "id": "docker-mac-001",
        "text": (
            "Docker Desktop on macOS: install via brew install --cask docker. "
            "Alternatively download Docker.dmg from docker.com/products/docker-desktop"
        ),
        "meta": {"software": "Docker Desktop", "os": "macos",
                 "doc_type": "install_guide", "source": "docker-docs"},
    },
    {
        "id": "git-linux-001",
        "text": (
            "Git on Linux (Ubuntu/Debian): sudo apt-get install -y git. "
            "On Fedora/RHEL: sudo dnf install git. Verify with: git --version"
        ),
        "meta": {"software": "Git", "os": "linux",
                 "doc_type": "install_guide", "source": "git-docs"},
    },
    {
        "id": "nodejs-linux-001",
        "text": (
            "Node.js LTS on Linux: use NodeSource repo for latest LTS. "
            "curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - "
            "&& sudo apt-get install -y nodejs"
        ),
        "meta": {"software": "Node.js", "os": "linux",
                 "doc_type": "install_guide", "source": "nodejs-docs"},
    },
]


async def seed_rag_store() -> None:
    """Populate Qdrant with baseline install guides. Safe to run multiple times (upsert)."""
    store = RAGStore()
    await store.ensure_collection()
    for doc in SEED_DOCS:
        await store.add_document(doc["id"], doc["text"], doc["meta"])
    logger.info("[RAG] Seeded %d documents into collection '%s'", len(SEED_DOCS), COLLECTION)