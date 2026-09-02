"""Live-service checks. Skipped unless -m network is requested.

These pin the two measured facts the streaming design rests on. If either ever
stops holding, the design is invalid and this suite should say so loudly rather
than letting runs quietly fall back to transferring whole products.

Run with:  .venv/Scripts/python.exe -m pytest -m network -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.network

LROC_URL = (
    "https://pds.lroc.im-ldi.com/data/LRO-L-LROC-3-CDR-V1.0/LROLRC_1001/"
    "DATA/COM/2009227/NAC/M104977327LC.IMG"
)


def test_pds_host_still_honours_single_ranges(tmp_path):
    """If this fails, the whole streaming design is invalid."""
    from lunar_matchbench.core.streaming import LrocStream

    stream = LrocStream.open(LROC_URL, cache_dir=tmp_path / "c")
    assert stream.total_samples == 5064
    assert stream.total_lines > 1000

    window = stream.read_lines(20000, 64)
    assert window.shape == (64, 5064)
    # 64 lines is 648 KB of pixels; anything near the 529 MB product size means
    # the range was ignored.
    assert stream.stats["fetched_bytes"] < 2_000_000


def test_streaming_beats_downloading_by_an_order_of_magnitude(tmp_path):
    from lunar_matchbench.core.streaming import LrocStream

    stream = LrocStream.open(LROC_URL, cache_dir=tmp_path / "c")
    stream.read_lines(20000, 1024)
    assert stream.stats["fetched_bytes"] < stream.rf.size / 10


def test_multirange_is_still_unsupported():
    """Documents why RangeFile never batches ranges.

    The host answers a comma-separated Range with 200 and the entire body. This
    asserts the trap is still there, so nobody 'optimises' the reader into it.
    """
    import requests

    # stream=True so the body is never pulled -- accepting it would mean
    # downloading 529 MB to prove a point.
    r = requests.get(LROC_URL, headers={"Range": "bytes=0-1023, 4096-5119"},
                     stream=True, timeout=(15, 60))
    try:
        status = r.status_code
        ctype = r.headers.get("Content-Type", "")
        length = r.headers.get("Content-Length", "")
    finally:
        r.close()

    assert "multipart/byteranges" not in ctype.lower(), (
        f"multi-range now appears SUPPORTED (Content-Type: {ctype}). "
        "Re-measure and update the spec before letting RangeFile batch ranges."
    )
    assert status == 200, (
        f"expected the multi-range request to be ignored with 200, got {status}. "
        "The host's behaviour has changed; re-measure before relying on it."
    )
    # And confirm being ignored means the whole body, which is the actual cost.
    assert length and int(length) > 100_000_000, (
        f"expected the full body on an ignored range, got Content-Length={length}"
    )


def test_pradan_still_honours_single_ranges():
    """CH2 streaming depends on this exactly as LROC streaming does."""
    from lunar_matchbench.core.ch2_fetch import (
        PRADAN_TRIGGER_URL, WFS_TRIGGER_URL, _discover_ch2_candidates,
        _get_credentials, _IssdcSession,
    )

    user, pw = _get_credentials()
    wfs = _IssdcSession(WFS_TRIGGER_URL, WFS_TRIGGER_URL, "chmapbrowse", user, pw)
    pradan = _IssdcSession(PRADAN_TRIGGER_URL, PRADAN_TRIGGER_URL, "pradan", user, pw)

    candidates = _discover_ch2_candidates(15.0, 289.2, "tmc", 0.2, wfs)
    assert candidates, "no CH2 candidates discovered for the reference coordinate"

    _, url = candidates[0]
    r = pradan.request("GET", url, headers={"Range": "bytes=0-1023"},
                       stream=True, allow_redirects=True, timeout=(15, 60))
    status, crange = r.status_code, r.headers.get("Content-Range", "")
    r.close()
    assert status == 206, f"PRADAN answered {status}, not 206"
    assert crange.startswith("bytes 0-1023/"), f"unexpected Content-Range: {crange!r}"
