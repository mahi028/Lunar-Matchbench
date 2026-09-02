"""Does PRADAN honour byte ranges? All CH2 streaming depends on the answer.

The NASA PDS host was measured and does support single ranges. ISRO's PRADAN
has not been tested, and assuming it behaves the same way is exactly the kind
of guess that already cost a 529 MB download when multi-range support was
assumed for PDS.

Run with:
    .venv/Scripts/python.exe -m pytest -m network tests/test_pradan_probe.py -s -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.network


def test_report_pradan_range_support():
    """Reports rather than asserts -- the answer decides the design."""
    from lunar_matchbench.core.ch2_fetch import (
        CH2_LAYERS, PRADAN_TRIGGER_URL, PRADAN_URL_PREFIX, WFS_TRIGGER_URL,
        WFS_URL, _candidate_pradan_paths, _get_credentials, _is_calibrated,
        _IssdcSession, _product_type,
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
        props = f.get("properties", {})
        pid = props.get("PRODUCT_ID")
        if not pid or _product_type(pid) != "TMC" or not _is_calibrated(f):
            continue
        for rel in _candidate_pradan_paths("TMC", props.get("DOWNLOAD", "")):
            url = PRADAN_URL_PREFIX + rel
            break
        if url:
            break
    assert url, "no candidate TMC product found to probe"

    print(f"\n  probing: {url}")

    # Full-object HEAD first, to learn the advertised size and Accept-Ranges.
    h = pradan.head(url, timeout=(15, 60), allow_redirects=True)
    print(f"  HEAD          -> {h.status_code} "
          f"Content-Length={h.headers.get('Content-Length')} "
          f"Accept-Ranges={h.headers.get('Accept-Ranges')}")

    r = pradan.request("GET", url, headers={"Range": "bytes=0-1023"},
                       stream=True, allow_redirects=True, timeout=(15, 60))
    status = r.status_code
    crange = r.headers.get("Content-Range")
    clen = r.headers.get("Content-Length")
    r.close()

    supported = status == 206 and bool(crange)
    print(f"  GET bytes=0-1023 -> {status} Content-Range={crange} Content-Length={clen}")
    print(f"\n  VERDICT: PRADAN ranges {'SUPPORTED' if supported else 'NOT SUPPORTED'}\n")
