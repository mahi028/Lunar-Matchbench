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
