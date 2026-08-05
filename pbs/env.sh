#!/bin/bash
# Shared environment for every C-DOT PBS job. Source after cd "$PBS_O_WORKDIR".

PROJECT_ROOT="${PROJECT_ROOT:-${PBS_O_WORKDIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}}"
CONDA_ENV_NAME="${CONDA_ENV_NAME:-penv}"
PROJECT_PYTHON="${PROJECT_PYTHON:-$PROJECT_ROOT/env/bin/python}"

if [ -x "$PROJECT_PYTHON" ]; then
    PYTHON_BIN="$PROJECT_PYTHON"
else
    CONDA_BASE_PATH=""
    if command -v conda >/dev/null 2>&1; then
        CONDA_BASE_PATH="$(conda info --base 2>/dev/null || true)"
    fi
    for candidate in "$CONDA_BASE_PATH" "$HOME/miniforge3" "$HOME/miniconda3" "$HOME/anaconda3" "$HOME/conda"; do
        if [ -n "$candidate" ] && [ -f "$candidate/etc/profile.d/conda.sh" ]; then
            # shellcheck disable=SC1090
            source "$candidate/etc/profile.d/conda.sh"
            conda activate "$CONDA_ENV_NAME" || true
            break
        fi
    done
    PYTHON_BIN="$(command -v python || true)"
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "ERROR: no usable Python found; set PROJECT_PYTHON or CONDA_ENV_NAME" >&2
    return 1 2>/dev/null || exit 1
fi

export PROJECT_ROOT PYTHON_BIN
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONHASHSEED=0
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-1}"

echo "[env] root=$PROJECT_ROOT"
echo "[env] python=$PYTHON_BIN ($($PYTHON_BIN --version 2>&1))"
echo "[env] host=$(hostname) job=${PBS_JOBID:-none} array_index=${PBS_ARRAY_INDEX:-none}"

