#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -n "${CDOT_WORKSHOP_PYTHON:-}" ]]; then
  PYTHON_BIN="$CDOT_WORKSHOP_PYTHON"
elif [[ -x "$PROJECT_DIR/env/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/env/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "No usable Python found; set CDOT_WORKSHOP_PYTHON." >&2
  exit 1
fi

WORKSHOP_HOST="${CDOT_WORKSHOP_HOST:-127.0.0.1}"
PUBLIC_HOST="${CDOT_WORKSHOP_PUBLIC_HOST:-$WORKSHOP_HOST}"
DASHBOARD_PORT="${CDOT_WORKSHOP_DASHBOARD_PORT:-8000}"
NOTEBOOK_PORT="${CDOT_WORKSHOP_NOTEBOOK_PORT:-8888}"
TEAM_COUNT="${CDOT_WORKSHOP_TEAMS:-6}"
OUTPUT_ROOT="$PROJECT_DIR/output/workshop"

if ! "$PYTHON_BIN" -c 'import jupyterlab' >/dev/null 2>&1; then
  echo "JupyterLab is missing. Install the rehearsed workshop environment with:" >&2
  echo "  env/bin/pip install -e '.[workshop]'" >&2
  exit 1
fi

"$PYTHON_BIN" -m workshop.build_notebooks
"$PYTHON_BIN" -m workshop.prepare_teams --teams "$TEAM_COUNT" --output-root "$OUTPUT_ROOT"

if [[ "${CDOT_WORKSHOP_SKIP_FRONTEND_BUILD:-0}" != "1" ]]; then
  npm --prefix frontend run build
fi
"$PYTHON_BIN" scripts/preflight.py --scope workshop

CDOT_DEMO_USER="${CDOT_DEMO_USER:-presenter}"
CDOT_DEMO_PASSWORD="${CDOT_DEMO_PASSWORD:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(12))')}"
CDOT_DEMO_SECRET="${CDOT_DEMO_SECRET:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(32))')}"
WORKSHOP_TOKEN="${CDOT_WORKSHOP_TOKEN:-$($PYTHON_BIN -c 'import secrets; print(secrets.token_urlsafe(18))')}"
export CDOT_DEMO_USER CDOT_DEMO_PASSWORD CDOT_DEMO_SECRET
PARTICIPANT_URL="http://$PUBLIC_HOST:$NOTEBOOK_PORT/lab?token=$WORKSHOP_TOKEN"
MATERIALS_QR="$OUTPUT_ROOT/materials-qr.svg"
"$PYTHON_BIN" -m workshop.materials_qr --url "$PARTICIPANT_URL" --output "$MATERIALS_QR" >/dev/null

DASHBOARD_PID=""
NOTEBOOK_PID=""
cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$NOTEBOOK_PID" ]] && kill -0 "$NOTEBOOK_PID" 2>/dev/null; then kill "$NOTEBOOK_PID" 2>/dev/null || true; fi
  if [[ -n "$DASHBOARD_PID" ]] && kill -0 "$DASHBOARD_PID" 2>/dev/null; then kill "$DASHBOARD_PID" 2>/dev/null || true; fi
  wait "$NOTEBOOK_PID" "$DASHBOARD_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$PYTHON_BIN" -m uvicorn demo_api.main:app --host "$WORKSHOP_HOST" --port "$DASHBOARD_PORT" &
DASHBOARD_PID=$!
"$PYTHON_BIN" -m jupyterlab \
  --no-browser \
  --ServerApp.ip="$WORKSHOP_HOST" \
  --ServerApp.port="$NOTEBOOK_PORT" \
  --ServerApp.port_retries=0 \
  --ServerApp.root_dir="$OUTPUT_ROOT" \
  --IdentityProvider.token="$WORKSHOP_TOKEN" \
  --ServerApp.allow_remote_access=True &
NOTEBOOK_PID=$!

echo
echo "C-DOT workshop services are starting"
echo "Participant notebook URL: $PARTICIPANT_URL"
echo "Participant QR: $MATERIALS_QR"
echo "Team folders: team-01 through team-$(printf '%02d' "$TEAM_COUNT")"
echo
echo "PRESENTER ONLY"
echo "Dashboard URL: http://$PUBLIC_HOST:$DASHBOARD_PORT"
echo "Dashboard username: $CDOT_DEMO_USER"
echo "Dashboard password: $CDOT_DEMO_PASSWORD"
echo
echo "Do not copy the presenter block into participant materials. Press Ctrl+C to stop both services."

set +e
wait -n "$DASHBOARD_PID" "$NOTEBOOK_PID"
STATUS=$?
set -e
exit "$STATUS"
