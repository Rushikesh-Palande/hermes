"""
Event-window encoder round-trip invariants.
"""

from __future__ import annotations

import math

import pytest

from hermes.detection.encoding import ENCODING_JSON, decode_window, encode_window


def test_round_trip_preserves_samples_exactly() -> None:
    samples = [(1.0, 10.0), (1.01, 10.5), (1.02, 9.8)]
    data, encoding = encode_window(samples)
    assert encoding == ENCODING_JSON
    assert decode_window(data, encoding) == samples


def test_empty_window_encodes_to_empty_list() -> None:
    data, encoding = encode_window([])
    assert decode_window(data, encoding) == []


def test_floats_stay_intact_at_high_precision() -> None:
    samples = [(1234567890.123456, 0.000001), (1234567890.987654, 99999.99999)]
    data, encoding = encode_window(samples)
    decoded = decode_window(data, encoding)
    for (orig_ts, orig_v), (got_ts, got_v) in zip(samples, decoded, strict=True):
        assert math.isclose(orig_ts, got_ts, rel_tol=1e-12)
        assert math.isclose(orig_v, got_v, rel_tol=1e-12)


def test_unsupported_encoding_raises() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        decode_window(b"{}", "zstd+delta-f32")
