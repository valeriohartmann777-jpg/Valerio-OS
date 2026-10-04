#!/usr/bin/env bash
# Installs JARVIS as a Mac app: ~/Applications/JARVIS.app (Dock, Spotlight).
#
# The app is a copy of Electron with JARVIS's name and icon whose only job is
# to start JARVIS from this folder — so updates (the Update button in JARVIS,
# or git pull) take effect without reinstalling. It is signed ad-hoc for this
# Mac only; nothing is downloaded and no admin rights are needed.
#
#   ./scripts/install-mac-app.sh                         build, install, start
#   ./scripts/install-mac-app.sh --skip-build --no-open  (used by the updater)
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
SKIP_BUILD=0
OPEN=1
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --no-open) OPEN=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "The JARVIS app can only be installed on macOS." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js wasn't found — run ./scripts/setup.sh first." >&2
  exit 1
fi
if [ ! -x backend/.venv/bin/python ] || [ ! -d node_modules ]; then
  ./scripts/setup.sh
fi
node scripts/preflight.mjs
if [ "$SKIP_BUILD" != 1 ]; then
  node scripts/build-app.mjs
fi

APP="$HOME/Applications/JARVIS.app"
ELECTRON_APP="$ROOT/node_modules/electron/dist/Electron.app"
BUNDLE_ID="com.jarvis.desktop"
if [ ! -d "$ELECTRON_APP" ]; then
  echo "Electron isn't installed (node_modules/electron) — run ./scripts/setup.sh." >&2
  exit 1
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
NEW="$STAGE/JARVIS.app"

echo "› Creating JARVIS.app"
ditto "$ELECTRON_APP" "$NEW"
RES="$NEW/Contents/Resources"
rm -f "$RES/default_app.asar"
node scripts/mac-app/bootstrap.mjs "$RES/app"

echo "› Icon"
ICONSET="$STAGE/jarvis.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" apps/desktop/assets/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" apps/desktop/assets/icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RES/electron.icns"

echo "› Name and permissions texts"
PLIST="$NEW/Contents/Info.plist"
set_plist() { plutil -replace "$1" -string "$2" "$PLIST"; }
set_plist CFBundleName "JARVIS"
set_plist CFBundleDisplayName "JARVIS"
set_plist CFBundleIdentifier "$BUNDLE_ID"
set_plist NSAppleEventsUsageDescription "JARVIS controls Spotify or Music when you ask it to."
set_plist NSDesktopFolderUsageDescription "JARVIS finds, reads and opens files on your Desktop when you ask it to."
set_plist NSDocumentsFolderUsageDescription "JARVIS finds, reads and opens your documents when you ask it to."
set_plist NSDownloadsFolderUsageDescription "JARVIS finds, reads and opens your downloads when you ask it to."
set_plist NSMicrophoneUsageDescription "JARVIS listens for its wake word and to what you ask it."
# Bumped (scripts/mac-app/VERSION) whenever the bundle itself changes; the app
# reinstalls itself on start when its bundle is older than this checkout.
set_plist JARVISBundleVersion "$(tr -d '[:space:]' < scripts/mac-app/VERSION)"
set_plist JARVISElectronVersion "$(node -p "require('./node_modules/electron/package.json').version")"
set_plist JARVISCheckout "$ROOT"
# Helper apps share the main app's identifier prefix (as electron-builder does).
for helper in "$NEW"/Contents/Frameworks/*Helper*.app; do
  info="$helper/Contents/Info.plist"
  current="$(plutil -extract CFBundleIdentifier raw "$info")"
  plutil -replace CFBundleIdentifier -string "${current/com.github.Electron/$BUNDLE_ID}" "$info"
done

echo "› Signing for this Mac (ad-hoc)"
codesign --force --deep --sign - "$NEW"

echo "› Installing to $APP"
mkdir -p "$HOME/Applications"
rm -rf "$APP"
mv "$NEW" "$APP"
touch "$APP" # let Finder and the Dock pick up the new icon

echo "✓ JARVIS.app is installed. Start it from the Dock, Spotlight (⌘ Space → JARVIS) or ~/Applications."
echo "  The first time it reads files or controls music, macOS asks once for JARVIS — allow it."
if [ "$OPEN" = 1 ]; then
  open "$APP"
fi
