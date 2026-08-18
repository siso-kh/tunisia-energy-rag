#!/usr/bin/env bash
# Manual one-shot Postgres backup (in addition to the nightly pg-backup service).
#
# Usage:
#   bash scripts/backup_db.sh            # dump to backups/energie_tunisie_<ts>.dump
#   bash scripts/backup_db.sh custom.dump # dump to backups/custom.dump
#
# Requires the compose stack to be running (docker compose up -d postgres).
set -euo pipefail

cd "$(dirname "$0")/.."

BACKUP_DIR="backups"
mkdir -p "$BACKUP_DIR"

NAME="${1:-energie_tunisie_$(date +%Y%m%d_%H%M%S).dump}"
DEST="$BACKUP_DIR/$NAME"

echo "[backup] dumping postgres -> $DEST"
docker compose exec -T postgres \
  pg_dump -U postgres -d energie_tunisie -F c \
  > "$DEST"

echo "[backup] done: $(du -h "$DEST" | cut -f1)"
echo "[backup] hint: restore with  bash scripts/restore_db.sh $NAME"
