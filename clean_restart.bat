@echo off
REM ═══════════════════════════════════════════════════════════
REM VoiceOps — Clean Restart (Windows cmd.exe)
REM Clears stale __pycache__ and kills anything on port 8000,
REM then starts uvicorn fresh.
REM USAGE:  scripts\clean_restart.bat
REM ═══════════════════════════════════════════════════════════
cd /d "%~dp0\.."

echo [1/3] Removing __pycache__ folders...
for /f "delims=" %%d in ('dir /s /b /ad __pycache__ 2^>nul') do rd /s /q "%%d"

echo [2/3] Killing any process on port 8000...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo      Killing PID %%p
    taskkill /PID %%p /F >nul 2>&1
)

echo [3/3] Starting uvicorn fresh...
uvicorn app.api.main:app --reload --port 8000