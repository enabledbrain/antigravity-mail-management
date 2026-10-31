#!/usr/bin/env bash
# antigravity-mail-management - Docker Runner (macOS / Linux)
set -e
cd "$(dirname "$0")"

# Copy .env.example to .env if not exists
if [ ! -f .env ]; then
  cp .env.example .env
  echo "ℹ️  Created .env from .env.example. Please review THUNDERBIRD_PROFILE_PATH in .env."
fi

# Ensure search-engine container is running
if ! docker compose ps | grep -q "ai-mailer-search"; then
  echo "🔍 [Meilisearch] Starting search engine container..."
  docker compose up -d search-engine
fi

# Run Slack Socket Mode listener if requested
if [ "$1" = "--listen" ]; then
  echo "🚀 [Slack Listener] Starting Socket Mode interactive listener..."
  docker compose run --rm --entrypoint python3 ai-mailer slack_listener.py
  exit 0
fi

# Run AI Mailer worker
docker compose run --rm ai-mailer "$@"
