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


@pytest.mark.downloads
def test_resume_discards_partial_when_server_restarts_from_zero(range_server, tmp_path):
    """A 206 that starts at 0 when we asked for N must not be appended.

    This reproduces the bug that made a fetched 713 MB CH2 zip exactly 240 MB
    too large: every central-directory offset was shifted, so the archive still
    listed its members correctly but the .img member failed to open with
    'Bad magic number for file header'.
    """
    import requests

    from lunar_matchbench.core.ch2_fetch import _IssdcSession, _download_file

    url, state, _ = range_server
    state.mode = "wrong_offset"

    dest = tmp_path / "product.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(state.payload[:4096])          # a half-finished download

    session = _IssdcSession.__new__(_IssdcSession)     # bypass Keycloak login
    session.session = requests.Session()
    session.name = "test"

    assert _download_file(session, url, dest, None) is True
    assert dest.read_bytes() == state.payload, "resumed file must match the source exactly"


@pytest.mark.downloads
def test_resume_appends_when_server_honours_the_offset(range_server, tmp_path):
    """The happy path must still resume rather than restart from scratch."""
    import requests

    from lunar_matchbench.core.ch2_fetch import _IssdcSession, _download_file

    url, state, _ = range_server
    dest = tmp_path / "product.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(state.payload[:4096])

    session = _IssdcSession.__new__(_IssdcSession)
    session.session = requests.Session()
    session.name = "test"

    assert _download_file(session, url, dest, None) is True
    assert dest.read_bytes() == state.payload
    assert state.request_log[-1] == "bytes=4096-", "should have asked to resume"


# ── PDS3 raster readers ──────────────────────────────────────────────────────

import numpy as np

from lunar_matchbench.core.streaming import (
    LocalLrocReader, LrocStream, parse_pds3_label,
)

# LABEL_RECORDS * RECORD_BYTES must equal the padded label length, or the
# raster offset lands inside the header.
_LABEL_BYTES = 512
PDS3_LABEL = (
    b"PDS_VERSION_ID = PDS3\r\n"
    b"RECORD_TYPE = FIXED_LENGTH\r\n"
    b"RECORD_BYTES = 512\r\n"
    b"LABEL_RECORDS = 1\r\n"
    b"PRODUCT_ID = \"nactest0001\"\r\n"
    b"START_TIME = 2009-08-15T00:00:00\r\n"
    b"DATA_SET_ID = \"LRO-L-LROC-3-CDR-V1.0\"\r\n"
    b"OBJECT = IMAGE\r\n"
    b"  LINES = 8\r\n"
    b"  LINE_SAMPLES = 10\r\n"
    b"  SAMPLE_BITS = 16\r\n"
    b"  SAMPLE_TYPE = LSB_INTEGER\r\n"
    b"END_OBJECT = IMAGE\r\n"
    b"END\r\n"
).ljust(_LABEL_BYTES, b" ")


def _synthetic_pds3(tmp_path):
    """A tiny but structurally real PDS3 product: 1 label record + 8x10 int16."""
    pixels = np.arange(80, dtype="<i2").reshape(8, 10)
    pixels[0, 0] = -32768                       # a PDS null
    path = tmp_path / "tiny.IMG"
    path.write_bytes(PDS3_LABEL + pixels.tobytes())
    return path, pixels


def test_parse_pds3_label_reads_geometry():
    label = parse_pds3_label(PDS3_LABEL)
    assert label["total_lines"] == 8
    assert label["total_samples"] == 10
    assert label["label_records"] == 1
    assert label["record_bytes"] == 512
    assert label["data_offset"] == 512
    assert label["product_id"] == "nactest0001"


def test_local_reader_reads_a_line_window(tmp_path):
    path, pixels = _synthetic_pds3(tmp_path)
    reader = LocalLrocReader(path)
    assert reader.total_lines == 8
    assert reader.total_samples == 10
    window = reader.read_lines(2, 3)
    assert window.shape == (3, 10)
    np.testing.assert_array_equal(window, pixels[2:5].astype(np.float32))


def test_local_reader_maps_nulls_to_nan(tmp_path):
    path, _ = _synthetic_pds3(tmp_path)
    window = LocalLrocReader(path).read_lines(0, 1)
    assert np.isnan(window[0, 0])
    assert not np.isnan(window[0, 1])


def test_local_reader_clamps_past_eof(tmp_path):
    path, _ = _synthetic_pds3(tmp_path)
    window = LocalLrocReader(path).read_lines(6, 10)
    assert window.shape == (2, 10)


