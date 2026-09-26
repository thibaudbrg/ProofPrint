#!/usr/bin/env bash
# One command to run Proofprint end to end: sets up the venv if needed, starts
# the auto-reloading server, opens a public HTTPS tunnel (the phone camera
# needs a secure context - a plain http://<lan-ip> won't work), and prints the
# link to open on your phone. Ctrl+C stops both.
#
# Usage:
#   ./demo.sh            # normal (mitigated) app
#   ./demo.sh naive       # Act 2 of the demo: decides on face-match only
set -euo pipefail
cd "$(dirname "$0")"

PORT=8010   # NOT 8000 - VS Code Live Share silently forwards a teammate's
            # :8000 onto your own localhost:8000, so you'd end up serving
            # (or tunnelling) their app instead of yours. See PROJECT.md §8.

echo "== Proofprint dev runner =="

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed. Install it, then re-run this script:"
  echo "  brew install cloudflared"
  exit 1
fi

cd server
if [ ! -d .venv ]; then
  echo "-- creating venv --"
  python3 -m venv .venv
fi
echo "-- installing/checking dependencies (fast if already up to date) --"
./.venv/bin/pip install -q -r requirements.txt

if ! ./.venv/bin/python -c "import passporteye" >/dev/null 2>&1; then
  echo "!! passporteye failed to import even after install - check 2 (MRZ) will silently"
  echo "   report 'no MRZ' for every document. See PROJECT.md's venv gotcha."
fi
if ! command -v tesseract >/dev/null 2>&1; then
  echo "!! tesseract is not installed (needed for check 2's OCR): brew install tesseract"
fi
cd ..

MODE="full"
if [ "${1:-}" = "naive" ]; then
  MODE="naive"
  echo "-- starting in NAIVE mode (Act 2 of the demo: face-match only) --"
fi

SERVER_LOG=$(mktemp)
TUNNEL_LOG=$(mktemp)

# uvicorn --reload forks its actual worker via Python's multiprocessing, which
# shows up in `ps`/`pgrep -f` as a bare "spawn_main(...)" line with NO trace of
# "uvicorn" or "main:app" in it - pattern-matching the command line misses it
# entirely, and a killed reloader can leave that worker orphaned (PPID 1),
# still bound to the port. Kill by PORT instead: authoritative regardless of
# what the process calls itself.
kill_port() {
  local pids; pids=$(lsof -ti "tcp:$1" 2>/dev/null || true)
  [ -z "$pids" ] && return 0
  echo "   clearing stale process(es) on :$1: $pids"
  kill $pids 2>/dev/null || true
  sleep 1
  pids=$(lsof -ti "tcp:$1" 2>/dev/null || true)
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  sleep 0.3
}

echo "-- making sure :$PORT is free (clearing any leftover run) --"
kill_port "$PORT"
# A crashed previous run can also leave its cloudflared pointed at OUR port
# still running (uselessly - its URL is dead once the old server is gone).
# Safe to clear here, before we start: nothing legitimate should be pointed
# at this exact port yet. (Never done during shutdown - see cleanup()'s note.)
pkill -f "cloudflared tunnel --url http://localhost:$PORT" 2>/dev/null || true

CLEANED_UP=0
cleanup() {
  [ "$CLEANED_UP" = 1 ] && return
  CLEANED_UP=1
  echo
  echo "-- stopping server + tunnel --"
  # Only ever touch PIDs THIS run started (SERVER_PID/its own children, and our
  # own TUNNEL_PID) - never pattern-match by command line here. Two instances
  # of this script share the exact same cloudflared command line, and a broad
  # `pkill -f cloudflared` in one instance's cleanup was seen to kill the
  # OTHER instance's tunnel too. Port-clearing (kill_port) is a pre-flight
  # step only (above), never part of shutdown, for the same reason: by the
  # time this fires, the port may legitimately belong to someone else's run.
  [ -n "${SERVER_PID:-}" ] && pkill -TERM -P "$SERVER_PID" 2>/dev/null || true
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  [ -n "${TUNNEL_PID:-}" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  sleep 1
  [ -n "${SERVER_PID:-}" ] && kill -9 "$SERVER_PID" 2>/dev/null || true
  [ -n "${TUNNEL_PID:-}" ] && kill -9 "$TUNNEL_PID" 2>/dev/null || true
  if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
    echo "   note: :$PORT is still occupied (probably a reload worker this script can't see" \
         "by PID) - it will be cleared automatically the next time you run ./demo.sh."
  fi
}
trap cleanup EXIT INT TERM

echo "-- starting server on :$PORT (auto-reloads on file changes) --"
(cd server && PROOFPRINT_MODE=$MODE ./.venv/bin/uvicorn main:app --host 0.0.0.0 --port "$PORT" --reload) \
  > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 20); do
  curl -sf -o /dev/null "http://localhost:$PORT/" && break
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "!! server process exited immediately - tail of its log:"
    tail -30 "$SERVER_LOG"
    exit 1
  fi
  sleep 0.5
done
if ! curl -sf -o /dev/null "http://localhost:$PORT/"; then
  echo "!! server did not come up - tail of its log:"
  tail -30 "$SERVER_LOG"
  exit 1
fi
echo "   server is up: http://localhost:$PORT"

echo "-- starting cloudflared tunnel (public HTTPS link) --"
cloudflared tunnel --url "http://localhost:$PORT" > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

URL=""
for _ in $(seq 1 30); do
  URL=$(grep -Eo 'https://[a-zA-Z0-9.-]*trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done

if [ -z "$URL" ]; then
  echo "!! tunnel did not report a URL in time - tail of its log:"
  tail -30 "$TUNNEL_LOG"
  exit 1
fi

echo
echo "================================================================"
echo " Open this on your phone (same or different WiFi, both fine):"
echo
echo "   $URL"
echo
echo " Mode: $MODE   ·   Server log: $SERVER_LOG   ·   Tunnel log: $TUNNEL_LOG"
echo " GET  $URL/log   -> recent scored sessions + calibration stats"
echo " Ctrl+C stops both the server and the tunnel."
echo "================================================================"
echo

# Quick tunnels can silently die after an idle stretch (see PROJECT.md §8) and
# then loop retrying forever without ever recovering the SAME url. Watch for
# that and say so clearly instead of leaving you staring at a dead link.
while true; do
  sleep 15
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "!! the server process died - tail of its log:"; tail -30 "$SERVER_LOG"; exit 1
  fi
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "!! the tunnel process died - tail of its log:"; tail -30 "$TUNNEL_LOG"; exit 1
  fi
  if grep -q "Tunnel not found" "$TUNNEL_LOG" 2>/dev/null; then
    echo "!! the tunnel reports 'Tunnel not found' and is stuck retrying - that URL is dead."
    echo "   Stop this (Ctrl+C) and run ./demo.sh again for a fresh link."
    break
  fi
done

wait
