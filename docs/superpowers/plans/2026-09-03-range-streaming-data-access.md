# Range-Streaming Data Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a registration run fetch only the bytes it needs over HTTP byte ranges instead of downloading whole multi-hundred-megabyte science products, and expose the real tie-point data the interactive UI will need.

**Architecture:** A new `core/streaming.py` provides one primitive — `RangeFile`, a cached HTTP byte-range reader that validates every `Content-Range` it receives — and two readers built on it: `LrocStream` for PDS3 rasters and `Ch2ZipStream` for remote ZIP members. `extract_lroc_patch` is restructured so its coarse-to-fine search runs against a single in-memory buffer rather than issuing a fetch per probe. Local files and remote streams satisfy the same `read_lines` interface, so cached data behaves identically to streamed data.

**Tech Stack:** Python 3.14, requests, numpy, OpenCV, FastAPI, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-streaming-and-interactive-ui-design.md`

## Global Constraints

- **Never send a comma-separated `Range` header.** The PDS host at `pds.lroc.im-ldi.com` ignores multi-range requests and returns the entire body (measured: 528,929,736 bytes, 251 s). One contiguous interval per request, always.
- **Every ranged read validates its response.** Status must be `206` and the `Content-Range` start must equal the requested offset. Anything else raises `RangeNotHonoured`. This is the invariant that prevents the 240 MB corruption already observed in `data_store/ch2/*.CORRUPT`.
- **Preserve the existing honesty contract.** An unconfident scan-line lock must still set `localization_info["confident"] = False`, and a failed run must still emit its step imagery. Do not make failures quieter than they are today.
- `MIN_CONFIDENT_MATCHES` stays at **100**. `MIN_INLIERS` stays at **8**. `PATCH_SIZE` stays at **1024**.
- Network-touching tests are marked `@pytest.mark.network` and skipped by default; the suite must pass offline.
- Python target is `>=3.14` per `pyproject.toml`. Run everything through `.venv/Scripts/python.exe`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/lunar_matchbench/core/streaming.py` *(create)* | `RangeNotHonoured`, `RangeFile`, `parse_pds3_label`, `LrocStream`, `LocalLrocReader`, `Ch2ZipStream` |
| `tests/conftest.py` *(create)* | A local HTTP server fixture that can deliberately mis-serve ranges |
| `tests/test_streaming.py` *(create)* | Unit tests for every streaming primitive |
| `src/lunar_matchbench/config.py` *(modify)* | Cache dir, search margin, inflate budget |
| `src/lunar_matchbench/core/downloader.py` *(modify)* | Geometry-first localisation; delegate label parsing to `streaming.py` |
| `src/lunar_matchbench/core/ch2_fetch.py` *(modify)* | Resume-integrity fix at `_download_file` |
| `src/lunar_matchbench/core/register.py` *(modify)* | Per-point reprojection residuals |
| `src/lunar_matchbench/api/models.py` *(modify)* | `TiePoints`, `TransferStats` schemas |
| `src/lunar_matchbench/api/app.py` *(modify)* | Tie-point payload, patch endpoints, persisted job store |
| `src/lunar_matchbench/cli.py` *(modify)* | `warm` subcommand |

---

## Task 1: `RangeFile` — validated, cached byte-range reads

**Files:**
- Create: `src/lunar_matchbench/core/streaming.py`
- Create: `tests/conftest.py`
- Create: `tests/test_streaming.py`
- Modify: `src/lunar_matchbench/config.py`

**Interfaces:**
- Consumes: `lunar_matchbench.config.CACHE_DIR`
- Produces:
  - `RangeNotHonoured(RuntimeError)` with attributes `url`, `requested_start`, `requested_length`, `detail`
  - `RangeFile(url, session=None, cache_dir=None, headers=None)` with `.size -> int`, `.read_range(offset: int, length: int) -> bytes`, `.stats -> dict` containing keys `fetched_bytes`, `cached_bytes`, `requests`

- [ ] **Step 1: Add config constants**

In `src/lunar_matchbench/config.py`, after the `JOB_DIR` line:

```python
CACHE_DIR    = DATA_ROOT / "cache"        # HTTP byte-range cache
```

Add `CACHE_DIR` to the list inside `ensure_dirs()` so it becomes:

```python
def ensure_dirs() -> None:
    """Create all required directories if absent."""
    for d in [CH2_DATA_DIR, LROC_DATA_DIR, POSTER_DIR, OVERLAP_DIR, JOB_DIR, CACHE_DIR]:
        d.mkdir(parents=True, exist_ok=True)
```

And at the end of the "HTTP download" section add:

```python
# ── Range streaming ──────────────────────────────────────────────────────────
# The PDS host honours single byte-ranges but silently ignores comma-separated
# multi-ranges (it returns the whole body). Never batch ranges.
RANGE_CHUNK_MAX     = 64 * 1024 * 1024   # refuse absurd single reads
LROC_SEARCH_MARGIN  = 0.5                # extra window height, as a fraction of raw_win
MAX_LROC_WINDOWS    = 3                  # hard cap on windows fetched per product
INFLATE_BUDGET      = 600 * 1024 * 1024  # max compressed bytes to stream-inflate
```

- [ ] **Step 2: Write the HTTP fixture**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures.

`range_server` is a real local HTTP server that can be told to misbehave, so
the Content-Range validation in RangeFile is tested against actual protocol
violations rather than mocks.
"""
from __future__ import annotations

import http.server
import threading

import pytest


class RangeServerState:
    """Mutable knobs so a test can make the server misbehave on demand."""

    def __init__(self) -> None:
        # Non-repeating enough that an off-by-N offset error is detectable.
        self.payload = bytes(range(256)) * 4096          # 1 MiB
        self.mode = "honest"                             # honest|ignore_range|wrong_offset
        self.request_log: list[str] = []


STATE = RangeServerState()


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:               # keep pytest output clean
        pass

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(STATE.payload)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        rng = self.headers.get("Range")
        STATE.request_log.append(rng or "")
        total = len(STATE.payload)

        if not rng or STATE.mode == "ignore_range":
            body = STATE.payload
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        start_s, _, end_s = rng.removeprefix("bytes=").partition("-")
        req_start = int(start_s)
        req_end = int(end_s) if end_s else total - 1
        start = 0 if STATE.mode == "wrong_offset" else req_start
        body = STATE.payload[start:start + (req_end - req_start + 1)]

        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{total}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def range_server(tmp_path):
    """Yield (url, STATE, cache_dir). Reset to honest mode for every test."""
    STATE.mode = "honest"
    STATE.request_log.clear()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/data.img", STATE, tmp_path / "cache"
    finally:
        srv.shutdown()
        srv.server_close()
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_streaming.py`:

```python
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
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    first = rf.read_range(2048, 128)
    n_after_first = len(state.request_log)
    second = RangeFile(url, cache_dir=cache).read_range(2048, 128)
    assert first == second
    assert len(state.request_log) == n_after_first
    assert RangeFile(url, cache_dir=cache).stats["requests"] == 0


def test_stats_track_fetched_and_cached(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    rf.read_range(0, 512)
    assert rf.stats["fetched_bytes"] == 512
    rf2 = RangeFile(url, cache_dir=cache)
    rf2.read_range(0, 512)
    assert rf2.stats["cached_bytes"] == 512
    assert rf2.stats["fetched_bytes"] == 0


def test_never_sends_multirange(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    rf.read_range(0, 64)
    rf.read_range(4096, 64)
    assert all("," not in r for r in state.request_log)


def test_refuses_oversized_read(range_server):
    url, state, cache = range_server
    rf = RangeFile(url, cache_dir=cache)
    with pytest.raises(ValueError):
        rf.read_range(0, 1024 * 1024 * 1024)
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lunar_matchbench.core.streaming'`

- [ ] **Step 5: Implement `RangeFile`**

Create `src/lunar_matchbench/core/streaming.py`:

```python
"""
Lunar-MatchBench: HTTP byte-range streaming
============================================
Read only the bytes a registration actually needs, instead of downloading
whole multi-hundred-megabyte science products.

Two hard-won constraints shape this module, both measured against the live
PDS host rather than assumed:

1. Comma-separated multi-range requests are IGNORED -- the server answers 200
   with the entire body. A 3-range request for 3 KB returned 529 MB. So every
   read here is exactly one contiguous interval.

2. A resumed request can come back 206 but starting somewhere other than where
   it was asked to. Appending such a response is what shifted every offset in
   a fetched CH2 zip by 240 MB and made its .img member unreadable. So every
   response's Content-Range is validated against what was requested, and a
   mismatch raises rather than silently returning wrong bytes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

from lunar_matchbench.config import (
    CACHE_DIR, HTTP_TIMEOUT, RANGE_CHUNK_MAX,
)

_CONTENT_RANGE_RE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


class RangeNotHonoured(RuntimeError):
    """The server did not serve the byte range that was requested."""

    def __init__(self, url: str, requested_start: int, requested_length: int, detail: str):
        self.url = url
        self.requested_start = requested_start
        self.requested_length = requested_length
        self.detail = detail
        super().__init__(
            f"Range not honoured for {url}: asked for {requested_length} bytes "
            f"at offset {requested_start}, but {detail}"
        )


class RangeFile:
    """A read-only, file-like view over an HTTP resource, cached on disk."""

    def __init__(self, url: str, session: requests.Session | None = None,
                 cache_dir: Path | None = None, headers: dict | None = None):
        self.url = url
        self._session = session or requests.Session()
        self._headers = headers or {}
        self._cache_dir = Path(cache_dir) if cache_dir is not None else CACHE_DIR
        self._size: int | None = None
        self.stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

    # ── size ────────────────────────────────────────────────────────────────
    @property
    def size(self) -> int:
        if self._size is None:
            self._size = self._probe_size()
        return self._size

    def _probe_size(self) -> int:
        r = self._session.head(self.url, headers=self._headers,
                               timeout=HTTP_TIMEOUT, allow_redirects=True)
        self.stats["requests"] += 1
        if r.status_code == 200 and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
        # Fall back to a one-byte range probe; Content-Range carries the total.
        r = self._session.get(self.url, headers={**self._headers, "Range": "bytes=0-0"},
                              timeout=HTTP_TIMEOUT)
        self.stats["requests"] += 1
        m = _CONTENT_RANGE_RE.search(r.headers.get("Content-Range", ""))
        if not m or m.group(3) == "*":
            raise RangeNotHonoured(self.url, 0, 1, "server did not report a total size")
        return int(m.group(3))

    # ── cache ───────────────────────────────────────────────────────────────
    def _cache_path(self, offset: int, length: int) -> Path:
        key = hashlib.sha256(self.url.encode()).hexdigest()[:16]
        return self._cache_dir / key / f"{offset}-{length}.bin"

    # ── the one read primitive ──────────────────────────────────────────────
    def read_range(self, offset: int, length: int) -> bytes:
        """Return exactly `length` bytes starting at `offset` (or fewer at EOF)."""
        if length <= 0:
            raise ValueError(f"length must be positive, got {length}")
        if length > RANGE_CHUNK_MAX:
            raise ValueError(
                f"refusing a single {length} byte read (max {RANGE_CHUNK_MAX}); "
                "split it into sequential reads"
            )
        if offset < 0:
            raise ValueError(f"offset must be non-negative, got {offset}")

        cached = self._cache_path(offset, length)
        if cached.exists():
            data = cached.read_bytes()
            self.stats["cached_bytes"] += len(data)
            return data

        end = offset + length - 1
        r = self._session.get(
            self.url,
            headers={**self._headers, "Range": f"bytes={offset}-{end}"},
            timeout=HTTP_TIMEOUT,
        )
        self.stats["requests"] += 1

        if r.status_code != 206:
            raise RangeNotHonoured(
                self.url, offset, length,
                f"responded {r.status_code} with {len(r.content)} bytes "
                "(a 200 means the range was ignored and the whole body was sent)",
            )
        m = _CONTENT_RANGE_RE.search(r.headers.get("Content-Range", ""))
        if not m:
            raise RangeNotHonoured(self.url, offset, length, "no parseable Content-Range header")
        got_start = int(m.group(1))
        if got_start != offset:
            raise RangeNotHonoured(
                self.url, offset, length,
                f"served from offset {got_start} instead — appending this "
                "response is what corrupts a resumed download",
            )

        data = r.content
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        self.stats["fetched_bytes"] += len(data)
        return data
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -v`
Expected: PASS — 8 passed

- [ ] **Step 7: Run the whole suite for regressions**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 14 passed

- [ ] **Step 8: Commit**

```bash
git add src/lunar_matchbench/core/streaming.py src/lunar_matchbench/config.py tests/conftest.py tests/test_streaming.py
git commit -m "feat: add validated, cached HTTP byte-range reader"
```

---

## Task 2: Fix the download-resume corruption

**Files:**
- Modify: `src/lunar_matchbench/core/ch2_fetch.py:226-253` (`_download_file`)
- Modify: `tests/test_streaming.py` (append)

**Interfaces:**
- Consumes: `RangeNotHonoured` from Task 1
- Produces: no new public API; `_download_file` gains correctness

- [ ] **Step 1: Write the failing test**

Append to `tests/test_streaming.py`:

```python
def test_resume_discards_partial_when_server_restarts_from_zero(range_server, tmp_path):
    """A 206 that starts at 0 when we asked for N must not be appended.

    This reproduces the bug that made a fetched 713 MB CH2 zip exactly 240 MB
    too large: every central-directory offset was shifted and the .img member
    failed with 'Bad magic number for file header'.
    """
    from lunar_matchbench.core.ch2_fetch import _download_file, _IssdcSession

    url, state, _ = range_server
    state.mode = "wrong_offset"

    dest = tmp_path / "product.zip"
    partial = dest.with_name(dest.name + ".part")
    partial.write_bytes(state.payload[:4096])          # a half-finished download

    session = _IssdcSession.__new__(_IssdcSession)     # bypass Keycloak login
    session.session = __import__("requests").Session()
    session.name = "test"

    assert _download_file(session, url, dest, None) is True
    assert dest.read_bytes() == state.payload, "resumed file must match the source exactly"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py::test_resume_discards_partial_when_server_restarts_from_zero -v`
Expected: FAIL — the written file is longer than `state.payload` because the mis-served body was appended.

- [ ] **Step 3: Implement the fix**

In `src/lunar_matchbench/core/ch2_fetch.py`, replace the body of the `try:` block inside `_download_file`'s retry loop (currently lines 232-249) with:

```python
        try:
            resume_from = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
            with session.request("GET", url, headers=headers, stream=True,
                                  timeout=(30, 600), allow_redirects=True) as r:
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")

                # A resumed request is only safe to append if the server
                # actually starts where we asked. Some servers answer 206 but
                # restart from byte 0 -- appending that duplicates the prefix
                # and silently corrupts the file (observed: a 713 MB zip that
                # was exactly 240 MB too large, with an unreadable .img
                # member). When the offset does not line up, throw the partial
                # away and take the whole body from scratch instead.
                append = False
                if resume_from and r.status_code == 206:
                    m = _CONTENT_RANGE_RE.search(r.headers.get("Content-Range", ""))
                    append = bool(m) and int(m.group(1)) == resume_from

                mode = "ab" if append else "wb"
                downloaded = resume_from if append else 0
                with open(partial, mode) as f:
                    for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb("downloading", downloaded)
            partial.rename(dest)
            return True
```

Add the import near the top of `ch2_fetch.py`, after `from lunar_matchbench.config import CH2_DATA_DIR`:

```python
from lunar_matchbench.core.streaming import _CONTENT_RANGE_RE
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/lunar_matchbench/core/ch2_fetch.py tests/test_streaming.py
git commit -m "fix: discard partial download when a resumed range restarts at zero"
```

---

## Task 3: `parse_pds3_label` + `LrocStream` + `LocalLrocReader`

**Files:**
- Modify: `src/lunar_matchbench/core/streaming.py` (append)
- Modify: `tests/test_streaming.py` (append)

**Interfaces:**
- Consumes: `RangeFile` from Task 1
- Produces:
  - `parse_pds3_label(raw: bytes) -> dict` with keys `total_lines`, `total_samples`, `label_records`, `record_bytes`, `data_offset`, `product_id`, `start_time`, `dataset_id`
  - `LrocStream(rf: RangeFile)` and `LrocStream.open(url, cache_dir=None) -> LrocStream`
  - `LocalLrocReader(path: Path)`
  - Both readers expose `total_lines: int`, `total_samples: int`, `read_lines(start_line: int, n_lines: int) -> np.ndarray` returning `float32` with PDS null values (`< -32752`) replaced by `nan`, and `stats: dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_streaming.py`:

```python
import numpy as np

from lunar_matchbench.core.streaming import (
    LocalLrocReader, LrocStream, parse_pds3_label,
)

PDS3_LABEL = (
    b"PDS_VERSION_ID = PDS3\r\n"
    b"RECORD_BYTES = 20\r\n"
    b"LABEL_RECORDS = 1\r\n"
    b"^IMAGE = 2\r\n"
    b"PRODUCT_ID = \"nactest0001\"\r\n"
    b"START_TIME = 2009-08-15T00:00:00\r\n"
    b"DATA_SET_ID = \"LRO-L-LROC-3-CDR-V1.0\"\r\n"
    b"OBJECT = IMAGE\r\n"
    b"  LINES = 8\r\n"
    b"  LINE_SAMPLES = 10\r\n"
    b"  SAMPLE_BITS = 16\r\n"
    b"END\r\n"
)


def _synthetic_pds3(tmp_path):
    """A tiny but structurally real PDS3 product: 1 label record + 8x10 int16."""
    label = PDS3_LABEL.ljust(20, b" ")
    pixels = np.arange(80, dtype="<i2").reshape(8, 10)
    pixels[0, 0] = -32768                       # a PDS null
    path = tmp_path / "tiny.IMG"
    path.write_bytes(label + pixels.tobytes())
    return path, pixels


def test_parse_pds3_label_reads_geometry():
    label = parse_pds3_label(PDS3_LABEL)
    assert label["total_lines"] == 8
    assert label["total_samples"] == 10
    assert label["label_records"] == 1
    assert label["record_bytes"] == 20
    assert label["data_offset"] == 20
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


def test_local_reader_clamps_past_eof(tmp_path):
    path, _ = _synthetic_pds3(tmp_path)
    window = LocalLrocReader(path).read_lines(6, 10)
    assert window.shape == (2, 10)


def test_stream_and_local_readers_agree(tmp_path, monkeypatch):
    """The streaming reader must produce byte-identical results to the local one."""
    import http.server
    import threading

    path, pixels = _synthetic_pds3(tmp_path)
    blob = path.read_bytes()

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
            s, e = int(s), int(e)
            body = blob[s:e + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {s}-{s + len(body) - 1}/{len(blob)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/tiny.IMG"
        stream = LrocStream.open(url, cache_dir=tmp_path / "c")
        assert stream.total_lines == 8
        np.testing.assert_array_equal(
            stream.read_lines(2, 3), LocalLrocReader(path).read_lines(2, 3)
        )
    finally:
        srv.shutdown()
        srv.server_close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -k "pds3 or reader or agree" -v`
Expected: FAIL — `ImportError: cannot import name 'parse_pds3_label'`

- [ ] **Step 3: Implement the readers**

Append to `src/lunar_matchbench/core/streaming.py`:

```python
# ── PDS3 raster readers ──────────────────────────────────────────────────────

PDS3_LABEL_PROBE = 65536     # the attached label always fits comfortably in 64 KB
PDS_NULL_FLOOR = -32752      # values at or below this are PDS nulls, not terrain


def parse_pds3_label(raw: bytes) -> dict:
    """Parse the key geometry fields out of an attached PDS3 label.

    Takes bytes rather than a path so a local file and a 64 KB ranged read of a
    remote product go through exactly the same parser.
    """
    hdr = raw.decode("latin-1", errors="replace")

    def _get(key: str, default):
        m = re.search(rf"\b{key}\s*=\s*\"?([^\r\n\"]+)\"?", hdr)
        return m.group(1).strip() if m else default

    label_records = int(_get("LABEL_RECORDS", 2))
    record_bytes = int(_get("RECORD_BYTES", 5064 * 2))
    return {
        "total_lines":   int(_get("LINES", 48128)),
        "total_samples": int(_get("LINE_SAMPLES", 5064)),
        "label_records": label_records,
        "record_bytes":  record_bytes,
        "data_offset":   label_records * record_bytes,
        "product_id":    _get("PRODUCT_ID", ""),
        "start_time":    _get("START_TIME", ""),
        "dataset_id":    _get("DATA_SET_ID", ""),
    }


def _decode_lines(raw: bytes, samples: int) -> "np.ndarray":
    """int16 LSB bytes -> float32 (lines, samples) with PDS nulls as nan."""
    import numpy as np
    n_lines = len(raw) // (samples * 2)
    if n_lines == 0:
        return np.empty((0, samples), dtype=np.float32)
    arr = np.frombuffer(raw[: n_lines * samples * 2], dtype="<i2")
    arr = arr.reshape(n_lines, samples).astype(np.float32)
    arr[arr <= PDS_NULL_FLOOR] = np.nan
    return arr


class LocalLrocReader:
    """Read line windows from a PDS3 product already on disk."""

    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            self.label = parse_pds3_label(f.read(PDS3_LABEL_PROBE))
        self.total_lines = self.label["total_lines"]
        self.total_samples = self.label["total_samples"]
        self.stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

    def read_lines(self, start_line: int, n_lines: int):
        start_line = max(0, start_line)
        n_lines = max(0, min(n_lines, self.total_lines - start_line))
        if n_lines == 0:
            return _decode_lines(b"", self.total_samples)
        row = self.total_samples * 2
        with open(self.path, "rb") as f:
            f.seek(self.label["data_offset"] + start_line * row)
            raw = f.read(n_lines * row)
        return _decode_lines(raw, self.total_samples)


class LrocStream:
    """Read line windows from a PDS3 product over HTTP byte ranges.

    Deliberately exposes the same surface as LocalLrocReader so the pipeline
    does not care which one it was handed.
    """

    def __init__(self, rf: RangeFile):
        self.rf = rf
        self.label = parse_pds3_label(rf.read_range(0, PDS3_LABEL_PROBE))
        self.total_lines = self.label["total_lines"]
        self.total_samples = self.label["total_samples"]

    @classmethod
    def open(cls, url: str, session=None, cache_dir=None) -> "LrocStream":
        return cls(RangeFile(url, session=session, cache_dir=cache_dir))

    @property
    def stats(self) -> dict:
        return self.rf.stats

    def read_lines(self, start_line: int, n_lines: int):
        start_line = max(0, start_line)
        n_lines = max(0, min(n_lines, self.total_lines - start_line))
        if n_lines == 0:
            return _decode_lines(b"", self.total_samples)
        row = self.total_samples * 2
        raw = self.rf.read_range(self.label["data_offset"] + start_line * row, n_lines * row)
        return _decode_lines(raw, self.total_samples)
```

Add `from pathlib import Path` is already imported at the top of the module; confirm it is present.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -v`
Expected: PASS — 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/lunar_matchbench/core/streaming.py tests/test_streaming.py
git commit -m "feat: add PDS3 label parser and local/streaming line-window readers"
```

---

## Task 4: `Ch2ZipStream` — remote ZIP members with early-stop inflation

**Files:**
- Modify: `src/lunar_matchbench/core/streaming.py` (append)
- Modify: `tests/test_streaming.py` (append)

**Interfaces:**
- Consumes: `RangeFile` from Task 1
- Produces: `Ch2ZipStream(rf: RangeFile)` and `Ch2ZipStream.open(url, session=None, cache_dir=None)` with `namelist() -> list[str]`, `member_bytes(name: str) -> bytes`, `member_info(name: str) -> dict`, `img_lines(name: str, samples: int, dtype: str, start_line: int, n_lines: int) -> np.ndarray`, `stats: dict`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_streaming.py`:

```python
import io
import zipfile

from lunar_matchbench.core.streaming import Ch2ZipStream


def _zip_blob() -> tuple[bytes, bytes]:
    """A zip shaped like a CH2 product: a small CSV and a large DEFLATE raster."""
    raster = np.arange(200 * 40, dtype="<u2").tobytes()   # 200 lines x 40 samples
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("geometry/grid.csv", "Longitude,Latitude,Pixel,Scan\n1,2,3,4\n")
        z.writestr("data/scene_d_img.img", raster)
    return buf.getvalue(), raster


def _serve(blob, tmp_path):
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
            e = min(int(e), len(blob) - 1)
            body = blob[s:e + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {s}-{s + len(body) - 1}/{len(blob)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/p.zip"


def test_zip_stream_lists_members(tmp_path):
    blob, _ = _zip_blob()
    srv, url = _serve(blob, tmp_path)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        assert "geometry/grid.csv" in z.namelist()
        assert "data/scene_d_img.img" in z.namelist()
    finally:
        srv.shutdown(); srv.server_close()


def test_zip_stream_reads_small_member_without_whole_file(tmp_path):
    blob, _ = _zip_blob()
    srv, url = _serve(blob, tmp_path)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        csv_bytes = z.member_bytes("geometry/grid.csv")
        assert csv_bytes.startswith(b"Longitude,Latitude,Pixel,Scan")
        assert z.stats["fetched_bytes"] < len(blob), "must not have pulled the whole zip"
    finally:
        srv.shutdown(); srv.server_close()


def test_zip_stream_img_lines_match_source(tmp_path):
    blob, raster = _zip_blob()
    srv, url = _serve(blob, tmp_path)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        got = z.img_lines("data/scene_d_img.img", samples=40, dtype="<u2",
                          start_line=5, n_lines=3)
        expected = np.frombuffer(raster, dtype="<u2").reshape(200, 40)[5:8]
        np.testing.assert_array_equal(got, expected.astype(np.float32))
    finally:
        srv.shutdown(); srv.server_close()


def test_zip_stream_img_lines_stop_early(tmp_path):
    """Reading line 0 must inflate far less than reading the whole member."""
    blob, _ = _zip_blob()
    srv, url = _serve(blob, tmp_path)
    try:
        z = Ch2ZipStream.open(url, cache_dir=tmp_path / "c")
        z.img_lines("data/scene_d_img.img", samples=40, dtype="<u2",
                    start_line=0, n_lines=1)
        assert z.inflated_bytes < 200 * 40 * 2
    finally:
        srv.shutdown(); srv.server_close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -k zip -v`
Expected: FAIL — `ImportError: cannot import name 'Ch2ZipStream'`

- [ ] **Step 3: Implement `Ch2ZipStream`**

Append to `src/lunar_matchbench/core/streaming.py`:

```python
# ── Remote ZIP reader ────────────────────────────────────────────────────────

import struct
import zlib

EOCD_SIG = b"PK\x05\x06"
CEN_SIG = b"PK\x01\x02"
EOCD_PROBE = 65536           # enough for the EOCD plus a normal comment


class Ch2ZipStream:
    """Read individual members out of a remote ZIP without downloading it all.

    ISSDC CH2 products store their raster DEFLATE-compressed, so the image
    cannot be seeked into -- but the geometry CSV that says WHICH scan line a
    coordinate falls on is under a megabyte, and that is the part the pipeline
    needs first. Reading it costs two small ranged reads instead of 713 MB.

    For the raster itself, `img_lines` inflates from the member start and stops
    as soon as the requested lines have been produced. Cost is therefore
    proportional to how deep the target scan line sits in the strip: cheap near
    the beginning, worst-case the whole member near the end. That is a real
    limitation of DEFLATE, not an implementation shortcut.
    """

    def __init__(self, rf: RangeFile):
        self.rf = rf
        self.inflated_bytes = 0
        self._entries = self._read_central_directory()

    @classmethod
    def open(cls, url: str, session=None, cache_dir=None) -> "Ch2ZipStream":
        return cls(RangeFile(url, session=session, cache_dir=cache_dir))

    @property
    def stats(self) -> dict:
        return self.rf.stats

    def namelist(self) -> list[str]:
        return list(self._entries)

    def member_info(self, name: str) -> dict:
        return self._entries[name]

    # ── central directory ───────────────────────────────────────────────────
    def _read_central_directory(self) -> dict[str, dict]:
        size = self.rf.size
        probe_len = min(EOCD_PROBE, size)
        tail = self.rf.read_range(size - probe_len, probe_len)
        idx = tail.rfind(EOCD_SIG)
        if idx < 0:
            raise RangeNotHonoured(self.rf.url, size - probe_len, probe_len,
                                   "no End Of Central Directory record found")
        cen_size, cen_off = struct.unpack("<II", tail[idx + 12: idx + 20])
        cen = self.rf.read_range(cen_off, cen_size)

        entries: dict[str, dict] = {}
        p = 0
        while p + 46 <= len(cen) and cen[p:p + 4] == CEN_SIG:
            method, = struct.unpack("<H", cen[p + 10: p + 12])
            comp_size, uncomp_size = struct.unpack("<II", cen[p + 20: p + 28])
            n_len, x_len, c_len = struct.unpack("<HHH", cen[p + 28: p + 34])
            local_off, = struct.unpack("<I", cen[p + 42: p + 46])
            name = cen[p + 46: p + 46 + n_len].decode("utf-8", errors="replace")
            entries[name] = {
                "method": method,
                "compress_size": comp_size,
                "file_size": uncomp_size,
                "local_offset": local_off,
            }
            p += 46 + n_len + x_len + c_len
        return entries

    def _data_offset(self, entry: dict) -> int:
        """Resolve a member's payload offset by reading its local file header."""
        hdr = self.rf.read_range(entry["local_offset"], 30)
        if hdr[:4] != b"PK\x03\x04":
            raise RangeNotHonoured(
                self.rf.url, entry["local_offset"], 30,
                "local file header magic missing -- the archive is corrupt or "
                "offsets are shifted (see the resumed-download bug)",
            )
        n_len, x_len = struct.unpack("<HH", hdr[26:30])
        return entry["local_offset"] + 30 + n_len + x_len

    # ── whole small members ─────────────────────────────────────────────────
    def member_bytes(self, name: str) -> bytes:
        entry = self._entries[name]
        raw = self.rf.read_range(self._data_offset(entry), entry["compress_size"])
        if entry["method"] == 0:
            data = raw
        else:
            data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw)
        self.inflated_bytes += len(data)
        return data

    # ── raster line windows with early stop ─────────────────────────────────
    def img_lines(self, name: str, samples: int, dtype: str,
                  start_line: int, n_lines: int):
        import numpy as np

        entry = self._entries[name]
        bps = 1 if dtype == "uint8" else 2
        row = samples * bps
        need_end = (start_line + n_lines) * row
        data_off = self._data_offset(entry)

        if entry["method"] == 0:
            raw = self.rf.read_range(data_off + start_line * row, n_lines * row)
            self.inflated_bytes += len(raw)
        else:
            dec = zlib.decompressobj(-zlib.MAX_WBITS)
            out = bytearray()
            pos = 0
            remaining = entry["compress_size"]
            chunk = 4 * 1024 * 1024
            while remaining > 0 and len(out) < need_end:
                take = min(chunk, remaining)
                part = dec.decompress(self.rf.read_range(data_off + pos, take))
                out += part
                pos += take
                remaining -= take
                if dec.eof:
                    break
            self.inflated_bytes += len(out)
            raw = bytes(out[start_line * row: need_end])

        n = len(raw) // row
        if n == 0:
            return np.empty((0, samples), dtype=np.float32)
        return np.frombuffer(raw[: n * row], dtype=dtype).reshape(n, samples).astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_streaming.py -v`
Expected: PASS — 18 passed

- [ ] **Step 5: Commit**

```bash
git add src/lunar_matchbench/core/streaming.py tests/test_streaming.py
git commit -m "feat: read remote ZIP members with early-stop inflation"
```

---

## Task 5: Geometry-first LROC localisation

**Files:**
- Modify: `src/lunar_matchbench/core/downloader.py:274-291` (delete `_parse_pds3_header`, import from `streaming`)
- Modify: `src/lunar_matchbench/core/downloader.py:293-420` (`extract_lroc_patch`)
- Modify: `src/lunar_matchbench/core/downloader.py:257-272` (`download_lroc` gains a streaming path)
- Modify: `tests/test_core.py` (append)

**Interfaces:**
- Consumes: `LrocStream`, `LocalLrocReader` from Task 3; `MAX_LROC_WINDOWS`, `LROC_SEARCH_MARGIN` from Task 1
- Produces:
  - `open_lroc_reader(candidate: dict, prefer_stream: bool = True) -> LocalLrocReader | LrocStream`
  - `extract_lroc_patch(reader, candidate, lat, lon, ref_patch=None, scale_factor=6, size=PATCH_SIZE) -> tuple[np.ndarray | None, dict]` — **note the first parameter changes from `img_path` to a reader**
  - `localization_info` gains keys `windows_fetched: int`, `window_lines: int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_core.py`:

```python
def test_extract_lroc_patch_caps_windows(tmp_path):
    """With no confident peak anywhere, the search must still stop at the cap."""
    import numpy as np
    from lunar_matchbench.config import MAX_LROC_WINDOWS
    from lunar_matchbench.core.downloader import extract_lroc_patch

    class FlatReader:
        """Featureless terrain: SIFT will never find a confident peak."""
        total_lines = 40000
        total_samples = 1024
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def __init__(self):
            self.calls = 0

        def read_lines(self, start, n):
            self.calls += 1
            n = max(0, min(n, self.total_lines - max(0, start)))
            rng = np.random.default_rng(0)
            return rng.normal(1000, 1, (n, self.total_samples)).astype(np.float32)

    reader = FlatReader()
    candidate = {"lat_min": 14.0, "lat_max": 16.0, "lon_min": 288.0, "lon_max": 290.0}
    ref = np.random.default_rng(1).integers(0, 255, (1024, 1024), dtype=np.uint8)

    patch, info = extract_lroc_patch(reader, candidate, 15.0, 289.0,
                                     ref_patch=ref, scale_factor=1.0)
    assert info["confident"] is False
    assert info["windows_fetched"] <= MAX_LROC_WINDOWS
    assert reader.calls <= MAX_LROC_WINDOWS + 1


def test_extract_lroc_patch_uses_geometry_when_unconfident(tmp_path):
    import numpy as np
    from lunar_matchbench.core.downloader import extract_lroc_patch

    class FlatReader:
        total_lines = 20000
        total_samples = 512
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

        def read_lines(self, start, n):
            n = max(0, min(n, self.total_lines - max(0, start)))
            rng = np.random.default_rng(2)
            return rng.normal(500, 1, (n, self.total_samples)).astype(np.float32)

    candidate = {"lat_min": 10.0, "lat_max": 20.0, "lon_min": 288.0, "lon_max": 290.0}
    _, info = extract_lroc_patch(FlatReader(), candidate, 15.0, 289.0,
                                 ref_patch=None, scale_factor=1.0)
    assert info["used_center_line"] == info["approx_center_line"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_core.py -k lroc -v`
Expected: FAIL — `extract_lroc_patch` still takes a path and `windows_fetched` does not exist.

- [ ] **Step 3: Replace `_parse_pds3_header` with the shared parser**

In `src/lunar_matchbench/core/downloader.py`, delete the whole `_parse_pds3_header` function (lines 274-291) and add to the imports near the top:

```python
from lunar_matchbench.core.streaming import (
    LocalLrocReader, LrocStream, parse_pds3_label,
)
```

Also add to the `from lunar_matchbench.config import (...)` block:

```python
    MAX_LROC_WINDOWS, LROC_SEARCH_MARGIN,
```

- [ ] **Step 4: Add the reader factory**

In `src/lunar_matchbench/core/downloader.py`, immediately after `download_lroc`, add:

```python
def open_lroc_reader(candidate: dict, prefer_stream: bool = True):
    """Return a line-window reader for an LROC product.

    A cached local copy always wins -- it is faster and costs no bandwidth.
    Otherwise the product is read over HTTP byte ranges rather than downloaded,
    which is the difference between ~39 MB and ~529 MB for a TMC-scale window.
    """
    fname = candidate["filename"]
    for d in LROC_SEARCH_DIRS:
        local = d / fname
        if local.exists() and local.stat().st_size > 0:
            return LocalLrocReader(local)
    if not prefer_stream:
        return LocalLrocReader(download_lroc(candidate, verbose=False))
    return LrocStream.open(candidate["url"])
```

- [ ] **Step 5: Rewrite `extract_lroc_patch`**

Replace the entire `extract_lroc_patch` function in `src/lunar_matchbench/core/downloader.py` with:

```python
def extract_lroc_patch(
    reader,
    candidate: dict,
    lat: float,
    lon: float,
    ref_patch: np.ndarray | None = None,
    scale_factor: float = 6,
    size: int = PATCH_SIZE,
) -> tuple[np.ndarray | None, dict]:
    """
    Extract a scale-matched patch from an LROC NAC strip.

    Strategy (geometry-first):
      1. Estimate the centre scan line from the product's own footprint.
      2. Fetch ONE oversized window around that estimate -- a single read.
      3. Run the coarse-to-fine descriptor search entirely INSIDE that buffer.
         Probing in memory is what makes this affordable over HTTP: the old
         approach re-read the source per probe, which is free on a local file
         and ruinous across ~30 network fetches.
      4. Only if no confident peak turns up, slide to an adjacent window --
         capped at MAX_LROC_WINDOWS so a hopeless coordinate cannot run away.

    Returns (patch_or_None, localization_info). `confident` False means the
    patch came from the raw geometry estimate rather than a verified visual
    correlation, so a downstream match failure cannot be read as "genuinely
    hard" without checking it first.
    """
    import cv2

    total_lines = reader.total_lines
    total_samples = reader.total_samples
    raw_win = min(int(round(size * scale_factor)), total_lines)
    raw_win_samples = min(int(round(size * scale_factor)), total_samples)
    margin = int(raw_win * LROC_SEARCH_MARGIN)
    buffer_lines = min(raw_win + 2 * margin, total_lines)

    # ── Geometry estimate ────────────────────────────────────────────────────
    lat_min, lat_max = candidate["lat_min"], candidate["lat_max"]
    lat_frac = (lat_max - lat) / (lat_max - lat_min + 1e-9)
    approx_center = int(np.clip(lat_frac * total_lines,
                                raw_win // 2, max(raw_win // 2, total_lines - raw_win // 2)))

    def _crop(arr: np.ndarray) -> np.ndarray | None:
        """Centre-crop the sample axis, then validate and normalise."""
        if raw_win_samples < total_samples:
            cs = (total_samples - raw_win_samples) // 2
            arr = arr[:, cs:cs + raw_win_samples]
        valid = arr[~np.isnan(arr)]
        if len(valid) < 500:
            return None
        # Percentile stretching would happily turn a dark calibration frame
        # into something that looks fully textured, so screen the raw values
        # for real spatial structure before normalising.
        if not has_real_content(arr):
            return None
        return normalise_uint8(arr)

    MIN_CONFIDENT_MATCHES = 100
    best_center = approx_center
    best_n = 0
    windows_fetched = 0
    chosen: np.ndarray | None = None

    sift = bf = kp_ref = des_ref = None
    if ref_patch is not None:
        sift = cv2.SIFT_create(nfeatures=1500)
        kp_ref, des_ref = sift.detectAndCompute(ref_patch, None)
        if des_ref is None or len(kp_ref) <= 10:
            sift = None
        else:
            bf = cv2.BFMatcher(cv2.NORM_L2)

    # Windows are tried outward from the geometry estimate: the estimate first,
    # then one buffer-height step either side.
    offsets = [0]
    for k in range(1, MAX_LROC_WINDOWS):
        offsets.append(buffer_lines * (k if k % 2 else -k))

    for off in offsets[:MAX_LROC_WINDOWS]:
        buf_start = int(np.clip(approx_center + off - buffer_lines // 2,
                                0, max(0, total_lines - buffer_lines)))
        buf = reader.read_lines(buf_start, buffer_lines)
        windows_fetched += 1
        if buf.shape[0] < raw_win // 2:
            continue

        if sift is None:
            chosen = _crop(buf[:raw_win])
            best_center = buf_start + raw_win // 2
            break

        # Probe centres inside the already-loaded buffer -- no further reads.
        step = LROC_SCAN_STEP
        lo = raw_win // 2
        hi = max(lo + 1, buf.shape[0] - raw_win // 2)
        for local_center in range(lo, hi, step):
            sl = local_center - raw_win // 2
            cand = _crop(buf[sl:sl + raw_win])
            if cand is None:
                continue
            thumb = resize_to(cand, size)
            kp_l, des_l = sift.detectAndCompute(thumb, None)
            if des_l is None or len(kp_l) <= 10:
                continue
            knn = bf.knnMatch(des_ref, des_l, k=2)
            good = [m for m, n in knn if m.distance < 0.78 * n.distance]
            if len(good) > best_n:
                best_n = len(good)
                best_center = buf_start + local_center
                chosen = cand

        if best_n >= MIN_CONFIDENT_MATCHES:
            # Fine pass, still inside this buffer.
            centre_local = best_center - buf_start
            for local_center in range(max(lo, centre_local - step),
                                      min(hi, centre_local + step), 500):
                sl = local_center - raw_win // 2
                cand = _crop(buf[sl:sl + raw_win])
                if cand is None:
                    continue
                thumb = resize_to(cand, size)
                kp_l, des_l = sift.detectAndCompute(thumb, None)
                if des_l is None or len(kp_l) <= 10:
                    continue
                knn = bf.knnMatch(des_ref, des_l, k=2)
                good = [m for m, n in knn if m.distance < 0.78 * n.distance]
                if len(good) > best_n:
                    best_n = len(good)
                    best_center = buf_start + local_center
                    chosen = cand
            break

    confident = best_n >= MIN_CONFIDENT_MATCHES
    if not confident:
        # Nothing convincing anywhere we looked -- fall back to the geometry
        # estimate rather than trusting a noise-level "winner".
        best_center = approx_center
        best_n = 0
        buf_start = int(np.clip(approx_center - raw_win // 2, 0,
                                max(0, total_lines - raw_win)))
        chosen = _crop(reader.read_lines(buf_start, raw_win))

    localization_info = {
        "best_n": best_n,
        "min_confident_matches": MIN_CONFIDENT_MATCHES,
        "confident": confident,
        "approx_center_line": approx_center,
        "used_center_line": best_center,
        "windows_fetched": windows_fetched,
        "window_lines": buffer_lines,
    }
    if chosen is None:
        return None, localization_info
    return resize_to(chosen, size), localization_info
```

- [ ] **Step 6: Update the pipeline call site**

In `src/lunar_matchbench/core/pipeline.py`, replace these two lines inside the candidate loop:

```python
        _progress(3, f"Downloading LROC NAC {candidate['filename']}...")
        path = download_lroc(candidate, verbose=True)
```

with:

```python
        _progress(3, f"Opening LROC NAC {candidate['filename']} (byte-range stream)...")
        path = open_lroc_reader(candidate)
```

and update the import at the top of `pipeline.py` from:

```python
from lunar_matchbench.core.downloader import (
    find_ch2_geometry_match, extract_ch2_patch,
    discover_lroc_products, download_lroc, extract_lroc_patch,
)
```

to:

```python
from lunar_matchbench.core.downloader import (
    find_ch2_geometry_match, extract_ch2_patch,
    discover_lroc_products, open_lroc_reader, extract_lroc_patch,
)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 20 passed

- [ ] **Step 8: Commit**

```bash
git add src/lunar_matchbench/core/downloader.py src/lunar_matchbench/core/pipeline.py tests/test_core.py
git commit -m "feat: geometry-first LROC localisation with in-memory probing"
```

---

## Task 6: Per-point reprojection residuals

**Files:**
- Modify: `src/lunar_matchbench/core/register.py:200-220`
- Modify: `tests/test_core.py` (append)

**Interfaces:**
- Produces: `register()` return dict gains `residuals_px: list[float]`, one entry per raw match, aligned with `mkpts_moving` / `mkpts_ref` / `inlier_mask`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core.py`:

```python
def test_register_returns_residual_per_raw_match():
    """The histogram in the UI needs the full distribution, not just inliers."""
    import cv2
    import numpy as np
    from lunar_matchbench.core.register import register

    rng = np.random.default_rng(7)
    moving = rng.integers(0, 255, (256, 256), dtype=np.uint8)
    moving = cv2.GaussianBlur(moving, (5, 5), 0)
    M = np.float32([[1, 0, 12], [0, 1, -7]])
    reference = cv2.warpAffine(moving, M, (256, 256))

    result = register(moving, reference, matcher="sift")
    assert "residuals_px" in result
    assert len(result["residuals_px"]) == len(result["mkpts_moving"])
    assert all(r >= 0 for r in result["residuals_px"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_core.py::test_register_returns_residual_per_raw_match -v`
Expected: FAIL — `KeyError: 'residuals_px'` / assertion on missing key

- [ ] **Step 3: Implement**

In `src/lunar_matchbench/core/register.py`, replace the metrics block (currently lines 199-207, from `in_m = pts_m[mask_flat]` through the `elapsed = ...` line) with:

```python
    in_m = pts_m[mask_flat]
    in_r = pts_r[mask_flat]

    # Residuals for EVERY raw match, not just the inliers. The inlier subset
    # alone is a censored distribution -- showing it as "the" error histogram
    # would flatter the result by hiding exactly the matches RANSAC threw out.
    all_proj = cv2.perspectiveTransform(pts_m.reshape(-1, 1, 2), H).reshape(-1, 2)
    all_resid = np.sqrt(np.sum((all_proj - pts_r) ** 2, axis=1))
    stage_data["residuals_px"] = [round(float(v), 4) for v in all_resid]

    rmse = float(np.sqrt(np.mean(all_resid[mask_flat] ** 2)))
    uniformity = _spatial_uniformity(in_r, h, w)

    elapsed = round(time.perf_counter() - t0, 3)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 21 passed

- [ ] **Step 5: Commit**

```bash
git add src/lunar_matchbench/core/register.py tests/test_core.py
git commit -m "feat: report reprojection residual for every raw match"
```

---

## Task 7: Transfer accounting through the pipeline

**Files:**
- Modify: `src/lunar_matchbench/core/pipeline.py`
- Modify: `tests/test_core.py` (append)

**Interfaces:**
- Consumes: `reader.stats` from Tasks 3–4
- Produces: `run_pipeline()` return dict gains `transfer: {"fetched_bytes": int, "cached_bytes": int, "product_bytes": int, "requests": int}`; `progress_cb` signature becomes `(step, total, msg, step_images, transfer)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core.py`:

```python
def test_transfer_dict_shape():
    from lunar_matchbench.core.pipeline import _transfer_snapshot

    class R:
        stats = {"fetched_bytes": 10, "cached_bytes": 5, "requests": 2}
        rf = type("F", (), {"size": 999})()

    snap = _transfer_snapshot(R())
    assert snap == {"fetched_bytes": 10, "cached_bytes": 5,
                    "requests": 2, "product_bytes": 999}


def test_transfer_snapshot_handles_local_reader():
    from lunar_matchbench.core.pipeline import _transfer_snapshot

    class Local:
        stats = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0}

    assert _transfer_snapshot(Local())["product_bytes"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_core.py -k transfer -v`
Expected: FAIL — `ImportError: cannot import name '_transfer_snapshot'`

- [ ] **Step 3: Implement**

In `src/lunar_matchbench/core/pipeline.py`, add after the design-token block:

```python
def _transfer_snapshot(reader) -> dict:
    """Byte accounting for the UI, so 'fetched 38.7 MB of 529 MB' is a fact."""
    stats = getattr(reader, "stats", {}) or {}
    rf = getattr(reader, "rf", None)
    return {
        "fetched_bytes": stats.get("fetched_bytes", 0),
        "cached_bytes":  stats.get("cached_bytes", 0),
        "requests":      stats.get("requests", 0),
        "product_bytes": getattr(rf, "size", 0) if rf is not None else 0,
    }
```

Change the `_progress` helper inside `run_pipeline` to carry transfer state:

```python
    transfer: dict = {"fetched_bytes": 0, "cached_bytes": 0, "requests": 0, "product_bytes": 0}

    def _progress(step: int, msg: str):
        if progress_cb:
            progress_cb(step, total_steps, msg, dict(step_images), dict(transfer))
```

After the successful candidate loop (right after `break` resolves, where `loc_info` is assigned), add:

```python
        transfer.update(_transfer_snapshot(path))
```

Add `"transfer": dict(transfer),` to both the SUCCESS and FAILED return dicts of `run_pipeline`.

- [ ] **Step 4: Update the two existing callbacks**

In `src/lunar_matchbench/cli.py`, change:

```python
        def _print_cb(step, total, msg, step_images=None):
```

to:

```python
        def _print_cb(step, total, msg, step_images=None, transfer=None):
```

In `src/lunar_matchbench/api/app.py`, change:

```python
        def _cb(step, total, msg, step_images=None):
            data = {"progress_msg": msg, "progress_step": step}
```

to:

```python
        def _cb(step, total, msg, step_images=None, transfer=None):
            data = {"progress_msg": msg, "progress_step": step}
            if transfer:
                data["transfer"] = transfer
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 23 passed

- [ ] **Step 6: Commit**

```bash
git add src/lunar_matchbench/core/pipeline.py src/lunar_matchbench/cli.py src/lunar_matchbench/api/app.py tests/test_core.py
git commit -m "feat: track fetched vs cached bytes through the pipeline"
```

---

## Task 8: API — tie-points, patches, persisted jobs

**Files:**
- Modify: `src/lunar_matchbench/api/models.py`
- Modify: `src/lunar_matchbench/api/app.py`
- Modify: `src/lunar_matchbench/core/pipeline.py` (save raw patches)
- Modify: `tests/test_api.py` (append)

**Interfaces:**
- Consumes: `residuals_px` (Task 6), `transfer` (Task 7)
- Produces:
  - `RegistrationResult` gains `tiepoints: Optional[TiePoints]`, `homography: Optional[list[list[float]]]`, `patch_size: Optional[int]`, `transfer: Optional[TransferStats]`
  - `GET /api/patch/{job_id}/{which}.png` where `which` is `ch2` | `lroc` | `warped`
  - Job records persist to `outputs/jobs/{job_id}.json`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
def test_patch_endpoint_404s_for_unknown_job():
    response = client.get("/api/patch/nope/ch2.png")
    assert response.status_code == 404


def test_patch_endpoint_rejects_unknown_kind():
    response = client.get("/api/patch/nope/sideways.png")
    assert response.status_code == 422


def test_result_model_accepts_tiepoints():
    from lunar_matchbench.api.models import RegistrationResult, TiePoints

    result = RegistrationResult(
        job_id="abc",
        status="done",
        tiepoints=TiePoints(
            moving=[[1.0, 2.0]], ref=[[3.0, 4.0]],
            inlier_mask=[True], residuals_px=[0.5],
        ),
        homography=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        patch_size=1024,
    )
    assert result.tiepoints.moving == [[1.0, 2.0]]
    assert result.patch_size == 1024
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'TiePoints'`

- [ ] **Step 3: Add the schemas**

In `src/lunar_matchbench/api/models.py`, add before `RegistrationResult`:

```python
class TiePoints(BaseModel):
    """Raw correspondences, so the browser can draw them itself.

    All four lists are the same length and index-aligned: entry i of `moving`
    corresponds to entry i of `ref`, was kept or rejected per `inlier_mask[i]`,
    and reprojects with `residuals_px[i]` error.
    """
    moving:       list[list[float]]
    ref:          list[list[float]]
    inlier_mask:  list[bool]
    residuals_px: list[float]


class TransferStats(BaseModel):
    fetched_bytes: int = 0
    cached_bytes:  int = 0
    requests:      int = 0
    product_bytes: int = 0
```

and add these fields to `RegistrationResult`:

```python
    tiepoints:        Optional[TiePoints]       = None
    homography:       Optional[list[list[float]]] = None
    patch_size:       Optional[int]             = None
    transfer:         Optional[TransferStats]   = None
```

- [ ] **Step 4: Persist raw patches in the pipeline**

In `src/lunar_matchbench/core/pipeline.py`, add near the other helpers:

```python
def _save_raw_patches(ch2: np.ndarray, lroc: np.ndarray, result: dict, label: str) -> dict:
    """Write bare patch PNGs (no chrome) for the browser to composite."""
    out_dir = POSTER_DIR / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"ch2": out_dir / f"{label}_ch2.png", "lroc": out_dir / f"{label}_lroc.png"}
    cv2.imwrite(str(paths["ch2"]), ch2)
    cv2.imwrite(str(paths["lroc"]), lroc)
    if result.get("status") == "SUCCESS":
        h, w = lroc.shape[:2]
        warped = cv2.warpPerspective(ch2, np.array(result["homography"]), (w, h))
        paths["warped"] = out_dir / f"{label}_warped.png"
        cv2.imwrite(str(paths["warped"]), warped)
    return {k: str(v) for k, v in paths.items()}
```

Call it just before the SUCCESS return, and add `"raw_patches": _save_raw_patches(ch2_patch, lroc_patch, result, label),` to both the SUCCESS and FAILED return dicts.

- [ ] **Step 5: Wire up the API**

In `src/lunar_matchbench/api/app.py`, import `Literal` and `json`, then add:

```python
def _persist(job_id: str) -> None:
    """Write a finished job to disk so a browser reload cannot lose it."""
    job = _read(job_id)
    if job is None:
        return
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    serialisable = {k: v for k, v in job.items() if k != "request"}
    (JOB_DIR / f"{job_id}.json").write_text(
        json.dumps(serialisable, default=str), encoding="utf-8"
    )


@app.get("/api/patch/{job_id}/{which}.png")
async def serve_patch(job_id: str, which: Literal["ch2", "lroc", "warped"]):
    job = _read(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    path = (job.get("result", {}).get("raw_patches") or {}).get(which)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail=f"No {which} patch for this job")
    return FileResponse(path, media_type="image/png")
```

Import `JOB_DIR` from config, call `_persist(job_id)` at the end of `_run_pipeline`, and extend `get_result` to populate the new fields from `r["metrics"]`-adjacent data:

```python
    reg = r.get("register_result", {})
    tiepoints = None
    if reg.get("mkpts_moving"):
        tiepoints = TiePoints(
            moving       = reg["mkpts_moving"],
            ref          = reg["mkpts_ref"],
            inlier_mask  = reg.get("inlier_mask", [False] * len(reg["mkpts_moving"])),
            residuals_px = reg.get("residuals_px", [0.0] * len(reg["mkpts_moving"])),
        )
```

To make `register_result` available, add `"register_result": result,` to both return dicts of `run_pipeline`.

Pass `tiepoints=tiepoints`, `homography=reg.get("homography")`, `patch_size=PATCH_SIZE`, and `transfer=TransferStats(**r.get("transfer", {}))` into both `RegistrationResult(...)` constructions.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 26 passed

- [ ] **Step 7: Commit**

```bash
git add src/lunar_matchbench/api/ src/lunar_matchbench/core/pipeline.py tests/test_api.py
git commit -m "feat: expose tie-points, raw patches and transfer stats over the API"
```

---

## Task 9: `warm` CLI subcommand

**Files:**
- Modify: `src/lunar_matchbench/cli.py`
- Modify: `tests/test_core.py` (append)

**Interfaces:**
- Produces: `lunar-matchbench warm [--instrument tmc|ohrc]` pre-fetches the preset coordinates into the range cache

- [ ] **Step 1: Write the failing test**

Append to `tests/test_core.py`:

```python
def test_warm_presets_are_the_ui_presets():
    """If these drift apart, pre-warming silently warms the wrong coordinates."""
    from pathlib import Path
    import re
    from lunar_matchbench.cli import WARM_PRESETS

    html = (Path(__file__).resolve().parents[1]
            / "src/lunar_matchbench/api/templates/index.html").read_text(encoding="utf-8")
    in_html = {(float(a), float(b))
               for a, b in re.findall(r'data-lat="([-\d.]+)"\s+data-lon="([-\d.]+)"', html)}
    assert in_html == set(WARM_PRESETS)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_core.py::test_warm_presets_are_the_ui_presets -v`
Expected: FAIL — `ImportError: cannot import name 'WARM_PRESETS'`

- [ ] **Step 3: Implement**

In `src/lunar_matchbench/cli.py`, add at module level:

```python
# Kept in step with the quick-preset buttons in index.html; a test asserts they
# match, because warming coordinates the demo does not use is worse than not
# warming at all -- it looks prepared and still stalls on stage.
WARM_PRESETS = [
    (15.0, 289.2),
    (10.2, 289.5),
    (5.17879877, 288.954173),
    (3.613415864967716, 289.12239203822105),
]
```

Register the subparser alongside `serve` and `register`:

```python
    p_warm = subparsers.add_parser("warm", help="Pre-fetch preset coordinates into the range cache")
    p_warm.add_argument("--instrument", choices=["tmc", "ohrc"], default="tmc")
```

and handle it in `main`, before the `register` branch:

```python
    if args.command == "warm":
        from lunar_matchbench.core.pipeline import run_pipeline

        print(f"\nWarming {len(WARM_PRESETS)} preset coordinates "
              f"({args.instrument.upper()})...\n")
        for lat, lon in WARM_PRESETS:
            print(f"  {lat:>10.5f} N, {lon:>11.5f} E ... ", end="", flush=True)
            try:
                res = run_pipeline(lat=lat, lon=lon, instrument=args.instrument,
                                    matcher="xfeat")
                t = res.get("transfer", {})
                print(f"{res['status']}  "
                      f"(fetched {t.get('fetched_bytes', 0) / 1e6:.1f} MB, "
                      f"cached {t.get('cached_bytes', 0) / 1e6:.1f} MB)")
            except Exception as exc:
                print(f"SKIPPED ({type(exc).__name__}: {exc})")
        print("\nCache warm. Preset runs will now serve from disk.\n")
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 27 passed

- [ ] **Step 5: Verify the CLI wiring**

Run: `.venv/Scripts/python.exe -m lunar_matchbench.cli warm --help`
Expected: help text listing `--instrument`

- [ ] **Step 6: Commit**

```bash
git add src/lunar_matchbench/cli.py tests/test_core.py
git commit -m "feat: add warm subcommand to pre-fetch preset coordinates"
```

---

## Task 10: CH2 streaming — gated on a PRADAN range-support probe

**Files:**
- Create: `tests/test_pradan_probe.py`
- Modify: `src/lunar_matchbench/core/ch2_fetch.py`
- Modify: `src/lunar_matchbench/core/downloader.py` (`find_ch2_geometry_match`)

**Interfaces:**
- Consumes: `Ch2ZipStream` (Task 4), `_IssdcSession` (existing)
- Produces: `probe_pradan_range_support(session, url) -> dict`; `fetch_ch2_streamed(lat, lon, instrument, progress_cb=None) -> tuple[dict | None, Ch2ZipStream | None]`

> **This task is conditional.** The PDS host was measured and honours single
> ranges. **PRADAN has not been tested.** Step 1 settles it. If PRADAN refuses
> ranges, stop after Step 2 — the bulk download stays, now protected by the
> Task 2 integrity fix, and the reason is recorded. Do not build Steps 3-5 on
> an assumption; that is precisely how the multi-range trap cost 251 seconds.

- [ ] **Step 1: Probe PRADAN for range support**

Create `tests/test_pradan_probe.py`:

```python
"""Does PRADAN honour byte ranges? Everything CH2-streaming depends on this.

Run with:  .venv/Scripts/python.exe -m pytest -m network tests/test_pradan_probe.py -s -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.network


def test_report_pradan_range_support():
    """Reports rather than asserts -- the answer decides the design."""
    from lunar_matchbench.core.ch2_fetch import (
        _IssdcSession, _get_credentials, PRADAN_TRIGGER_URL,
        CH2_LAYERS, WFS_URL, WFS_TRIGGER_URL, _candidate_pradan_paths,
        PRADAN_URL_PREFIX, _product_type, _is_calibrated,
    )

    user, pw = _get_credentials()
    wfs = _IssdcSession(WFS_TRIGGER_URL, WFS_TRIGGER_URL, "chmapbrowse", user, pw)
    pradan = _IssdcSession(PRADAN_TRIGGER_URL, PRADAN_TRIGGER_URL, "pradan", user, pw)

    params = {
        "service": "wfs", "version": "2.0.0", "request": "GetFeature",
        "outputFormat": "application/json",
        "cql_filter": "(BBOX(the_geom,-71.0,14.8,-70.6,15.2))",
        "typeName": ",".join(CH2_LAYERS["tmc"]),
    }
    feats = wfs.get(WFS_URL, params=params, timeout=120).json()["features"]
    url = None
    for f in feats:
        pid = f.get("properties", {}).get("PRODUCT_ID")
        if pid and _product_type(pid) == "TMC" and _is_calibrated(f):
            for rel in _candidate_pradan_paths("TMC", f["properties"]["DOWNLOAD"]):
                url = PRADAN_URL_PREFIX + rel
                break
        if url:
            break
    assert url, "no candidate product found to probe"

    r = pradan.request("GET", url, headers={"Range": "bytes=0-1023"},
                       stream=True, allow_redirects=True)
    status = r.status_code
    crange = r.headers.get("Content-Range")
    clen = r.headers.get("Content-Length")
    r.close()

    print(f"\n  PRADAN range probe -> status={status} "
          f"Content-Range={crange} Content-Length={clen}")
    print(f"  RANGES {'SUPPORTED' if status == 206 and crange else 'NOT SUPPORTED'}\n")
```

- [ ] **Step 2: Run the probe and record the verdict**

Run: `.venv/Scripts/python.exe -m pytest -m network tests/test_pradan_probe.py -s -v`

Record the outcome in the spec under a new "PRADAN range support" bullet in §2,
with the measured status code and headers, exactly as the PDS findings are
recorded. Then commit:

```bash
git add tests/test_pradan_probe.py docs/superpowers/specs/
git commit -m "test: probe and record whether PRADAN honours byte ranges"
```

**If the probe reports NOT SUPPORTED, stop here.** Skip Steps 3-5, and note in
the spec that CH2 must continue to bulk-download, now guarded by the Task 2
integrity check.

- [ ] **Step 3: Add the streamed CH2 fetch (only if ranges are supported)**

In `src/lunar_matchbench/core/ch2_fetch.py`, add:

```python
def fetch_ch2_streamed(lat: float, lon: float, instrument: str,
                        bbox: float = 0.2, progress_cb: ProgressCB | None = None):
    """Locate a CH2 product and open it as a remote ZIP, without downloading it.

    Only the geometry CSV (~0.8 MB) is pulled up front. The raster is inflated
    lazily and only as far as the target scan line, so a coordinate near the
    start of a strip costs a small fraction of the 713 MB product.
    """
    from lunar_matchbench.core.streaming import Ch2ZipStream

    username, password = _get_credentials()
    pradan = _IssdcSession(PRADAN_TRIGGER_URL, PRADAN_TRIGGER_URL, "pradan",
                            username, password)
    for filename, url in _discover_ch2_urls(lat, lon, instrument, bbox,
                                             username, password, progress_cb):
        resolved, throttled = _probe_url(pradan, url)
        if throttled:
            time.sleep(PROBE_THROTTLE_BACKOFF)
            resolved, _ = _probe_url(pradan, url)
        if not resolved:
            continue
        if progress_cb:
            progress_cb("stream", filename)
        return filename, Ch2ZipStream.open(url, session=pradan.session)
    return None, None
```

Extract the WFS discovery half of `fetch_ch2_product` into
`_discover_ch2_urls(...) -> Iterator[tuple[str, str]]` yielding
`(filename, url)` pairs, and have both `fetch_ch2_product` and
`fetch_ch2_streamed` consume it, so discovery logic exists once.

- [ ] **Step 4: Teach `find_ch2_geometry_match` to accept a stream**

In `src/lunar_matchbench/core/downloader.py`, add a sibling function:

```python
def find_ch2_geometry_match_streamed(zstream, lat: float, lon: float,
                                      instrument: str) -> dict | None:
    """Same nearest-grid-point search, against a remote ZIP's geometry CSV."""
    meta = INSTRUMENT_META[instrument]
    csv_names = [n for n in zstream.namelist() if n.endswith(".csv")]
    if not csv_names:
        return None
    text = zstream.member_bytes(csv_names[0]).decode("utf-8", errors="replace")

    best, best_dist = None, float("inf")
    lon_weight = np.cos(np.radians(lat))
    for row in csv.DictReader(io.StringIO(text)):
        try:
            rlat, rlon = float(row["Latitude"]), float(row["Longitude"])
            dist = (rlat - lat) ** 2 + ((rlon - lon) * lon_weight) ** 2
            if dist < best_dist:
                best_dist = dist
                best = {"lat": rlat, "lon": rlon, "scan": int(row["Scan"]),
                        "pixel": int(row["Pixel"]), "dist_deg": dist ** 0.5,
                        "zstream": zstream}
        except (ValueError, KeyError):
            continue

    patch_half_km = PATCH_SIZE * meta["gsd_m"] / 1000 / 2
    if best is None or best["dist_deg"] > patch_half_km / DEG_TO_KM_LAT:
        return None
    xml_names = [n for n in zstream.namelist() if n.lower().endswith(".xml")]
    if xml_names:
        xml = zstream.member_bytes(xml_names[0]).decode("utf-8", errors="replace")
        m = re.search(r"pixel_resolution[^>]*>([\d.]+)<", xml, re.IGNORECASE)
        best["gsd_m"] = float(m.group(1)) if m else meta["gsd_m"]
    else:
        best["gsd_m"] = meta["gsd_m"]
    return best
```

and extend `extract_ch2_patch` to branch on `match.get("zstream")`, using
`zstream.img_lines(img_name, samples, dtype, line_start, size)` instead of the
local `zipfile` read, with identical clamping and `normalise_uint8` handling.

- [ ] **Step 5: Run the suite and commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — all previously passing tests still pass

```bash
git add src/lunar_matchbench/core/
git commit -m "feat: stream CH2 geometry and raster from PRADAN without full download"
```

---

## Task 11: End-to-end verification against live services

**Files:**
- Create: `tests/test_live.py`

**Interfaces:**
- Consumes: everything above

- [ ] **Step 1: Write the network-marked live test**

Create `tests/test_live.py`:

```python
"""Live-service checks. Skipped unless -m network is requested.

Run with:  .venv/Scripts/python.exe -m pytest -m network -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.network

LROC_URL = (
    "https://pds.lroc.im-ldi.com/data/LRO-L-LROC-3-CDR-V1.0/LROLRC_1001/"
    "DATA/COM/2009227/NAC/M104977327LC.IMG"
)


def test_pds_host_still_honours_single_ranges():
    """If this ever fails, the whole streaming design is invalid."""
    from lunar_matchbench.core.streaming import LrocStream

    stream = LrocStream.open(LROC_URL)
    assert stream.total_samples == 5064
    assert stream.total_lines > 1000
    window = stream.read_lines(20000, 64)
    assert window.shape == (64, 5064)
    assert stream.stats["fetched_bytes"] < 2_000_000


def test_streaming_beats_downloading_by_an_order_of_magnitude():
    from lunar_matchbench.core.streaming import LrocStream

    stream = LrocStream.open(LROC_URL)
    stream.read_lines(20000, 1024)
    assert stream.stats["fetched_bytes"] < stream.rf.size / 10
```

- [ ] **Step 2: Register the marker**

In `pyproject.toml`, extend the pytest section:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["src_prot", ".venv", "issdc_ch2_output", "lroc_reference_output"]
markers = ["network: touches live ISSDC/NASA services (deselected by default)"]
addopts = "-m 'not network'"
```

- [ ] **Step 3: Verify the offline suite stays green and fast**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — 27 passed, live tests deselected

- [ ] **Step 4: Run the live checks**

Run: `.venv/Scripts/python.exe -m pytest -m network -v`
Expected: PASS — 2 passed

- [ ] **Step 5: Run a real registration and confirm bytes stayed small**

Run: `.venv/Scripts/python.exe -u -m lunar_matchbench.cli register --lat 15.0 --lon 289.2 --instrument tmc --matcher xfeat`
Expected: completes without downloading a full LROC product; the printed transfer line reports well under 529 MB fetched.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live.py pyproject.toml
git commit -m "test: add opt-in live checks for PDS range support"
```

---

## Self-Review Notes

**Spec coverage:** §3.1 `RangeFile`/`LrocStream`/`Ch2ZipStream` → Tasks 1, 3, 4. §3.2 geometry-first + download integrity + residuals + byte accounting → Tasks 5, 2, 6, 7. §3.3 API → Task 8. §3.5 demo safety → Task 9. §5 testing → distributed across every task plus Task 10.

**Deferred to Plan 2 (UI):** §3.4 in its entirety. This plan deliberately leaves `index.html`, `style.css` and `app.js` untouched; the existing UI keeps working against the extended API because every new field is optional.

**Conditional work:** Task 10 is gated on a measured fact that does not exist yet — whether PRADAN honours byte ranges. The PDS host does; PRADAN is untested. Step 1 of that task settles it and Step 2 records the answer in the spec. If the answer is no, Steps 3-5 are skipped and CH2 keeps bulk-downloading, protected by the Task 2 integrity fix. The plan must not be executed as though the favourable answer were already known.

**Ordering note:** Tasks 1-9 are independent of that verdict and can be completed either way. Task 10's probe can be run early, out of order, since its result changes only how much of Task 10 gets built.

---

## Execution Record (2026-09-03)

All 11 tasks complete. 44 offline tests + 5 live tests passing.

Findings that changed the plan during execution, recorded because each was a
real defect rather than a plan detail:

1. **PRADAN honours byte-ranges** (Task 10's gate). `HEAD` → `200`,
   `Accept-Ranges: bytes`, `Content-Length: 508443288`; `GET bytes=0-1023` →
   `206`. Task 10 therefore proceeded in full.
2. **The corruption diagnosis was confirmed to the byte.** True product
   508,443,288; corrupt local copy 748,451,120; difference 240,007,832 —
   exactly the first member's recorded central-directory offset.
3. **The test suite began downloading for real** once a working `.env` existed.
   `TestClient` runs `BackgroundTasks` inline, so `test_post_registration_job`
   was performing a live ISSDC fetch. Stubbed, and a suite-wide tripwire now
   fails any offline test that reaches a bulk-download entry point.
4. **`LROC_SCAN_STEP` was wrong for in-memory probing.** Its fixed 2500 lines
   was sized for per-read probes and fitted only one probe into a small buffer,
   missing planted matches entirely. The step is now derived from the buffer
   span (`LROC_PROBE_COUNT`).
5. **The 64 MB single-read ceiling rejected a legitimate window.** A real TMC
   window is 78,643,920 bytes. Added `RangeFile.read_span`, which splits into
   sequential single ranges; `read_range` remains the strict primitive.
6. **Transient `RemoteDisconnected` killed a run.** Ranged reads now retry
   transport failures a bounded number of times. Mis-served ranges are still
   never retried — they go straight to validation.
7. **A test fixture, not the code, caused the planted-match failure.** A raw
   low-contrast reference (28 keypoints) was being matched against a
   percentile-stretched candidate (1500 keypoints). Real CH2 references are
   normalised by `extract_ch2_patch`, so production was already symmetric.

### Deferred

Spec §3.4 (the interactive mission-control UI) is Plan 2. This plan leaves
`index.html`, `style.css` and `app.js` untouched; every new API field is
optional, so the existing UI keeps working unchanged.

### Post-execution defects found by the first live run

8. **A truncated local product poisoned every later run.** A killed download
   left `M1359306139LC.IMG` at 201,326,592 bytes of its real 528,929,736 under
   the true product name. `open_lroc_reader` accepted it because it was
   non-empty; reads past its end returned zero lines; the run reported "no
   usable LROC patch". The coordinate the README documents succeeding with 872
   inliers therefore failed for reasons that looked like science.
   `LocalLrocReader.is_complete` now compares the file against the size its own
   PDS3 label declares, and `_http_download` stages through `.part`.
9. **A single run could transfer ~1.1 GB.** Three LROC windows (77-130 MB each)
   across three candidates, plus the CH2 inflate. `TransferBudget` imposes one
   shared ceiling per run and fails with a clear reason. Cached reads are never
   charged so pre-warming cannot exhaust it.

Also verified: the streamed CH2 patch is **byte-identical** to the same patch
read from a complete local archive (same scan/pixel 91700/1900, identical
pixels, 1500 SIFT keypoints each). Streaming is not a lossy shortcut.
