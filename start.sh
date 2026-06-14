#!/usr/bin/env bash
# ── start.sh  ──────────────────────────────────────────────────────────────
# Used by Render.com (and any Linux host) to launch both services.
# On Render, deploy as TWO separate Web Services — one per command below.
#
# Service 1 — Dash frontend  (set Start Command to the gunicorn line)
# Service 2 — FastAPI backend (set Start Command to the uvicorn line)
# ───────────────────────────────────────────────────────────────────────────

set -e

# Load .env if it exists (local dev)
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

SERVICE=${1:-dash}   # pass "dash" or "api" as first argument

if [ "$SERVICE" = "api" ]; then
  echo "[start] Launching FastAPI on :${API_PORT:-8000}"
  exec uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${API_PORT:-8000}" \
    --workers 2
else
  echo "[start] Launching Dash on :${PORT:-8050}"
  exec gunicorn app:server \
    --bind "0.0.0.0:${PORT:-8050}" \
    --workers 2 \
    --timeout 120
fi
