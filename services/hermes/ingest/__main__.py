"""
Production entry point for the HERMES ingest service.

Invoked as either:
    python -m hermes.ingest
    uv run hermes-ingest
    hermes-ingest                 (after `pip install .`)

Wraps `hermes.ingest.main.run()` in an asyncio event loop and exits
cleanly on SIGINT/SIGTERM. systemd is the production supervisor.
"""

from __future__ import annotations

import asyncio

from hermes.ingest.main import run


def main() -> None:
    """Run the ingest loop until a signal tears it down."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
