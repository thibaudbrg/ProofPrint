#!/usr/bin/env bash
# Start the auto-reloading server. Leave your cloudflared tunnel running
# separately so the https URL stays the same.
cd "$(dirname "$0")/server"
exec ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
