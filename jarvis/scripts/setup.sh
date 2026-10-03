#!/usr/bin/env bash
# JARVIS — one-time setup on macOS/Linux (the desktop is simulated off Windows).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-$(command -v python3.13 || command -v python3.12 || command -v python3)}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'

[ -d backend/.venv ] || "$PYTHON" -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -e "backend[dev]"
npm install
[ -f .env ] || cp .env.example .env

echo "Done. Start JARVIS with: npm run dev"
