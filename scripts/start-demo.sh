#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

usage() {
  cat <<'EOF'
Usage: ./scripts/start-demo.sh [--cloudflare yes|no]

Options:
  --cloudflare yes|no  Enable the Cloudflare Quick Tunnel (default: yes).
  --no-cloudflare      Shorthand for --cloudflare no.
  -h, --help           Show this help message.

Environment:
  CDOT_DEMO_CLOUDFLARE  yes/no equivalent of --cloudflare.
  CDOT_DEMO_TUNNEL      Legacy 1/0 equivalent (still supported).
  CDOT_DEMO_PYTHON      Python executable to run the API with.
  CDOT_DEMO_HOST        Bind address (default 127.0.0.1).
  CDOT_DEMO_PORT        Preferred port (default 8000).
  CDOT_LIVE_SOURCE      replay | prometheus for the C-DOT console.

Command-line options take precedence over environment variables.
EOF
}

CLOUDFLARE_SETTING="${CDOT_DEMO_CLOUDFLARE:-${CDOT_DEMO_TUNNEL:-yes}}"
while (( $# > 0 )); do
  case "$1" in
    --cloudflare)
      if (( $# < 2 )); then
        echo "--cloudflare requires yes or no" >&2
        usage >&2
        exit 2
      fi
      CLOUDFLARE_SETTING="$2"
      shift 2
      ;;
    --cloudflare=*)
      CLOUDFLARE_SETTING="${1#*=}"
      shift
      ;;
    --no-cloudflare)
      CLOUDFLARE_SETTING="no"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${CLOUDFLARE_SETTING,,}" in
  y|yes|true|1|on)
    TUNNEL_ENABLED=1
    CLOUDFLARE_SETTING=yes
    ;;
  n|no|false|0|off)
    TUNNEL_ENABLED=0
    CLOUDFLARE_SETTING=no
    ;;
  *)
    echo "Invalid Cloudflare setting '$CLOUDFLARE_SETTING'; expected yes or no." >&2
    exit 2
    ;;
esac

if [[ -n "${CDOT_DEMO_PYTHON:-}" ]]; then
  PYTHON_BIN="$CDOT_DEMO_PYTHON"
