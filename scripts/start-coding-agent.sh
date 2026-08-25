#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$#" -lt 1 ]; then
  printf '%s\n' 'Usage: bash scripts/start-coding-agent.sh codex|claude [arguments...]' >&2
  exit 2
fi
AGENT="$1"
shift
case "$AGENT" in
  codex|claude) ;;
  *) printf 'Unsupported coding agent: %s\n' "$AGENT" >&2; exit 2 ;;
esac
command -v "$AGENT" >/dev/null 2>&1 || {
  printf '%s is not installed or not available on PATH. Use the vendor official installer first.\n' "$AGENT" >&2
  exit 2
}
cd "$PROJECT_ROOT"
exec "$AGENT" "$@"
