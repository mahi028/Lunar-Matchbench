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


# ── Remote ZIP reader ────────────────────────────────────────────────────────

import struct
import zlib

EOCD_SIG = b"PK\x05\x06"
CEN_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"
EOCD_PROBE = 65536           # enough for the EOCD plus a normal comment
INFLATE_CHUNK = 4 * 1024 * 1024   # compressed bytes pulled per inflate step


class Ch2ZipStream:
    """Read individual members out of a remote ZIP without downloading it all.

    ISSDC CH2 products store their raster DEFLATE-compressed, so the image
    cannot be seeked into -- but the geometry CSV that says WHICH scan line a
    coordinate falls on is under a megabyte, and that is the part the pipeline
    needs first. Reading it costs two small ranged reads instead of 508 MB.

    For the raster itself, `img_lines` inflates from the member start and stops
    as soon as the requested lines have been produced. Cost is therefore
    proportional to how deep the target scan line sits in the strip: cheap near
    the beginning, worst-case the whole member near the end. That is a real
    limitation of DEFLATE, not an implementation shortcut.

    Offsets are validated at both levels -- the central directory must actually
    begin with a central-directory signature, and each member's local header
    must begin with a local-header signature. A real archive failed exactly
    this way after a bad resume duplicated 240,007,832 bytes at its front: the
    directory still listed every member, but every offset in it pointed into
    the middle of compressed data.
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
    def _read_central_directory(self) -> dict:
        size = self.rf.size
        probe_len = min(EOCD_PROBE, size)
        tail = self.rf.read_range(size - probe_len, probe_len)
        idx = tail.rfind(EOCD_SIG)
        if idx < 0:
            raise RangeNotHonoured(self.rf.url, size - probe_len, probe_len,
                                   "no End Of Central Directory record found")
        cen_size, cen_off = struct.unpack("<II", tail[idx + 12: idx + 20])
        cen = self.rf.read_range(cen_off, cen_size)

        if not cen.startswith(CEN_SIG):
            raise RangeNotHonoured(
                self.rf.url, cen_off, cen_size,
                "the central directory does not start with a central-directory "
                "signature -- the archive's offsets are shifted, which is what a "
                "bad resume does when it duplicates bytes at the front",
            )

        entries: dict = {}
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
        if hdr[:4] != LFH_SIG:
            raise RangeNotHonoured(
                self.rf.url, entry["local_offset"], 30,
                "local file header magic missing -- the archive is corrupt or "
                "its offsets are shifted",
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
            # Stored: seekable, so read precisely the window.
            raw = self.rf.read_range(data_off + start_line * row, n_lines * row)
            self.inflated_bytes += len(raw)
        else:
            dec = zlib.decompressobj(-zlib.MAX_WBITS)
            out = bytearray()
            pos = 0
            remaining = entry["compress_size"]
            chunk = INFLATE_CHUNK
            while remaining > 0 and len(out) < need_end:
                take = min(chunk, remaining)
                out += dec.decompress(self.rf.read_range(data_off + pos, take))
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
