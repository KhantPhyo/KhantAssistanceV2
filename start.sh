#!/usr/bin/env bash
# Khant Assistance v2 — one-shot launcher
# Backend (FastAPI) + Frontend (Vite) — single terminal, Ctrl+C stops both.

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
LOG_DIR="$ROOT/.logs"
mkdir -p "$LOG_DIR"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; CYN=$'\e[36m'; RST=$'\e[0m'
log()  { printf "${CYN}[launcher]${RST} %s\n" "$*"; }
err()  { printf "${RED}[error]${RST} %s\n" "$*" >&2; }

# Pick the newest Python 3.10+ available (3.9 lacks PEP 604 `str | None` syntax we use)
PYTHON=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
      PYTHON="$cand"; break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  err "Python 3.10+ not found. Install with: brew install python@3.12  (macOS) or apt install python3.11 (Linux)"
  exit 1
fi
log "Using $PYTHON ($(command -v "$PYTHON"))"
command -v npm >/dev/null 2>&1 || { err "npm not found (install Node.js 18+)"; exit 1; }

# --- kill any stale instance of THIS project ---
# Telegram allows only ONE getUpdates poller per bot token. If a previous
# uvicorn worker is still alive (e.g. orphaned by --reload, crashed, or a
# second start.sh invocation), the new one will fight it for /getUpdates and
# log 409 Conflict in a tight loop. We kill aggressively by pattern AND port.
log "Cleaning up any previous instance..."
# 1) by process pattern — catches uvicorn workers/parents and bare python -m
PATTERN_PIDS=$(pgrep -f "uvicorn app\.main:app|KhantAssistanceV2.*app\.main" 2>/dev/null || true)
# 2) by port (vite + uvicorn both)
PORT_PIDS=""
for port in 8000 5173; do
  p=$(lsof -ti :$port 2>/dev/null || true)
  [ -n "$p" ] && PORT_PIDS="$PORT_PIDS $p"
done
ALL_STALE=$(echo "$PATTERN_PIDS $PORT_PIDS" | tr ' ' '\n' | sort -u | grep -v '^$' || true)
if [ -n "$ALL_STALE" ]; then
  log "Killing stale PIDs: $(echo $ALL_STALE | tr '\n' ' ')"
  echo "$ALL_STALE" | xargs kill -9 2>/dev/null || true
fi
# Telegram needs ~3-5s to release the previous getUpdates session — wait it out
# so the new bot doesn't immediately get its own 409 from the prior socket.
sleep 4

# ---------------- BACKEND SETUP ----------------
cd "$BACKEND_DIR"
if [ ! -d ".venv" ]; then
  log "Creating Python venv with $PYTHON..."
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

MARK=".venv/.requirements.sha"
NEW_SHA=$(shasum requirements.txt | awk '{print $1}')
OLD_SHA=$(cat "$MARK" 2>/dev/null || echo "")
if [ "$NEW_SHA" != "$OLD_SHA" ]; then
  log "Installing backend dependencies..."
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  echo "$NEW_SHA" > "$MARK"
else
  log "Backend deps up-to-date."
fi

if [ ! -f ".env" ]; then
  log "Creating backend/.env from .env.example"
  cp .env.example .env
fi

# ---------------- FRONTEND SETUP ----------------
cd "$FRONTEND_DIR"
if [ ! -d "node_modules" ] || [ package.json -nt node_modules ]; then
  log "Installing frontend dependencies (this may take a minute)..."
  npm install --silent
else
  log "Frontend deps up-to-date."
fi

# ---------------- RUN ----------------
cd "$ROOT"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
: > "$BACKEND_LOG"; : > "$FRONTEND_LOG"

# --reload spawns parent+worker processes; on file change the worker is
# replaced but the OLD bot polling task can briefly survive, causing a
# 409 Conflict on Telegram /getUpdates. Disabled by default. Opt in via:
#   DEV_RELOAD=1 ./start.sh
RELOAD_FLAG=""
if [ "${DEV_RELOAD:-}" = "1" ]; then
  RELOAD_FLAG="--reload"
  log "DEV_RELOAD=1 → uvicorn will auto-reload on file change (may cause 409 with Telegram polling)"
fi

log "Starting backend → http://localhost:8000"
(
  cd "$BACKEND_DIR"
  # shellcheck disable=SC1091
  source .venv/bin/activate
  exec uvicorn app.main:app $RELOAD_FLAG --host 0.0.0.0 --port 8000
) >"$BACKEND_LOG" 2>&1 &
BACK_PID=$!

log "Starting frontend → http://localhost:5173"
(
  cd "$FRONTEND_DIR"
  exec npm run dev -- --host
) >"$FRONTEND_LOG" 2>&1 &
FRONT_PID=$!

cleanup() {
  echo
  log "Shutting down..."
  kill "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
  wait "$BACK_PID" 2>/dev/null || true
  wait "$FRONT_PID" 2>/dev/null || true
  log "Stopped."
  exit 0
}
trap cleanup INT TERM

stream() {
  local tag=$1 color=$2 file=$3
  tail -n +1 -F "$file" 2>/dev/null | while IFS= read -r line; do
    printf "${color}[%s]${RST} %s\n" "$tag" "$line"
  done
}

stream "backend"  "$GRN" "$BACKEND_LOG"  &
TAIL1=$!
stream "frontend" "$BLU" "$FRONTEND_LOG" &
TAIL2=$!

log "${YLW}Press Ctrl+C to stop both servers.${RST}"
log "Login: khantphyo.myanmar@gmail.com / Cisco@123"
log "Web UI: http://localhost:5173"

wait -n "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
kill "$TAIL1" "$TAIL2" 2>/dev/null || true
cleanup
