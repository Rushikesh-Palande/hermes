"""
Integration tests that need a live Postgres + Mosquitto.

Run with:
    docker compose -f docker-compose.dev.yml up -d postgres mosquitto
    ./scripts/db-migrate.sh
    pytest -m 'db or mqtt'

CI spins these services up as GitHub Actions service containers.
"""
