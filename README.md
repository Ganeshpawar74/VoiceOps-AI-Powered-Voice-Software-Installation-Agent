# VoiceOps — AI-Powered Voice Software Installation Agent

> **Speak a command → AI installs the software for you.**

```
"Install VS Code"
  🎤 STT  →  🧠 Intent  →  📋 Plan  →  🌐 Browse  →  ⬇ Download  →  ⚙ Install  →  🔔 Notify
```

VoiceOps is a voice-first AI platform. Users say things like *"Install VS Code"* or *"Python install chahiye"* and a pipeline of eight specialised agents transcribes the speech, detects intent, plans the action, finds the official installer on the web, downloads it with checksum verification, installs it silently, and returns a voice confirmation — all without the user opening a browser or clicking anything.

---

## Table of Contents

1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Agent Pipeline](#agent-pipeline)
4. [Tech Stack](#tech-stack)
5. [Project Structure](#project-structure)
6. [Quick Start](#quick-start)
7. [Docker Deployment](#docker-deployment)
8. [Environment Variables](#environment-variables)
9. [API Reference](#api-reference)
10. [Supported Commands](#supported-commands)
11. [Feature Flags](#feature-flags)
12. [Observability](#observability)
13. [Testing](#testing)
14. [Contributing](#contributing)

---

## Features

- **Voice-first UX** — submit raw audio (base64) or plain text; both paths lead to the same agent pipeline
- **Multilingual** — English, Hindi, and Hinglish commands supported out of the box via Sarvam AI STT fallback
- **8 specialised agents** orchestrated with LangGraph Plan-and-Execute
- **Secure downloads** — SHA-256 checksum + publisher signature verification before any installer is run
- **Cross-platform installation** — winget (Windows), Homebrew (macOS), apt (Linux), or direct installer
- **Real-time progress** — Server-Sent Events and WebSocket streams keep the frontend live
- **Full observability** — distributed tracing (Jaeger), metrics (Prometheus), and dashboards (Grafana)
- **RAG-ready** — optional Qdrant vector DB for software knowledge base (toggled by `FEATURE_RAG_ENABLED`)
- **Production Docker Compose** — nine services, health checks, Celery workers with horizontal replicas, nginx reverse proxy
- **CI/CD** — GitHub Actions workflow included (`.github/workflows/`)

---

## UI Preview

<p align="center">
  <img src="docs/Uiresult.png" alt="VoiceOps UI — Command Interface" width="900"/>
</p>

> The VoiceOps frontend showing the voice/text command interface, live agent pipeline progress bar, and real-time task completion status.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                     User Input                          │
│         🎤 Voice (base64)  ·  💬 Text command           │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────▼────────────────────────────────┐
│              FastAPI Gateway  (app/api/)                 │
│  /voice/command · /text/command · /tasks · /ws/{uid}    │
└──────────┬──────────────────────────────────────────────┘
           │
  ┌────────▼───────┐         ┌──────────────────┐
  │  Speech Agent  │         │  Intent Agent    │
  │  faster-whisper│────────▶│  rule-based +    │
  │  + noise reduce│         │  Mistral fallback│
  └────────────────┘         └────────┬─────────┘
                                      │
                             ┌────────▼─────────┐
                             │  Planner Agent   │
                             │  LangGraph P&E   │
                             └──┬───────┬───┬───┘
                                │       │   │
              ┌─────────────────▼┐  ┌───▼─┐ └──────────────┐
              │  Browser Agent   │  │Down │  │ Install Agent │
              │  Playwright+OCR  │  │load │  │ winget/brew/  │
              └──────────────────┘  │Agent│  │ apt/direct    │
                                    └──┬──┘  └──────┬────────┘
                                       │             │
              ┌────────────────────────▼─────────────▼──────┐
              │        Celery Task Queue  (Redis broker)     │
              │        2 worker replicas · 4 concurrent      │
              └──────────┬────────────────────────┬──────────┘
                         │                        │
              ┌──────────▼────────┐  ┌────────────▼──────────┐
              │ Monitoring Agent  │  │ Notification Agent    │
              │ Redis pub/sub     │  │ SSE · WebSocket push  │
              └───────────────────┘  └───────────────────────┘
                         │
              ┌──────────▼────────────────────────────────────┐
              │  Infrastructure: PostgreSQL · Redis · Qdrant  │
              │  Mistral AI API                               │
              └───────────────────────────────────────────────┘
                         │
              ┌──────────▼────────────────────────────────────┐
              │  Observability: Jaeger · Prometheus · Grafana │
              └───────────────────────────────────────────────┘
```

<p align="center">
  <img src="docs/System.png" alt="VoiceOps — Full System Architecture" width="750"/>
</p>

---

## Agent Pipeline

<p align="center">
  <img src="docs/Agent.png" alt="VoiceOps — Agent Pipeline (How Agents Work)" width="750"/>
</p>

### 1. Speech Agent (`app/agents/speech_agent.py`)

Receives raw audio bytes, applies `noisereduce` denoising, and transcribes with `faster-whisper` (local, free). For better Hindi/Hinglish accuracy, set `STT_PROVIDER=sarvam` and provide a Sarvam AI API key. Outputs a plain-text transcript.

**Configurable options:**

| Env var | Values | Default |
|---|---|---|
| `STT_PROVIDER` | `whisper`, `sarvam` | `whisper` |
| `STT_WHISPER_MODEL_SIZE` | `tiny`, `base`, `small`, `medium`, `large-v3` | `base` |
| `STT_WHISPER_DEVICE` | `cpu`, `cuda` | `cpu` |
| `STT_WHISPER_COMPUTE_TYPE` | `int8`, `float16` | `int8` |

### 2. Intent Agent (`app/agents/intent_agent.py`)

Classifies the transcript into a structured intent (`software_name`, `action`, `platform`, `version`). Uses rule-based pattern matching first; falls back to Mistral `mistral-small-latest` for ambiguous or multilingual commands.

### 3. Planner Agent (`app/agents/planner_agent.py`)

LangGraph Plan-and-Execute loop. Generates a step-by-step plan (browse → download → verify → install → confirm), then dispatches each step to the appropriate downstream agent. Supports optional human-in-the-loop confirmation via `FEATURE_HUMAN_IN_LOOP=true`.

### 4. Browser Agent (`app/agents/browser_agent.py`)

Headless Playwright browser that navigates to trusted official sources (vendor websites, package managers) to locate the correct installer URL. Falls back to OCR-based page parsing when dynamic content prevents direct link extraction (`FEATURE_OCR_FALLBACK=true`).

### 5. Download Agent (`app/agents/download_agent.py`)

Streaming file download with progress reporting. After completion, verifies SHA-256 checksum and publisher code signature (`SEC_VERIFY_CHECKSUMS`, `SEC_VERIFY_PUBLISHER`). Rejects any file that fails verification.

### 6. Install Agent (`app/agents/install_agent.py`)

Runs the installer silently using the correct package manager for the host OS:
- **Windows** — `winget install`
- **macOS** — `brew install`
- **Linux** — `apt install`
- **Fallback** — runs the downloaded `.exe`/`.pkg`/`.deb` directly

### 7. Monitoring Agent (`app/agents/monitoring_agent.py`)

Publishes granular progress events to Redis pub/sub channels. The API layer subscribes and forwards these to clients as Server-Sent Events or WebSocket messages, giving real-time percentage and status updates.

### 8. Notification Agent (`app/agents/notification_agent.py`)

Composes and delivers the final success/failure notification. If `FEATURE_TTS_ENABLED=true`, synthesises a voice confirmation via Sarvam AI TTS (`TTS_PROVIDER=sarvam`, `bulbul:v1` model) and returns the audio alongside the text response.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **API** | FastAPI ≥ 0.111 · Uvicorn · python-multipart |
| **Agent orchestration** | LangGraph ≥ 0.1.5 · langchain-core |
| **LLM** | Mistral AI (`mistral-small-latest`) |
| **STT** | faster-whisper ≥ 1.0 · Sarvam AI (optional) |
| **TTS** | Sarvam AI `bulbul:v1` (optional) |
| **Browser automation** | Playwright ≥ 1.43 |
| **Task queue** | Celery ≥ 5.3 · Redis ≥ 5.0 · Flower (UI) |
| **Database** | PostgreSQL 16 · SQLAlchemy async · asyncpg |
| **Vector DB (RAG)** | Qdrant ≥ 1.9 (optional) |
| **Auth** | PyJWT ≥ 2.8 |
| **Observability** | OpenTelemetry · Jaeger · Prometheus · Grafana |
| **Frontend** | React / Next.js (Vite build) |
| **Proxy** | nginx 1.25 |
| **Containerisation** | Docker Compose (9 services) |
| **CI/CD** | GitHub Actions |

---

## Project Structure

```
voiceops/
├── app/
│   ├── agents/             # 8 agent modules (speech, intent, planner, browser,
│   │                       #   download, install, monitoring, notification)
│   ├── api/
│   │   └── main.py         # FastAPI app, route definitions, WebSocket handler
│   ├── database/
│   │   └── schema.sql      # PostgreSQL schema (auto-applied on first container start)
│   ├── models/             # SQLAlchemy ORM models
│   ├── services/
│   │   └── tasks/          # Celery task definitions (celery_app.py)
│   └── core/               # Settings, logging, telemetry bootstrap
├── docker/
│   ├── Dockerfile.api      # Python 3.11 API container
│   ├── Dockerfile.worker   # Celery worker container
│   ├── Dockerfile.frontend # React build → nginx serve
│   ├── nginx.conf          # Main reverse proxy (port 80)
│   ├── frontend.nginx.conf # Frontend static serving
│   ├── prometheus.yml      # Prometheus scrape config
│   └── grafana/            # Grafana provisioning dashboards
├── frontend/
│   ├── src/                # React components & pages
│   └── package.json
├── docs/
│   ├── INSTALLATION.md     # Full setup guide
│   └── API.md              # Detailed API reference
├── scripts/
│   └── setup.sh            # One-shot dev bootstrap
├── tests/                  # pytest test suite
├── .github/workflows/      # CI pipeline (lint, test, build)
├── docker-compose.yml      # Production compose (9 services)
├── requirements.txt        # Python dependencies (pinned)
├── pyproject.toml          # Build metadata & tool config
├── pytest.ini              # Test configuration
├── .env.example            # Environment variable template
└── QUICKSTART.md           # 5-minute setup guide
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ running locally
- Redis 7+ running locally
- A free [Mistral AI API key](https://console.mistral.ai/)

### Local Development

```bash
# 1. Clone the repo
git clone https://github.com/Ganeshpawar74/VoiceOps-AI-Powered-Voice-Software-Installation-Agent
cd VoiceOps-AI-Powered-Voice-Software-Installation-Agent

# 2. Copy and configure environment
cp .env.example .env
# Edit .env — set LLM_MISTRAL_API_KEY at minimum

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium

# 5. Start the API
uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

In a second terminal:

```bash
# Start a Celery worker
celery -A app.services.tasks.celery_app worker --loglevel=info -Q installs -c 4
```

In a third terminal (optional frontend):

```bash
cd frontend
npm install
npm run dev
```

**Access points:**

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/api/docs |

---

## Docker Deployment

The `docker-compose.yml` launches all nine services with a single command:

```bash
# From the project root
docker-compose up --build
```

**Services started:**

| Container | Role |
|---|---|
| `voiceops_postgres` | PostgreSQL 16 |
| `voiceops_redis` | Redis 7 (broker + results + pub/sub) |
| `voiceops_qdrant` | Qdrant vector DB |
| `voiceops_api` | FastAPI on port 8000 |
| `voiceops_worker` (×2) | Celery worker replicas |
| `voiceops_flower` | Celery monitor on port 5555 |
| `voiceops_frontend` | React/Next.js on port 3000 |
| `voiceops_nginx` | Reverse proxy on port 80 |
| `voiceops_jaeger` | Distributed tracing UI on port 16686 |
| `voiceops_prometheus` | Metrics scraper on port 9090 |
| `voiceops_grafana` | Dashboard on port 3001 |

**All access points (Docker):**

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API docs | http://localhost/api/docs |
| Flower | http://localhost:5555 |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin/admin) |
| Jaeger | http://localhost:16686 |

---

## Environment Variables

Copy `.env.example` to `.env`. Required keys are marked with `*`.

| Variable | Description | Default |
|---|---|---|
| `LLM_MISTRAL_API_KEY` * | Mistral AI API key | — |
| `DB_URL` | PostgreSQL connection string | `postgresql+asyncpg://voiceops:secret@localhost:5432/voiceops` |
| `REDIS_URL` | Redis general connection | `redis://localhost:6379/0` |
| `REDIS_CELERY_BROKER` | Celery broker URL | `redis://localhost:6379/1` |
| `REDIS_CELERY_BACKEND` | Celery results URL | `redis://localhost:6379/2` |
| `STT_PROVIDER` | `whisper` or `sarvam` | `whisper` |
| `STT_SARVAM_API_KEY` | Sarvam AI key (if `STT_PROVIDER=sarvam`) | — |
| `TTS_SARVAM_API_KEY` | Sarvam AI key for TTS | — |
| `FEATURE_RAG_ENABLED` | Enable Qdrant RAG knowledge base | `false` |
| `FEATURE_TTS_ENABLED` | Return voice confirmation audio | `true` |
| `FEATURE_HUMAN_IN_LOOP` | Require user confirmation before install | `false` |
| `FEATURE_OCR_FALLBACK` | OCR fallback in Browser Agent | `true` |
| `SEC_JWT_SECRET` | JWT signing secret — **change in production** | — |
| `SEC_VERIFY_CHECKSUMS` | Verify SHA-256 after download | `true` |
| `SEC_VERIFY_PUBLISHER` | Verify code signature of installer | `true` |
| `BROWSER_HEADLESS` | Run Playwright browser headlessly | `true` |
| `OBS_ENABLE_TRACING` | Send traces to Jaeger | `true` |
| `OBS_JAEGER_ENDPOINT` | Jaeger HTTP collector endpoint | `http://localhost:14268/api/traces` |

> **Security note:** The `.env.example` contains a placeholder Mistral key for illustration. Always set your own key and rotate `SEC_JWT_SECRET` before any public deployment.

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/voice/command` | Submit voice command (JSON `{ audio: "<base64>" }`) |
| `POST` | `/api/v1/text/command` | Submit text command (JSON `{ text: "Install VS Code" }`) |
| `GET` | `/api/v1/tasks/{id}` | Get task status and result |
| `GET` | `/api/v1/tasks/{id}/stream` | SSE stream of real-time progress events |
| `GET` | `/api/v1/tasks` | List task history for the current user |
| `WS` | `/api/v1/ws/{user_id}` | WebSocket live update channel |
| `GET` | `/api/health` | Health check (returns service statuses) |
| `GET` | `/api/docs` | Swagger UI |

Full request/response schemas are documented at `/api/docs` when the server is running.

---

## Supported Commands

```
# English
"Install VS Code"
"Download Python 3.12 for Windows"
"Install Docker Desktop"
"Install Postman and open it"

# Hindi / Hinglish
"VS Code install karo"
"Python install chahiye"
"Docker Desktop download karna hai"
```

Any software name + intent the Mistral model can extract will be processed. For best results, name the software explicitly.

---

## Feature Flags

All flags are set via environment variables and can be toggled without code changes.

| Flag | Description |
|---|---|
| `FEATURE_RAG_ENABLED` | Pre-loads a Qdrant collection with known software metadata to improve planner accuracy |
| `FEATURE_TTS_ENABLED` | Synthesises a voice reply via Sarvam AI after task completion |
| `FEATURE_OCR_FALLBACK` | Browser Agent uses OCR when JS-rendered pages block direct link extraction |
| `FEATURE_VISION_FALLBACK` | Vision model fallback for screenshot-based navigation (experimental) |
| `FEATURE_HUMAN_IN_LOOP` | Pauses the pipeline after planning for explicit user approval before download |

---

## Observability

VoiceOps ships with a full observability stack:

**Distributed tracing (Jaeger):** Every request is traced end-to-end with OpenTelemetry spans. The Jaeger UI at `http://localhost:16686` shows the full agent call graph.

**Metrics (Prometheus + Grafana):** `prometheus-client` exposes metrics at `/metrics`. Prometheus scrapes them; Grafana dashboards are auto-provisioned from `docker/grafana/`.

**Structured logging:** All log output is JSON-formatted (`OBS_LOG_FORMAT=json`), routed through Python's standard logging, and captured by Docker's json-file driver.

---

## Testing

```bash
# Install test dependencies (included in requirements.txt)
pip install pytest pytest-asyncio

# Run all tests
pytest

# Run with coverage
pytest --cov=app tests/
```

Test configuration is in `pytest.ini`. Integration tests require a running PostgreSQL and Redis instance (or use the Docker Compose `test` profile).

---

## Contributing

1. Fork the repo and create a feature branch
2. Follow the existing agent pattern in `app/agents/`
3. Add tests in `tests/`
4. Run `pytest` and ensure all tests pass
5. Open a pull request — CI will lint, test, and build automatically

---

## Roadmap

- [ ] GPU inference support for faster-whisper (`STT_WHISPER_DEVICE=cuda`)
- [ ] Multi-step installations (e.g. "Install Node.js then create a React app")
- [ ] Windows service / macOS LaunchAgent for always-on voice listening
- [ ] Web-based admin panel for task history and agent configuration
- [ ] Package manager auto-detection per OS without manual env config
- [ ] Expanded RAG knowledge base with vulnerability advisories

---

## License

See [LICENSE](LICENSE) for details.

---

*Built with FastAPI · LangGraph · Mistral AI · Playwright · Celery · Redis · PostgreSQL · Qdrant*