"""Tests for the HTTP byte-range streaming layer."""
from __future__ import annotations

import pytest

from lunar_matchbench.core.streaming import RangeFile, RangeNotHonoured


def test_size_from_head(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    assert rf.size == len(state.payload)


def test_read_range_returns_exact_bytes(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    got = rf.read_range(1000, 256)
    assert got == state.payload[1000:1256]


def test_rejects_server_that_ignores_range(range_server):
    """A 200 full-body response must never be mistaken for the requested slice."""
    url, state, cache = range_server
    state.mode = "ignore_range"
    rf = RangeFile(url, cache_dir=cache)
    with pytest.raises(RangeNotHonoured):
        rf.read_range(1000, 256)


def test_rejects_wrong_start_offset(range_server):
    """This is the exact failure that corrupted a 713 MB product."""
    url, state, cache = range_server
    state.mode = "wrong_offset"
    rf = RangeFile(url, cache_dir=cache)
    with pytest.raises(RangeNotHonoured) as exc:
        rf.read_range(4096, 256)
    assert exc.value.requested_start == 4096


def test_cache_hit_avoids_second_request(range_server):
    """A second reader for the same interval must not touch the network."""
    url, state, cache = range_server
    first = RangeFile(url, cache_dir=cache).read_range(2048, 128)
    n_after_first = len(state.request_log)

    second_reader = RangeFile(url, cache_dir=cache)
    second = second_reader.read_range(2048, 128)

    assert second == first
    assert len(state.request_log) == n_after_first, "cache hit still hit the network"
    assert second_reader.stats["requests"] == 0
    assert second_reader.stats["cached_bytes"] == 128


def test_stats_track_fetched_and_cached(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    rf.read_range(0, 512)
    assert rf.stats["fetched_bytes"] == 512
    assert rf.stats["cached_bytes"] == 0

    rf2 = RangeFile(url, cache_dir=cache)
    rf2.read_range(0, 512)
    assert rf2.stats["cached_bytes"] == 512
    assert rf2.stats["fetched_bytes"] == 0


def test_never_sends_multirange(range_server):
    """Multi-range is silently ignored by the real PDS host -- never emit one."""
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    rf.read_range(0, 64)
    rf.read_range(4096, 64)
    assert state.request_log, "expected at least one ranged request"
    assert all("," not in r for r in state.request_log)


def test_refuses_oversized_read(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    with pytest.raises(ValueError):
        rf.read_range(0, 1024 * 1024 * 1024)


def test_refuses_negative_offset(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    with pytest.raises(ValueError):
        rf.read_range(-1, 64)
