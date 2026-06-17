#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# VoiceOps — Development Setup Script
# Run: bash scripts/setup.sh
# ═══════════════════════════════════════════════════════
set -euo pipefail

echo "==> Checking Python version..."
python3 --version

echo "==> Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Installing Playwright browser..."
playwright install chromium

echo "==> Copying .env file..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    ⚠  Edit .env and set LLM_MISTRAL_API_KEY before running"
fi

echo "==> faster-whisper model will download on first use"
echo "    Model: base (~145 MB) → ~/.cache/huggingface/hub"
echo "    To pre-download: python scripts/download_whisper.py"

echo "==> Starting infrastructure (Docker)..."
docker compose up -d postgres redis qdrant

echo "==> Waiting for services..."
sleep 5

echo "==> Running DB migrations..."
python -c "import asyncio; from app.services.task_store import init_db; asyncio.run(init_db())"

echo "==> Seeding RAG store..."
python -c "import asyncio; from app.rag.store import seed_rag_store; asyncio.run(seed_rag_store())"

echo ""
echo "✅ Setup complete!"
echo "   Start API:    uvicorn app.api.main:app --reload"
echo "   Start worker: celery -A app.services.tasks.celery_app worker --loglevel=info -Q installs"
echo "   API docs:     http://localhost:8000/api/docs"
