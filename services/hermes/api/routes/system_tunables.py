"""
/api/system-tunables — read-only system status (gap 8).

A single endpoint that surfaces the runtime configuration the
operator already deployed via env vars + the live system state
(active sessions, recording, Modbus pollers, shard topology). The
``/settings`` SvelteKit page renders this as a "what's running right
now?" dashboard so operators don't have to ssh into the host to
check ``systemctl show`` or ``cat /etc/hermes/api.env``.

Read-only by design (alpha.22):

  Detection thresholds are operator-tunable today via /api/config —
  /settings just links there. The MQTT broker UI lives at
  /api/mqtt-brokers (gap 4). Session lifecycle is at /api/sessions
  (gap 5). What's left is the small set of process-level knobs that
  are read at boot from env vars. Making those runtime-editable
  requires a re-read mechanism in every consumer (TtlGateSink,
  ClockRegistry, etc.) and is enough scope to land on its own.
  Until then, the response carries explicit hints about which
  values can be edited live and which need a service restart.

The response shape is intentionally flat-keyed so a future POST/PATCH
that adds writability won't have to rename anything.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from hermes import __version__
from hermes.api.deps import CurrentUser, DbSession
from hermes.config import Settings, get_settings
from hermes.db.models import Device, DeviceProtocol, SessionScope
from hermes.db.models import Session as SessionRow

router = APIRouter()


# ─── Shapes ─────────────────────────────────────────────────────


class TunableField(BaseModel):
    """One entry in the response table.

    ``editable`` distinguishes settings the operator can change today
    via /api/config or another route from settings that require
    editing the systemd env file + restarting.
    """

    model_config = ConfigDict(from_attributes=True)

    key: str
    value: object
    description: str
    editable: Literal["live", "restart", "via_other_route"]
    edit_hint: str | None = None


class SystemStateOut(BaseModel):
    """Aggregate live system state (counts, mode, version)."""

    version: str
    ingest_mode: Literal["all", "shard", "live_only"]
    shard_count: int
    shard_index: int
    dev_mode: bool
    log_format: str
    active_global_session_id: str | None
    active_local_session_count: int
    sessions_recording_count: int
    modbus_devices_active: int
    mqtt_devices_active: int


class SystemTunablesOut(BaseModel):
    state: SystemStateOut
    tunables: list[TunableField]


# ─── Helpers ───────────────────────────────────────────────────


async def _gather_state(session: DbSession, settings: Settings) -> SystemStateOut:
    """Pull live counts from the DB; returns a snapshot, not a stream."""
    # Active global session id (if any).
    glb_row = (
        await session.execute(
            select(SessionRow).where(
                SessionRow.scope == SessionScope.GLOBAL,
                SessionRow.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    active_global_id = str(glb_row.session_id) if glb_row is not None else None

    # Count of active locals.
    active_locals = (
        await session.execute(
            select(func.count())
            .select_from(SessionRow)
            .where(
                SessionRow.scope == SessionScope.LOCAL,
                SessionRow.ended_at.is_(None),
            )
        )
    ).scalar_one()

    # Recording-active sessions.
    recording = (
        await session.execute(
            select(func.count())
            .select_from(SessionRow)
            .where(
                SessionRow.ended_at.is_(None),
                SessionRow.record_raw_samples.is_(True),
            )
        )
    ).scalar_one()

    # Devices by protocol that are currently active.
    modbus_active = (
        await session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.protocol == DeviceProtocol.MODBUS_TCP,
                Device.is_active.is_(True),
            )
        )
    ).scalar_one()
    mqtt_active = (
        await session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.protocol == DeviceProtocol.MQTT,
                Device.is_active.is_(True),
            )
        )
    ).scalar_one()

    return SystemStateOut(
        version=__version__,
        ingest_mode=settings.hermes_ingest_mode,
        shard_count=settings.hermes_shard_count,
        shard_index=settings.hermes_shard_index,
        dev_mode=settings.hermes_dev_mode,
        log_format=settings.hermes_log_format,
        active_global_session_id=active_global_id,
        active_local_session_count=int(active_locals),
        sessions_recording_count=int(recording),
        modbus_devices_active=int(modbus_active),
        mqtt_devices_active=int(mqtt_active),
    )


def _build_tunables(settings: Settings) -> list[TunableField]:
    """Project the runtime-configurable Settings fields into UI rows."""
    return [
        TunableField(
            key="event_ttl_seconds",
            value=settings.event_ttl_seconds,
            description=(
                "TtlGateSink dedup window. Within this window same-type events "
                "merge, lower-priority types are blocked, BREAK bypasses."
            ),
            editable="restart",
            edit_hint="EVENT_TTL_SECONDS in /etc/hermes/ingest.env, then "
            "systemctl restart hermes-ingest",
        ),
        TunableField(
            key="live_buffer_max_samples",
            value=settings.live_buffer_max_samples,
            description=(
                "Per-device LiveDataHub ring buffer depth. At 100 Hz this is "
                "~20 s of history available to the SSE endpoint."
            ),
            editable="restart",
            edit_hint="LIVE_BUFFER_MAX_SAMPLES in /etc/hermes/api.env, then "
            "systemctl restart hermes-api",
        ),
        TunableField(
            key="mqtt_drift_threshold_s",
            value=settings.mqtt_drift_threshold_s,
            description=(
                "ClockRegistry re-anchors STM32 wall time when drift exceeds this "
                "value. Affects timestamp anchoring only; events still fire."
            ),
            editable="restart",
            edit_hint="MQTT_DRIFT_THRESHOLD_S in /etc/hermes/ingest.env, then "
            "systemctl restart hermes-ingest",
        ),
        TunableField(
            key="hermes_jwt_expiry_seconds",
            value=settings.hermes_jwt_expiry_seconds,
            description="JWT lifetime for operator sessions issued by /api/auth.",
            editable="restart",
            edit_hint="HERMES_JWT_EXPIRY_SECONDS in /etc/hermes/api.env, then "
            "systemctl restart hermes-api",
        ),
        TunableField(
            key="otp_expiry_seconds",
            value=settings.otp_expiry_seconds,
            description="OTP lifetime for the email-based login flow.",
            editable="restart",
            edit_hint="OTP_EXPIRY_SECONDS in /etc/hermes/api.env, then "
            "systemctl restart hermes-api",
        ),
        TunableField(
            key="otp_max_per_hour",
            value=settings.otp_max_per_hour,
            description="Per-user OTP rate limit (login flow).",
            editable="restart",
            edit_hint="OTP_MAX_PER_HOUR in /etc/hermes/api.env, then systemctl restart hermes-api",
        ),
        TunableField(
            key="detector_thresholds",
            value="see /api/config",
            description=(
                "Type A/B/C/D + mode-switching thresholds are scope-resolved "
                "(SENSOR > DEVICE > GLOBAL) and editable live via /api/config."
            ),
            editable="via_other_route",
            edit_hint="Use the Config page or PUT /api/config/{type}/...",
        ),
        TunableField(
            key="mqtt_brokers",
            value="see /api/mqtt-brokers",
            description=(
                "Operator-managed MQTT broker registry. Editing the active row "
                "currently still requires hermes-ingest restart for the broker "
                "switchover (see gap 4 docs)."
            ),
            editable="via_other_route",
            edit_hint="Use the MQTT page or POST /api/mqtt-brokers",
        ),
    ]


# ─── Routes ────────────────────────────────────────────────────


@router.get("", response_model=SystemTunablesOut)
async def get_system_tunables(
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> SystemTunablesOut:
    """Return live system status + the boot-time tunable values.

    All values are read straight from ``Settings`` + the DB at request
    time, so a service restart with new env vars is reflected on the
    very next call.
    """
    del request, user
    settings = get_settings()
    state = await _gather_state(session, settings)
    return SystemTunablesOut(
        state=state,
        tunables=_build_tunables(settings),
    )
