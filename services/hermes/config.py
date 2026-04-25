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

from pydantic import Field, SecretStr, model_validator
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

    # ─── Multi-process shard (Layer 3) ─────────────────────────────
    # The single-process default is fine for the 2 000 msg/s production
    # target on a Pi 4 (bench shows ~5 500 msg/s sustained on Pi 4 with
    # alpha.14 micro-opts, ~2.7x headroom). Multi-shard mode exists for
    # safety: bursty workloads, future device-count growth, or when
    # other services on the same Pi steal CPU.
    #
    # Operating modes (set via ``hermes_ingest_mode``):
    #   * "all"       — single process subscribes to everything, runs
    #                   detection on all devices, fills live ring buffer.
    #                   Default. ``shard_count`` is ignored.
    #   * "shard"     — one of N detection processes. Subscribes to all
    #                   stm32/adc messages but discards anything where
    #                   ``device_id % shard_count != shard_index``.
    #                   Runs detection + DB sink + outbound MQTT for its
    #                   slice of devices. Does NOT fill the live ring
    #                   buffer (the API does that in "live_only" mode).
    #   * "live_only" — subscribes to all stm32/adc messages, fills the
    #                   live ring buffer for SSE, runs NO detection.
    #                   Used by the API process when shard_count > 1.
    #
    # In multi-shard deployments the API runs as ``live_only`` and N
    # ``hermes-ingest@.service`` instances run as ``shard``. Each shard
    # owns ``device_id % shard_count == shard_index`` for hash-stable
    # routing; detection state and the TTL gate stay isolated per
    # (device, sensor), so sharding by device preserves all rules.
    hermes_ingest_mode: Literal["all", "shard", "live_only"] = "all"
    hermes_shard_count: int = 1
    hermes_shard_index: int = 0

    # ─── Development ───────────────────────────────────────────────
    hermes_dev_mode: bool = False

    @model_validator(mode="after")
    def _validate_shard_config(self) -> Settings:
        """Cross-field check: shard math must be consistent."""
        if self.hermes_shard_count < 1:
            raise ValueError("hermes_shard_count must be >= 1")
        if not (0 <= self.hermes_shard_index < self.hermes_shard_count):
            raise ValueError(
                f"hermes_shard_index ({self.hermes_shard_index}) must satisfy "
                f"0 <= index < shard_count ({self.hermes_shard_count})"
            )
        if self.hermes_ingest_mode == "shard" and self.hermes_shard_count == 1:
            # Allowed but probably a misconfig; warn-shaped via ValueError so
            # the deployment fails fast rather than silently running a single
            # "shard" that owns every device (functionally equivalent to
            # "all", but the operator probably meant to set shard_count > 1).
            raise ValueError(
                "hermes_ingest_mode='shard' requires hermes_shard_count > 1; "
                "use mode='all' for single-process deployments"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Using `lru_cache` means env vars are read exactly once per process.
    Tests can invalidate the cache with `get_settings.cache_clear()` if
    they need to flip a setting between test cases.
    """
    return Settings()  # type: ignore[call-arg]  # fields populated from env
