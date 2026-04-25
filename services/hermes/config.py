"""
Runtime configuration for HERMES, loaded once per process.

All config comes from environment variables (or a `.env` file in dev);
nothing is hard-coded. Values are typed and validated by Pydantic at
process start, so a misconfigured deployment fails fast rather than
surfacing as a cryptic runtime error two hours in.

Why a single `Settings` class (and not per-service configs):
    * `hermes.api` and `hermes.ingest` share almost all of the same
      knobs (database URL, JWT secret, MQTT broker). Splitting them
      invites drift.
    * If a future service needs a service-specific section, add a
      nested sub-model (e.g. `class IngestSettings(BaseModel): …`)
      rather than forking the whole class.

Precedence (highest wins):
    1. Environment variable.
    2. `.env` file (dev only — ignored when running under systemd).
    3. Default values declared on the fields below.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration. Instantiate via `get_settings()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate unrelated env vars (systemd adds many)
        case_sensitive=False,
    )

    # ─── Database ──────────────────────────────────────────────────
    database_url: str = Field(
        ...,
        description="asyncpg URL used by hermes-api and hermes-ingest at runtime.",
    )
    migrate_database_url: str = Field(
        ...,
        description="psycopg URL used by the migration runner; separate role with DDL privileges.",
    )

    # ─── API server ────────────────────────────────────────────────
    hermes_api_host: str = "0.0.0.0"
    hermes_api_port: int = 8080
    hermes_api_workers: int = 1
    hermes_api_log_level: Literal["debug", "info", "warning", "error"] = "info"

    # ─── Auth ──────────────────────────────────────────────────────
    hermes_jwt_secret: SecretStr = Field(
        ...,
        min_length=32,
        description="JWT HMAC key. 32+ bytes. Rotating invalidates all sessions.",
    )
    hermes_jwt_expiry_seconds: int = 3600

    # ─── MQTT ──────────────────────────────────────────────────────
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: SecretStr = SecretStr("")
    mqtt_topic_adc: str = "stm32/adc"
    mqtt_topic_events_prefix: str = "stm32/events"

    # ─── Email / OTP ───────────────────────────────────────────────
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass: SecretStr = SecretStr("")
    smtp_from: str = ""

    otp_expiry_seconds: int = 300
    otp_max_attempts: int = 5
    otp_resend_cooldown_seconds: int = 60
    otp_max_per_hour: int = 5
    allowed_emails_path: Path = Path("./config/allowed_emails.txt")

    # ─── Observability ─────────────────────────────────────────────
    hermes_log_format: Literal["json", "console"] = "json"
    hermes_metrics_enabled: bool = True
    hermes_metrics_port: int = 9090

    # ─── Ingest pipeline ───────────────────────────────────────────
    # Depth of in-memory ring buffer per device (samples). At 123 Hz this
    # is ~16 seconds of history; SSE clients typically request ≤6 s.
    live_buffer_max_samples: int = 2000

    # Re-anchor the STM32 clock offset when computed wall-time diverges
    # from receive-time by more than this many seconds (STM counter wrap).
    mqtt_drift_threshold_s: float = 5.0

    # How long ``TtlGateSink`` holds a fired event before forwarding to
    # the durable sinks (DB + outbound MQTT). Within this window,
    # duplicates of the same type are merged and lower-priority types
    # are blocked. Legacy default is 5 s. BREAK events bypass.
    event_ttl_seconds: float = 5.0

    # ─── Development ───────────────────────────────────────────────
    hermes_dev_mode: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Using `lru_cache` means env vars are read exactly once per process.
    Tests can invalidate the cache with `get_settings.cache_clear()` if
    they need to flip a setting between test cases.
    """
    return Settings()  # type: ignore[call-arg]  # fields populated from env
