#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

TUNNEL_ENABLED="${CDOT_DEMO_TUNNEL:-1}"
DEMO_HOST="${CDOT_DEMO_HOST:-127.0.0.1}"
PREFERRED_PORT="${CDOT_DEMO_PORT:-8000}"
TUNNEL_ORIGIN_HOST="${CDOT_DEMO_TUNNEL_ORIGIN_HOST:-127.0.0.1}"
DEMO_USER="${CDOT_DEMO_USER:-presenter}"

if ! [[ "$PREFERRED_PORT" =~ ^[0-9]+$ ]] || (( PREFERRED_PORT < 1 || PREFERRED_PORT > 65535 )); then
  echo "CDOT_DEMO_PORT must be an integer between 1 and 65535" >&2
  exit 1
fi

port_is_free() {
  ./env/bin/python - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", port))
    except OSError:
        raise SystemExit(1)
PY
}

DEMO_PORT="$PREFERRED_PORT"
for ((candidate = PREFERRED_PORT; candidate <= PREFERRED_PORT + 50 && candidate <= 65535; candidate++)); do
  if port_is_free "$candidate"; then
    DEMO_PORT="$candidate"
    break
  fi
done

if ! port_is_free "$DEMO_PORT"; then
  echo "No free demo port found between $PREFERRED_PORT and $((PREFERRED_PORT + 50))" >&2
  exit 1
fi

if [[ "$DEMO_PORT" != "$PREFERRED_PORT" ]]; then
  echo "Port $PREFERRED_PORT is busy; using $DEMO_PORT for the C-DOT demo."
fi

if [[ "$TUNNEL_ENABLED" == "1" ]]; then
  if ! command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared is required when CDOT_DEMO_TUNNEL=1" >&2
    exit 1
  fi
  if [[ -z "${CDOT_DEMO_PASSWORD:-}" ]]; then
    CDOT_DEMO_PASSWORD="$(./env/bin/python -c 'import secrets; print(secrets.token_urlsafe(12))')"
  fi
  if [[ -z "${CDOT_DEMO_SECRET:-}" ]]; then
    CDOT_DEMO_SECRET="$(./env/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
else
  CDOT_DEMO_PASSWORD="${CDOT_DEMO_PASSWORD:-demo}"
  CDOT_DEMO_SECRET="${CDOT_DEMO_SECRET:-local-demo-only-change-me}"
fi

export CDOT_DEMO_USER="$DEMO_USER"
export CDOT_DEMO_PASSWORD
export CDOT_DEMO_SECRET

if [[ "${CDOT_DEMO_SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the operator dashboard" >&2
    exit 1
  fi
  npm --prefix frontend run build
fi

./env/bin/python scripts/preflight.py

if [[ "$TUNNEL_ENABLED" != "1" ]]; then
  echo "Local URL: http://$DEMO_HOST:$DEMO_PORT"
  echo "Presenter username: $CDOT_DEMO_USER"
  echo "Presenter password: $CDOT_DEMO_PASSWORD"
  exec ./env/bin/uvicorn demo_api.main:app --host "$DEMO_HOST" --port "$DEMO_PORT"
fi

RUNTIME_DIR="$(mktemp -d -t cdot-demo.XXXXXX)"
TUNNEL_LOG="$RUNTIME_DIR/cloudflared.log"
SERVER_PID=""
TUNNEL_PID=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$TUNNEL_PID" ]] && kill -0 "$TUNNEL_PID" 2>/dev/null; then
    kill "$TUNNEL_PID" 2>/dev/null || true
    wait "$TUNNEL_PID" 2>/dev/null || true
  fi
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$TUNNEL_LOG"
  rmdir "$RUNTIME_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

./env/bin/uvicorn demo_api.main:app --host "$DEMO_HOST" --port "$DEMO_PORT" &
SERVER_PID=$!

READY=0
for _ in {1..80}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    wait "$SERVER_PID"
    exit 1
  fi
  if ./env/bin/python - "$TUNNEL_ORIGIN_HOST" "$DEMO_PORT" <<'PY'
import json
import sys
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(
        f"http://{sys.argv[1]}:{sys.argv[2]}/api/v1/health", timeout=0.5
    ) as response:
        payload = json.load(response)
except (OSError, urllib.error.URLError):
    raise SystemExit(1)
if payload.get("status") != "ok":
    raise SystemExit(1)
PY
  then
    READY=1
    break
  fi
  sleep 0.25
done

if [[ "$READY" != "1" ]]; then
  echo "The C-DOT API did not become ready on port $DEMO_PORT" >&2
  exit 1
fi

cloudflared tunnel --url "http://$TUNNEL_ORIGIN_HOST:$DEMO_PORT" --no-autoupdate >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

PUBLIC_URL=""
for _ in {1..120}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    cat "$TUNNEL_LOG" >&2
    wait "$TUNNEL_PID"
    exit 1
  fi
  PUBLIC_URL="$(sed -nE 's|.*(https://[-a-z0-9]+\.trycloudflare\.com).*|\1|p' "$TUNNEL_LOG" | head -n 1)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 0.25
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "cloudflared started but did not publish a tunnel URL" >&2
  cat "$TUNNEL_LOG" >&2
  exit 1
fi

echo
echo "C-DOT demo is ready"
echo "Local URL:  http://$TUNNEL_ORIGIN_HOST:$DEMO_PORT"
echo "Public URL: $PUBLIC_URL"
echo "Presenter username: $CDOT_DEMO_USER"
echo "Presenter password: $CDOT_DEMO_PASSWORD"
echo "Press Ctrl+C to stop both the API and Cloudflare tunnel."
echo

set +e
wait -n "$SERVER_PID" "$TUNNEL_PID"
EXIT_STATUS=$?
set -e

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "The C-DOT API stopped." >&2
elif ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
  echo "The Cloudflare tunnel stopped." >&2
  cat "$TUNNEL_LOG" >&2
fi
exit "$EXIT_STATUS"
