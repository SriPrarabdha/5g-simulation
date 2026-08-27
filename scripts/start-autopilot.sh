#!/usr/bin/env bash
#
# Keep the C-DOT closed loop running on this machine.
#
# Polls their Prometheus continuously and, every ten minutes, forecasts, solves,
# and POSTs new per-UPF weights to their SMF.  Restarts itself if the process
# dies, and survives logout when started with --detach.
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

usage() {
  cat <<'EOF'
Usage: ./scripts/start-autopilot.sh [options] [-- <runner args>]

Options:
  --prometheus URL   C-DOT Prometheus base URL   (default: $CDOT_PROMETHEUS_URL or the config)
  --smf URL          C-DOT SMF base URL          (default: $CDOT_SMF_URL or the config)
  --control-seconds N  Seconds between optimise-and-write cycles (default 600)
  --poll-seconds N     Seconds between telemetry polls           (default 30)
  --dry-run          Compute and log the weights, never POST them. Rehearse with this first.
  --once             Prime, run one cycle, print the outcome, exit. Good for a first contact test.
  --detach           Run in the background under a restart supervisor, survives logout.
  --stop             Stop a detached run.
  --status           Show whether a detached run is alive, and tail its log.
  -h, --help         Show this help.

Everything after `--` is passed straight to `python -m demo_api.cdot_live.runner`.

Files (under logs/):
  cdot-autopilot.log        the loop's own log, rotated at 10 MB
  autopilot-supervisor.log  restarts and exit codes
  autopilot.pid             the supervisor's pid while detached

Note: this is the same loop the dashboard runs when started with
CDOT_LIVE_AUTOPILOT=1.  Run one or the other, never both -- two loops writing
/upf-admin on different ten-minute phases fight over the weight table.
EOF
}

LOG_DIR="$PROJECT_DIR/logs"
PIDFILE="$LOG_DIR/autopilot.pid"
SUPERVISOR_LOG="$LOG_DIR/autopilot-supervisor.log"
LOOP_LOG="$LOG_DIR/cdot-autopilot.log"
mkdir -p "$LOG_DIR"

DETACH=0
ARGS=()
while (( $# > 0 )); do
  case "$1" in
    --prometheus|--smf|--control-seconds|--poll-seconds)
      if (( $# < 2 )); then echo "$1 requires a value" >&2; exit 2; fi
      ARGS+=("$1" "$2"); shift 2 ;;
    --prometheus=*|--smf=*|--control-seconds=*|--poll-seconds=*)
      ARGS+=("$1"); shift ;;
    --dry-run|--once|--verbose) ARGS+=("$1"); shift ;;
    --detach) DETACH=1; shift ;;
    --stop)
      if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        pkill -TERM -P "$(cat "$PIDFILE")" 2>/dev/null || true
        kill -TERM "$(cat "$PIDFILE")" 2>/dev/null || true
        echo "Stopped the C-DOT autopilot (supervisor pid $(cat "$PIDFILE"))."
        rm -f "$PIDFILE"
      else
        echo "No detached autopilot is running."
        rm -f "$PIDFILE"
      fi
      exit 0 ;;
    --status)
      if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Running (supervisor pid $(cat "$PIDFILE"))."
      else
        echo "Not running."
      fi
      echo "--- last 20 lines of $LOOP_LOG ---"
      tail -n 20 "$LOOP_LOG" 2>/dev/null || echo "(no log yet)"
      exit 0 ;;
    --) shift; ARGS+=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -n "${CDOT_AUTOPILOT_PYTHON:-}" ]]; then
  PYTHON_BIN="$CDOT_AUTOPILOT_PYTHON"
elif [[ -x "$PROJECT_DIR/env/bin/python" ]]; then
  PYTHON_BIN="$PROJECT_DIR/env/bin/python"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  PYTHON_BIN="$CONDA_PREFIX/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
fi
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "No usable Python found; set CDOT_AUTOPILOT_PYTHON." >&2
  exit 1
fi

# A live endpoint means live mode, whatever the config file's demo default says,
# whether it arrived as an environment variable or as --prometheus.
if [[ -n "${CDOT_PROMETHEUS_URL:-}" ]] || printf '%s\n' ${ARGS[@]+"${ARGS[@]}"} | grep -q '^--prometheus'; then
  export CDOT_LIVE_SOURCE="${CDOT_LIVE_SOURCE:-prometheus}"
fi

supervise() {
  local attempt=0
  while true; do
    attempt=$((attempt + 1))
    echo "$(date -Iseconds) starting the autopilot (attempt $attempt)" >>"$SUPERVISOR_LOG"
    set +e
    if [[ "$DETACH" == "1" ]]; then
      # The loop's own rotating file already holds every health line, so drop
      # stdout and keep only stderr here: this file must stay small enough to
      # read, and it has no rotation of its own.
      "$PYTHON_BIN" -m demo_api.cdot_live.runner "${ARGS[@]+"${ARGS[@]}"}" \
        >/dev/null 2>>"$SUPERVISOR_LOG"
    else
      "$PYTHON_BIN" -m demo_api.cdot_live.runner "${ARGS[@]+"${ARGS[@]}"}"
    fi
    local code=$?
    set -e
    # 0 = clean exit, 130 = Ctrl-C, 143 = SIGTERM: all deliberate, so stay down.
    if [[ $code -eq 0 || $code -eq 130 || $code -eq 143 ]]; then
      echo "$(date -Iseconds) autopilot exited cleanly (code $code); not restarting" >>"$SUPERVISOR_LOG"
      return 0
    fi
    echo "$(date -Iseconds) autopilot died with code $code; restarting in 10s" >>"$SUPERVISOR_LOG"
    sleep 10
  done
}

# --once is a one-shot probe; supervising it would restart it forever.
for arg in ${ARGS[@]+"${ARGS[@]}"}; do
  if [[ "$arg" == "--once" ]]; then
    exec "$PYTHON_BIN" -m demo_api.cdot_live.runner "${ARGS[@]}"
  fi
done

if [[ "$DETACH" == "1" ]]; then
  if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "An autopilot is already running (supervisor pid $(cat "$PIDFILE")). Stop it first." >&2
    exit 1
  fi
  supervise >>"$SUPERVISOR_LOG" &
  SUPERVISOR_PID=$!
  disown "$SUPERVISOR_PID" 2>/dev/null || true
  echo "$SUPERVISOR_PID" >"$PIDFILE"
  echo "C-DOT autopilot detached (supervisor pid $SUPERVISOR_PID)."
  echo "  loop log   : $LOOP_LOG"
  echo "  restarts   : $SUPERVISOR_LOG"
  echo "  stop it    : ./scripts/start-autopilot.sh --stop"
  echo "  check it   : ./scripts/start-autopilot.sh --status"
  exit 0
fi

echo "Running the C-DOT autopilot in the foreground. Ctrl-C to stop."
supervise
