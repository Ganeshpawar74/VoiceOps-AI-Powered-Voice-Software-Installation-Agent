#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# VoiceOps — Clean Restart
#
# WHY THIS EXISTS:
#   If you edit a .py file and the bug "doesn't go away", it is almost
#   always one of two things:
#     1. A stale __pycache__/*.pyc is being imported instead of your
#        edited source.
#     2. An OLD uvicorn process is still bound to port 8000 and is
#        the one actually answering your curl/browser requests.
#   This script eliminates both possibilities before starting fresh.
#
# USAGE (Git Bash / WSL / Linux / macOS):
#   bash scripts/clean_restart.sh
# ═══════════════════════════════════════════════════════════
set -e
cd "$(dirname "$0")/.."

echo "[1/3] Removing all __pycache__ directories and .pyc files..."
find . -type d -name "__pycache__" -not -path "*/.venv/*" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -not -path "*/.venv/*" -delete 2>/dev/null || true
echo "      done."

echo "[2/3] Killing any process listening on port 8000..."
if command -v lsof >/dev/null 2>&1; then
    PIDS=$(lsof -ti tcp:8000 || true)
    if [ -n "$PIDS" ]; then
        echo "      Killing PID(s): $PIDS"
        kill -9 $PIDS || true
    else
        echo "      Nothing listening on 8000."
    fi
elif command -v netstat >/dev/null 2>&1; then
    # Git Bash on Windows fallback
    PID=$(netstat -ano | grep ":8000" | grep LISTENING | awk '{print $5}' | head -n1)
    if [ -n "$PID" ]; then
        echo "      Killing PID: $PID (taskkill)"
        taskkill //PID "$PID" //F || true
    else
        echo "      Nothing listening on 8000."
    fi
fi

echo "[3/3] Starting uvicorn fresh (reload mode)..."
exec uvicorn app.api.main:app --reload --port 8000