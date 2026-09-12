#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_ROOT"
PYTHON_COMMAND="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_COMMAND" ]; then PYTHON_COMMAND="python3"; fi
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME NODE_PATH NODE_OPTIONS
exec "$PYTHON_COMMAND" -m agent_platform launch -- "$@"
