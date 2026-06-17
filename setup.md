# VoiceOps — Complete Setup Guide

---

## Prerequisites

| Tool | Min Version | Install |
|------|------------|---------|
| Docker Desktop | 4.x | https://www.docker.com/products/docker-desktop/ |
| Docker Compose | v2 (bundled with Desktop) | — |
| Python | 3.11+ | https://www.python.org/downloads/ |
| Git | any | https://git-scm.com/downloads |
| make (Windows) | — | `winget install GnuWin32.Make` or use Git Bash |

---

## Quick Start (Docker — recommended)

```bash
# 1. Clone and enter the project
git clone <your-repo-url> voiceops
cd voiceops

# 2. Copy env file and fill in your API keys
cp .env.example .env
#    Edit .env:  LLM_MISTRAL_API_KEY=  and  TTS_SARVAM_API_KEY=

# 3. First-time build (takes 5-10 min; Playwright downloads Chromium)
make setup
# OR without make:
# docker compose build --no-cache

# 4. Start everything
make up
# OR:
# docker compose up -d

# 5. Check health
make health
# Expected: {"status":"ok","components":{"redis":"ok","database":"ok"},...}
```

---

## Service URLs After `make up`

| Service | URL | Notes |
|---------|-----|-------|
| API | http://localhost:8000 | FastAPI backend |
| Swagger Docs | http://localhost:8000/api/docs | Interactive API explorer |
| Frontend | http://localhost:3000 | React UI |
| App (via nginx) | http://localhost:80 | Proxied — use in production |
| Flower | http://localhost:5555 | Celery task monitor |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics |
| Jaeger | http://localhost:16686 | Distributed tracing |

---

## Step-by-Step Commands

### First Time Setup

```bash
# Copy env (required)
cp .env.example .env

# Edit your API keys in .env
# Required:
#   LLM_MISTRAL_API_KEY=sk-...   (https://console.mistral.ai)
#   TTS_SARVAM_API_KEY=...       (https://dashboard.sarvam.ai)

# Build all Docker images
docker compose build --no-cache

# OR using Make
make build
```

### Starting & Stopping

```bash
# Start all services (detached / background)
docker compose up -d
make up                   # same + prints URLs

# Start only infrastructure (postgres + redis + qdrant)
make up-infra
docker compose up -d postgres redis qdrant

# Start only application (api + worker + flower)
make up-app
docker compose up -d api worker flower

# Stop everything (data preserved)
docker compose down
make down

# Stop and DELETE all data volumes (nuclear option)
make down-volumes
```

### Restarting

```bash
# Restart all
docker compose restart
make restart

# Restart only the API (after code change)
docker compose restart api
make restart-api

# Restart only the workers (after code change)
docker compose restart worker
make restart-worker
```

### Viewing Logs

```bash
# All services (live tail)
docker compose logs -f
make logs

# Only the API
docker compose logs -f api
make api-logs

# Only the Celery workers
docker compose logs -f worker
make worker-logs

# Last 200 lines of worker logs
docker compose logs --tail=200 worker
```

---

## Testing the API

### Health Check
```bash
curl http://localhost:8000/api/health
# Expected:
# {"status":"ok","version":"1.0.0","components":{"redis":"ok","database":"ok"},...}
```

### Submit a Text Command
```bash
curl -X POST http://localhost:8000/api/v1/text/command \
  -H "Content-Type: application/json" \
  -d '{"query": "install vs code", "os_hint": "windows"}'

# Response:
# {"task_id":"abc-123-...","status":"pending","message":"Command queued: install vs code"}
```

### Submit a Voice Command (base64 audio)
```bash
# Encode your audio file
AUDIO_B64=$(base64 -i command.wav)

curl -X POST http://localhost:8000/api/v1/voice/command \
  -H "Content-Type: application/json" \
  -d "{\"audio_base64\": \"$AUDIO_B64\"}"
```

### Check Task Status
```bash
# Use the task_id returned from the previous command
TASK_ID="abc-123-your-task-id-here"

curl http://localhost:8000/api/v1/tasks/$TASK_ID
# Returns: status, progress_pct, result, error
```

### Stream Task Progress (SSE)
```bash
TASK_ID="abc-123-your-task-id-here"

curl -N http://localhost:8000/api/v1/tasks/$TASK_ID/stream
# Streams Server-Sent Events until task completes
```

### List All Tasks for Current User
```bash
curl http://localhost:8000/api/v1/tasks
```

### Make Shortcut Commands
```bash
make health                          # GET /api/health
make verify                          # POST /api/v1/text/command with "install vs code"
make verify-task TASK_ID=abc-123-... # GET /api/v1/tasks/<id>
```

