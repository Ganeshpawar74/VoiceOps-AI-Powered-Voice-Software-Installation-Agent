# VoiceOps — Quick Start Guide

## Prerequisites

- Python 3.11+
- Node.js 18+ (for frontend)
- A [Mistral API key](https://console.mistral.ai/) (free tier available)

## Option A — Local Dev (No Docker, No PostgreSQL/Redis required)

The app runs fully in-process with in-memory task storage when external services are unavailable. Perfect for development.

### 1. Install Python dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Install Playwright browsers (needed for download link discovery)

```bash
playwright install chromium
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` — only two required fields:

```env
LLM_MISTRAL_API_KEY=your_mistral_api_key_here   # REQUIRED
STT_PROVIDER=whisper                              # Local Whisper (free, no key needed)
```

All other services (Redis, PostgreSQL, Qdrant) are optional — the app degrades gracefully.

### 4. Run the API

```bash
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Test it

```bash
# Health check
curl http://localhost:8000/api/health

# Text command (no audio needed to test)
curl -X POST http://localhost:8000/api/v1/text/command \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "query": "install VS Code", "os_hint": "windows"}'

# Check task status (use task_id from above response)
curl http://localhost:8000/api/v1/tasks/<task_id>
```

### 6. Run the frontend

```bash
cd frontend
npm install
npm run dev
# Opens at http://localhost:5173
```
```bash
### Run in Docker (API + Worker) seprate terminal
celery -A app.services.tasks.celery_app worker --loglevel=info -Q installs --pool=solo

```
---

## Option B — Full Stack with Docker Compose

Runs everything: API, worker, PostgreSQL, Redis, Qdrant, monitoring.

```bash
cp .env.example .env
# Edit .env and set LLM_MISTRAL_API_KEY

docker-compose up --build
```

| Service     | URL                          |
|-------------|------------------------------|
| API docs    | http://localhost:8000/api/docs |
| Frontend    | http://localhost:3000        |
| Flower      | http://localhost:5555        |
| Prometheus  | http://localhost:9090        |
| Grafana     | http://localhost:3001        |
| Jaeger      | http://localhost:16686       |

---

## Architecture

```
User Voice/Text
      │
      ▼
┌─────────────┐     ┌──────────────────────────────────────────┐
│  FastAPI    │────▶│            LangGraph Workflow            │
│  (main.py)  │     │  Speech → Intent → Planner → Browser     │
└─────────────┘     │  → Download → Install → Notify           │
                    └──────────────────────────────────────────┘
                              │              │
                    ┌─────────┘    ┌─────────┘
                    ▼              ▼
               Mistral AI    faster-whisper
               (LLM/Intent)  (STT, local)
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `500 Internal Server Error` on `/voice/command` | Fixed in this version. Was a missing try/except around PostgreSQL in TaskStore |
| No voice processing / audio ignored | Check `STT_PROVIDER=whisper` in `.env`. faster-whisper downloads model on first use (~150MB for `base`) |
| `ModuleNotFoundError: jwt` | Run `pip install PyJWT` (was accidentally listed as `python-jose` in old requirements) |
| `Celery broker unavailable` | Normal in local dev — app automatically runs tasks in-process |
| `DATABASE_URL` not found | Use `DB_URL` (matches settings.py `env_prefix="DB_"`) |
