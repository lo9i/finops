#!/usr/bin/env bash
# Bootstrap + run script using uv. Idempotent.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "[run.sh] uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

echo "[run.sh] Syncing dependencies with uv..."
uv sync --quiet

echo "[run.sh] Seeding demo data..."
uv run python -m app.seed

echo "[run.sh] Starting API on http://127.0.0.1:8000"
echo "  - Dashboard:  http://127.0.0.1:8000/"
echo "  - OpenAPI:    http://127.0.0.1:8000/docs"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
