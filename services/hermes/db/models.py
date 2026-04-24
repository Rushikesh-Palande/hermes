"""
SQLAlchemy ORM models mirroring `migrations/0002_core_tables.sql`.

These are hand-maintained — the SQL migration is the source of truth;
models follow. A migration test exercises each model with a real row to
catch drift. Keep fields in the same order as the DDL so diffs stay
reviewable.

Columns NOT modelled here (yet):
    * `events.metadata` — stays as raw JSONB on read; application code
      will ship typed wrappers per event_type in a later phase.
    * `event_windows.data` — the ±9s BLOB. Model exposes bytes; decoding
      lives in a separate module.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Common declarative base. No shared columns beyond what SA adds."""


# ─── Enums (match CREATE TYPE in 0002_core_tables.sql) ─────────────


class ParameterScope(str, enum.Enum):
    GLOBAL = "global"
    DEVICE = "device"
    SENSOR = "sensor"


class SessionScope(str, enum.Enum):
    GLOBAL = "global"
    LOCAL = "local"


class SessionLogEvent(str, enum.Enum):
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    RECONFIGURE = "reconfigure"
    ERROR = "error"


class EventType(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    BREAK = "BREAK"


class DeviceProtocol(str, enum.Enum):
    MQTT = "mqtt"
    MODBUS_TCP = "modbus_tcp"


# ─── Tables ────────────────────────────────────────────────────────


class Device(Base):
    """
    A physical data source. Currently STM32-over-MQTT; Modbus TCP
    support is documented but low-priority for the rewrite.
    """

    __tablename__ = "devices"

    device_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[DeviceProtocol] = mapped_column(
        Enum(DeviceProtocol, name="device_protocol"),
        nullable=False,
        default=DeviceProtocol.MQTT,
    )
    topic: Mapped[str | None] = mapped_column(Text)
    modbus_config: Mapped[dict | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("device_id BETWEEN 1 AND 999", name="devices_id_range"),
        CheckConstraint(
            "protocol = 'mqtt' OR modbus_config IS NOT NULL",
            name="devices_modbus_has_config",
        ),
    )


class Package(Base):
    """
    A named configuration preset. Immutable once a session that used
    it has closed (trigger-enforced in `0004_triggers.sql`). Editing a
    locked package forks a new one via the packages.clone API.
    """

    __tablename__ = "packages"

    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_package_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("packages.package_id")
    )


class Parameter(Base):
    """
    One configuration key-value row attached to a package. Scope and
    (device_id, sensor_id) together express the parameter's applicability.
    Resolution walks sensor → device → global in application code.
    """

    __tablename__ = "parameters"

    parameter_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("packages.package_id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[ParameterScope] = mapped_column(
        Enum(ParameterScope, name="parameter_scope"), nullable=False
    )
    device_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.device_id")
    )
    sensor_id: Mapped[int | None] = mapped_column(SmallInteger)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Session(Base):
    """
    A monitoring run. A session binds events to the config (package)
    that was active when they fired. One active global session system-
    wide; at most one active local session per device overriding the
    global.
    """

    __tablename__ = "sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    scope: Mapped[SessionScope] = mapped_column(
        Enum(SessionScope, name="session_scope"), nullable=False
    )
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.session_id")
    )
    device_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("devices.device_id")
    )
    package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("packages.package_id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[str | None] = mapped_column(Text)
    ended_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    record_raw_samples: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class SessionLog(Base):
    """Append-only audit trail for session lifecycle events."""

    __tablename__ = "session_logs"

    log_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[SessionLogEvent] = mapped_column(
        Enum(SessionLogEvent, name="session_log_event"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


class Event(Base):
    """
    One detected event. `triggered_at` is the ORIGINAL crossing moment
    (not when the row was written — see docs/contracts/BUG_DECISION_LOG.md
    for why debounce keeps the early timestamp). `fired_at` is when the
    row was persisted.
    """

    __tablename__ = "events"

    # Composite PK because the Timescale hypertable partitions on triggered_at.
    event_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=False
    )
    device_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("devices.device_id"), nullable=False
    )
    sensor_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type"), nullable=False
    )
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    triggered_value: Mapped[float] = mapped_column(nullable=False)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    window_id: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (
        # Composite PK declared here so `triggered_at` is part of the key.
        UniqueConstraint("event_id", "triggered_at", name="events_pkey"),
    )


class EventWindow(Base):
    """
    ±9s raw sample buffer attached to an event. Encoding defaults to
    zstd+delta-f32 (~100× smaller than legacy double-precision BLOBs).
    """

    __tablename__ = "event_windows"

    window_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    end_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sample_rate_hz: Mapped[float] = mapped_column(nullable=False, default=123.0)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    encoding: Mapped[str] = mapped_column(
        Text, nullable=False, default="zstd+delta-f32"
    )
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class SensorOffset(Base):
    """
    Physical calibration for one sensor on one device.
    Formula (unchanged from legacy): `adjusted = raw - offset`.
    """

    __tablename__ = "sensor_offsets"

    device_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("devices.device_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sensor_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    offset_value: Mapped[float] = mapped_column(nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    """Operator account. Authentication is OTP-only; no password column."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserOtp(Base):
    """One-time password issued for login. OTPs are hashed (argon2id)."""

    __tablename__ = "user_otps"

    otp_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MqttBroker(Base):
    """MQTT broker settings. Exactly one row may have is_active=TRUE."""

    __tablename__ = "mqtt_brokers"

    broker_id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True
    )
    host: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, default=1883)
    username: Mapped[str | None] = mapped_column(Text)
    password_enc: Mapped[str | None] = mapped_column(Text)
    use_tls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
