"""
HERMES — the top-level package.

This package is split into three subpackages that ship as independent
processes (systemd units in production, separate `uv run` commands in
development):

    hermes.api     — FastAPI HTTP + SSE server. See `hermes.api.__main__`.
    hermes.ingest  — MQTT consumer + detection workers. See
                     `hermes.ingest.__main__`.
    hermes.db      — shared SQLAlchemy engine + ORM models. Imported by
                     both services above; has no runtime of its own.

Keeping API and ingest in a single Python package (rather than two
sibling packages) means they can share the same ORM models, the same
config loader, and the same structured logger without cross-package
dependency gymnastics. Deployment-wise they remain fully independent —
see `packaging/` for the systemd units.
"""

__version__ = "0.1.0a1"
