#!/usr/bin/env bash
# dev-down.sh
#
# Stop the dev stack. Does NOT delete volumes — pass --purge to wipe
# the Postgres volume and start clean on the next `dev-up.sh`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ "${1:-}" == "--purge" ]]; then
    echo ">>> docker compose down -v  (DESTROYS all local Postgres + MQTT data)"
    docker compose -f docker-compose.dev.yml down -v
else
    docker compose -f docker-compose.dev.yml down
fi
