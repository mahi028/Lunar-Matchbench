# Lunar-MatchBench: Range-Streaming Data Access + Interactive UI

**Date:** 2026-09-03
**Problem statement:** SIH26166 — Multi-modal, sun-angle and scale invariant image
correspondence using Chandrayaan-2 optical images
**Branch:** `feat/streaming-and-interactive-ui`

---

## 1. Motivation

Two problems block the prototype from being demonstrable.

**Bandwidth.** A single registration run currently downloads whole science
products before it can read the few thousand pixel rows it actually needs:

| Product | Full size | Bytes actually needed |
|---|---|---|
| LROC NAC `M104977327LC.IMG` | 529 MB | 38.7 MB (TMC window) / 1.9 MB (OHRC window) |
| CH2 TMC-2 `ch2_tmc_ncf_…zip` | 713 MB | 0.79 MB (geometry CSV) + a line window |

The LROC figures follow from the instrument scale ratio and are worth stating
exactly, because the saving is very different per instrument:

| Instrument | scale | window lines | window bytes | vs 529 MB |
|---|---|---|---|---|
| TMC-2 (5.0 m/px) | 5.0 / 1.341 = 3.73 | 3,818 | 38.7 MB | 13.7× less |
| OHRC (0.25 m/px) | 0.25 / 1.341 = 0.19 | 190 | 1.9 MB | 278× less |

With the search margin described in §3.2 the TMC case doubles to ~77 MB, still
6.8× less than downloading the product. TMC is the expensive case and OHRC is
nearly free; the design should not be described as uniformly cheap.

Downloading gigabytes per coordinate is not viable during a live demo, and it is
not how the tool should work in production either.

**Data corruption.** `core/ch2_fetch.py::_download_file` resumes interrupted
downloads with a `Range: bytes=N-` header and appends the response body whenever
the status is `206`. It never verifies that the returned `Content-Range` actually
begins at `N`. An observed run produced a file exactly 240 MB larger than the
true product: the central directory was intact and the geometry CSV read
correctly, but the `.img` member failed with `BadZipFile: Bad magic number for
file header` because every stored offset was shifted by the duplicated prefix.

A third, softer problem: every visual the UI shows is a server-rendered
matplotlib PNG. The registration result is not explorable, so the strongest
evidence the pipeline produces — where the tie-points are, how tightly they
reproject — cannot be inspected by a viewer.

---

## 2. Empirical findings

These were measured against the live services on 2026-09-02/03 and constrain the
design. They are recorded here because two of them are non-obvious and the design
is invalid if they change.

**LROC PDS host** (`pds.lroc.im-ldi.com`):

- `Accept-Ranges: bytes`. Single-range `GET` returns `206 Partial Content` with
  an accurate `Content-Range`, in roughly 1–5 s.
- **Multi-range requests are not supported.** A request with
  `Range: bytes=0-1023, 2000000-2001023, 4000000-4001023` returned `200` and the
  entire 528,929,736-byte body (251 s). The reader must therefore issue exactly
  one range per request and must never batch ranges.
- A 1024-line window at full sample width cost 10.37 MB versus 528.9 MB for the
  whole file — a 51× reduction.
- The PDS3 attached label is recoverable from the first 64 KB. For the product
  above: `LINES = 52224`, `LINE_SAMPLES = 5064`, `LABEL_RECORDS = 1`,
  `RECORD_BYTES = 5064`, `SAMPLE_BITS = 16`, `SAMPLE_TYPE = LSB_INTEGER`.
- The archive browser at that host is a JavaScript single-page app. Every
  directory path returns the same HTML shell, so there is no directory listing to
  scrape and no cheap browse/thumbnail image to localise against.
- ODE `Product_files` lists only `.IMG`, `.XML`, and derived KML/shapefile
  entries. No downsampled raster is offered.

**ISSDC CH2 zip** (`ch2_tmc_ncf_20191218T1121183775_d_img_gds.zip`):

- Members are `DEFLATE`, not `STORED`, so the image raster has no random access.
- The `.img` member is 1423.02 MB raw / 504.28 MB compressed.
- The geometry CSV member is 2.48 MB raw / **0.79 MB compressed** and contains
  72,980 rows of `Longitude, Latitude, Pixel, Scan`, spanning latitudes
  −0.331 … 29.414 and longitudes 288.657 … 289.853, with scan lines 0 … 177,877.
