@echo off
setlocal
cd /d "%~dp0"

if not exist .env (
    copy .env.example .env >nul
    echo [INFO] Created .env from .env.example. Please review THUNDERBIRD_PROFILE_PATH in .env.
)

echo [Meilisearch] Ensuring search engine container is running...
docker compose up -d search-engine >nul 2>&1

if "%~1"=="--listen" (
    echo [Slack Listener] Starting Socket Mode interactive listener...
    docker compose run --rm --entrypoint python3 ai-mailer slack_listener.py
    exit /b 0
)

echo [AI Mailer] Running worker...
docker compose run --rm ai-mailer %*