---

## Database

```bash
# Open psql shell inside the postgres container
make db-shell
docker compose exec postgres psql -U voiceops -d voiceops

# Common psql queries:
\dt                                  -- list tables
SELECT * FROM tasks ORDER BY created_at DESC LIMIT 10;
SELECT status, count(*) FROM tasks GROUP BY status;

# Run migrations (creates tables if missing)
make migrate
```

---

## Redis

```bash
# Open redis-cli
make redis-cli
docker compose exec redis redis-cli

# Useful commands in redis-cli:
KEYS voiceops:task:*               # list all stored tasks
GET voiceops:task:<task_id>        # read a task's JSON
KEYS voiceops:progress:*           # SSE progress channels
TTL voiceops:task:<task_id>        # seconds until expiry

# Monitor all Redis commands in real time
make redis-monitor
```

---

## Celery Workers

```bash
# Check what's currently running/queued
make celery-status
docker compose exec worker celery -A app.services.tasks.celery_app inspect active

# Purge all pending tasks from the queue (emergency)
make celery-purge

# Scale workers to 3 replicas
make scale-workers
docker compose up -d --scale worker=3 worker

# Flower Web UI
# Open http://localhost:5555 in browser
```

---

## Local Development (without Docker)

```bash
# 1. Create virtualenv
python -m venv .venv

# Windows (Git Bash / PowerShell)
.venv\Scripts\activate

# Mac/Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers (for browser_agent)
playwright install chromium

# 4. Clear pycache (IMPORTANT — do this before every restart)
make pycache-clean
# OR manually:
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete

# 5. Copy and edit .env for local URLs
cp .env.example .env
# Change:
#   DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
#   REDIS_URL=redis://localhost:6379/0

# 6. Start the FastAPI server
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000

# 7. Start a Celery worker (in a second terminal)
celery -A app.services.tasks.celery_app worker --loglevel=info -Q installs -c 2
```

---

## Running Tests

```bash
# Full test suite
pytest tests/ -v

# With Make
make test

# Fast (stop on first failure)
make test-fast

# IMPORTANT: clear pycache before tests to avoid stale bytecode issues
make pycache-clean && pytest tests/ -v
```

---

## Troubleshooting

### `AttributeError: 'AppSettings' object has no attribute 'celery_max_retries'`
**Fix:** Replace `app/config/settings.py` with the patched version (see `voiceops_fixes.zip`), then clear pycache:
```bash
make pycache-clean
docker compose restart api worker
```

### `{"detail":[{"type":"missing","loc":["body","user_id"],...}]}`
**Fix:** Stale `.pyc` bytecode. Clear it:
```bash
make pycache-clean
# Or if running in Docker:
docker compose down && docker compose up -d
```

### `500 Internal Server Error` on all POST requests
Check logs first:
```bash
make api-logs
# Look for the actual exception — it will be in the [500] line
```
Most common causes:
1. Missing settings fields → apply patches from `voiceops_fixes.zip`
2. Redis connection error → `make up-infra` first
3. Database not ready → wait for postgres healthcheck to pass

### Container won't start / keeps restarting
```bash
docker compose logs api       # read the actual error
docker compose ps             # see which containers are unhealthy
```

### Port already in use
```bash
# Find what's on port 8000
netstat -ano | findstr :8000   # Windows
lsof -i :8000                   # Mac/Linux

# Or change the port in docker-compose.yml:
#   ports: - "8001:8000"   (host:container)
```

### Fresh start (keep code, delete all data)
```bash
docker compose down -v    # removes volumes (postgres data, redis data)
docker compose up -d
make migrate              # recreate tables
```

### Nuclear reset (delete everything including images)
```bash
make nuke
make setup
make up
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_MISTRAL_API_KEY` | — | **Required.** Mistral AI key |
| `TTS_SARVAM_API_KEY` | — | Required for TTS voice responses |
| `STT_PROVIDER` | `whisper` | `whisper` (local) or `sarvam` (API) |
| `STT_WHISPER_MODEL_SIZE` | `base` | `tiny`/`base`/`small`/`medium`/`large` |
| `DB_URL` | postgres://... | PostgreSQL connection string |
| `REDIS_URL` | redis://... | Redis connection string |
| `FEATURE_RAG_ENABLED` | `false` | Enable RAG (needs Qdrant populated) |
| `FEATURE_HUMAN_IN_LOOP` | `false` | Require approval before install |
| `FEATURE_TTS_ENABLED` | `true` | Enable Sarvam TTS voice responses |
| `SEC_JWT_SECRET` | changeme | **Change in production!** |
| `OBS_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |