# VoiceOps — AI Voice Operating Agent

> Speak a command → AI installs the software for you.

```
"Install VS Code"  →  🎤 STT  →  🧠 Intent  →  📋 Plan  →  🌐 Browse  →  ⬇ Download  →  ⚙ Install  →  🔔 Notify
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| STT | faster-whisper (local) + Sarvam AI |
| LLM | Mistral API (`mistral-small-latest`) |
| Agent Orchestration | LangGraph |
| Browser Automation | Playwright |
| Vector DB (RAG) | Qdrant |
| Task Queue | Celery + Redis |
| API | FastAPI |
| Database | PostgreSQL (SQLAlchemy async) |

## Quick Start

```bash
git clone https://github.com/yourname/voiceops
cd voiceops
cp .env.example .env       # set LLM_MISTRAL_API_KEY
bash scripts/setup.sh
uvicorn app.api.main:app --reload
```

See [docs/INSTALLATION.md](docs/INSTALLATION.md) for full guide.

## Supported Commands

```
"Install VS Code"
"Download Python 3.12 for Windows"
"Install Docker Desktop"
"Install Postman and open it"
"VS Code install karo"          # Hinglish
"Python install chahiye"         # Hindi
```

## Agents

1. **Speech Agent** — faster-whisper STT + noise reduction
2. **Intent Agent** — rule-based + Mistral API fallback
3. **Planner Agent** — LangGraph Plan-and-Execute
4. **Browser Agent** — Playwright + OCR fallback
5. **Download Agent** — streaming download + SHA-256 + signature verify
6. **Install Agent** — winget / brew / apt / direct installer
7. **Monitoring Agent** — Redis pub/sub progress events
8. **Notification Agent** — SSE / WebSocket notifications

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/voice/command` | Submit voice (base64 audio) |
| POST | `/api/v1/text/command` | Submit text command |
| GET | `/api/v1/tasks/{id}` | Task status |
| GET | `/api/v1/tasks/{id}/stream` | SSE progress stream |
| GET | `/api/v1/tasks` | Task history |
| WS | `/api/v1/ws/{user_id}` | WebSocket live updates |
| GET | `/api/health` | Health check |
| GET | `/api/docs` | Swagger UI |
