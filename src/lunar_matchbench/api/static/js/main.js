import { fetchResult, fetchStatus, startRun } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";
import { mountComparator } from "./comparator.js";
import { mountTiePoints } from "./tiepoints.js";
import { renderCharts } from "./charts.js";
import { renderContext } from "./context.js";
import { renderTransform } from "./transform.js";
import { mountSky } from "./sky.js";

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

const intro = document.getElementById("intro");
mountSky(document.getElementById("sky"));
clearLocator(locator);

function setChip(state, label) {
  chip.dataset.state = state;
  chipText.textContent = label;
}

const fmtMB = (bytes) => `${((bytes || 0) / 1e6).toFixed(1)} MB`;

// ── in flight ───────────────────────────────────────────────────────────────
function renderProgress(status) {
  stage.hidden = false;
  stageTools.innerHTML = "";
  const step = status.progress_step || 0;
  const tx = status.transfer || {};
  const live = tx.fetched_bytes
    ? `Streamed ${fmtMB(tx.fetched_bytes)}${tx.product_bytes ? ` of ${fmtMB(tx.product_bytes)}` : ""}`
    : tx.cached_bytes ? `${fmtMB(tx.cached_bytes)} read from cache` : "";

  stageBody.innerHTML = `
    <ol class="steps">
      ${STEPS.map((label, i) => {
        const idx = i + 1;
        const state = idx < step ? "done" : idx === step ? "active" : "todo";
        return `<li class="step" data-state="${state}"><i></i>${label}</li>`;
      }).join("")}
    </ol>
    <p class="progress-msg num">${status.progress_msg || ""}</p>
    <p class="transfer-live num">${live}</p>`;
}

// ── finished ────────────────────────────────────────────────────────────────
function renderDiagnosis(container, data) {
  const loc = data?.provenance?.lroc_localization;
  const tx = data.transfer || {};
  const rows = [];

  if (loc) {
    const ok = loc.confident;
    const pct = Math.round((loc.strip_fraction_searched || 0) * 100);
    // Runs recorded before coverage was tracked have no total_lines. Saying
    // "0% of 0 lines searched" would be worse than saying nothing.
    const coverage = loc.total_lines
      ? ` · ${pct}% of ${loc.total_lines.toLocaleString()} lines searched`
      : "";
    rows.push(`
      <div class="diag ${ok ? "diag--good" : "diag--warn"}">
        <span class="eyebrow">Scan-line lock</span>
        <b class="num">${ok ? "VERIFIED" : "NOT VERIFIED"}</b>
        <span class="diag-detail num">${loc.best_n} / ${loc.min_confident_matches} matches${coverage}</span>
      </div>`);

    // "Not confident" means two different things, and reporting them the same
    // way either understates a real negative result or invents one.
    if (!ok && loc.whole_strip_searched) {
      rows.push(`<p class="diag-note">The <b>entire LROC strip was searched</b>
        (${(loc.lines_searched || 0).toLocaleString()} of
        ${(loc.total_lines || 0).toLocaleString()} lines) and nothing correlated above the
        threshold. This is a <b>genuine content or illumination mismatch</b>, not a
        mislocalized patch.</p>`);
    } else if (!ok && loc.total_lines) {
      rows.push(`<p class="diag-note">Only <b>${pct}%</b> of the strip was searched, so this
        patch is the raw pushbroom geometry estimate. This run does <b>not</b> show that the
        two images fail to correspond.</p>`);
    }
  }

  const fetched = tx.fetched_bytes || 0;
  const cached = tx.cached_bytes || 0;
  if (fetched || cached) {
    // Leading with "0 MB" whenever every read came from disk reads as though
    // nothing happened, and would let a pre-warmed demo pass for a live fetch.
    const fromCache = fetched === 0 && cached > 0;
    rows.push(`
      <div class="diag diag--info">
        <span class="eyebrow">${fromCache ? "Served from cache" : "Streamed"}</span>
        <b class="num">${fmtMB(fromCache ? cached : fetched)}</b>
        <span class="diag-detail num">${
          tx.product_bytes ? `of a ${fmtMB(tx.product_bytes)} product` : ""
        }${fromCache
            ? " · no imagery pulled over the network this run"
            : ` · ${tx.requests} byte-range request${tx.requests === 1 ? "" : "s"}`}</span>
      </div>`);
  }

  container.innerHTML = rows.join("");
}

