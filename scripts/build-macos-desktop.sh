#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
TARGET="universal-apple-darwin"
SKIP_SELF_TEST=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target)
      [ "$#" -ge 2 ] || { printf '%s\n' 'Missing value for --target.' >&2; exit 2; }
      TARGET="$2"
      shift
      ;;
    --skip-self-test) SKIP_SELF_TEST=1 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

[ "$(uname -s)" = "Darwin" ] || { printf '%s\n' 'macOS desktop artifacts must be built on macOS.' >&2; exit 2; }
case "$TARGET" in
  universal-apple-darwin|aarch64-apple-darwin|x86_64-apple-darwin) ;;
  *) printf 'Unsupported macOS target: %s\n' "$TARGET" >&2; exit 2 ;;
esac

TAURI_CLI="$PROJECT_ROOT/node_modules/.bin/tauri"
[ -x "$TAURI_CLI" ] || { printf '%s\n' 'Tauri CLI is missing. Run pnpm install first.' >&2; exit 2; }
command -v rustup >/dev/null 2>&1 || { printf '%s\n' 'rustup is required to build the macOS desktop app.' >&2; exit 2; }
command -v ditto >/dev/null 2>&1 || { printf '%s\n' 'macOS ditto is required to preserve application bundle metadata.' >&2; exit 2; }
command -v codesign >/dev/null 2>&1 || { printf '%s\n' 'macOS codesign is required.' >&2; exit 2; }
# The app and exported source must describe the same commit. Do not package a
# dirty native build together with an unrelated git archive HEAD.
git -C "$PROJECT_ROOT" diff --quiet || { printf '%s\n' 'Commit reviewed program changes before building a release.' >&2; exit 2; }
git -C "$PROJECT_ROOT" diff --cached --quiet || { printf '%s\n' 'Commit staged program changes before building a release.' >&2; exit 2; }
if [ -n "$(git -C "$PROJECT_ROOT" ls-files --others --exclude-standard -- agent_platform desktop/src-tauri/src ui scripts)" ]; then
  printf '%s\n' 'Untracked program files must be reviewed and committed before building a release.' >&2
  exit 2
fi

if [ "$TARGET" = "universal-apple-darwin" ]; then
  rustup target add aarch64-apple-darwin x86_64-apple-darwin
else
  rustup target add "$TARGET"
fi

export APPLE_SIGNING_IDENTITY="${APPLE_SIGNING_IDENTITY:--}"
(
  cd "$PROJECT_ROOT/desktop"
  "$TAURI_CLI" build --target "$TARGET" --bundles app,dmg
)

BUNDLE_ROOT="$PROJECT_ROOT/desktop/src-tauri/target/$TARGET/release/bundle"
APP_SOURCE="$BUNDLE_ROOT/macos/Agent4Market.app"
[ -d "$APP_SOURCE" ] || { printf 'macOS app bundle was not produced: %s\n' "$APP_SOURCE" >&2; exit 2; }
DMG_SOURCE="$(find "$BUNDLE_ROOT/dmg" -maxdepth 1 -type f -name '*.dmg' -print -quit)"
[ -n "$DMG_SOURCE" ] && [ -f "$DMG_SOURCE" ] || { printf '%s\n' 'macOS DMG was not produced.' >&2; exit 2; }

codesign --verify --deep --strict "$APP_SOURCE"
ARCHS="$(lipo -archs "$APP_SOURCE/Contents/MacOS/Agent4Market")"
if [ "$TARGET" = "universal-apple-darwin" ]; then
  # Rust names the target aarch64-apple-darwin, while macOS lipo reports it as arm64.
  case " $ARCHS " in *" arm64 "*) ;; *) printf '%s\n' 'Universal app is missing Apple Silicon code.' >&2; exit 2 ;; esac
  case " $ARCHS " in *" x86_64 "*) ;; *) printf '%s\n' 'Universal app is missing Intel code.' >&2; exit 2 ;; esac
fi

OUTPUT_ROOT="$PROJECT_ROOT/dist/macos/$TARGET"
mkdir -p "$OUTPUT_ROOT"
APP_OUTPUT="$OUTPUT_ROOT/Agent4Market.app"
if [ -e "$APP_OUTPUT" ]; then
  [ ! -L "$APP_OUTPUT" ] || { printf '%s\n' 'Refusing to replace a symlinked app output.' >&2; exit 2; }
  mv "$APP_OUTPUT" "$OUTPUT_ROOT/Agent4Market.app.previous-$(date -u +%Y%m%dT%H%M%SZ)"
fi
ditto "$APP_SOURCE" "$APP_OUTPUT"
cp "$DMG_SOURCE" "$OUTPUT_ROOT/Agent4Market-$TARGET.dmg"
ditto -c -k --sequesterRsrc --keepParent "$APP_OUTPUT" "$OUTPUT_ROOT/Agent4Market-$TARGET-app.zip"

if [ "$SKIP_SELF_TEST" -eq 0 ]; then
  # Explicit no-business build probe; never adopt a user's registered runtime.
  "$APP_OUTPUT/Contents/MacOS/Agent4Market" --macos-build-self-test "$PROJECT_ROOT"
fi

STAGING_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent4market-macos.XXXXXX")"
trap 'rm -rf "$STAGING_ROOT"' EXIT
RUNTIME_ROOT="$STAGING_ROOT/Agent4Market"
mkdir -p "$RUNTIME_ROOT"
git -C "$PROJECT_ROOT" archive HEAD | tar -x -C "$RUNTIME_ROOT"
if [ "$TARGET" = "universal-apple-darwin" ]; then
  python3 "$PROJECT_ROOT/scripts/build-macos-update.py" --source-export "$RUNTIME_ROOT" --app "$APP_OUTPUT" \
    --app-archive "$OUTPUT_ROOT/Agent4Market-$TARGET-app.zip" --output "$OUTPUT_ROOT"
fi
ditto "$APP_OUTPUT" "$RUNTIME_ROOT/Agent4Market.app"
ditto -c -k --sequesterRsrc --keepParent "$RUNTIME_ROOT" "$OUTPUT_ROOT/Agent4Market-$TARGET-runtime.zip"

(
  cd "$OUTPUT_ROOT"
  shasum -a 256 \
    "Agent4Market-$TARGET.dmg" \
    "Agent4Market-$TARGET-app.zip" \
    "Agent4Market-$TARGET-runtime.zip" > SHA256SUMS.txt
  if [ "$TARGET" = "universal-apple-darwin" ]; then
    shasum -a 256 macOS-install-manifest.json Agent4Market-*-macos-programs.zip Agent4Market-*-macos-universal-app.zip >> SHA256SUMS.txt
  fi
)

printf '{"status":"ok","target":"%s","architectures":"%s","output":"%s"}\n' \
  "$TARGET" "$ARCHS" "$OUTPUT_ROOT"
