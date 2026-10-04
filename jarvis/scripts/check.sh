#!/usr/bin/env bash
# Every check a contributor runs before pushing.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "› backend: ruff";            backend/.venv/bin/ruff check backend
echo "› backend: format";          backend/.venv/bin/ruff format --check backend
echo "› backend: mypy";            (cd backend && .venv/bin/mypy jarvis tests)
echo "› backend: mypy (win32)";    (cd backend && .venv/bin/mypy --platform win32 jarvis)
echo "› backend: mypy (darwin)";   (cd backend && .venv/bin/mypy --platform darwin jarvis)
echo "› backend: pytest";          (cd backend && .venv/bin/pytest -q)
echo "› desktop: typecheck";       npm run typecheck --silent
echo "› desktop: vitest";          npm test --silent
echo "✓ all checks passed"
