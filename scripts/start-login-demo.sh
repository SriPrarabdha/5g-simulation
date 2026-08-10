#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "[demo] host=$(hostname)"
echo "[demo] project=$PROJECT_ROOT"

# Login shells do not always expose the `conda` shell function. Find the base
# installation and source its initialization script without modifying dotfiles.
CONDA_SH=""
if command -v conda >/dev/null 2>&1; then
    CONDA_BASE_PATH="$(conda info --base 2>/dev/null || true)"
    if [[ -n "$CONDA_BASE_PATH" && -f "$CONDA_BASE_PATH/etc/profile.d/conda.sh" ]]; then
        CONDA_SH="$CONDA_BASE_PATH/etc/profile.d/conda.sh"
    fi
fi
if [[ -z "$CONDA_SH" ]]; then
    for candidate in "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/conda"; do
        if [[ -f "$candidate/etc/profile.d/conda.sh" ]]; then
            CONDA_SH="$candidate/etc/profile.d/conda.sh"
            break
        fi
    done
fi
if [[ -z "$CONDA_SH" ]]; then
    echo "ERROR: Conda was not found." >&2
    echo "Load the cluster Conda module and run this script again, for example:" >&2
    echo "  module avail conda" >&2
    echo "  module load <cluster-conda-module>" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$CONDA_SH"

# This prefix is on the shared project filesystem and is reused on later runs.
CONDA_ENV_PREFIX="${CDOT_DEMO_CONDA_PREFIX:-$PROJECT_ROOT/.conda/cdot-demo}"
if [[ ! -x "$CONDA_ENV_PREFIX/bin/python" ]]; then
    echo "[deps] creating Conda environment at $CONDA_ENV_PREFIX"
    conda create --yes --prefix "$CONDA_ENV_PREFIX" --channel conda-forge \
        python=3.11 pip "nodejs>=22.12"
fi
conda activate "$CONDA_ENV_PREFIX"

if ! python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "[deps] installing a supported Python version"
    conda install --yes --prefix "$CONDA_ENV_PREFIX" --channel conda-forge python=3.11 pip
fi

if ! command -v npm >/dev/null 2>&1 || \
   ! node -e 'const [M,m]=process.versions.node.split(".").map(Number); process.exit(M>22 || (M===22&&m>=12) || (M===20&&m>=19) ? 0 : 1)'; then
    echo "[deps] installing a Vite-compatible Node.js and npm"
    conda install --yes --prefix "$CONDA_ENV_PREFIX" --channel conda-forge "nodejs>=22.12"
fi

PYTHON_IMPORTS='import fastapi, uvicorn, numpy, scipy, pyarrow, lightgbm, duckdb, httpx'
PYPROJECT_HASH="$(python -c 'import hashlib; print(hashlib.sha256(open("pyproject.toml", "rb").read()).hexdigest())')"
PYTHON_DEPS_STAMP="$CONDA_ENV_PREFIX/.cdot-python-deps-$PYPROJECT_HASH"
if [[ ! -f "$PYTHON_DEPS_STAMP" ]] || ! python -c "$PYTHON_IMPORTS" >/dev/null 2>&1; then
    echo "[deps] installing Python project dependencies"
    python -m pip install --upgrade pip
    python -m pip install --editable "$PROJECT_ROOT"
    touch "$PYTHON_DEPS_STAMP"
else
    echo "[deps] Python dependencies are available"
fi

PACKAGE_LOCK_HASH="$(python -c 'import hashlib; print(hashlib.sha256(open("frontend/package-lock.json", "rb").read()).hexdigest())')"
FRONTEND_DEPS_STAMP="$PROJECT_ROOT/frontend/node_modules/.cdot-package-lock-$PACKAGE_LOCK_HASH"
if [[ ! -f "$FRONTEND_DEPS_STAMP" ]] || ! npm --prefix frontend ls --depth=0 --silent >/dev/null 2>&1; then
    echo "[deps] installing pinned frontend dependencies with npm ci"
    npm --prefix frontend ci
    touch "$FRONTEND_DEPS_STAMP"
else
    echo "[deps] frontend dependencies are available"
fi

# cloudflared is distributed as a standalone binary. Keep it inside the Conda
# prefix so no root access or system-wide installation is required.
if ! command -v cloudflared >/dev/null 2>&1; then
    case "$(uname -m)" in
        x86_64) CLOUDFLARED_ARCH="amd64" ;;
        aarch64|arm64) CLOUDFLARED_ARCH="arm64" ;;
        *)
            echo "ERROR: unsupported cloudflared architecture: $(uname -m)" >&2
            exit 1
            ;;
    esac
    CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CLOUDFLARED_ARCH}"
    CLOUDFLARED_TMP="$(mktemp "$CONDA_ENV_PREFIX/bin/cloudflared.XXXXXX")"
    trap 'rm -f "$CLOUDFLARED_TMP"' EXIT
    echo "[deps] downloading cloudflared from the official Cloudflare release"
    python - "$CLOUDFLARED_URL" "$CLOUDFLARED_TMP" <<'PY'
import shutil
import sys
import urllib.request

request = urllib.request.Request(sys.argv[1], headers={"User-Agent": "cdot-demo-installer"})
with urllib.request.urlopen(request, timeout=120) as response, open(sys.argv[2], "wb") as output:
    shutil.copyfileobj(response, output)
PY
    chmod 0755 "$CLOUDFLARED_TMP"
    mv "$CLOUDFLARED_TMP" "$CONDA_ENV_PREFIX/bin/cloudflared"
    trap - EXIT
else
    echo "[deps] cloudflared is available: $(command -v cloudflared)"
fi

export CDOT_DEMO_PYTHON="$CONDA_ENV_PREFIX/bin/python"
export CDOT_DEMO_TUNNEL="${CDOT_DEMO_TUNNEL:-1}"
export CDOT_DEMO_HOST="${CDOT_DEMO_HOST:-127.0.0.1}"

echo "[deps] python=$($CDOT_DEMO_PYTHON --version 2>&1)"
echo "[deps] node=$(node --version) npm=$(npm --version)"
echo "[deps] cloudflared=$(cloudflared --version)"
echo "[demo] starting on the login node; press Ctrl+C to stop"

exec "$PROJECT_ROOT/scripts/start-demo.sh"
