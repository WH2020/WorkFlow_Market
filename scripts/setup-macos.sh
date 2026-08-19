#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_ROOT"

SKIP_DEPENDENCIES=0
SKIP_PI_INSTALL=0
REQUIRE_PPT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-dependencies) SKIP_DEPENDENCIES=1 ;;
    --skip-pi-install) SKIP_PI_INSTALL=1 ;;
    --require-ppt) REQUIRE_PPT=1 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$(uname -s)" != "Darwin" ]; then
  printf 'This installer is for macOS. Use setup-windows.ps1 on Windows.\n' >&2
  exit 2
fi
command -v python3 >/dev/null 2>&1 || { printf 'Python 3.11+ is required.\n' >&2; exit 2; }
command -v node >/dev/null 2>&1 || { printf 'Node.js 22.19+ is required.\n' >&2; exit 2; }

if ! command -v pnpm >/dev/null 2>&1; then
  command -v corepack >/dev/null 2>&1 || { printf 'pnpm 9+ or Corepack is required.\n' >&2; exit 2; }
  corepack enable
  corepack prepare pnpm@10 --activate
fi
if [ "$SKIP_DEPENDENCIES" -eq 0 ]; then
  pnpm install --frozen-lockfile
fi
PI_COMMAND="$(command -v pi || true)"
if [ -z "$PI_COMMAND" ] && [ -x "$PROJECT_ROOT/node_modules/.bin/pi" ]; then
  PI_COMMAND="$PROJECT_ROOT/node_modules/.bin/pi"
fi
if [ -z "$PI_COMMAND" ]; then
  if [ "$SKIP_PI_INSTALL" -eq 1 ]; then
    printf 'Pi is missing. Remove --skip-pi-install or install project dependencies first.\n' >&2
    exit 2
  fi
  npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.2
  PI_COMMAND="$(command -v pi)"
fi
python3 plugin/market-director-copilot/scripts/init_local_data.py --project .
python3 -m agent_platform validate
"$PI_COMMAND" install -l .
python3 -m agent_platform doctor

if python3 -m agent_platform doctor --require-ppt >/dev/null 2>&1; then
  printf '%s\n' 'PPT runtime detected. The start script will inject it only into the Pi process.'
elif [ "$REQUIRE_PPT" -eq 1 ]; then
  python3 -m agent_platform doctor --require-ppt
  exit 3
else
  printf '%s\n' "Warning: Core Agent is ready, but the Codex PPT runtime was not detected. Run 'python3 -m agent_platform doctor --require-ppt' after installing or opening Codex Desktop." >&2
fi

printf '%s\n' "Setup complete. Start the Agent with: bash scripts/start-macos.sh"
printf '%s\n' "Start the local workbench in another terminal with: python3 ui/server.py"
