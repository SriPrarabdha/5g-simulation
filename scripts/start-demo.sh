#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

./env/bin/python scripts/preflight.py
exec ./env/bin/uvicorn demo_api.main:app --host "${CDOT_DEMO_HOST:-127.0.0.1}" --port "${CDOT_DEMO_PORT:-8000}"
