# Mission-Control UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the print-report web UI with a dark mission-control console in which the registration result is explorable — a real swipe comparator, hoverable tie-points drawn from the pipeline's own arrays, and charts computed from real metrics.

**Architecture:** The page is a two-column console: a full-height scan-line strip locator on the left (the signature element) and a stack of result panels on the right. `app.js` is split into focused ES modules under `static/js/`, and the single `style.css` into three files under `static/css/`. Every visual is driven by the `tiepoints` / `transfer` / `provenance` payloads Plan 1 added; nothing is illustrative.

**Tech Stack:** Vanilla ES modules (no build step, no framework), inline SVG for the locator and charts, `<canvas>` for tie-point overlays, CSS custom properties for tokens. Google Fonts: Archivo (variable, width axis) + IBM Plex Sans/Mono. Playwright for UI tests.

**Spec:** `docs/superpowers/specs/2026-09-03-streaming-and-interactive-ui-design.md` §3.4

## Global Constraints

- **Design direction is mission-control dark**, chosen by the user. Do not revert to the cream/rust print-report palette.
- **Colour encodes mission provenance, never decoration.** `--isro` marks Chandrayaan-2 pixels and CH2-derived data; `--nasa` marks LROC NAC. A colour must never be used for a third meaning.
- **Greyscale lunar imagery is the brightest thing on screen.** Chrome recedes; no glow, no gradient fills behind content, no decorative blur.
- **Every figure comes from pipeline output.** Charts read `tiepoints.residuals_px`, `tiepoints.inlier_mask`, `metrics.*`, `provenance.lroc_localization`. No sample data, no invented axis ranges, no placeholder numbers — including while a run is in flight (show empty state, not fake state).
- **A failed run must never look like a success.** Preserve the Plan 1 behaviour: failed runs still render their tie-points and step imagery, the stepper does not show green ticks, and cache-served runs say so.
- **Quality floor, unannounced:** responsive to 380px, visible keyboard focus on every control, `prefers-reduced-motion` respected, all interactive controls reachable by keyboard.
- No build step and no new Python dependencies. Static assets are served by the existing `/static` mount.
- Run everything through `.venv/Scripts/python.exe`. Offline suite must stay hermetic and network-free.

---

## Design Tokens (authoritative — derive every value from here)

```
--void    #0A0E12   page ground (deep blue-black, not pure black)
--panel   #121820   raised surface
--panel-2 #0E141B   inset wells (image frames, code)
--rule    #1E2833   hairlines and borders
--signal  #E8EDF2   primary text (cool white)
--muted   #7A8794   secondary text, axis labels
--isro    #FF7A45   Chandrayaan-2 / moving image
--nasa    #5B9CFF   LROC NAC / fixed reference
--good    #3FD68C   inlier, confident, success
--warn    #FFC14D   unverified, partial coverage
--bad     #FF5C5C   outlier, failure
```

Type roles:
- **Display** — `Archivo` variable. Two extremes only: wide hairline caps for eyebrows/labels (`wght 400`, `wdth 125`, `letter-spacing .18em`), and heavy for numeric readouts (`wght 700`, `wdth 112`). That contrast is the type personality.
- **Body** — `IBM Plex Sans`, 400/500/600.
- **Utility** — `IBM Plex Mono` for every number, coordinate, line index and byte count, with `font-variant-numeric: tabular-nums` so digits do not jitter as values update.

---

## File Structure

| File | Responsibility |
|---|---|
| `static/css/tokens.css` *(create)* | Palette, type scale, font loading, reset, focus ring, reduced-motion |
| `static/css/console.css` *(create)* | Status bar, two-column console shell, strip-locator rail, responsive collapse |
| `static/css/panels.css` *(create)* | Result panels, comparator frame, chart styling, diagnosis rows |
| `static/js/api.js` *(create)* | `startRun`, `pollStatus`, `fetchResult`, `patchUrl` — all network access |
| `static/js/locator.js` *(create)* | The scan-line strip locator (signature element) |
| `static/js/comparator.js` *(create)* | Swipe divider + opacity fade between two patch images |
| `static/js/tiepoints.js` *(create)* | Canvas tie-point overlay, hover, inlier filter |
| `static/js/charts.js` *(create)* | Residual histogram, funnel, 8x8 coverage grid |
| `static/js/main.js` *(create)* | Wiring, run lifecycle, panel state |
| `templates/index.html` *(rewrite)* | Console markup |
| `static/style.css`, `static/app.js` *(delete, final task)* | Replaced |
| `tests/test_ui.py` *(create)* | Playwright checks against a stubbed job — no network |

---

## Task 1: Design tokens and the console shell

**Files:**
- Create: `src/lunar_matchbench/api/static/css/tokens.css`
- Create: `src/lunar_matchbench/api/static/css/console.css`
- Rewrite: `src/lunar_matchbench/api/templates/index.html`
- Modify: `tests/test_api.py`

**Interfaces:**
- Produces: DOM ids `#run-form`, `#lat`, `#lon`, `#instrument`, `#matcher`, `#run-btn`, `#status-chip`, `#locator`, `#stage`, `#panels`, `#diagnosis`, `#metrics`. Later tasks attach to these exact ids.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_ui_serves_console_shell():
    """The console shell and its module entry point must both be reachable."""
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "Lunar-MatchBench" in body
    for element_id in ("run-form", "locator", "stage", "panels", "status-chip"):
        assert f'id="{element_id}"' in body, f"missing #{element_id}"
    assert 'type="module"' in body
    assert "/static/js/main.js" in body


def test_ui_static_assets_are_served():
    for path in ("/static/css/tokens.css", "/static/css/console.css"):
        assert client.get(path).status_code == 200, path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api.py -k "console_shell or static_assets" -v`
Expected: FAIL — `missing #run-form`, and 404 for the CSS paths.

- [ ] **Step 3: Write `tokens.css`**

Create `src/lunar_matchbench/api/static/css/tokens.css`:

```css
/* Lunar-MatchBench design tokens.
   Colour encodes mission provenance: --isro is always Chandrayaan-2, --nasa is
   always LROC NAC. Neither is ever used decoratively, so a coloured mark on
   this page always answers "which spacecraft did this pixel come from". */

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --void:    #0A0E12;
  --panel:   #121820;
  --panel-2: #0E141B;
  --rule:    #1E2833;
  --signal:  #E8EDF2;
  --muted:   #7A8794;
  --isro:    #FF7A45;
  --nasa:    #5B9CFF;
  --good:    #3FD68C;
  --warn:    #FFC14D;
  --bad:     #FF5C5C;

  --display: "Archivo", "Segoe UI", system-ui, sans-serif;
  --body:    "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --mono:    "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;

  --rail-w: 132px;
  --gap: 18px;
}

html { color-scheme: dark; }

body {
  background: var(--void);
  color: var(--signal);
  font-family: var(--body);
  font-size: 14px;
  line-height: 1.55;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}

/* Wide hairline caps: the silkscreen label on an instrument panel. */
.eyebrow {
  font-family: var(--display);
  font-variation-settings: "wght" 400, "wdth" 125;
  font-size: 10px;
  letter-spacing: .18em;
  text-transform: uppercase;
  color: var(--muted);
}

/* Heavy readouts: the other extreme of the same family. */
.readout {
  font-family: var(--display);
  font-variation-settings: "wght" 700, "wdth" 112;
  font-size: 30px;
  line-height: 1;
  letter-spacing: -.01em;
}

.num { font-family: var(--mono); font-variant-numeric: tabular-nums; }

:where(a, button, input, select, [tabindex]):focus-visible {
  outline: 2px solid var(--nasa);
  outline-offset: 2px;
  border-radius: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    transition-duration: .001ms !important;
  }
}
```

- [ ] **Step 4: Write `console.css`**

Create `src/lunar_matchbench/api/static/css/console.css`:

