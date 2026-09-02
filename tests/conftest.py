"""Shared pytest fixtures.

`range_server` is a real local HTTP server that can be told to misbehave, so
the Content-Range validation in RangeFile is tested against actual protocol
violations rather than against mocks. The two failure modes it can simulate are
both real ones observed in the wild:

  ignore_range  -- answers 200 with the whole body, as pds.lroc.im-ldi.com does
                   for multi-range requests
  wrong_offset  -- answers 206 but starts at byte 0 regardless of what was
                   asked, which is what corrupted a fetched 713 MB CH2 product
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
        # An open-ended "bytes=N-" is legal and is what the resume path sends.
        req_end = int(end_s) if end_s else total - 1

        if STATE.mode == "wrong_offset":
            # A server that ignores the start offset streams from the beginning
            # rather than sending a truncated slice -- that is what makes the
            # bug so damaging, since the body looks plausible and complete.
            start = 0
            body = STATE.payload[0:req_end + 1]
        else:
            start = req_start
            body = STATE.payload[req_start:req_end + 1]

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
