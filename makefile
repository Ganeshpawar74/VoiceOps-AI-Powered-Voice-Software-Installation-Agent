# ============================================================
# VoiceOps — Makefile
# All commands to build, run, test, and maintain the project.
# Usage: make <target>
# ============================================================

.PHONY: help setup build up down restart logs shell test clean nuke \
        api-logs worker-logs db-shell redis-cli flower migrate \
        celery-status celery-purge scale-workers health verify

# ── Default ───────────────────────────────────────────────────
help:
	@echo ""
	@echo "  VoiceOps — Available Commands"
	@echo "  ─────────────────────────────────────────────"
	@echo "  SETUP"
	@echo "    make setup          First-time setup (copy .env, build images)"
	@echo "    make build          Build/rebuild Docker images"
	@echo ""
	@echo "  RUN"
	@echo "    make up             Start all services (detached)"
	@echo "    make up-infra       Start only infrastructure (postgres, redis, qdrant)"
	@echo "    make up-app         Start only application (api, worker, flower)"
	@echo "    make down           Stop all services"
	@echo "    make restart        Restart all services"
	@echo "    make restart-api    Restart only the API container"
	@echo "    make restart-worker Restart only the workers"
	@echo ""
	@echo "  LOGS"
	@echo "    make logs           Tail all logs"
	@echo "    make api-logs       Tail API logs only"
	@echo "    make worker-logs    Tail Celery worker logs"
	@echo ""
	@echo "  DEBUG / SHELL"
	@echo "    make shell          Bash into the API container"
	@echo "    make db-shell       psql shell inside postgres"
	@echo "    make redis-cli      redis-cli inside redis container"
	@echo ""
	@echo "  TEST & VERIFY"
	@echo "    make test           Run pytest suite"
	@echo "    make health         Call /api/health"
	@echo "    make verify         Full smoke test (install vs code)"
	@echo ""
	@echo "  CELERY"
	@echo "    make celery-status  Show active/reserved Celery tasks"
	@echo "    make celery-purge   Purge the installs queue"
	@echo "    make scale-workers  Scale workers to 3 replicas"
	@echo ""
	@echo "  MAINTENANCE"
	@echo "    make clean          Remove stopped containers + dangling images"
	@echo "    make nuke           ⚠ Destroy ALL containers, volumes, images"
	@echo "    make pycache-clean  Delete all __pycache__ and .pyc files"
	@echo ""


# ── Setup ─────────────────────────────────────────────────────
setup:
	@echo "→ Checking .env..."
	@test -f .env || (cp .env.example .env && echo "  Created .env from .env.example — fill in API keys!")
	@echo "→ Building Docker images..."
	docker compose build --no-cache
	@echo ""
	@echo "✅ Setup complete. Edit .env with your API keys, then run: make up"

build:
	docker compose build

build-nocache:
	docker compose build --no-cache


# ── Run ───────────────────────────────────────────────────────
up:
	docker compose up -d
	@echo ""
	@echo "✅ VoiceOps is up!"
	@echo "   API      → http://localhost:8000"
	@echo "   Docs     → http://localhost:8000/api/docs"
	@echo "   Frontend → http://localhost:3000"
	@echo "   Flower   → http://localhost:5555"
	@echo "   Grafana  → http://localhost:3001  (admin/admin)"
	@echo "   Jaeger   → http://localhost:16686"
	@echo "   Prometheus → http://localhost:9090"

up-infra:
	docker compose up -d postgres redis qdrant
	@echo "✅ Infrastructure up (postgres, redis, qdrant)"

up-app:
	docker compose up -d api worker flower
	@echo "✅ Application up (api, worker, flower)"

up-obs:
	docker compose up -d jaeger prometheus grafana
	@echo "✅ Observability up (jaeger, prometheus, grafana)"

down:
	docker compose down

down-volumes:
	@echo "⚠  This will DELETE all data volumes (postgres, redis, qdrant)!"
	@read -p "   Are you sure? [y/N] " ans && [ "$$ans" = "y" ] && docker compose down -v || echo "Aborted."

restart:
	docker compose restart

restart-api:
	docker compose restart api

restart-worker:
	docker compose restart worker


# ── Logs ──────────────────────────────────────────────────────
logs:
	docker compose logs -f --tail=100

api-logs:
	docker compose logs -f --tail=100 api

worker-logs:
	docker compose logs -f --tail=100 worker

flower-logs:
	docker compose logs -f --tail=50 flower

postgres-logs:
	docker compose logs -f --tail=50 postgres


# ── Shell / Debug ─────────────────────────────────────────────
shell:
	docker compose exec api bash

worker-shell:
	docker compose exec worker bash

db-shell:
	docker compose exec postgres psql -U voiceops -d voiceops

redis-cli:
	docker compose exec redis redis-cli

redis-monitor:
	docker compose exec redis redis-cli monitor


# ── Database ──────────────────────────────────────────────────
migrate:
	docker compose exec api python -c \
		"import asyncio; from app.services.task_store import init_db; asyncio.run(init_db())"
	@echo "✅ Database tables created/verified"

db-tasks:
	docker compose exec postgres psql -U voiceops -d voiceops \
		-c "SELECT id, user_id, status, query, created_at FROM tasks ORDER BY created_at DESC LIMIT 20;"


# ── Celery ────────────────────────────────────────────────────
celery-status:
	docker compose exec worker celery -A app.services.tasks.celery_app inspect active
	docker compose exec worker celery -A app.services.tasks.celery_app inspect reserved

celery-purge:
	@echo "⚠  Purging all tasks from the installs queue..."
	docker compose exec worker celery -A app.services.tasks.celery_app purge -Q installs -f

scale-workers:
	docker compose up -d --scale worker=3 worker
	@echo "✅ Scaled to 3 worker replicas"

flower:
	@echo "Flower UI → http://localhost:5555"


# ── Test & Smoke ──────────────────────────────────────────────
test:
	@echo "→ Clearing pycache first..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	pytest tests/ -v --tb=short

test-fast:
	pytest tests/ -v -x --tb=short -q

health:
	@curl -s http://localhost:8000/api/health | python -m json.tool

verify:
	@echo "→ Smoke test: install vs code..."
	@curl -s -X POST http://localhost:8000/api/v1/text/command \
		-H "Content-Type: application/json" \
		-d '{"query": "install vs code", "os_hint": "windows"}' \
		| python -m json.tool

verify-task:
	@echo "Usage: make verify-task TASK_ID=<your-task-id>"
	@curl -s http://localhost:8000/api/v1/tasks/$(TASK_ID) | python -m json.tool

# ── Maintenance ───────────────────────────────────────────────
pycache-clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Python bytecode cache cleared"

clean: pycache-clean
	docker compose down --remove-orphans
	docker image prune -f
	@echo "✅ Cleaned stopped containers and dangling images"

nuke:
	@echo "⚠  WARNING: This destroys ALL containers, volumes, and images!"
	@read -p "   Type 'yes' to confirm: " ans && [ "$$ans" = "yes" ] \
		&& docker compose down -v --rmi all --remove-orphans \
		|| echo "Aborted."

# ── CI / Local pre-commit ──────────────────────────────────────
lint:
	ruff check app/ tests/

format:
	ruff format app/ tests/

typecheck:
	mypy app/ --ignore-missing-imports