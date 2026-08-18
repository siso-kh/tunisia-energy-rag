#!/usr/bin/env bash
# Restore drill: load a custom-format pg_dump into the compose Postgres.
#
# Usage:
#   bash scripts/restore_db.sh backups/energie_tunisie_<ts>.dump
#
# Drops and recreates the target schema (--clean --if-exists) then restores.
# Run this on a THROWAWAY environment first to validate a backup; it
# overwrites the current database contents.
set -euo pipefail

cd "$(dirname "$0")/.."

DUMP="${1:?usage: bash scripts/restore_db.sh <path-to-dump>}"
if [ ! -f "$DUMP" ]; then
    echo "[restore] error: file not found: $DUMP" >&2
    exit 1
fi

echo "[restore] about to REPLACE the database contents from: $DUMP"
read -r -p "[restore] type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "[restore] aborted."
    exit 1
fi

echo "[restore] restoring..."
docker compose exec -T postgres \
  pg_restore -U postgres -d energie_tunisie --clean --if-exists \
  < "$DUMP"

echo "[restore] done. Verify with:  curl -s http://localhost/api/outages | head -c 300"
