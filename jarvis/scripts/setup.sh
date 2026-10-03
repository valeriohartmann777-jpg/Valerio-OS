#!/usr/bin/env bash
# JARVIS — one-time setup on macOS/Linux (the desktop is simulated off Windows).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3.14 || command -v python3.13 || command -v python3.12 || command -v python3 || true)}"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
  echo "Python 3.12+ is required. On macOS: brew install python@3.13" >&2
  exit 1
fi
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 20 ? 1 : 0)' 2>/dev/null; then
  echo "Node.js 20+ is required. On macOS: brew install node" >&2
  exit 1
fi
echo "Using $("$PYTHON" --version) and Node $(node --version)"

[ -d backend/.venv ] || "$PYTHON" -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -e "backend[dev]"
npm install
[ -f .env ] || cp .env.example .env

echo "Done. Start JARVIS with: npm run dev"