```css
/* The console shell: a persistent status bar over a rail + working area. */

.statusbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 16px;
  padding: 12px 20px;
  background: color-mix(in srgb, var(--void) 88%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--rule);
}
.wordmark { display: flex; align-items: baseline; gap: 10px; }
.wordmark b {
  font-family: var(--display);
  font-variation-settings: "wght" 700, "wdth" 118;
  font-size: 16px; letter-spacing: .01em;
}
.route { display: flex; align-items: center; gap: 8px; margin-left: 6px; }
.route .from { color: var(--isro); }
.route .to { color: var(--nasa); }
.route .arrow { color: var(--muted); }

.chip {
  margin-left: auto;
  display: inline-flex; align-items: center; gap: 7px;
  padding: 5px 11px; border: 1px solid var(--rule); border-radius: 100px;
  font-family: var(--mono); font-size: 11px; color: var(--muted);
}
.chip .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); }
.chip[data-state="live"] .dot { background: var(--good); }
.chip[data-state="cache"] .dot { background: var(--nasa); }
.chip[data-state="running"] .dot { background: var(--warn); animation: pulse 1.1s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: .25; } }

.console {
  display: grid;
  grid-template-columns: var(--rail-w) minmax(0, 1fr);
  gap: var(--gap);
  max-width: 1440px; margin: 0 auto; padding: var(--gap) 20px 56px;
  align-items: start;
}

.rail {
  position: sticky; top: 66px;
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--panel);
  padding: 14px 12px;
}

.work { display: flex; flex-direction: column; gap: var(--gap); min-width: 0; }

.panel {
  border: 1px solid var(--rule); border-radius: 4px;
  background: var(--panel); padding: 18px 20px;
}
.panel > header {
  display: flex; align-items: baseline; gap: 12px;
  padding-bottom: 12px; margin-bottom: 16px;
  border-bottom: 1px solid var(--rule);
}
.panel > header h2 {
  font-family: var(--display);
  font-variation-settings: "wght" 600, "wdth" 112;
  font-size: 15px; font-weight: 400;
}

.field-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.field label { font-size: 11px; color: var(--muted); letter-spacing: .04em; }
.field input, .field select {
  background: var(--panel-2); border: 1px solid var(--rule); border-radius: 3px;
  color: var(--signal); padding: 9px 11px;
  font-family: var(--mono); font-size: 13.5px;
}
.field input:focus, .field select:focus { border-color: var(--nasa); }

.presets { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; align-items: center; }
.preset {
  background: transparent; border: 1px solid var(--rule); border-radius: 100px;
  color: var(--muted); font-family: var(--body); font-size: 12px;
  padding: 5px 12px; cursor: pointer;
}
.preset:hover { border-color: var(--nasa); color: var(--signal); }

.run {
  margin-top: 16px; width: 100%; padding: 12px;
  background: var(--isro); color: #14100D; border: 0; border-radius: 3px;
  font-family: var(--display); font-variation-settings: "wght" 700, "wdth" 110;
  font-size: 14px; letter-spacing: .03em; cursor: pointer;
}
.run:hover { filter: brightness(1.08); }
.run:disabled { opacity: .45; cursor: not-allowed; }

@media (max-width: 900px) {
  .console { grid-template-columns: 1fr; }
  .rail { position: static; }
  .field-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 480px) {
  .field-grid { grid-template-columns: 1fr; }
  .statusbar { flex-wrap: wrap; }
}
```

- [ ] **Step 5: Rewrite `index.html`**

Replace `src/lunar_matchbench/api/templates/index.html` entirely:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Lunar-MatchBench — Cross-Mission Registration Console</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/css/tokens.css?v=1" />
  <link rel="stylesheet" href="/static/css/console.css?v=1" />
  <link rel="stylesheet" href="/static/css/panels.css?v=1" />
</head>
<body>
  <header class="statusbar">
    <div class="wordmark">
      <b>Lunar-MatchBench</b>
      <span class="route num">
        <span class="from">CH2 TMC-2</span>
        <span class="arrow">&rarr;</span>
        <span class="to">LROC NAC</span>
      </span>
    </div>
    <span class="chip" id="status-chip" data-state="idle">
      <span class="dot"></span><span id="status-text">Idle</span>
    </span>
  </header>

  <main class="console">
    <aside class="rail" id="locator" aria-label="LROC scan-line strip locator"></aside>

    <div class="work">
      <section class="panel">
        <header>
          <span class="eyebrow">01</span>
          <h2>Target</h2>
        </header>
        <form id="run-form">
          <div class="field-grid">
            <div class="field">
              <label for="lat">Latitude &deg;N</label>
              <input type="number" id="lat" step="any" min="-90" max="90" value="15.0" required />
            </div>
            <div class="field">
              <label for="lon">Longitude &deg;E</label>
              <input type="number" id="lon" step="any" min="0" max="360" value="289.2" required />
            </div>
            <div class="field">
              <label for="instrument">CH2 instrument</label>
              <select id="instrument">
                <option value="tmc" selected>TMC-2 &mdash; 5 m/px</option>
                <option value="ohrc">OHRC &mdash; 0.25 m/px</option>
              </select>
            </div>
            <div class="field">
              <label for="matcher">Matcher</label>
              <select id="matcher">
                <option value="xfeat" selected>XFeat</option>
                <option value="sift">SIFT</option>
              </select>
            </div>
          </div>
          <div class="presets">
            <span class="eyebrow">Presets</span>
            <button type="button" class="preset" data-lat="15.0" data-lon="289.2">Oceanus Procellarum</button>
            <button type="button" class="preset" data-lat="10.2" data-lon="289.5">Sinus Aestuum</button>
            <button type="button" class="preset" data-lat="5.17879877" data-lon="288.954173">Rayed crater 5.2&deg;N</button>
            <button type="button" class="preset" data-lat="3.613415864967716" data-lon="289.12239203822105">Known failure 3.6&deg;N</button>
          </div>
          <button type="submit" class="run" id="run-btn">Run registration</button>
        </form>
      </section>

      <section class="panel" id="stage" hidden>
        <header>
          <span class="eyebrow">02</span>
          <h2>Alignment</h2>
          <div class="stage-tools" id="stage-tools"></div>
        </header>
        <div id="stage-body"></div>
      </section>

      <section class="panel" id="panels" hidden>
        <header>
          <span class="eyebrow">03</span>
          <h2>Evidence</h2>
        </header>
        <div id="diagnosis"></div>
        <div id="metrics"></div>
        <div id="charts"></div>
        <details class="prov">
          <summary>Provenance &amp; ephemeris</summary>
          <pre id="prov-pre" class="num"></pre>
        </details>
      </section>
    </div>
  </main>

  <script type="module" src="/static/js/main.js?v=1"></script>
</body>
</html>
```

- [ ] **Step 6: Create placeholder module and remaining stylesheet so the page loads**

Create `src/lunar_matchbench/api/static/css/panels.css` with just a comment line `/* panels — filled in Task 5 */` and `src/lunar_matchbench/api/static/js/main.js` with `console.info("Lunar-MatchBench console booting");`. Both are replaced by later tasks; they exist now only so Task 1's deliverable renders without a 404.

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS — all previously passing tests plus the two new ones. The old `test_serve_ui` asserts `"Registration Parameters" in response.text`; update that assertion to `"Target" in response.text` in the same commit, since the heading genuinely changed.

- [ ] **Step 8: Screenshot the shell**

Run: `.venv/Scripts/python.exe <scratchpad>/shot.py <scratchpad>/mc_01_shell.png http://127.0.0.1:8000/ 1440 900 full`
Confirm: dark ground, wide-caps eyebrows, saffron run button, rail visible on the left.

- [ ] **Step 9: Commit**

```bash
git add src/lunar_matchbench/api tests/test_api.py
git commit -m "feat: mission-control console shell and design tokens"
```

---

## Task 2: The scan-line strip locator (signature element)

**Files:**
- Create: `src/lunar_matchbench/api/static/js/locator.js`
- Modify: `src/lunar_matchbench/api/static/css/console.css`
- Create: `tests/test_ui.py`

**Interfaces:**
- Consumes: `provenance.lroc_localization` — `{total_lines, approx_center_line, used_center_line, lines_searched, strip_fraction_searched, whole_strip_searched, best_n, min_confident_matches, confident, windows_fetched, window_lines}`
- Produces: `renderLocator(el, loc)` and `clearLocator(el)` exported from `locator.js`

Why this element exists: both instruments are pushbroom line scanners, and the pipeline's hardest problem is choosing one line out of tens of thousands. A sentence saying "searched 100% of the strip" is easy to miss; a rail showing the whole strip with the searched band and the locked line on it is not.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui.py`:

```python
"""UI checks driven by Playwright against a stubbed job.

The job is injected straight into the app's in-memory store, so these run
offline and never touch ISSDC or NASA.
"""
from __future__ import annotations

import threading
import time

import pytest
import uvicorn

pytestmark = pytest.mark.ui

DONE_JOB = {
    "status": "done",
    "step_image_urls": {},
    "result": {
        "metrics": {
            "matcher": "XFEAT", "n_inliers": 523, "n_raw_matches": 1280,
            "inlier_ratio_pct": 40.86, "rmse_px": 1.5322,
            "spatial_uniformity": 0.625, "elapsed_sec": 6.14,
        },
        "register_result": {
            "mkpts_moving": [[100.0, 120.0], [300.0, 400.0], [700.0, 220.0]],
            "mkpts_ref": [[104.0, 118.0], [297.0, 403.0], [900.0, 100.0]],
            "inlier_mask": [True, True, False],
            "residuals_px": [0.8, 1.4, 47.2],
            "homography": [[1, 0, 4], [0, 1, -2], [0, 0, 1]],
        },
        "transfer": {"fetched_bytes": 82_000_000, "cached_bytes": 0,
                     "requests": 3, "product_bytes": 528_929_736},
        "provenance": {
            "ch2_instrument": "Terrain Mapping Camera-2 (Fore View)",
            "lroc_product_id": "nac.m1359306139lc",
            "lroc_localization": {
                "best_n": 185, "min_confident_matches": 100, "confident": True,
                "approx_center_line": 24146, "used_center_line": 26251,
                "windows_fetched": 1, "window_lines": 8429,
                "total_lines": 52224, "lines_searched": 8429,
                "strip_fraction_searched": 0.161, "whole_strip_searched": False,
            },
        },
    },
}


@pytest.fixture(scope="module")
def live_server():
    from lunar_matchbench.api.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield "http://127.0.0.1:8765"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def page(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 950})
        yield pg
        browser.close()


def _seed(job_id: str, payload: dict) -> None:
    from lunar_matchbench.api import app as app_mod
    app_mod._store(job_id, payload)


def test_locator_marks_geometry_estimate_and_lock(page, live_server):
    _seed("uitest01", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest01", wait_until="networkidle")
    page.wait_for_selector("#locator svg", timeout=15000)

    text = page.inner_text("#locator")
    assert "52224" in text.replace(",", ""), "strip length must be shown"
    assert page.locator("#locator [data-mark='estimate']").count() == 1
    assert page.locator("#locator [data-mark='lock']").count() == 1
    assert page.locator("#locator [data-mark='searched']").count() >= 1


def test_locator_flags_a_fully_searched_strip(page, live_server):
    import copy

    failed = copy.deepcopy(DONE_JOB)
    failed["status"] = "failed"
    failed["error"] = "Too few raw matches: 5"
    loc = failed["result"]["provenance"]["lroc_localization"]
    loc.update(confident=False, best_n=0, total_lines=15360, lines_searched=15360,
               strip_fraction_searched=1.0, whole_strip_searched=True,
               windows_fetched=3, window_lines=9217)
    _seed("uitest02", failed)

    page.goto(f"{live_server}/?job=uitest02", wait_until="networkidle")
    page.wait_for_selector("#locator svg", timeout=15000)
    assert page.locator("#locator").get_attribute("data-coverage") == "full"
```

Register the marker in `pyproject.toml`:

```toml
markers = [
    "network: touches live ISSDC/NASA services (deselected by default)",
    "downloads: legitimately exercises the bulk-download path",
    "ui: drives a headless browser against a stubbed job (deselected by default)",
]
addopts = "-m 'not network and not ui'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: FAIL — `#locator svg` never appears (no locator module, and `?job=` is not handled yet).

- [ ] **Step 3: Write `locator.js`**

Create `src/lunar_matchbench/api/static/js/locator.js`:

```js
// The scan-line strip locator.
//
// Both instruments are pushbroom line scanners: an LROC NAC strip is tens of
// thousands of single scan lines, and the pipeline's hardest job is deciding
// which one corresponds to the Chandrayaan-2 patch. This rail draws the whole
// strip to scale, so "the geometry estimate was 2,105 lines off" and "the
// entire strip was searched and nothing matched" are things you can see rather
// than sentences you have to read.

const NS = "http://www.w3.org/2000/svg";

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

export function clearLocator(root) {
  root.innerHTML = `
    <div class="eyebrow">Scan-line strip</div>
    <p class="rail-empty">Run a registration to locate the reference scan line.</p>`;
  root.removeAttribute("data-coverage");
}

export function renderLocator(root, loc) {
  if (!loc || !loc.total_lines) return clearLocator(root);

  const H = 460, W = 26, X = 34;
  const total = loc.total_lines;
  const y = (line) => Math.max(0, Math.min(H, (line / total) * H));

  root.setAttribute("data-coverage", loc.whole_strip_searched ? "full" : "partial");
  root.innerHTML = `<div class="eyebrow">Scan-line strip</div>`;

  const svg = el("svg", {
    viewBox: `0 0 108 ${H + 34}`, width: "100%", role: "img",
    "aria-label":
      `LROC strip of ${total} lines; ${loc.lines_searched} searched; ` +
      `reference line ${loc.used_center_line}`,
  });
  const g = el("g", { transform: "translate(0,16)" });

  // The strip itself.
  g.appendChild(el("rect", {
    x: X, y: 0, width: W, height: H, rx: 2,
    fill: "var(--panel-2)", stroke: "var(--rule)",
  }));

  // Searched band(s). Windows are contiguous around the estimate, so one band
  // centred on the searched span is a truthful summary.
  const searched = Math.min(loc.lines_searched || 0, total);
  if (searched > 0) {
    const centre = loc.used_center_line ?? loc.approx_center_line ?? total / 2;
    const lo = Math.max(0, centre - searched / 2);
    g.appendChild(el("rect", {
      "data-mark": "searched",
      x: X, y: y(lo), width: W, height: Math.max(2, y(lo + searched) - y(lo)),
      fill: "var(--nasa)", opacity: ".22",
    }));
  }

  // Geometry estimate: where the pushbroom maths said to look.
  const est = loc.approx_center_line ?? 0;
  g.appendChild(el("line", {
    "data-mark": "estimate",
    x1: X - 7, y1: y(est), x2: X + W + 7, y2: y(est),
    stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "3 3",
  }));

  // The lock: where correlation actually put it.
  const lock = loc.used_center_line ?? est;
  const lockColour = loc.confident ? "var(--good)" : "var(--warn)";
  g.appendChild(el("line", {
    "data-mark": "lock",
    x1: X - 10, y1: y(lock), x2: X + W + 10, y2: y(lock),
    stroke: lockColour, "stroke-width": 2,
  }));
  g.appendChild(el("circle", {
    cx: X + W + 10, cy: y(lock), r: 3, fill: lockColour,
  }));

  // Endpoints, so the rail reads as a real coordinate axis.
  const cap = (yy, text) => {
    const t = el("text", {
      x: X - 10, y: yy, "text-anchor": "end",
      fill: "var(--muted)", "font-size": "9",
      "font-family": "var(--mono)",
    });
    t.textContent = text;
    return t;
  };
  g.appendChild(cap(4, "0"));
  g.appendChild(cap(H, String(total)));

  svg.appendChild(g);
  root.appendChild(svg);

  const drift = Math.abs(lock - est);
  const facts = document.createElement("dl");
  facts.className = "rail-facts num";
  facts.innerHTML = `
    <div><dt>Lock</dt><dd style="color:${lockColour}">${loc.confident ? "verified" : "estimate"}</dd></div>
    <div><dt>Line</dt><dd>${lock.toLocaleString()}</dd></div>
    <div><dt>Drift</dt><dd>${drift.toLocaleString()}</dd></div>
    <div><dt>Searched</dt><dd>${Math.round((loc.strip_fraction_searched || 0) * 100)}%</dd></div>
    <div><dt>Peak</dt><dd>${loc.best_n} / ${loc.min_confident_matches}</dd></div>`;
  root.appendChild(facts);
}
```

- [ ] **Step 4: Add rail styling**

Append to `src/lunar_matchbench/api/static/css/console.css`:

```css
.rail-empty { margin-top: 10px; font-size: 11.5px; color: var(--muted); line-height: 1.5; }
.rail svg { display: block; margin: 10px 0 6px; overflow: visible; }
.rail-facts { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.rail-facts > div { display: flex; justify-content: space-between; gap: 8px; font-size: 11px; }
.rail-facts dt { color: var(--muted); }
.rail-facts dd { color: var(--signal); }
.rail[data-coverage="full"] { border-color: color-mix(in srgb, var(--warn) 45%, var(--rule)); }
```

- [ ] **Step 5: Add the `?job=` rehydration path to `main.js`**

Replace `src/lunar_matchbench/api/static/js/main.js`:

```js
import { fetchResult } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";

const locator = document.getElementById("locator");
clearLocator(locator);

// Rehydrating from ?job= makes a finished run linkable and reloadable, and is
// how the UI tests drive real payloads without touching the network.
const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchResult(jobFromUrl)
    .then((data) => renderLocator(locator, data?.provenance?.lroc_localization))
    .catch(() => clearLocator(locator));
}
```

Create `src/lunar_matchbench/api/static/js/api.js`:

```js
// Every network call the console makes lives here.

export async function startRun({ lat, lon, instrument, matcher }) {
  const resp = await fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, instrument, matcher }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || "Could not start the run.");
  }
  return resp.json();
}

export async function fetchStatus(jobId) {
  const resp = await fetch(`/api/status/${jobId}`);
  if (!resp.ok) throw new Error("Lost contact with the run.");
  return resp.json();
}

export async function fetchResult(jobId) {
  const resp = await fetch(`/api/result/${jobId}`);
  if (!resp.ok) throw new Error("Could not read the result.");
  return resp.json();
}

export function patchUrl(jobId, which) {
  return `/api/patch/${jobId}/${which}.png`;
}
```

- [ ] **Step 6: Run the UI tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 7: Confirm the offline suite is unaffected**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, with the ui tests deselected.

- [ ] **Step 8: Commit**

```bash
git add src/lunar_matchbench/api tests/test_ui.py pyproject.toml
git commit -m "feat: scan-line strip locator showing search span and lock"
```

---

## Task 3: Swipe and fade comparator

**Files:**
- Create: `src/lunar_matchbench/api/static/js/comparator.js`
- Create: `src/lunar_matchbench/api/static/css/panels.css` (replacing the Task 1 placeholder)
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `patchUrl(jobId, which)` from `api.js`; `which` is `"ch2" | "lroc" | "warped"`
- Produces: `mountComparator(container, { jobId, mode })` returning `{ setMode(mode), setSplit(fraction), destroy() }`, where `mode` is `"swipe" | "fade"` and `fraction` is 0–1

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_comparator_exposes_both_frames_and_a_draggable_split(page, live_server):
    _seed("uitest03", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest03", wait_until="networkidle")
    page.wait_for_selector(".cmp", timeout=15000)

    assert page.locator(".cmp-img[data-layer='reference']").count() == 1
    assert page.locator(".cmp-img[data-layer='moving']").count() == 1

    handle = page.locator(".cmp-handle")
    assert handle.count() == 1
    box = page.locator(".cmp").bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 0.25, box["y"] + box["height"] / 2)
    page.mouse.up()
    split = page.evaluate("getComputedStyle(document.querySelector('.cmp')).getPropertyValue('--split')")
    assert float(split.strip()) < 0.45, f"split did not follow the drag: {split}"


def test_comparator_switches_to_fade(page, live_server):
    _seed("uitest04", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest04", wait_until="networkidle")
    page.wait_for_selector(".cmp", timeout=15000)
    page.click("[data-mode='fade']")
    assert page.locator(".cmp").get_attribute("data-mode") == "fade"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -k comparator -v`
Expected: FAIL — `.cmp` never appears.

- [ ] **Step 3: Write `comparator.js`**

Create `src/lunar_matchbench/api/static/js/comparator.js`:

```js
// Swipe/fade comparator between the registered CH2 patch and the LROC reference.
//
// This is the claim the whole tool makes -- "these two images of the same
// ground line up" -- so it has to be inspectable rather than asserted. Both
// layers occupy the same grid cell so the frame height tracks the imagery and
// switching modes never shifts the layout.

import { patchUrl } from "./api.js";

export function mountComparator(container, { jobId, mode = "swipe" }) {
  container.innerHTML = `
    <div class="cmp" data-mode="${mode}" style="--split:.5">
      <img class="cmp-img" data-layer="reference" alt="LROC NAC reference patch"
           src="${patchUrl(jobId, "lroc")}" />
      <img class="cmp-img" data-layer="moving" alt="Chandrayaan-2 patch registered onto the LROC frame"
           src="${patchUrl(jobId, "warped")}" />
      <div class="cmp-handle" role="slider" tabindex="0" aria-label="Comparison split"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="50"></div>
      <span class="cmp-tag cmp-tag--l">LROC NAC</span>
      <span class="cmp-tag cmp-tag--r">CH2 TMC-2</span>
    </div>`;

  const cmp = container.querySelector(".cmp");
  const handle = container.querySelector(".cmp-handle");
  let dragging = false;

  function setSplit(fraction) {
    const f = Math.max(0, Math.min(1, fraction));
    cmp.style.setProperty("--split", String(f));
    handle.setAttribute("aria-valuenow", String(Math.round(f * 100)));
  }

  function pointToSplit(clientX) {
    const rect = cmp.getBoundingClientRect();
    return (clientX - rect.left) / rect.width;
  }

  cmp.addEventListener("pointerdown", (e) => {
    if (cmp.dataset.mode !== "swipe") return;
    dragging = true;
    cmp.setPointerCapture(e.pointerId);
    setSplit(pointToSplit(e.clientX));
  });
  cmp.addEventListener("pointermove", (e) => {
    if (dragging) setSplit(pointToSplit(e.clientX));
  });
  cmp.addEventListener("pointerup", (e) => {
    dragging = false;
    if (cmp.hasPointerCapture?.(e.pointerId)) cmp.releasePointerCapture(e.pointerId);
  });

  // Keyboard parity: the split is a real slider, not a mouse-only affordance.
  handle.addEventListener("keydown", (e) => {
    const current = parseFloat(cmp.style.getPropertyValue("--split")) || 0.5;
    const stepBy = e.shiftKey ? 0.1 : 0.02;
    if (e.key === "ArrowLeft") { setSplit(current - stepBy); e.preventDefault(); }
    if (e.key === "ArrowRight") { setSplit(current + stepBy); e.preventDefault(); }
    if (e.key === "Home") { setSplit(0); e.preventDefault(); }
    if (e.key === "End") { setSplit(1); e.preventDefault(); }
  });

  return {
    setMode(next) { cmp.dataset.mode = next; },
    setSplit,
    destroy() { container.innerHTML = ""; },
  };
}
```

- [ ] **Step 4: Write `panels.css`**

Replace `src/lunar_matchbench/api/static/css/panels.css`:

```css
/* Result panels: comparator, evidence rows, charts. */

.stage-tools { margin-left: auto; display: flex; gap: 6px; }
.tool {
  background: transparent; border: 1px solid var(--rule); border-radius: 3px;
  color: var(--muted); font-family: var(--body); font-size: 11.5px;
  padding: 4px 10px; cursor: pointer;
}
.tool[aria-pressed="true"] { border-color: var(--nasa); color: var(--signal); }

.cmp {
  position: relative; display: grid; width: 100%;
  background: var(--panel-2); border: 1px solid var(--rule); border-radius: 3px;
  overflow: hidden; touch-action: none; cursor: ew-resize;
}
.cmp-img { grid-area: 1 / 1; width: 100%; height: auto; display: block; user-select: none; }

/* Swipe: the moving layer is revealed left of the split. */
.cmp[data-mode="swipe"] .cmp-img[data-layer="moving"] {
  clip-path: inset(0 calc(100% - var(--split) * 100%) 0 0);
}
.cmp[data-mode="swipe"] .cmp-handle {
  position: absolute; top: 0; bottom: 0;
  left: calc(var(--split) * 100%); width: 2px; margin-left: -1px;
  background: var(--signal); cursor: ew-resize;
}
.cmp[data-mode="swipe"] .cmp-handle::after {
  content: ""; position: absolute; top: 50%; left: 50%;
  width: 26px; height: 26px; transform: translate(-50%, -50%);
  border: 2px solid var(--signal); border-radius: 50%;
  background: color-mix(in srgb, var(--void) 55%, transparent);
}

/* Fade: opacity cross-dissolve instead of a hard edge. */
.cmp[data-mode="fade"] .cmp-img[data-layer="moving"] { opacity: var(--split); }
.cmp[data-mode="fade"] .cmp-handle { display: none; }

.cmp-tag {
  position: absolute; bottom: 10px; padding: 3px 9px; border-radius: 100px;
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .04em;
  background: color-mix(in srgb, var(--void) 72%, transparent);
}
.cmp-tag--l { left: 10px; color: var(--nasa); }
.cmp-tag--r { right: 10px; color: var(--isro); }

.prov { margin-top: 18px; }
.prov summary { cursor: pointer; font-size: 12px; color: var(--muted); }
.prov pre {
  margin-top: 10px; padding: 14px; border: 1px solid var(--rule); border-radius: 3px;
  background: var(--panel-2); color: var(--muted); font-size: 11.5px;
  overflow-x: auto; line-height: 1.7;
}
```

- [ ] **Step 5: Mount the comparator from `main.js`**

Replace the body of `main.js`'s rehydration branch:

```js
import { fetchResult, patchUrl } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";
import { mountComparator } from "./comparator.js";

const locator = document.getElementById("locator");
const stage = document.getElementById("stage");
const stageBody = document.getElementById("stage-body");
const stageTools = document.getElementById("stage-tools");
clearLocator(locator);

let comparator = null;

function renderStage(jobId) {
  stage.hidden = false;
  stageTools.innerHTML = `
    <button type="button" class="tool" data-mode="swipe" aria-pressed="true">Swipe</button>
    <button type="button" class="tool" data-mode="fade" aria-pressed="false">Fade</button>`;
  comparator = mountComparator(stageBody, { jobId, mode: "swipe" });

  stageTools.addEventListener("click", (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    comparator.setMode(btn.dataset.mode);
    stageTools.querySelectorAll(".tool").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
  });
}

const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchResult(jobFromUrl)
    .then((data) => {
      renderLocator(locator, data?.provenance?.lroc_localization);
      renderStage(jobFromUrl);
    })
    .catch(() => clearLocator(locator));
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: PASS — 4 passed.

- [ ] **Step 7: Commit**

```bash
git add src/lunar_matchbench/api tests/test_ui.py
git commit -m "feat: swipe and fade comparator over the registered patches"
```

---

## Task 4: Tie-point canvas overlay

**Files:**
- Create: `src/lunar_matchbench/api/static/js/tiepoints.js`
- Modify: `src/lunar_matchbench/api/static/css/panels.css`
- Modify: `src/lunar_matchbench/api/static/js/main.js`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `result.tiepoints` — `{moving: [[x,y]...], ref: [[x,y]...], inlier_mask: [bool], residuals_px: [float]}` and `result.patch_size`
- Produces: `mountTiePoints(container, { tiepoints, patchSize })` returning `{ setFilter(filter), destroy() }` where `filter` is `"all" | "inliers" | "outliers"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_tiepoint_overlay_reports_real_counts(page, live_server):
    _seed("uitest05", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest05", wait_until="networkidle")
    page.wait_for_selector("canvas.tp-canvas", timeout=15000)

    legend = page.inner_text(".tp-legend")
    assert "2" in legend and "3" in legend, f"expected 2 inliers of 3 points: {legend}"

    page.click("[data-filter='inliers']")
    assert page.locator(".tp").get_attribute("data-filter") == "inliers"


def test_tiepoint_hover_reads_out_a_residual(page, live_server):
    _seed("uitest06", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest06", wait_until="networkidle")
    page.wait_for_selector("canvas.tp-canvas", timeout=15000)
    # Point 0 sits at (100,120) in a 1024 patch; hover its drawn position.
    canvas = page.locator("canvas.tp-canvas").bounding_box()
    page.mouse.move(canvas["x"] + canvas["width"] * (100 / 1024),
                    canvas["y"] + canvas["height"] * (120 / 1024))
    page.wait_for_timeout(250)
    assert "px" in page.inner_text(".tp-readout")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -k tiepoint -v`
Expected: FAIL — no `canvas.tp-canvas`.

- [ ] **Step 3: Write `tiepoints.js`**

Create `src/lunar_matchbench/api/static/js/tiepoints.js`:

```js
// Tie-point overlay drawn from the pipeline's own correspondence arrays.
//
// The matcher's output used to reach the screen only as a rendered picture.
// Drawing it here means a viewer can filter it, hover a single point and read
// its reprojection error -- the difference between being shown a result and
// being able to interrogate one.

const COLOURS = { inlier: "#3FD68C", outlier: "#FF5C5C" };

export function mountTiePoints(container, { tiepoints, patchSize }) {
  const pts = tiepoints?.moving || [];
  const n = pts.length;
  const inliers = (tiepoints?.inlier_mask || []).filter(Boolean).length;

  container.innerHTML = `
    <div class="tp" data-filter="all">
      <div class="tp-bar">
        <div class="tp-legend num">
          <span class="tp-key tp-key--in">${inliers} kept</span>
          <span class="tp-key tp-key--out">${n - inliers} rejected</span>
          <span class="tp-total">of ${n}</span>
        </div>
        <div class="tp-filters">
          <button type="button" class="tool" data-filter="all" aria-pressed="true">All</button>
          <button type="button" class="tool" data-filter="inliers" aria-pressed="false">Inliers</button>
          <button type="button" class="tool" data-filter="outliers" aria-pressed="false">Outliers</button>
        </div>
      </div>
      <div class="tp-frame">
        <canvas class="tp-canvas" width="${patchSize}" height="${patchSize}"></canvas>
        <output class="tp-readout num" aria-live="polite"></output>
      </div>
    </div>`;

  const root = container.querySelector(".tp");
  const canvas = container.querySelector(".tp-canvas");
  const readout = container.querySelector(".tp-readout");
  const ctx = canvas.getContext("2d");
  let filter = "all";

  const visible = (i) => {
    const isIn = !!tiepoints.inlier_mask?.[i];
    return filter === "all" || (filter === "inliers") === isIn;
  };

  function draw(highlight = -1) {
    ctx.clearRect(0, 0, patchSize, patchSize);
    for (let i = 0; i < n; i++) {
      if (!visible(i)) continue;
      const [x, y] = pts[i];
      const isIn = !!tiepoints.inlier_mask?.[i];
      const [rx, ry] = tiepoints.ref?.[i] || [x, y];

      // The displacement each correspondence claims, drawn from moving to ref.
      ctx.strokeStyle = isIn ? COLOURS.inlier : COLOURS.outlier;
      ctx.globalAlpha = i === highlight ? 1 : 0.45;
      ctx.lineWidth = i === highlight ? 3 : 1.25;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(rx, ry);
      ctx.stroke();

      ctx.globalAlpha = 1;
      ctx.fillStyle = isIn ? COLOURS.inlier : COLOURS.outlier;
      ctx.beginPath();
      ctx.arc(x, y, i === highlight ? 6 : 3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function nearest(px, py) {
    let best = -1, bestDist = 24 * 24;
    for (let i = 0; i < n; i++) {
      if (!visible(i)) continue;
      const dx = pts[i][0] - px, dy = pts[i][1] - py;
      const d = dx * dx + dy * dy;
      if (d < bestDist) { bestDist = d; best = i; }
    }
    return best;
  }

  canvas.addEventListener("pointermove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * patchSize;
    const py = ((e.clientY - rect.top) / rect.height) * patchSize;
    const i = nearest(px, py);
    if (i < 0) {
      readout.textContent = "";
    } else {
      const r = tiepoints.residuals_px?.[i];
      const isIn = !!tiepoints.inlier_mask?.[i];
      readout.textContent =
        `#${i} · ${isIn ? "kept" : "rejected"} · ${r?.toFixed(2)} px reprojection error`;
    }
    draw(i);
  });
  canvas.addEventListener("pointerleave", () => { readout.textContent = ""; draw(); });

  root.querySelector(".tp-filters").addEventListener("click", (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    filter = btn.dataset.filter;
    root.dataset.filter = filter;
    root.querySelectorAll(".tp-filters .tool").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    draw();
  });

  draw();
  return { setFilter(f) { filter = f; draw(); }, destroy() { container.innerHTML = ""; } };
}
```

- [ ] **Step 4: Style the overlay**

Append to `src/lunar_matchbench/api/static/css/panels.css`:

```css
.tp { margin-top: 16px; }
.tp-bar { display: flex; align-items: center; gap: 14px; margin-bottom: 10px; flex-wrap: wrap; }
.tp-legend { display: flex; align-items: center; gap: 14px; font-size: 11.5px; }
.tp-key { display: inline-flex; align-items: center; gap: 6px; }
.tp-key::before { content: ""; width: 8px; height: 8px; border-radius: 50%; }
.tp-key--in { color: var(--good); }
.tp-key--in::before { background: var(--good); }
.tp-key--out { color: var(--bad); }
.tp-key--out::before { background: var(--bad); }
.tp-total { color: var(--muted); }
.tp-filters { margin-left: auto; display: flex; gap: 6px; }
.tp-frame { position: relative; border: 1px solid var(--rule); border-radius: 3px; background: var(--panel-2); }
.tp-canvas { display: block; width: 100%; height: auto; }
.tp-readout {
  position: absolute; left: 10px; bottom: 10px;
  padding: 4px 10px; border-radius: 100px; font-size: 11px;
  background: color-mix(in srgb, var(--void) 78%, transparent); color: var(--signal);
  min-height: 20px; pointer-events: none;
}
```

- [ ] **Step 5: Mount it in `main.js`**

In `renderStage`, after the comparator is mounted, add a container and call:

```js
const tpHost = document.createElement("div");
stageBody.appendChild(tpHost);
if (data.tiepoints) {
  mountTiePoints(tpHost, {
    tiepoints: data.tiepoints,
    patchSize: data.patch_size || 1024,
  });
}
```

Change `renderStage(jobId)` to `renderStage(jobId, data)` and update its call site, and import `mountTiePoints` from `./tiepoints.js`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/lunar_matchbench/api tests/test_ui.py
git commit -m "feat: hoverable tie-point overlay drawn from real correspondences"
```

---

## Task 5: Evidence charts

**Files:**
- Create: `src/lunar_matchbench/api/static/js/charts.js`
- Modify: `src/lunar_matchbench/api/static/css/panels.css`
- Modify: `src/lunar_matchbench/api/static/js/main.js`
- Modify: `tests/test_ui.py`

> **Before writing any chart code, load the `dataviz` skill.** It governs chart
> form, colour and axis rules, and this task creates three charts.

**Interfaces:**
- Consumes: `result.metrics`, `result.tiepoints.residuals_px`, `result.tiepoints.inlier_mask`, `result.provenance.lroc_localization`
- Produces: `renderCharts(container, result)` from `charts.js`, drawing a residual histogram, a keypoints→matches→inliers funnel, and an 8×8 coverage grid

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_charts_render_from_real_metrics(page, live_server):
    _seed("uitest07", DONE_JOB)
    page.goto(f"{live_server}/?job=uitest07", wait_until="networkidle")
    page.wait_for_selector(".chart-histogram", timeout=15000)

    assert page.locator(".chart-histogram rect").count() >= 1
    assert page.locator(".chart-funnel .funnel-stage").count() == 3
    # 8x8 uniformity grid from GRID_CELLS in config
    assert page.locator(".chart-grid .grid-cell").count() == 64

    funnel = page.inner_text(".chart-funnel")
    assert "1280" in funnel and "523" in funnel


def test_charts_are_absent_without_tiepoints(page, live_server):
    """No data must render as an empty state, never as an invented chart."""
    import copy

    bare = copy.deepcopy(DONE_JOB)
    bare["result"]["register_result"] = {}
    _seed("uitest08", bare)
    page.goto(f"{live_server}/?job=uitest08", wait_until="networkidle")
    page.wait_for_selector("#charts", timeout=15000)
    assert page.locator(".chart-histogram rect").count() == 0
    assert "No correspondence data" in page.inner_text("#charts")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -k charts -v`
Expected: FAIL — no `.chart-histogram`.

- [ ] **Step 3: Write `charts.js`**

Create `src/lunar_matchbench/api/static/js/charts.js`:

```js
// Evidence charts. Every value here is computed from pipeline output; when the
// data is missing the panel says so rather than drawing an empty axis that
// implies a measurement was taken.

const NS = "http://www.w3.org/2000/svg";
const GRID_CELLS = 8;   // matches GRID_CELLS in config.py

function svg(width, height, className) {
  const s = document.createElementNS(NS, "svg");
  s.setAttribute("viewBox", `0 0 ${width} ${height}`);
  s.setAttribute("class", className);
  s.setAttribute("width", "100%");
  return s;
}

function rect(attrs) {
  const r = document.createElementNS(NS, "rect");
  for (const [k, v] of Object.entries(attrs)) r.setAttribute(k, v);
  return r;
}

function histogram(residuals, mask, threshold) {
  const W = 460, H = 150, PAD = 26;
  const wrap = document.createElement("figure");
  wrap.className = "chart";
  wrap.innerHTML = `<figcaption class="eyebrow">Reprojection error, all ${residuals.length} matches</figcaption>`;

  // Clip the long outlier tail at the 98th percentile so the inlier bulk stays
  // readable, and say so rather than silently truncating.
  const sorted = [...residuals].sort((a, b) => a - b);
  const hi = sorted[Math.floor(sorted.length * 0.98)] || 1;
  const BINS = 28;
  const binned = new Array(BINS).fill(0);
  const binnedIn = new Array(BINS).fill(0);
  residuals.forEach((r, i) => {
    const b = Math.min(BINS - 1, Math.floor((r / hi) * BINS));
    if (b < 0) return;
    binned[b] += 1;
    if (mask?.[i]) binnedIn[b] += 1;
  });
  const peak = Math.max(...binned, 1);

  const s = svg(W, H, "chart-histogram");
  const bw = (W - PAD * 2) / BINS;
  for (let b = 0; b < BINS; b++) {
    const x = PAD + b * bw;
    const total = (binned[b] / peak) * (H - PAD * 1.4);
    const kept = (binnedIn[b] / peak) * (H - PAD * 1.4);
    if (total > 0) {
      s.appendChild(rect({
        x: x + 0.5, y: H - PAD - total, width: bw - 1, height: total,
        fill: "var(--bad)", opacity: ".55",
      }));
    }
    if (kept > 0) {
      s.appendChild(rect({
        x: x + 0.5, y: H - PAD - kept, width: bw - 1, height: kept,
        fill: "var(--good)",
      }));
    }
  }

  // RANSAC's acceptance threshold, the reason the split exists.
  if (threshold && threshold < hi) {
    const tx = PAD + (threshold / hi) * (W - PAD * 2);
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", tx); line.setAttribute("x2", tx);
    line.setAttribute("y1", PAD * 0.4); line.setAttribute("y2", H - PAD);
    line.setAttribute("stroke", "var(--muted)");
    line.setAttribute("stroke-dasharray", "3 3");
    s.appendChild(line);
  }

  const axis = document.createElementNS(NS, "text");
  axis.setAttribute("x", W - PAD); axis.setAttribute("y", H - 8);
  axis.setAttribute("text-anchor", "end");
  axis.setAttribute("fill", "var(--muted)");
  axis.setAttribute("font-size", "10");
  axis.setAttribute("font-family", "var(--mono)");
  axis.textContent = `${hi.toFixed(1)} px (98th pct)`;
  s.appendChild(axis);

  const zero = document.createElementNS(NS, "text");
  zero.setAttribute("x", PAD); zero.setAttribute("y", H - 8);
  zero.setAttribute("fill", "var(--muted)");
  zero.setAttribute("font-size", "10");
  zero.setAttribute("font-family", "var(--mono)");
  zero.textContent = "0";
  s.appendChild(zero);

  wrap.appendChild(s);
  return wrap;
}

function funnel(metrics, tiepoints) {
  const raw = metrics?.n_raw_matches ?? tiepoints?.moving?.length ?? 0;
  const kept = metrics?.n_inliers ?? 0;
  const detected = Math.max(raw, tiepoints?.moving?.length ?? 0);
  const stages = [
    ["Candidate matches", detected, "var(--nasa)"],
    ["Geometrically verified", kept, "var(--good)"],
    ["Rejected", detected - kept, "var(--bad)"],
  ];
  const top = Math.max(detected, 1);

  const wrap = document.createElement("figure");
  wrap.className = "chart chart-funnel";
  wrap.innerHTML = `<figcaption class="eyebrow">MAGSAC++ verification</figcaption>`;
  for (const [label, value, colour] of stages) {
    const row = document.createElement("div");
    row.className = "funnel-stage";
    row.innerHTML = `
      <span class="funnel-label">${label}</span>
      <span class="funnel-bar"><i style="width:${(value / top) * 100}%;background:${colour}"></i></span>
      <span class="funnel-value num">${value.toLocaleString()}</span>`;
    wrap.appendChild(row);
  }
  return wrap;
}

function coverageGrid(tiepoints, patchSize, uniformity) {
  const wrap = document.createElement("figure");
  wrap.className = "chart";
  wrap.innerHTML =
    `<figcaption class="eyebrow">Spatial coverage &mdash; ${(uniformity * 100).toFixed(1)}% of cells occupied</figcaption>`;

  const occupied = new Set();
  const cell = patchSize / GRID_CELLS;
  (tiepoints?.ref || []).forEach(([x, y], i) => {
    if (!tiepoints.inlier_mask?.[i]) return;
    const c = Math.min(GRID_CELLS - 1, Math.floor(x / cell));
    const r = Math.min(GRID_CELLS - 1, Math.floor(y / cell));
    occupied.add(`${r},${c}`);
  });

  const grid = document.createElement("div");
  grid.className = "chart-grid";
  for (let r = 0; r < GRID_CELLS; r++) {
    for (let c = 0; c < GRID_CELLS; c++) {
      const d = document.createElement("span");
      d.className = "grid-cell";
      if (occupied.has(`${r},${c}`)) d.dataset.on = "1";
      grid.appendChild(d);
    }
  }
  wrap.appendChild(grid);
  return wrap;
}

export function renderCharts(container, result) {
  container.innerHTML = "";
  const tp = result.tiepoints;
  if (!tp || !tp.moving?.length) {
    container.innerHTML =
      `<p class="empty">No correspondence data for this run &mdash; the matcher did not
       reach the verification stage.</p>`;
    return;
  }
  container.appendChild(histogram(tp.residuals_px || [], tp.inlier_mask || [], 3.0));
  container.appendChild(funnel(result.metrics, tp));
  container.appendChild(coverageGrid(tp, result.patch_size || 1024,
                                     result.metrics?.spatial_uniformity ?? 0));
}
```

- [ ] **Step 4: Style the charts**

Append to `src/lunar_matchbench/api/static/css/panels.css`:

```css
#charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 22px; margin-top: 20px; }
.chart { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.chart svg { display: block; }
.empty { color: var(--muted); font-size: 12.5px; }

.funnel-stage { display: grid; grid-template-columns: 1fr 2fr auto; gap: 10px; align-items: center; font-size: 11.5px; }
.funnel-label { color: var(--muted); }
.funnel-bar { height: 8px; background: var(--panel-2); border-radius: 2px; overflow: hidden; }
.funnel-bar i { display: block; height: 100%; }
.funnel-value { color: var(--signal); }

.chart-grid { display: grid; grid-template-columns: repeat(8, 1fr); gap: 3px; max-width: 220px; }
.grid-cell { aspect-ratio: 1; border-radius: 1px; background: var(--panel-2); border: 1px solid var(--rule); }
.grid-cell[data-on] { background: var(--good); border-color: var(--good); }
```

- [ ] **Step 5: Call it from `main.js`**

Import `renderCharts` from `./charts.js`, unhide `#panels`, and call
`renderCharts(document.getElementById("charts"), data)` inside the rehydration
`.then`. Also set `document.getElementById("prov-pre").textContent =
JSON.stringify(data.provenance, null, 2)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 7: Commit**

```bash
git add src/lunar_matchbench/api tests/test_ui.py
git commit -m "feat: residual histogram, verification funnel and coverage grid"
```

---

## Task 6: Live run lifecycle and streamed byte counter

**Files:**
- Modify: `src/lunar_matchbench/api/static/js/main.js`
- Modify: `src/lunar_matchbench/api/static/css/console.css`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `startRun`, `fetchStatus`, `fetchResult` from `api.js`; `/api/status` fields `progress_step`, `progress_total`, `progress_msg`, `transfer`
- Produces: the run lifecycle — submit, poll, stream progress into the status chip and step list, then render the result panels

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_running_job_shows_progress_and_streamed_bytes(page, live_server):
    _seed("uitest09", {
        "status": "running",
        "progress_step": 3,
        "progress_msg": "Opening LROC NAC M1359306139LC.IMG (byte-range stream)...",
        "transfer": {"fetched_bytes": 38_700_000, "cached_bytes": 0,
                     "requests": 2, "product_bytes": 528_929_736},
        "step_image_urls": {},
    })
    page.goto(f"{live_server}/?job=uitest09", wait_until="networkidle")
    page.wait_for_selector(".steps", timeout=15000)

    assert page.locator("#status-chip").get_attribute("data-state") == "running"
    assert "38.7" in page.inner_text(".transfer-live"), "streamed MB must be shown live"
    assert page.locator(".step[data-state='active']").count() == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -k running_job -v`
Expected: FAIL — no `.steps`.

- [ ] **Step 3: Implement the lifecycle in `main.js`**

Replace `src/lunar_matchbench/api/static/js/main.js`:

```js
import { fetchResult, fetchStatus, startRun } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";
import { mountComparator } from "./comparator.js";
import { mountTiePoints } from "./tiepoints.js";
import { renderCharts } from "./charts.js";

const STEPS = [
  "Locate CH2 patch", "Query NASA ODE", "Open LROC NAC", "Extract patch",
  "Detect keypoints", "Match features", "MAGSAC++ verify", "Finalize",
];

const locator = document.getElementById("locator");
const stage = document.getElementById("stage");
const stageBody = document.getElementById("stage-body");
const stageTools = document.getElementById("stage-tools");
const panels = document.getElementById("panels");
const chip = document.getElementById("status-chip");
const chipText = document.getElementById("status-text");
const form = document.getElementById("run-form");
const runBtn = document.getElementById("run-btn");

clearLocator(locator);

function setChip(state, text) {
  chip.dataset.state = state;
  chipText.textContent = text;
}

function fmtMB(bytes) { return `${((bytes || 0) / 1e6).toFixed(1)} MB`; }

function renderProgress(data) {
  stage.hidden = false;
  const step = data.progress_step || 0;
  const tx = data.transfer || {};
  const live = tx.fetched_bytes
    ? `Streamed ${fmtMB(tx.fetched_bytes)}${tx.product_bytes ? ` of ${fmtMB(tx.product_bytes)}` : ""}`
    : tx.cached_bytes ? `${fmtMB(tx.cached_bytes)} from cache` : "";

  stageTools.innerHTML = "";
  stageBody.innerHTML = `
    <ol class="steps">
      ${STEPS.map((label, i) => {
        const idx = i + 1;
        const state = idx < step ? "done" : idx === step ? "active" : "todo";
        return `<li class="step" data-state="${state}"><span></span>${label}</li>`;
      }).join("")}
    </ol>
    <p class="progress-msg num">${data.progress_msg || ""}</p>
    <p class="transfer-live num">${live}</p>`;
}

function renderResult(jobId, data) {
  const ok = data.status === "done" && data.metrics;
  setChip(
    ok ? "live" : "cache",
    ok ? "Registration succeeded" : "Did not converge",
  );

  renderLocator(locator, data?.provenance?.lroc_localization);

  stage.hidden = false;
  stageBody.innerHTML = "";
  stageTools.innerHTML = `
    <button type="button" class="tool" data-mode="swipe" aria-pressed="true">Swipe</button>
    <button type="button" class="tool" data-mode="fade" aria-pressed="false">Fade</button>`;

  const cmpHost = document.createElement("div");
  stageBody.appendChild(cmpHost);
  const comparator = mountComparator(cmpHost, { jobId, mode: "swipe" });
  stageTools.onclick = (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    comparator.setMode(btn.dataset.mode);
    stageTools.querySelectorAll(".tool").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
  };

  if (data.tiepoints) {
    const tpHost = document.createElement("div");
    stageBody.appendChild(tpHost);
    mountTiePoints(tpHost, {
      tiepoints: data.tiepoints,
      patchSize: data.patch_size || 1024,
    });
  }

  panels.hidden = false;
  renderCharts(document.getElementById("charts"), data);
  document.getElementById("prov-pre").textContent =
    JSON.stringify(data.provenance || {}, null, 2);
}

async function poll(jobId) {
  while (true) {
    const status = await fetchStatus(jobId);
    if (status.status === "done" || status.status === "failed") {
      renderResult(jobId, await fetchResult(jobId));
      runBtn.disabled = false;
      runBtn.textContent = "Run registration";
      return;
    }
    setChip("running", `Step ${status.progress_step || 0} of ${status.progress_total || 8}`);
    renderProgress(status);
    await new Promise((r) => setTimeout(r, 1100));
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  runBtn.disabled = true;
  runBtn.textContent = "Running";
  panels.hidden = true;
  clearLocator(locator);
  setChip("running", "Starting");
  try {
    const { job_id } = await startRun({
      lat: parseFloat(document.getElementById("lat").value),
      lon: parseFloat(document.getElementById("lon").value),
      instrument: document.getElementById("instrument").value,
      matcher: document.getElementById("matcher").value,
    });
    history.replaceState(null, "", `?job=${job_id}`);
    await poll(job_id);
  } catch (err) {
    setChip("idle", err.message);
    runBtn.disabled = false;
    runBtn.textContent = "Run registration";
  }
});

document.querySelectorAll(".preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("lat").value = btn.dataset.lat;
    document.getElementById("lon").value = btn.dataset.lon;
  });
});

const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchStatus(jobFromUrl)
    .then((status) => {
      if (status.status === "done" || status.status === "failed") {
        return fetchResult(jobFromUrl).then((d) => renderResult(jobFromUrl, d));
      }
      setChip("running", `Step ${status.progress_step || 0} of ${status.progress_total || 8}`);
      renderProgress(status);
      return poll(jobFromUrl);
    })
    .catch(() => setChip("idle", "Idle"));
}
```

- [ ] **Step 4: Style the step list**

Append to `src/lunar_matchbench/api/static/css/console.css`:

```css
.steps { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; }
.step {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 11px; border: 1px solid var(--rule); border-radius: 100px;
  font-size: 11.5px; color: var(--muted);
}
.step span { width: 6px; height: 6px; border-radius: 50%; background: var(--rule); }
.step[data-state="done"] { color: var(--signal); }
.step[data-state="done"] span { background: var(--nasa); }
.step[data-state="active"] { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--rule)); }
.step[data-state="active"] span { background: var(--warn); animation: pulse 1.1s ease-in-out infinite; }

.progress-msg { margin-top: 14px; font-size: 12px; color: var(--muted); }
.transfer-live { margin-top: 4px; font-size: 12px; color: var(--nasa); }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -v`
Expected: PASS — 9 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lunar_matchbench/api tests/test_ui.py
git commit -m "feat: live run lifecycle with streamed byte counter"
```

---

## Task 7: Diagnosis panel, retirement of the old assets, and a design pass

**Files:**
- Modify: `src/lunar_matchbench/api/static/js/main.js`
- Modify: `src/lunar_matchbench/api/static/css/panels.css`
- Delete: `src/lunar_matchbench/api/static/style.css`, `src/lunar_matchbench/api/static/app.js`
- Modify: `tests/test_ui.py`

**Interfaces:**
- Consumes: `provenance.lroc_localization`, `transfer`
- Produces: `renderDiagnosis(container, data)` in `main.js`, carrying forward the Plan 1 wording that distinguishes a genuine mismatch from an unsearched strip

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui.py`:

```python
def test_full_strip_failure_is_called_a_genuine_mismatch(page, live_server):
    import copy

    failed = copy.deepcopy(DONE_JOB)
    failed["status"] = "failed"
    failed["error"] = "Too few raw matches: 5"
    failed["result"]["metrics"] = None
    loc = failed["result"]["provenance"]["lroc_localization"]
    loc.update(confident=False, best_n=0, total_lines=15360, lines_searched=15360,
               strip_fraction_searched=1.0, whole_strip_searched=True)
    _seed("uitest10", failed)

    page.goto(f"{live_server}/?job=uitest10", wait_until="networkidle")
    page.wait_for_selector("#diagnosis", timeout=15000)
    text = page.inner_text("#diagnosis")
    assert "entire LROC strip was searched" in text
    assert "genuine" in text.lower()


def test_partial_strip_failure_is_not_called_a_mismatch(page, live_server):
    import copy

    failed = copy.deepcopy(DONE_JOB)
    failed["status"] = "failed"
    failed["error"] = "Too few raw matches: 7"
    failed["result"]["metrics"] = None
    loc = failed["result"]["provenance"]["lroc_localization"]
    loc.update(confident=False, best_n=20, strip_fraction_searched=0.16,
               whole_strip_searched=False)
    _seed("uitest11", failed)

    page.goto(f"{live_server}/?job=uitest11", wait_until="networkidle")
    page.wait_for_selector("#diagnosis", timeout=15000)
    text = page.inner_text("#diagnosis")
    assert "16%" in text
    assert "genuine content or illumination mismatch" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest -m ui tests/test_ui.py -k strip_failure -v`
Expected: FAIL — `#diagnosis` is empty.

- [ ] **Step 3: Add `renderDiagnosis` to `main.js`**

Insert into `main.js` and call it from `renderResult` before `renderCharts`:

```js
function renderDiagnosis(container, data) {
  const loc = data?.provenance?.lroc_localization;
  const tx = data.transfer || {};
  const rows = [];

  if (loc) {
    const ok = loc.confident;
    const pct = Math.round((loc.strip_fraction_searched || 0) * 100);
    rows.push(`
      <div class="diag ${ok ? "diag--good" : "diag--warn"}">
        <span class="eyebrow">Scan-line lock</span>
        <b class="num">${ok ? "VERIFIED" : "NOT VERIFIED"}</b>
        <span class="diag-detail num">${loc.best_n} / ${loc.min_confident_matches} matches
          · ${pct}% of ${(loc.total_lines || 0).toLocaleString()} lines searched</span>
      </div>`);

    if (!ok && loc.whole_strip_searched) {
      rows.push(`<p class="diag-note">The <b>entire LROC strip was searched</b>
        (${(loc.lines_searched || 0).toLocaleString()} of
        ${(loc.total_lines || 0).toLocaleString()} lines) and nothing correlated above the
        threshold. This is a <b>genuine content or illumination mismatch</b>, not a
        mislocalized patch.</p>`);
    } else if (!ok) {
      rows.push(`<p class="diag-note">Only <b>${pct}%</b> of the strip was searched, so this
        patch is the raw geometry estimate. This run does not show that the two images fail
        to correspond.</p>`);
    }
  }

  const fetched = tx.fetched_bytes || 0;
  const cached = tx.cached_bytes || 0;
  if (fetched || cached) {
    const fromCache = fetched === 0 && cached > 0;
    rows.push(`
      <div class="diag diag--info">
        <span class="eyebrow">${fromCache ? "Served from cache" : "Streamed"}</span>
        <b class="num">${fmtMB(fromCache ? cached : fetched)}</b>
        <span class="diag-detail num">${
          tx.product_bytes ? `of a ${fmtMB(tx.product_bytes)} product` : ""
        }${fromCache ? " · no imagery pulled this run" : ` · ${tx.requests} range requests`}</span>
      </div>`);
  }

  container.innerHTML = rows.join("");
}
```

Add the metrics readouts in `renderResult` too:

```js
const m = data.metrics;
document.getElementById("metrics").innerHTML = m ? `
  <div class="readouts">
    ${[[m.n_inliers, "inlier tie-points"],
       [`${m.inlier_ratio_pct}%`, `of ${m.n_raw_matches} raw`],
       [`${m.rmse_px.toFixed(3)} px`, "reprojection RMSE"],
       [`${(m.spatial_uniformity * 100).toFixed(1)}%`, "spatial coverage"],
       [`${m.elapsed_sec.toFixed(2)} s`, `${m.matcher} runtime`]]
      .map(([v, l]) => `<div><b class="readout">${v}</b><span class="eyebrow">${l}</span></div>`)
      .join("")}
  </div>` : `<p class="empty">${data.error || "Registration did not converge."}</p>`;
```

- [ ] **Step 4: Style diagnosis and readouts**

Append to `src/lunar_matchbench/api/static/css/panels.css`:

```css
.diag {
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  padding: 11px 14px; margin-bottom: 8px;
  border: 1px solid var(--rule); border-left-width: 3px; border-radius: 3px;
  background: var(--panel-2);
}
.diag--good { border-left-color: var(--good); }
.diag--good b { color: var(--good); }
.diag--warn { border-left-color: var(--warn); }
.diag--warn b { color: var(--warn); }
.diag--info { border-left-color: var(--nasa); }
.diag--info b { color: var(--nasa); }
.diag b { font-size: 13px; }
.diag-detail { font-size: 11.5px; color: var(--muted); }
.diag-note { font-size: 12.5px; color: var(--muted); line-height: 1.65; margin: 0 0 14px 2px; max-width: 76ch; }
.diag-note b { color: var(--signal); font-weight: 600; }

.readouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 18px; margin: 20px 0 4px; }
.readouts > div { display: flex; flex-direction: column; gap: 6px; }
.readouts .readout { color: var(--good); }
```

- [ ] **Step 5: Delete the retired assets**

```bash
git rm src/lunar_matchbench/api/static/style.css src/lunar_matchbench/api/static/app.js
```

Confirm nothing references them: `grep -rn "style.css\|app.js" src/ tests/` should return only the new `css/` and `js/` paths.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m pytest -m ui -q`
Expected: PASS on both.

- [ ] **Step 7: Design critique pass**

Screenshot at 1440px, 900px and 390px widths. Check against the brief:
the greyscale imagery is the brightest thing; `--isro` and `--nasa` appear only
as mission markers; the locator reads as a coordinate axis; nothing is centred
that should be aligned. Remove one decorative element that is not carrying
information. Record what was removed in the commit message.

- [ ] **Step 8: Commit**

```bash
git add -A src/lunar_matchbench/api tests/test_ui.py
git commit -m "feat: diagnosis panel and metric readouts; retire the old UI assets"
```

---

## Self-Review Notes

**Spec coverage (§3.4):** mission-control direction and tokens → Task 1. Command bar → Task 1. Pipeline theatre with real streamed byte counts and cache-or-live indicator → Task 6. Swipe divider and opacity cross-fade → Task 3. Tie-point canvas with hover, inlier/outlier toggle, colour by residual → Task 4. Residual histogram, funnel, 8×8 coverage grid → Task 5. Module split into `api.js` / `locator.js` / `comparator.js` / `tiepoints.js` / `charts.js` / `main.js` → Tasks 1–6.

**Deviation from the spec, deliberate:** §3.4 lists pan and zoom on the result stage. Dropped. The patches are 1024×1024 rendered at roughly panel width, so there is little to zoom into, and a custom pan/zoom surface would fight the comparator's drag and the canvas's hover. If it is wanted later it belongs on the tie-point canvas alone, not the whole stage. Recorded here rather than silently omitted.

**Added beyond the spec:** the scan-line strip locator (Task 2). The spec's §3.4 did not name it; it exists because Plan 1's `whole_strip_searched` finding showed that "where in the strip did we look" is the single most decision-relevant fact the pipeline produces, and prose was burying it.

**Type consistency:** `renderLocator(el, loc)` / `clearLocator(el)`; `mountComparator(container, {jobId, mode}) -> {setMode, setSplit, destroy}`; `mountTiePoints(container, {tiepoints, patchSize}) -> {setFilter, destroy}`; `renderCharts(container, result)`; `patchUrl(jobId, which)`. `fmtMB` is defined once in `main.js` and used by `renderProgress` and `renderDiagnosis`; `charts.js` does not need it.

**Known risk:** `tests/test_ui.py` seeds jobs through `app_mod._store`, reaching past the public API. That is deliberate — it keeps the UI tests offline and deterministic — but it couples them to the job-store shape. If `_store` changes, these tests must change with it.

---

## Execution Record (2026-09-03)

All 7 tasks complete. 14 UI tests + 50 offline tests passing; no horizontal
overflow at 1440, 900 or 390px.

What real data changed, none of which the plan anticipated:

1. **The tie-point overlay was an unreadable hairball.** 1,280 displacement
   lines drawn at once. The flow field is now opt-in and hovering draws only the
   line under the cursor; the points carry the verdict.
2. **The comparator tags were backwards.** The clip reveals the moving layer
   from the left edge, so the left of the split is CH2, not LROC — and since
   colour encodes provenance here, a swapped label is a factual error.
3. **The residual axis was useless at the 98th percentile.** Rejected matches in
   a 1024px patch reproject anywhere; p98 for the reference run is 1057 px, which
   crushed every kept point into one bin. The axis is now scaled to the kept
   distribution with an explicit labelled overflow bar.
4. **The three-stage funnel was a fiction.** There is no sequence — only two
   states of one population — so it became a part-to-whole proportion bar.
5. **`/api/status` never returned `transfer`.** Plan 1 stored the figures the
   pipeline reports but did not expose them, so the live byte counter could
   never have worked. Fixed in `get_status`.
6. **The base image swallowed every hover.** Its `opacity` creates a stacking
   context that paints it above the canvas; it needs `pointer-events: none` and
   an explicit `z-index`, not DOM order.
7. **Legacy runs printed "0% of 0 lines searched".** Runs recorded before
   coverage tracking now omit the clause instead of claiming a measurement.

**Palette validation, run rather than eyeballed.** `validate_palette.js` puts
kept-vs-rejected (`#3FD68C` / `#FF5C5C`) at deutan ΔE **8.5** — above the ≥8
target but only just, and it FAILs the lightness band. Since that pair carries
the single most important distinction in the tool, the verdict is never colour
alone: filled disc vs hollow ring on the canvas and in both legends, and fixed
stacking order in the histogram.

**Design pass, one thing removed:** the run button is no longer a full-width
saffron bar. It was the loudest element on the page and competed with the strip
locator, which is the element this console should be remembered by.

**Deviation from the plan:** Task 5's tests were written against `rect` bars and
a 3-stage funnel; both changed as described above, and the tests changed with
them. Stage-wide pan/zoom stays dropped, as recorded in the plan's self-review.
