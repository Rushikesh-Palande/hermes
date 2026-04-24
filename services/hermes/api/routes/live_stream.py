"""
/api/live_stream — Server-Sent Events feed of live sensor samples.

Browsers open a long-lived connection; we push one ``data:`` frame per
batch of new samples. Clients reconnect automatically on disconnect
(``EventSource`` semantics).

Why SSE and not WebSockets:
    The channel is unidirectional (server→client), request-reply for
    commands goes through plain REST. SSE survives proxies that rewrite
    WebSocket upgrades, needs no explicit framing library on the server,
    and reconnect logic is free on the browser side.

Why polling the hub instead of a pub/sub event:
    Clients request batches — a pub/sub-per-sample model would send 123
    tiny events/sec per client and crush the browser. We poll at
    ``interval_s`` (default 100 ms), batch up to ``max_samples`` new rows,
    serialise once, send.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from hermes.ingest.live_data import LiveDataHub

router = APIRouter()

# Safety cap — browsers choke when rendering >500 rows × 12 sensors in one
# paint. Legacy dashboard discovered this the hard way; see CLAUDE.md.
_MAX_SAMPLES_HARD_CAP = 500


def _sse_event(data: str) -> bytes:
    """Encode a single SSE frame. No event: or id: fields — plain data only."""
    return f"data: {data}\n\n".encode()


async def _stream_samples(
    request: Request,
    hub: LiveDataHub,
    device_id: int,
    interval_s: float,
    max_samples: int,
) -> AsyncIterator[bytes]:
    """
    Polling generator: yields SSE frames until the client disconnects.

    Tracks the timestamp of the last sample sent; each tick pulls only
    newer samples from the hub.
    """
    last_ts: float | None = None

    # Keepalive comment every ``keepalive_s`` seconds to defeat proxies
    # that close idle connections. SSE treats lines starting with `:` as
    # comments so they don't trigger any handler on the client.
    keepalive_s = 15.0
    last_send = asyncio.get_event_loop().time()

    # Send a one-shot initial ``retry:`` hint so reconnects happen quickly.
    yield b"retry: 3000\n\n"

    while True:
        if await request.is_disconnected():
            return

        samples = hub.since(device_id, after_ts=last_ts)
        if samples:
            # Trim to the newest ``max_samples`` to bound per-tick payload.
            if len(samples) > max_samples:
                samples = samples[-max_samples:]
            last_ts = samples[-1].ts
            payload: dict[str, Any] = {
                "device_id": device_id,
                "samples": [{"ts": s.ts, "values": s.values} for s in samples],
            }
            yield _sse_event(json.dumps(payload, separators=(",", ":")))
            last_send = asyncio.get_event_loop().time()
        elif asyncio.get_event_loop().time() - last_send >= keepalive_s:
            yield b": keepalive\n\n"
            last_send = asyncio.get_event_loop().time()

        await asyncio.sleep(interval_s)


@router.get("/{device_id}")
async def live_stream(
    request: Request,
    device_id: int,
    interval: float = Query(
        default=0.1,
        ge=0.02,
        le=2.0,
        description="Poll interval in seconds (server-side tick rate).",
    ),
    max_samples: int = Query(
        default=500,
        ge=1,
        le=_MAX_SAMPLES_HARD_CAP,
        description="Cap on samples per SSE frame.",
    ),
) -> StreamingResponse:
    """
    Open an SSE feed for a single device.

    Each frame carries ``{"device_id": int, "samples": [{"ts": float,
    "values": {sensor_id: float, ...}}, ...]}``. Frames may be empty if
    no new data arrived within the interval — clients should treat
    ``samples: []`` as a keepalive, not an error.
    """
    hub: LiveDataHub = request.app.state.live_data

    return StreamingResponse(
        _stream_samples(request, hub, device_id, interval, max_samples),
        media_type="text/event-stream",
        headers={
            # Disable buffering on reverse proxies that respect the hint.
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