def _serve_blob(blob):
    """Serve a fixed byte blob over HTTP with honest single-range support."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

        def do_GET(self):
            s, _, e = self.headers["Range"].removeprefix("bytes=").partition("-")
            s = int(s)
            e = min(int(e), len(blob) - 1) if e else len(blob) - 1
            body = blob[s:e + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {s}-{s + len(body) - 1}/{len(blob)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/blob"


def test_stream_and_local_readers_agree(tmp_path):
    """The streaming reader must produce identical results to the local one."""
    path, _ = _synthetic_pds3(tmp_path)
    srv, url = _serve_blob(path.read_bytes())
    try:
        stream = LrocStream.open(url, cache_dir=tmp_path / "c")
        assert stream.total_lines == 8
        assert stream.total_samples == 10
        np.testing.assert_array_equal(
            stream.read_lines(2, 3), LocalLrocReader(path).read_lines(2, 3)
        )
    finally:
        srv.shutdown()
        srv.server_close()


def test_stream_reader_requests_only_the_lines_asked_for(tmp_path):
    """A line window must cost exactly its own bytes, not the whole raster.

    The synthetic product here is far too small for a megabyte-scale saving to
    mean anything, so this asserts the property that scales instead: the read
    after the label probe fetches precisely n_lines * samples * 2 bytes. The
    end-to-end size win is measured against the live product in test_live.py.
    """
    path, _ = _synthetic_pds3(tmp_path)
    srv, url = _serve_blob(path.read_bytes())
    try:
        stream = LrocStream.open(url, cache_dir=tmp_path / "c")
        after_label = stream.stats["fetched_bytes"]
        stream.read_lines(2, 3)
        assert stream.stats["fetched_bytes"] - after_label == 3 * 10 * 2
    finally:
        srv.shutdown()
        srv.server_close()


# ── Remote ZIP reader ────────────────────────────────────────────────────────

import io
import zipfile

from lunar_matchbench.core import streaming as streaming_mod
from lunar_matchbench.core.streaming import Ch2ZipStream

# Big enough that a 64 KB EOCD probe is genuinely partial, and incompressible
# so DEFLATE cannot shrink it into a single chunk -- both properties are what
# make the streaming behaviour observable at all.
_ZIP_LINES, _ZIP_SAMPLES = 400, 512


def _zip_blob():
    """A zip shaped like a CH2 product: a small CSV and a large DEFLATE raster."""
    rng = np.random.default_rng(11)
    raster = rng.integers(0, 65535, _ZIP_LINES * _ZIP_SAMPLES,
                          dtype="<u2").tobytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("geometry/grid.csv",
                   "Longitude,Latitude,Pixel,Scan\n1,2,3,4\n")
        z.writestr("data/scene_d_img.img", raster)
    return buf.getvalue(), raster


def test_zip_stream_lists_members(tmp_path):
    blob, _ = _zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        assert "geometry/grid.csv" in z.namelist()
        assert "data/scene_d_img.img" in z.namelist()
    finally:
        srv.shutdown()
        srv.server_close()


def test_zip_stream_reads_small_member_without_whole_file(tmp_path):
    """Reaching the geometry CSV must not cost the whole archive."""
    blob, _ = _zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        csv_bytes = z.member_bytes("geometry/grid.csv")
        assert csv_bytes.startswith(b"Longitude,Latitude,Pixel,Scan")
        assert z.stats["fetched_bytes"] < len(blob), "must not have pulled the whole zip"
    finally:
        srv.shutdown()
        srv.server_close()


def test_zip_stream_img_lines_match_source(tmp_path):
    blob, raster = _zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        got = z.img_lines("data/scene_d_img.img", samples=_ZIP_SAMPLES, dtype="<u2",
                          start_line=5, n_lines=3)
        expected = np.frombuffer(raster, dtype="<u2").reshape(_ZIP_LINES, _ZIP_SAMPLES)[5:8]
        np.testing.assert_array_equal(got, expected.astype(np.float32))
    finally:
        srv.shutdown()
        srv.server_close()


def test_zip_stream_img_lines_stop_early(tmp_path, monkeypatch):
    """Reading line 0 must inflate far less than the whole member.

    The chunk size is shrunk so the effect is visible on a test-sized product;
    on a real 508 MB archive the 4 MB default already gives the same behaviour.
    """
    monkeypatch.setattr(streaming_mod, "INFLATE_CHUNK", 32 * 1024)
    blob, raster = _zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        z.img_lines("data/scene_d_img.img", samples=_ZIP_SAMPLES, dtype="<u2",
                    start_line=0, n_lines=1)
        assert z.inflated_bytes < len(raster) / 2
    finally:
        srv.shutdown()
        srv.server_close()


def test_zip_stream_rejects_shifted_offsets(tmp_path):
    """A corrupt archive must fail loudly, the way the real one did.

    Prepending bytes is exactly what the bad resume did: the directory still
    lists every member, but each recorded offset now points into the middle of
    compressed data.
    """
    blob, _ = _zip_blob()
    srv, url = _serve_blob(bytes(4096) + blob)
    try:
        with pytest.raises(RangeNotHonoured):
            Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
    finally:
        srv.shutdown()
        srv.server_close()


# ── Streamed CH2 geometry + patch extraction ─────────────────────────────────

def _ch2_zip_blob():
    """A zip shaped like a real CH2 TMC product: geometry CSV + DEFLATE raster."""
    samples, lines = 4000, 300
    rng = np.random.default_rng(21)
    raster = rng.integers(0, 4000, lines * samples, dtype="<u2").tobytes()

    rows = ["Longitude,Latitude,Pixel,Scan"]
    for scan in range(0, lines, 10):
        lat = 15.0 + (scan - 150) * 0.0001
        rows.append(f"289.20,{lat:.6f},2000,{scan}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("geometry/calibrated/g_grd.csv", "\n".join(rows))
        z.writestr("data/calibrated/scene_d_img.img", raster)
    return buf.getvalue()


def test_streamed_ch2_geometry_match_and_patch(tmp_path):
    """Resolve a coordinate and pull its patch without transferring the archive."""
    from lunar_matchbench.core.downloader import (
        extract_ch2_patch, find_ch2_geometry_match_streamed,
    )

    blob = _ch2_zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        match = find_ch2_geometry_match_streamed(z, 15.0, 289.20, "tmc")
        assert match is not None, "the planted grid should cover this coordinate"
        assert match["zstream"] is z
        assert 0 <= match["scan"] < 300

        after_geometry = z.stats["fetched_bytes"]
        assert after_geometry < len(blob), "geometry lookup pulled the whole archive"

        patch = extract_ch2_patch(match, "tmc", size=256)
        assert patch is not None
        assert patch.shape == (256, 256)
        assert patch.dtype == np.uint8
    finally:
        srv.shutdown()
        srv.server_close()


def test_streamed_ch2_rejects_far_coordinate(tmp_path):
    """A coordinate outside the imaged swath must return None, not a bogus edge pixel."""
    from lunar_matchbench.core.downloader import find_ch2_geometry_match_streamed

    blob = _ch2_zip_blob()
    srv, url = _serve_blob(blob)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        assert find_ch2_geometry_match_streamed(z, -40.0, 100.0, "tmc") is None
    finally:
        srv.shutdown()
        srv.server_close()


def test_stream_reader_splits_oversized_windows(tmp_path, monkeypatch):
    """A window larger than the single-read ceiling must be split, not refused.

    A real TMC-scale LROC window is ~78.6 MB against a 64 MB ceiling, which
    failed a live run outright with 'refusing a single 78643920 byte read'.
    """
    path, pixels = _synthetic_pds3(tmp_path)
    srv, url = _serve_blob(path.read_bytes())
    try:
        # Force a tiny ceiling so both the label probe and the 8-line read
        # have to be split across several single-range requests.
        monkeypatch.setattr(streaming_mod, "RANGE_CHUNK_MAX", 64)
        stream = LrocStream.open(url, cache_dir=tmp_path / "c")
        window = stream.read_lines(0, 8)
        assert window.shape == (8, 10)
        np.testing.assert_array_equal(window[1:], pixels[1:].astype(np.float32))
    finally:
        srv.shutdown()
        srv.server_close()


def test_truncated_local_product_is_detected(tmp_path):
    """A killed download leaves a short .IMG under the real product name.

    Reading past its end returns zero lines, which reads as empty imagery
    rather than a broken file -- that is how a documented-working benchmark
    coordinate silently started failing.
    """
    path, _ = _synthetic_pds3(tmp_path)
    full = LocalLrocReader(path)
    assert full.is_complete is True

    truncated = tmp_path / "cut.IMG"
    truncated.write_bytes(path.read_bytes()[:len(PDS3_LABEL) + 20])
    short = LocalLrocReader(truncated)
    assert short.is_complete is False
    assert short.expected_bytes == full.expected_bytes


def test_transfer_budget_stops_a_runaway_run(range_server):
    """A run must fail loudly rather than quietly transferring a gigabyte."""
    from lunar_matchbench.core.streaming import TransferBudget, TransferBudgetExceeded

    url, state, cache = range_server
    budget = TransferBudget(1000)
    rf = RangeFile(url, cache_dir=cache, budget=budget)
    rf.read_range(0, 600)
    assert budget.used == 600
    with pytest.raises(TransferBudgetExceeded):
        rf.read_range(2000, 600)


def test_cached_reads_do_not_consume_budget(range_server, tmp_path):
    """A pre-warmed demo must not be able to exhaust its own budget."""
    from lunar_matchbench.core.streaming import TransferBudget

    url, state, cache = range_server
    RangeFile(url, cache_dir=cache).read_range(0, 4096)      # warm it

    budget = TransferBudget(100)
    rf = RangeFile(url, cache_dir=cache, budget=budget)
    rf.read_range(0, 4096)
    assert budget.used == 0
    assert rf.stats["cached_bytes"] == 4096
