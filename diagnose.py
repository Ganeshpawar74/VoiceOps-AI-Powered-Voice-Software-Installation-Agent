#!/usr/bin/env python3
"""
VoiceOps Diagnostic Script
Run before starting the server to catch config/env issues early.
Usage: python diagnose.py
"""
import sys
import os
import importlib

PASS = "✓"
FAIL = "✗"
WARN = "⚠"

errors   = []
warnings = []


def check(label: str, fn):
    try:
        result = fn()
        if result is True or result is None:
            print(f"  {PASS} {label}")
        elif result is False:
            print(f"  {FAIL} {label}")
            errors.append(label)
        else:
            print(f"  {WARN} {label}: {result}")
            warnings.append(f"{label}: {result}")
    except Exception as exc:
        print(f"  {FAIL} {label}: {exc}")
        errors.append(f"{label}: {exc}")


print("VoiceOps Diagnostics")
print("=" * 50)

# ── Python version ──────────────────────────────
print("\n[Python]")
check("Python 3.11+", lambda: sys.version_info >= (3, 11) or f"Found {sys.version}")

# ── Environment file ────────────────────────────
print("\n[.env]")
check(".env exists", lambda: os.path.isfile(".env") or "Missing .env — copy .env.example")

def _check_env_keys():
    required = ["LLM_MISTRAL_API_KEY"]
    missing = []
    if os.path.isfile(".env"):
        with open(".env") as f:
            content = f.read()
        for key in required:
            if key not in content or f"{key}=" not in content.replace(" ", ""):
                missing.append(key)
    if missing:
        return f"Missing in .env: {', '.join(missing)}"

check("Required env keys", _check_env_keys)

# ── Core packages ───────────────────────────────
print("\n[Packages]")
core_pkgs = [
    ("fastapi",           "FastAPI"),
    ("uvicorn",           "Uvicorn"),
    ("pydantic",          "Pydantic v2"),
    ("pydantic_settings", "pydantic-settings"),
    ("redis",             "redis (aioredis)"),
    ("sqlalchemy",        "SQLAlchemy async"),
    ("httpx",             "httpx"),
    ("langgraph",         "LangGraph"),
    ("mistralai",         "mistralai"),
    ("dotenv",            "python-dotenv"),
    ("jwt",               "PyJWT"),
    ("prometheus_client", "prometheus-client"),
]
for pkg, label in core_pkgs:
    check(label, lambda p=pkg: importlib.import_module(p) and None)

# ── Optional packages ───────────────────────────
print("\n[Optional packages]")
optional_pkgs = [
    ("faster_whisper", "faster-whisper (STT)"),
    ("playwright",     "Playwright (browser agent)"),
    ("qdrant_client",  "qdrant-client (RAG)"),
    ("celery",         "Celery (task queue)"),
    ("asyncpg",        "asyncpg (PostgreSQL)"),
]
for pkg, label in optional_pkgs:
    try:
        importlib.import_module(pkg)
        print(f"  {PASS} {label}")
    except ImportError:
        print(f"  {WARN} {label}: not installed (optional)")

# ── Settings load ───────────────────────────────
print("\n[Settings]")
def _load_settings():
    from dotenv import load_dotenv
    load_dotenv()
    from app.config.settings import get_settings
    s = get_settings()
    return f"env={s.environment} api={s.api_prefix}"
check("Settings load", _load_settings)

# ── Redis connectivity ──────────────────────────
print("\n[Services]")
def _check_redis():
    import socket
    from urllib.parse import urlparse
    from dotenv import load_dotenv
    load_dotenv()
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=2):
            return None  # success
    except Exception:
        return f"Redis not reachable at {host}:{port} — app will use in-process fallback"

check("Redis", _check_redis)

def _check_postgres():
    import socket
    from urllib.parse import urlparse
    from dotenv import load_dotenv
    load_dotenv()
    url = os.environ.get("DB_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=2):
            return None  # success
    except Exception:
        return f"PostgreSQL not reachable at {host}:{port} — app will use in-memory fallback"

check("PostgreSQL", _check_postgres)

# ── Mistral API key ─────────────────────────────
print("\n[API Keys]")
def _check_mistral_key():
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("LLM_MISTRAL_API_KEY", "")
    if not key:
        return "LLM_MISTRAL_API_KEY not set — intent agent will fail"
    if len(key) < 20:
        return "LLM_MISTRAL_API_KEY looks too short"

check("Mistral API key", _check_mistral_key)

# ── Summary ─────────────────────────────────────
print("\n" + "=" * 50)
if errors:
    print(f"{FAIL} {len(errors)} error(s) found — fix before starting:")
    for e in errors:
        print(f"   • {e}")
    sys.exit(1)
elif warnings:
    print(f"{WARN} {len(warnings)} warning(s) — server will start with reduced functionality")
    for w in warnings:
        print(f"   • {w}")
    sys.exit(0)
else:
    print(f"{PASS} All checks passed — ready to start")
    print("\nStart command:")
    print("  uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug")
    sys.exit(0)