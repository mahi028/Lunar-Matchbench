"""
Lunar-MatchBench: HTTP byte-range streaming
============================================
Read only the bytes a registration actually needs, instead of downloading
whole multi-hundred-megabyte science products.

Two hard-won constraints shape this module, both measured against the live
services rather than assumed:

1. Comma-separated multi-range requests are IGNORED. A request for three 1 KB
   ranges of an LROC NAC product came back 200 with the entire 528,929,736-byte
   body, taking 251 seconds. So every read here is exactly one contiguous
   interval, and there is a test asserting no comma ever reaches the wire.

2. A resumed request can come back 206 but starting somewhere other than where
   it was asked to. Appending such a response is what shifted every offset in a
   fetched CH2 zip by 240 MB and left its .img member unreadable while the
   central directory still looked fine. So every response's Content-Range is
   validated against what was requested, and a mismatch raises rather than
   quietly returning the wrong bytes.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

from lunar_matchbench.config import CACHE_DIR, HTTP_TIMEOUT, RANGE_CHUNK_MAX

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
        # Some hosts refuse HEAD. A one-byte range still reports the total in
        # its Content-Range, so fall back to that rather than giving up.
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
        """Return the bytes at [offset, offset+length), or fewer at EOF."""
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
            raise RangeNotHonoured(self.url, offset, length,
                                   "no parseable Content-Range header")
        got_start = int(m.group(1))
        if got_start != offset:
            raise RangeNotHonoured(
                self.url, offset, length,
                f"served from offset {got_start} instead -- appending this "
                "response is what corrupts a resumed download",
            )

        data = r.content
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        self.stats["fetched_bytes"] += len(data)
        return data


# ── PDS3 raster readers ──────────────────────────────────────────────────────

PDS3_LABEL_PROBE = 65536     # the attached label always fits comfortably in 64 KB
PDS_NULL_FLOOR = -32752      # values at or below this are PDS nulls, not terrain


def parse_pds3_label(raw: bytes) -> dict:
    """Parse the key geometry fields out of an attached PDS3 label.

    Takes bytes rather than a path so a local file and a 64 KB ranged read of a
    remote product go through exactly the same parser -- the two must agree, or
    a streamed patch would silently come from a different part of the strip
    than a cached one.
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


def _decode_lines(raw: bytes, samples: int):
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

    Deliberately exposes the same surface as LocalLrocReader -- total_lines,
    total_samples, read_lines, stats -- so the pipeline cannot tell which one
    it was handed and a cached product behaves identically to a streamed one.
    """

    def __init__(self, rf: RangeFile):
        self.rf = rf
        probe = min(PDS3_LABEL_PROBE, rf.size)
        self.label = parse_pds3_label(rf.read_range(0, probe))
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
        raw = self.rf.read_range(self.label["data_offset"] + start_line * row,
                                 n_lines * row)
        return _decode_lines(raw, self.total_samples)
