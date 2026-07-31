#!/usr/bin/env bash
# Start the API and the frontend together, wait until both actually answer,
# and shut both down cleanly on Ctrl-C.
#
# Ports are overridable, and both sides are told about each other so a custom
# port cannot leave the browser calling the wrong place:
#   API_PORT=9000 WEB_PORT=3000 ./dev.sh
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
PYTHON="./venv/bin/python"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$1"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$1" >&2; exit 1; }

# --- prerequisites -----------------------------------------------------------
# Checked up front with a fix in the message, rather than letting a subprocess
# die three seconds later with something less obvious.

[ -x "$PYTHON" ] || fail "no venv at $PYTHON. Create one:
  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"

"$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null \
  || fail "the venv is missing FastAPI. Install it:
  ./venv/bin/pip install fastapi 'uvicorn[standard]'"

# nvm installs node into ~/.nvm and wires it up in ~/.bashrc, which a
# non-interactive shell never reads -- so find it here rather than assuming.
if ! command -v node >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  [ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true
fi
command -v node >/dev/null 2>&1 \
  || fail "node is not on PATH. If you use nvm, open a new terminal, or:
  export PATH=\"\$HOME/.nvm/versions/node/\$(ls ~/.nvm/versions/node | tail -1)/bin:\$PATH\""

[ -d web/node_modules ] || { log "installing frontend deps (slow on /mnt/c)"; (cd web && npm install); }

# --- shutdown ----------------------------------------------------------------
# npm does not forward signals to the vite process it spawns, so killing the
# listener on the port is what actually frees it. Without this the next run
# fails with EADDRINUSE.

cleanup() {
  trap - EXIT INT TERM
  log "shutting down"
  for port in "$API_PORT" "$WEB_PORT"; do
    lsof -ti:"$port" -sTCP:LISTEN 2>/dev/null | xargs -r kill 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for port in "$API_PORT" "$WEB_PORT"; do
  if lsof -ti:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "port $port is already in use. Free it with:
  lsof -ti:$port -sTCP:LISTEN | xargs -r kill"
  fi
done

# --- launch ------------------------------------------------------------------

log "API      → http://127.0.0.1:$API_PORT  (docs at /docs)"
# The frontend origin has to be allow-listed or the browser blocks every call.
SCHEDULER_ALLOWED_ORIGINS="http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT" \
  "$PYTHON" -m uvicorn api:app --port "$API_PORT" --log-level warning &

log "frontend → http://localhost:$WEB_PORT"
VITE_API_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix web run dev -- --port "$WEB_PORT" --strictPort >/dev/null &

# Poll rather than sleep: cold Vite startup on /mnt/c is unpredictable.
wait_for() {
  local url=$1 name=$2
  for _ in $(seq 1 60); do
    curl -sf -o /dev/null "$url" && return 0
    sleep 1
  done
  fail "$name did not come up within 60s"
}

wait_for "http://127.0.0.1:$API_PORT/factors" "the API"
wait_for "http://127.0.0.1:$WEB_PORT/" "the frontend"

printf '\n\033[1;32m  Ready →\033[0m http://localhost:%s\n' "$WEB_PORT"
printf '  First "Find schedules" takes ~20s; after that re-ranking is instant.\n'
printf '  Ctrl-C to stop both.\n\n'

wait
