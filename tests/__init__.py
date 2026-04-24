"""
Pytest root. Intentionally empty; fixtures live in conftest.py.

Subdirectories:
    unit/         — pure-python unit tests. No DB, no network, no sleeps.
    integration/  — tests that need Postgres + Mosquitto (docker-compose).
    e2e/          — Playwright end-to-end tests (added in Phase 5).
    golden/       — golden-traffic replay + diff (added in Phase 3).
"""