function renderMetrics(container, data) {
  const m = data.metrics;
  if (!m) {
    container.innerHTML =
      `<p class="empty">${data.error || "Registration did not converge."}</p>`;
    return;
  }
  const cells = [
    [m.n_inliers.toLocaleString(), "inlier tie-points"],
    [`${m.inlier_ratio_pct}%`, `of ${m.n_raw_matches.toLocaleString()} raw`],
    [`${m.rmse_px.toFixed(3)} px`, "reprojection RMSE"],
    [`${(m.spatial_uniformity * 100).toFixed(1)}%`, "spatial coverage"],
    [`${m.elapsed_sec.toFixed(2)} s`, `${m.matcher} runtime`],
  ];
  container.innerHTML = `<div class="readouts">${cells
    .map(([v, l]) => `<div><b class="readout num">${v}</b><span class="eyebrow">${l}</span></div>`)
    .join("")}</div>`;
}

function renderResult(jobId, data) {
  const ok = data.status === "done" && data.metrics;
  const fromCache = (data.transfer?.fetched_bytes || 0) === 0
    && (data.transfer?.cached_bytes || 0) > 0;
  setChip(
    ok ? (fromCache ? "cache" : "live") : "idle",
    ok ? (fromCache ? "Succeeded · from cache" : "Succeeded · live") : "Did not converge",
  );

  renderLocator(locator, data?.provenance?.lroc_localization, { jobId });
  if (intro) intro.hidden = true;

  stage.hidden = false;
  stageBody.innerHTML = "";
  stageTools.innerHTML = `
    <button type="button" class="tool" data-mode="swipe" aria-pressed="true">Swipe</button>
    <button type="button" class="tool" data-mode="fade" aria-pressed="false">Fade</button>`;

  // Two square views of the same ground, side by side where there is room, so
  // the alignment and the correspondences can be read against each other.
  const media = document.createElement("div");
  media.className = "stage-media";
  stageBody.appendChild(media);

  const cmpHost = document.createElement("div");
  media.appendChild(cmpHost);
  const comparator = mountComparator(cmpHost, { jobId, mode: "swipe" });
  stageTools.onclick = (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    comparator.setMode(btn.dataset.mode);
    stageTools.querySelectorAll(".tool").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
  };

  if (data?.tiepoints?.moving?.length) {
    const tpHost = document.createElement("div");
    media.appendChild(tpHost);
    mountTiePoints(tpHost, {
      tiepoints: data.tiepoints,
      patchSize: data.patch_size || 1024,
      jobId,
    });
  }

  panels.hidden = false;
  renderDiagnosis(document.getElementById("diagnosis"), data);
  renderMetrics(document.getElementById("metrics"), data);
  renderCharts(document.getElementById("charts"), data);
  renderTransform(document.getElementById("transform"), data);
  renderContext(document.getElementById("context"), data);
  document.getElementById("prov-pre").textContent =
    JSON.stringify(data.provenance || {}, null, 2);
}

// ── lifecycle ───────────────────────────────────────────────────────────────
function resetRunButton() {
  runBtn.disabled = false;
  runBtn.textContent = "Run registration";
}

async function poll(jobId) {
  for (;;) {
    const status = await fetchStatus(jobId);
    if (status.status === "done" || status.status === "failed") {
      renderResult(jobId, await fetchResult(jobId));
      resetRunButton();
      return;
    }
    setChip("running",
            `Step ${status.progress_step || 0} of ${status.progress_total || 8}`);
    renderProgress(status);
    await new Promise((resolve) => setTimeout(resolve, 1100));
  }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  runBtn.disabled = true;
  runBtn.textContent = "Running";
  panels.hidden = true;
  if (intro) intro.hidden = true;
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
    resetRunButton();
  }
});

document.querySelectorAll(".preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.getElementById("lat").value = btn.dataset.lat;
    document.getElementById("lon").value = btn.dataset.lon;
  });
});

// Rehydrating from ?job= makes a finished run linkable and survivable across a
// reload, and is how the UI tests drive real payloads without touching the
// network.
const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchStatus(jobFromUrl)
    .then((status) => {
      if (status.status === "done" || status.status === "failed") {
        return fetchResult(jobFromUrl).then((d) => renderResult(jobFromUrl, d));
      }
      setChip("running",
              `Step ${status.progress_step || 0} of ${status.progress_total || 8}`);
      renderProgress(status);
      return poll(jobFromUrl);
    })
    .catch(() => setChip("idle", "Idle"));
}
