"""
Event-window sample encoding.

The contract calls for ``zstd+delta-f32`` as the production encoding
(roughly 100× smaller than legacy float64 BLOBs). For Phase 3e we ship
a simpler ``json-utf8`` encoder so writes are debuggable; the decoder
is open-coded here too. Swapping in the production encoder is a single
function replacement at this seam — no caller change required.

The ``encoding`` string stored on ``event_windows.encoding`` lets a
future reader pick the matching decoder; we never break old rows even
when the default flips.
"""

from __future__ import annotations

import json

# String stored on ``event_windows.encoding`` for the format below.
# When the production zstd+delta-f32 encoder lands, it'll use
# ``"zstd+delta-f32"`` and live alongside this one.
ENCODING_JSON: str = "json-utf8"


def encode_window(samples: list[tuple[float, float]]) -> tuple[bytes, str]:
    """
    Encode a list of ``(ts, value)`` pairs into bytes.

    Returns ``(data, encoding_string)`` so the caller can write the
    matching ``encoding`` column verbatim. Choice of encoding lives
    in this module so the storage shape can evolve independently of
    the sink that calls it.
    """
    payload = [{"ts": ts, "v": v} for ts, v in samples]
    return json.dumps(payload, separators=(",", ":")).encode("utf-8"), ENCODING_JSON


def decode_window(data: bytes, encoding: str) -> list[tuple[float, float]]:
    """
    Inverse of ``encode_window``. Used by the API event-history endpoint
    (Phase 4) and by tests.
    """
    if encoding != ENCODING_JSON:
        raise ValueError(f"unsupported event-window encoding: {encoding!r}")
    payload = json.loads(data)
    return [(item["ts"], item["v"]) for item in payload]
