# VoiceOps — Installation Guide

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | 3.12 recommended |
| Docker + Compose | v2.x | For PostgreSQL, Redis, Qdrant |
| Git | any | |
| Tesseract | 5.x | `apt install tesseract-ocr` (OCR fallback) |

---

## 1. Clone & Virtual Environment

```bash
git clone https://github.com/yourname/voiceops.git
cd voiceops
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

---

## 2. Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Key packages explained

| Package | Purpose | Notes |
|---------|---------|-------|
| `faster-whisper` | Local STT (offline, free) | Downloads model on first run |
| `mistralai` / `httpx` | Mistral API calls | Needs `LLM_MISTRAL_API_KEY` |
| `playwright` | Browser automation | Run `playwright install chromium` |
| `langgraph` | Agent orchestration | LangGraph state machines |
| `qdrant-client` | Vector DB (RAG) | Needs Qdrant running |
| `celery` + `redis` | Background task queue | Needs Redis running |

---

## 3. Install Playwright Browser

```bash
playwright install chromium
```

---

## 4. System Packages (Linux/Ubuntu)

```bash
sudo apt-get update && sudo apt-get install -y \
    ffmpeg \
    libsndfile1 \
    tesseract-ocr \
    portaudio19-dev     # only if using real-time mic input
```

### macOS
```bash
brew install ffmpeg libsndfile tesseract
```

### Windows
- Install FFmpeg from https://ffmpeg.org/download.html (add to PATH)
- Install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki

---

## 5. API Keys

### Mistral API (REQUIRED for LLM)
1. Sign up at https://console.mistral.ai/
2. Create an API key (free tier available)
3. Set in `.env`: `LLM_MISTRAL_API_KEY=your_key`

### Sarvam AI (OPTIONAL — better Hindi/Hinglish STT)
1. Sign up at https://www.sarvam.ai/
2. Set in `.env`: `STT_SARVAM_API_KEY=your_key` and `STT_PROVIDER=sarvam`

---

## 6. Configure Environment

```bash
cp .env.example .env
# Edit .env — at minimum set LLM_MISTRAL_API_KEY
```

---

## 7. Start Infrastructure

```bash
# Start PostgreSQL, Redis, Qdrant (detached)
docker compose up -d postgres redis qdrant
```

---

## 8. Initialize Database

```bash
python -c "import asyncio; from app.services.task_store import init_db; asyncio.run(init_db())"
```

---

## 9. Seed RAG Store (optional)

```bash
python -c "import asyncio; from app.rag.store import seed_rag_store; asyncio.run(seed_rag_store())"
```

---

## 10. Download Whisper Model (optional pre-download)

```bash
python scripts/download_whisper.py
```

Whisper model sizes and approximate download sizes:

| Size | Disk | Speed | Accuracy |
|------|------|-------|----------|
| `tiny` | ~75 MB | Fastest | Low |
| `base` | ~145 MB | Fast | Good (default) |
| `small` | ~466 MB | Medium | Better |
| `medium` | ~1.5 GB | Slow | High |
| `large-v3` | ~3 GB | Slowest | Best |

Set `STT_WHISPER_MODEL_SIZE=base` in `.env` (recommended for development).

---

## 11. Run the Application

### Terminal 1 — API Server
```bash
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
```

### Terminal 2 — Celery Worker
```bash
celery -A app.services.tasks.celery_app worker --loglevel=info -Q installs -c 4
```

### Terminal 3 — Celery Flower (optional monitoring)
```bash
celery -A app.services.tasks.celery_app flower --port=5555
```

---

## 12. Verify Everything Works

```bash
# Health check
curl http://localhost:8000/api/health

# Text command test
curl -X POST http://localhost:8000/api/v1/text/command \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test-user", "query": "Install VS Code"}'

# API docs
open http://localhost:8000/api/docs
```

---

## 13. Run Tests

```bash
pytest tests/ -v --cov=app
```

---

## 14. Full Docker Deployment

```bash
docker compose up --build
```

Services available:
- API: http://localhost:8000
- Frontend: http://localhost:3000
- Flower (Celery monitor): http://localhost:5555
- Grafana: http://localhost:3001 (admin/admin)
- Jaeger (traces): http://localhost:16686
- Prometheus: http://localhost:9090
- Qdrant UI: http://localhost:6333/dashboard

---

## Troubleshooting

### "faster-whisper download failed"
```bash
# Manual download
python -c "from faster_whisper import WhisperModel; WhisperModel('base')"
# Or set HF_HUB_OFFLINE=1 and ensure model is cached
```

### "LLM_MISTRAL_API_KEY not configured"
Set `LLM_MISTRAL_API_KEY=your_key` in `.env` or export as env var.

### "Playwright browser not found"
```bash
playwright install chromium --with-deps
```

### "Redis connection refused"
```bash
docker compose up -d redis
```

### "No download links found" for a software
The browser agent uses a known-URL table for 20+ popular tools.
For unknown software, it falls back to Google search + DOM scraping.