- A browse JPEG member (3.37 MB) exists but is too coarse for registration.

The consequence: the CH2 geometry lookup is nearly free over the network, while
the raster still requires sequential inflation from the member start.

---

## 3. Design

### 3.1 `core/streaming.py` (new module)

Three classes, each with a single responsibility and no dependency on the
pipeline.

**`RangeFile`** — a read-only, file-like view over an HTTP resource.

- `size` — resolved from `HEAD`, falling back to the `Content-Range` of a
  one-byte probe when `HEAD` is unavailable.
- `read_range(offset, length) -> bytes` — issues exactly one range request.
  **Validates that the response is `206` and that the parsed `Content-Range`
  start equals `offset`**, raising `RangeNotHonoured` otherwise. This is the
  corruption fix, expressed once as an invariant rather than patched at each
  call site.
- Results are cached on disk under `data_store/cache/<sha256(url)>/<offset>-<length>`.
  A cache hit skips the network entirely, which is what makes a pre-warmed
  demo fast.
- Never emits a comma-separated `Range` header. A single request maps to a
  single contiguous byte interval.

**`LrocStream`** — a PDS3 raster reader built on `RangeFile`.

- `open(url)` fetches the first 64 KB and parses the attached label.
  `_parse_pds3_header` moves from `core/downloader.py` into this module and is
  refactored to accept `bytes` rather than a path, so local files and remote
  streams share one parser.
- `read_lines(start_line, n_lines) -> np.ndarray` — one range request covering
  `[offset + start*samples*2, …)`. Sample-axis cropping is applied **after** the
  fetch, because a PDS3 raster is row-major and cropping samples would make the
  byte interval non-contiguous.

**`Ch2ZipStream`** — a remote-zip reader built on `RangeFile`.

- `central_directory()` — reads the trailing 64 KB, locates the End of Central
  Directory record, and parses member offsets, sizes and compression methods.
- `member_bytes(name)` — range-fetches one member's compressed extent and
  inflates it. Used for the geometry CSV and the PDS4 XML label.
- `img_lines(start_line, n_lines)` — streams the `.img` member through an
  incremental decompressor and **stops as soon as the requested line range has
  been produced**. DEFLATE forbids seeking, so cost is proportional to the
  target scan line's depth in the strip: cheap near the start, worst-case the
  full member near the end. This is a real limitation and is reported as such
  in the UI rather than hidden.

### 3.2 Pipeline changes (`core/downloader.py`, `core/pipeline.py`)

**Geometry-first LROC localisation.** `extract_lroc_patch` is restructured so
the coarse-to-fine SIFT search operates on an **in-memory buffer** instead of
re-reading the source for every probe:

1. Compute `approx_center` from the ODE footprint exactly as today.
2. Fetch **one** window of `raw_win + 2 × LROC_SEARCH_MARGIN` lines centred
   there — a single range request. `LROC_SEARCH_MARGIN` defaults to
   `raw_win / 2`, giving a window twice the strictly-needed height: 77 MB for
   TMC-2, 3.8 MB for OHRC. The margin is a config constant so it can be tuned
   down on a constrained connection at the cost of search range.
3. Run the existing coarse and fine scans entirely inside that buffer. No
   network traffic per probe.
4. If `best_n < MIN_CONFIDENT_MATCHES` (100), fetch one adjacent window and
   repeat. **Hard cap of 3 windows per product.**
5. If no window clears the threshold, fall back to the geometry estimate and
   set `localization_info["confident"] = False`, preserving today's honesty
   contract.

The local-file and streaming paths implement the same `read_lines` interface, so
a cached product behaves identically to a streamed one and existing behaviour
does not regress.

**Download integrity.** `ch2_fetch._download_file` adopts the same
`Content-Range` validation. On a mismatch it discards the partial file and
restarts from zero rather than appending, which is what would have prevented the
observed 240 MB corruption.

**Per-point residuals.** `core/register.py` currently computes reprojection
error only for inliers. It is extended to compute a residual for **every** raw
match, so the histogram in the UI is a real distribution rather than a filtered
one. The returned dict gains `residuals_px: list[float]`, aligned with
`mkpts_moving` / `mkpts_ref` / `inlier_mask`.

**Byte accounting.** `RangeFile` tallies bytes fetched and bytes served from
cache. The pipeline's `progress_cb` carries these figures so the UI can state
"10.4 MB fetched of 529 MB total" truthfully.

### 3.3 API changes (`api/app.py`, `api/models.py`)

- `GET /api/result/{job_id}` gains:
  - `tiepoints`: `{moving: [[x,y]…], ref: [[x,y]…], inlier_mask: [bool…], residuals_px: [float…]}`
  - `homography`: 3×3 list
  - `patch_size`: int
  - `transfer`: `{fetched_bytes, cached_bytes, product_bytes}`
- `GET /api/patch/{job_id}/{ch2|lroc|warped}.png` — the bare patches, no
  matplotlib chrome, for canvas compositing. Warped is `ch2` under the estimated
  homography.
- `GET /api/status/{job_id}` gains `transfer` so the live view can show bytes
  accumulating.
- The job store persists to `outputs/jobs/{job_id}.json`. A browser reload during
  a demo no longer loses a completed result.
- Existing poster and overlap endpoints are unchanged. The matplotlib posters
  remain the scientific record and the failure diagnostic.

### 3.4 Frontend (`api/templates/`, `api/static/`)

Visual direction: **mission control**. Near-black ground, precise monospace for
all numerics, ISRO-saffron and NASA-blue as the two mission accents, thin
data-grid rules, and restrained emphasis reserved for genuinely live elements.
Greyscale lunar imagery reads strongly against a dark ground, which the current
cream treatment works against.

Four regions:

1. **Command bar** — coordinate entry, instrument, matcher, presets.
2. **Pipeline theatre** — the eight steps, with the real streamed byte counter
   and a cache-or-live indicator per fetch.
3. **Result stage** — a swipe divider and an opacity cross-fade between
   registered CH2 and reference LROC, plus a canvas tie-point overlay drawn from
   the real `tiepoints` arrays: hoverable, inlier/outlier toggle, coloured by
   reprojection residual. Pan and zoom.
4. **Evidence panel** — residual histogram, a keypoints → raw matches → inliers
   funnel, and the 8×8 spatial-uniformity grid as an occupancy heatmap. Every
   figure is computed from pipeline output; none are illustrative.

`static/app.js` is split into focused modules — `api.js`, `pipeline.js`,
`comparator.js`, `tiepoints.js`, `charts.js` — because the current single
282-line controller would roughly triple in size otherwise.

### 3.5 Demo safety

A `lunar-matchbench warm` CLI subcommand pre-fetches the preset coordinates into
the range cache before a demo. On stage those runs return in seconds; a
coordinate a judge invents still runs genuinely live. The UI always states
whether a given fetch was served from cache or from the network, so the
distinction is visible rather than concealed.

---

## 4. Error handling

- `RangeNotHonoured` — the server ignored or mis-served a range. Surfaced as a
  named failure with the requested and received intervals, not a generic 500.
- A product whose `.img` member would require inflating more than a configurable
  budget is reported to the UI with the estimated cost before it is attempted,
  so a long fetch is a visible choice rather than an apparent hang.
- Existing failure semantics are preserved: an unconfident scan-line lock still
  taints a downstream match failure with the explanation already present in
  `pipeline.py`, and a failed run still emits its step imagery.

---

## 5. Testing

- **`RangeFile` invariants** — offset validation, refusal to batch ranges, cache
  hit/miss accounting. Tested against a local `http.server` fixture that can be
  instructed to mis-serve a range, reproducing the corruption bug directly.
- **`Ch2ZipStream`** — central-directory parsing and early-stop inflation
  verified against a synthetic zip built in the test, asserting that fewer bytes
  are consumed than the full member.
- **`LrocStream`** — label parsing from a synthetic PDS3 header; line-window
  arithmetic against a known raster.
- **Geometry-first search** — asserts the 3-window cap holds and that an
  unconfident result still sets `confident: False`.
- **Residuals** — length equals the raw match count, and inlier residuals match
  the previously reported RMSE.
- **API** — new endpoints return the documented shapes; existing tests continue
  to pass unchanged.

Network-dependent tests are marked and skipped by default so the suite stays
fast and offline-safe.

---

## 6. Out of scope

- User accounts or authentication — confirmed not wanted.
- IIRS support. The problem statement covers it and the pipeline is structured
  to admit it, but this work does not add it.
- Replacing XFeat or adding the physics-guided stability estimator described in
  the team's presentation. This work makes the existing engine usable and
  legible; it does not change the science.
- Deployment changes. The Dockerfile and Railway config are untouched.
