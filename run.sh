#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/server"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
echo "Server on http://localhost:8000  —  expose it with:"
echo "  cloudflared tunnel --url http://localhost:8000"
uvicorn main:app --host 0.0.0.0 --port 8000
