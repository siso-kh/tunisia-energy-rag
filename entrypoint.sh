#!/usr/bin/env bash
#
# Container entrypoint for the Hugging Face Docker Space.
#
# 1. Apply Alembic migrations (async engine -> uses asyncpg, matching the app).
#    We deliberately do NOT call src.database.seed for the schema step: its
#    ensure_schema() inspects the DB through a *sync* SQLAlchemy engine, which
#    would require psycopg2 (not in requirements.txt). `alembic upgrade head`
#    uses the async engine configured in alembic/env.py instead.
# 2. Seed demo data (idempotent: seed() skips when users already exist).
# 3. Run uvicorn (127.0.0.1:8000) and nginx (:7860) together; if either dies,
#    the container exits so the Space restarts.
#
# Requires DATABASE_URL to be set (Space secret), e.g.
#   postgresql+asyncpg://user:pass@ep-xxx.aws.neon.tech/neondb?ssl=require

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[entrypoint] WARNING: DATABASE_URL is not set — the DB-backed features will fail."
fi

echo "[entrypoint] applying database migrations (alembic upgrade head)..."
alembic upgrade head || echo "[entrypoint] WARNING: migrations failed — continuing anyway"

echo "[entrypoint] seeding demo data (idempotent)..."
python -m src.database.seed || echo "[entrypoint] WARNING: seeding skipped/failed — continuing anyway"

echo "[entrypoint] launching uvicorn on 127.0.0.1:8000..."
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &
UVI_PID=$!

echo "[entrypoint] launching nginx on :7860..."
nginx -g 'daemon off;' &
NGINX_PID=$!

term() {
    kill "$UVI_PID" "$NGINX_PID" 2>/dev/null || true
}
trap term TERM INT

# Exit as soon as either process stops, then clean up the other. `|| true` stops
# `set -e` from aborting before the cleanup trap runs.
wait -n "$UVI_PID" "$NGINX_PID" || true
term
wait || true
