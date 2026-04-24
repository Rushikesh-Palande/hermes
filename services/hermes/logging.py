"""
Structured logging for HERMES using structlog.

Why structlog and not the stdlib `logging` alone:
    * We ship logs to `journalctl` in production; structured JSON lets
      us grep with `jq` ("all events for device 3 in the last hour")
      instead of eyeballing free-form text.
    * structlog lets us bind context (request_id, device_id, session_id)
      once per logical operation and have it appear on every subsequent
      log line automatically.

Format selection:
    * `HERMES_LOG_FORMAT=json`    — production (default). One JSON
                                     object per line; systemd-journal
                                     picks them up via
                                     `StandardOutput=journal`.
    * `HERMES_LOG_FORMAT=console` — development. Human-coloured output
                                     with pretty tracebacks.

Call `configure_logging()` ONCE at process start (from api.__main__ or
ingest.__main__). Module-level loggers are obtained via `get_logger()`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from hermes.config import get_settings


def configure_logging() -> None:
    """Initialise structlog + stdlib logging. Safe to call multiple times."""
    settings = get_settings()

    # stdlib root logger: stream to stdout so systemd/journalctl captures it.
    logging.basicConfig(
        level=settings.hermes_api_log_level.upper(),
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    if settings.hermes_log_format == "json":
        # JSON output: one event per line, parseable by anything.
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        # Console output: coloured, multi-line, prettified exceptions.
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.hermes_api_log_level.upper()),
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **initial_context: Any) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger, optionally pre-bound with context.

    Usage:
        log = get_logger(__name__, component="ingest")
        log.info("mqtt_connected", host=host, port=port)
    """
    logger = structlog.get_logger(name)
    if initial_context:
        logger = logger.bind(**initial_context)
    return logger  # type: ignore[no-any-return]  # structlog types are partial
