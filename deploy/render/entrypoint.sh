#!/usr/bin/env bash
#
# Container entrypoint for the Render single-container deployment.
#
# 1. Render the nginx config for the platform-assigned PORT.
# 2. Fetch the ChromaDB index if the image was built without it.
# 3. Apply Alembic migrations (async engine -> asyncpg, matching the app).
# 4. Seed demo data (idempotent: seed() skips when users already exist).
# 5. Run uvicorn (127.0.0.1:8000) + nginx ($PORT); if either dies the
#    container exits so the platform restarts it.
#
# Requires DATABASE_URL (Render secret), e.g.
#   postgresql://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require
#   (sync format: psycopg2 in ensure_schema can't parse '+asyncpg' or 'ssl=require')

set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
PORT="${PORT:-10000}"

# --- 1. Bind nginx to the platform's port -----------------------------
# Targeted sed on purpose: envsubst would also blank nginx's own $host/$uri.
sed "s/\${PORT}/${PORT}/g" \
    "${APP_DIR}/deploy/render/nginx.conf.template" > /etc/nginx/nginx.conf
echo "[entrypoint] nginx will listen on :${PORT}"

# --- 2. Chroma index --------------------------------------------------
# src/rag/retrieve.py opens the collection at import time, so a missing
# index is a hard boot failure. Normally it is baked in at build time; this
# is the safety net for images built without the local data/chroma_db.
if [[ ! -f "${APP_DIR}/data/chroma_db/chroma.sqlite3" && -n "${CHROMA_URL:-}" ]]; then
    echo "[entrypoint] ChromaDB index missing — downloading from CHROMA_URL..."
    curl -fsSL "${CHROMA_URL}" -o /tmp/chroma.tar.gz
    mkdir -p "${APP_DIR}/data"
    tar --no-same-owner -xzf /tmp/chroma.tar.gz -C "${APP_DIR}/data"
    rm -f /tmp/chroma.tar.gz
fi

if [[ ! -f "${APP_DIR}/data/chroma_db/chroma.sqlite3" ]]; then
    echo "[entrypoint] FATAL: no ChromaDB index and no CHROMA_URL set."
    echo "[entrypoint]   src/rag/retrieve.py opens the collection at import time,"
    echo "[entrypoint]   so the backend cannot start. See deploy/render/README.md §3."
    exit 1
fi

# --- 3. Database ------------------------------------------------------
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "[entrypoint] WARNING: DATABASE_URL is not set — DB-backed features will fail."
fi

echo "[entrypoint] applying database migrations (alembic upgrade head)..."
alembic upgrade head || { echo "[entrypoint] FATAL: migrations failed — container cannot boot without a working DB."; exit 1; }

# psycopg2-binary is in requirements.runtime.txt specifically so this step can
# run: ensure_schema() inspects the DB through a *sync* SQLAlchemy engine.
echo "[entrypoint] seeding demo data (idempotent)..."
python -m src.database.seed || echo "[entrypoint] WARNING: seeding skipped/failed — continuing anyway"

# --- 4. Processes -----------------------------------------------------
echo "[entrypoint] launching uvicorn on 127.0.0.1:8000..."
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 &
UVI_PID=$!

echo "[entrypoint] launching nginx on :${PORT}..."
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