elif [[ -x "$PROJECT_DIR/env/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/env/bin/python"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  PYTHON_BIN="$CONDA_PREFIX/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "No usable Python found; set CDOT_DEMO_PYTHON to the Python executable." >&2
  exit 1
fi

DEMO_HOST="${CDOT_DEMO_HOST:-127.0.0.1}"
PREFERRED_PORT="${CDOT_DEMO_PORT:-8000}"
TUNNEL_ORIGIN_HOST="${CDOT_DEMO_TUNNEL_ORIGIN_HOST:-127.0.0.1}"
DEMO_USER="${CDOT_DEMO_USER:-presenter}"

if ! [[ "$PREFERRED_PORT" =~ ^[0-9]+$ ]] || (( PREFERRED_PORT < 1 || PREFERRED_PORT > 65535 )); then
  echo "CDOT_DEMO_PORT must be an integer between 1 and 65535" >&2
  exit 1
fi

port_is_free() {
  "$PYTHON_BIN" - "$1" <<'PY'
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
  echo "If you are forwarding a port over SSH, forward $DEMO_PORT, not $PREFERRED_PORT."
fi

if [[ "$TUNNEL_ENABLED" == "1" ]]; then
  if ! command -v cloudflared >/dev/null 2>&1; then
    echo "cloudflared is required when --cloudflare yes (the default)" >&2
    echo "On a cluster login node, use: ./scripts/start-demo.sh --cloudflare no" >&2
    exit 1
  fi
  if [[ -z "${CDOT_DEMO_PASSWORD:-}" ]]; then
    CDOT_DEMO_PASSWORD="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(12))')"
  fi
  if [[ -z "${CDOT_DEMO_SECRET:-}" ]]; then
    CDOT_DEMO_SECRET="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
else
  CDOT_DEMO_PASSWORD="${CDOT_DEMO_PASSWORD:-demo}"
  CDOT_DEMO_SECRET="${CDOT_DEMO_SECRET:-local-demo-only-change-me}"
fi

export CDOT_DEMO_USER="$DEMO_USER"
export CDOT_DEMO_PASSWORD
export CDOT_DEMO_SECRET
export CDOT_DEMO_CLOUDFLARE="$CLOUDFLARE_SETTING"
export CDOT_DEMO_TUNNEL="$TUNNEL_ENABLED"

if [[ "${CDOT_DEMO_SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the operator dashboard" >&2
    exit 1
  fi
  npm --prefix frontend run build
fi

"$PYTHON_BIN" scripts/preflight.py

if [[ "$TUNNEL_ENABLED" != "1" ]]; then
  echo "Cloudflare tunnel: disabled"
  echo "Local URL: http://$DEMO_HOST:$DEMO_PORT"
  echo "C-DOT console: http://$DEMO_HOST:$DEMO_PORT/live-cdot"
  echo "Telemetry source: ${CDOT_LIVE_SOURCE:-replay}"
  echo "Presenter username: $CDOT_DEMO_USER"
  echo "Presenter password: $CDOT_DEMO_PASSWORD"
  exec "$PYTHON_BIN" -m uvicorn demo_api.main:app --host "$DEMO_HOST" --port "$DEMO_PORT"
fi

RUNTIME_DIR="$(mktemp -d -t cdot-demo.XXXXXX)"
TUNNEL_LOG="$RUNTIME_DIR/cloudflared.log"
TUNNEL_PIDFILE="$RUNTIME_DIR/cloudflared.pid"
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
  rm -f "$TUNNEL_LOG" "$TUNNEL_PIDFILE"
  rmdir "$RUNTIME_DIR" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -m uvicorn demo_api.main:app --host "$DEMO_HOST" --port "$DEMO_PORT" &
SERVER_PID=$!

READY=0
for _ in {1..80}; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    wait "$SERVER_PID"
    exit 1
  fi
  if "$PYTHON_BIN" - "$TUNNEL_ORIGIN_HOST" "$DEMO_PORT" <<'PY'
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
try:
    with socket.create_connection((host, port), timeout=1):
        pass
except OSError:
    raise SystemExit(1)
PY
  then
    READY=1
    break
  fi
  sleep 0.5
done

if [[ "$READY" != "1" ]]; then
  echo "The demo API did not become reachable on $TUNNEL_ORIGIN_HOST:$DEMO_PORT" >&2
  exit 1
fi

cloudflared tunnel --no-autoupdate --url "http://$TUNNEL_ORIGIN_HOST:$DEMO_PORT" \
  >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
echo "$TUNNEL_PID" >"$TUNNEL_PIDFILE"

PUBLIC_URL=""
for _ in {1..60}; do
  if ! kill -0 "$TUNNEL_PID" 2>/dev/null; then
    echo "cloudflared exited before publishing a URL:" >&2
    cat "$TUNNEL_LOG" >&2
    exit 1
  fi
  PUBLIC_URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n1 || true)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "cloudflared did not publish a Quick Tunnel URL in time:" >&2
  cat "$TUNNEL_LOG" >&2
  exit 1
fi

echo "Cloudflare tunnel: enabled"
echo "Public URL: $PUBLIC_URL"
echo "C-DOT console: $PUBLIC_URL/live-cdot"
echo "Local URL: http://$DEMO_HOST:$DEMO_PORT"
echo "Telemetry source: ${CDOT_LIVE_SOURCE:-replay}"
echo "Presenter username: $CDOT_DEMO_USER"
echo "Presenter password: $CDOT_DEMO_PASSWORD"
echo "Press Ctrl+C to stop the demo and close the tunnel."

wait "$SERVER_PID"
