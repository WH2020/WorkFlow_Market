#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$PROJECT_ROOT"

SKIP_DEPENDENCIES=0
SKIP_PI_INSTALL=0
SKIP_LIBREOFFICE_INSTALL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --skip-dependencies) SKIP_DEPENDENCIES=1 ;;
    --skip-pi-install) SKIP_PI_INSTALL=1 ;;
    --skip-libreoffice-install) SKIP_LIBREOFFICE_INSTALL=1 ;;
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

PNPM_COMMAND="$(command -v pnpm || true)"
case "$PNPM_COMMAND" in
  *codex-runtimes*|*/.codex/*)
    CLEAN_PATH=""
    OLD_IFS="$IFS"
    IFS=:
    for SEGMENT in $PATH; do
      case "$SEGMENT" in *codex-runtimes*|*/.codex/*) continue ;; esac
      CLEAN_PATH="${CLEAN_PATH:+$CLEAN_PATH:}$SEGMENT"
    done
    IFS="$OLD_IFS"
    PATH="$CLEAN_PATH"
    export PATH
    PNPM_COMMAND="$(command -v pnpm || true)"
    ;;
esac
if [ -z "$PNPM_COMMAND" ]; then
  if command -v corepack >/dev/null 2>&1; then
    corepack enable
    corepack prepare pnpm@10 --activate
  elif command -v npm >/dev/null 2>&1; then
    npm install -g pnpm@10
  elif command -v brew >/dev/null 2>&1; then
    brew install pnpm
  else
    printf 'Independent pnpm 9+ is required. Install it with the official Node.js installer or Homebrew.\n' >&2
    exit 2
  fi
  PNPM_COMMAND="$(command -v pnpm || true)"
fi
case "$PNPM_COMMAND" in
  ""|*codex-runtimes*|*/.codex/*) printf 'A non-Codex pnpm command is required.\n' >&2; exit 2 ;;
esac
if [ "$SKIP_DEPENDENCIES" -eq 0 ]; then
  "$PNPM_COMMAND" install --frozen-lockfile --ignore-scripts
fi
MISSING_CLI=()
command -v rg >/dev/null 2>&1 || MISSING_CLI+=(ripgrep)
command -v fd >/dev/null 2>&1 || MISSING_CLI+=(fd)
if [ "${#MISSING_CLI[@]}" -gt 0 ]; then
  command -v brew >/dev/null 2>&1 || { printf 'Homebrew is required to install ripgrep and fd.\n' >&2; exit 2; }
  brew install "${MISSING_CLI[@]}"
fi
LIBREOFFICE_PATH=""
for candidate in "/Applications/LibreOffice.app/Contents/MacOS/soffice" "$HOME/Applications/LibreOffice.app/Contents/MacOS/soffice" "/opt/homebrew/bin/soffice" "/usr/local/bin/soffice"; do
  if [ -x "$candidate" ]; then LIBREOFFICE_PATH="$candidate"; break; fi
done
if [ -z "$LIBREOFFICE_PATH" ] && command -v soffice >/dev/null 2>&1; then
  LIBREOFFICE_PATH="$(command -v soffice)"
fi
if [ -z "$LIBREOFFICE_PATH" ]; then
  if [ "$SKIP_LIBREOFFICE_INSTALL" -eq 1 ]; then
    printf 'LibreOffice was not found. Remove --skip-libreoffice-install or install LibreOffice first.\n' >&2
    exit 2
  fi
  command -v brew >/dev/null 2>&1 || { printf 'Homebrew is required to install LibreOffice automatically. Install LibreOffice from libreoffice.org and retry.\n' >&2; exit 2; }
  brew install --cask libreoffice
  LIBREOFFICE_PATH="/Applications/LibreOffice.app/Contents/MacOS/soffice"
fi
[ -x "$LIBREOFFICE_PATH" ] || { printf 'LibreOffice installation completed but soffice was not found.\n' >&2; exit 2; }
export WORKFLOW_LIBREOFFICE_PATH="$LIBREOFFICE_PATH"
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
python3 -m agent_platform configure-subagents
python3 -m agent_platform validate
"$PI_COMMAND" install -l . --approve
python3 -m agent_platform doctor --require-ppt
printf '%s\n' 'Independent PPT runtime detected: PptxGenJS + LibreOffice + PDF.js.'

EXPECTED_VERSION="$(python3 -c 'import json; print(json.load(open("package.json", encoding="utf-8"))["version"])')"
BUNDLED_APP="$PROJECT_ROOT/Agent4Market.app"
BUNDLED_VERSION=""
if [ -d "$BUNDLED_APP" ] && [ -f "$BUNDLED_APP/Contents/Info.plist" ]; then
  BUNDLED_VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$BUNDLED_APP/Contents/Info.plist" 2>/dev/null || true)"
fi
if [ "$BUNDLED_VERSION" = "$EXPECTED_VERSION" ]; then
  APP_SOURCE="$BUNDLED_APP"
else
  command -v cargo >/dev/null 2>&1 || { printf '%s\n' 'Rust/Cargo is required to build Agent4Market.app. Install rustup and retry.' >&2; exit 2; }
  command -v rustup >/dev/null 2>&1 || { printf '%s\n' 'rustup is required to build the universal macOS app.' >&2; exit 2; }
  bash "$PROJECT_ROOT/scripts/build-macos-desktop.sh"
  APP_SOURCE="$PROJECT_ROOT/dist/macos/universal-apple-darwin/Agent4Market.app"
fi
[ -d "$APP_SOURCE" ] || { printf 'Agent4Market.app was not found: %s\n' "$APP_SOURCE" >&2; exit 2; }

SUPPORT_DIR="$HOME/Library/Application Support/Agent4Market"
[ ! -L "$SUPPORT_DIR" ] || { printf '%s\n' 'Refusing to use a symlinked Agent4Market support directory.' >&2; exit 2; }
mkdir -p "$SUPPORT_DIR"
chmod 700 "$SUPPORT_DIR"
INSTALL_MARKER="$SUPPORT_DIR/install-root"
[ ! -L "$INSTALL_MARKER" ] || { printf '%s\n' 'Refusing to replace a symlinked Agent4Market install marker.' >&2; exit 2; }
if [ -e "$INSTALL_MARKER" ] && [ ! -f "$INSTALL_MARKER" ]; then
  printf '%s\n' 'Agent4Market install marker exists but is not a regular file.' >&2
  exit 2
fi
MARKER_TEMP="$(mktemp "$SUPPORT_DIR/.install-root.XXXXXX")"
printf '%s\n' "$PROJECT_ROOT" > "$MARKER_TEMP"
chmod 600 "$MARKER_TEMP"
mv -f "$MARKER_TEMP" "$INSTALL_MARKER"

USER_APPLICATIONS="$HOME/Applications"
[ ! -L "$USER_APPLICATIONS" ] || { printf '%s\n' 'Refusing to install into a symlinked Applications directory.' >&2; exit 2; }
mkdir -p "$USER_APPLICATIONS"
INSTALL_APP="$USER_APPLICATIONS/Agent4Market.app"
[ ! -L "$INSTALL_APP" ] || { printf '%s\n' 'Refusing to replace a symlinked Agent4Market.app.' >&2; exit 2; }
if [ -e "$INSTALL_APP" ]; then
  mv "$INSTALL_APP" "$USER_APPLICATIONS/Agent4Market.app.previous-$(date -u +%Y%m%dT%H%M%SZ)"
fi
ditto "$APP_SOURCE" "$INSTALL_APP"
codesign --verify --deep --strict "$INSTALL_APP"
"$INSTALL_APP/Contents/MacOS/Agent4Market" --self-test

printf '%s\n' "Setup complete. Open $INSTALL_APP or run: open '$INSTALL_APP'"
printf '%s\n' 'The app uses this checked-out directory as its local runtime and data root.'
