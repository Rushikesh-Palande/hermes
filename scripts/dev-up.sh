#!/usr/bin/env bash
# dev-up.sh
#
# Bring up the dev stack (Postgres + TimescaleDB, Mosquitto, Redis)
# and apply migrations. Does NOT start the Python services — run
# `uv run hermes-api` and `uv run hermes-ingest` in separate terminals
# so their logs stay legible.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f .env ]]; then
    echo "No .env found. Copying .env.example. Edit it before running services." >&2
    cp .env.example .env
fi

echo ">>> docker compose up -d"
docker compose -f docker-compose.dev.yml up -d

echo ">>> waiting for postgres healthcheck"
# docker compose exit code is 0 once the healthcheck passes.
until docker compose -f docker-compose.dev.yml ps --format json postgres \
    | grep -q '"Health":"healthy"'; do
    sleep 1
done

echo ">>> applying migrations"
# shellcheck disable=SC1091
set -a; source .env; set +a
"$REPO_ROOT/scripts/db-migrate.sh"

echo
echo "Stack is up. Next steps:"
echo "  terminal 1:  uv run hermes-api"
echo "  terminal 2:  uv run hermes-ingest"
echo "  terminal 3:  cd ui && pnpm dev"
