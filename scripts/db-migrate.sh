#!/usr/bin/env bash
# db-migrate.sh
#
# Applies every file in migrations/ in sorted order against the
# Postgres at $MIGRATE_DATABASE_URL. Fails fast on any SQL error.
#
# Idempotent: re-running after a successful pass is a no-op because
# every migration uses IF NOT EXISTS / if_not_exists semantics.
#
# Usage:
#   export MIGRATE_DATABASE_URL=postgresql://hermes_migrate:CHANGEME@localhost:5432/hermes
#   ./scripts/db-migrate.sh

set -euo pipefail

: "${MIGRATE_DATABASE_URL:?MIGRATE_DATABASE_URL is required (see .env.example)}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIG_DIR="$REPO_ROOT/migrations"

if ! command -v psql >/dev/null 2>&1; then
    echo "psql not found. Install postgresql-client (apt) or connect through Docker." >&2
    echo "Alternative:" >&2
    echo "  docker compose exec postgres psql -U hermes_migrate -d hermes -f /migrations/0001_init_extensions.sql" >&2
    exit 127
fi

echo "Applying migrations from $MIG_DIR"
for sql in "$MIG_DIR"/00*.sql; do
    echo ">>> $(basename "$sql")"
    psql "$MIGRATE_DATABASE_URL" \
        --set=ON_ERROR_STOP=1 \
        --quiet \
        --single-transaction \
        -f "$sql"
done

echo "Migrations complete."
