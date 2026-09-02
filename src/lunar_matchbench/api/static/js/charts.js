// Evidence charts.
//
// Every value here is computed from pipeline output. When the data is missing
// the panel says so rather than drawing an empty axis, which would imply a
// measurement was taken.
//
// kept/rejected is a status pair, not a categorical one, and green-vs-red is
// the classic colour-vision trap. The validator puts this pair at deutan
// dE 8.5 -- above the >=8 target but only just -- so colour never carries the
// verdict alone: the histogram stacks the two in fixed order with a labelled
// legend, and the overlay draws kept as a filled disc against rejected as a
// hollow ring.

const NS = "http://www.w3.org/2000/svg";

function node(name, attrs = {}) {
  const el = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function text(x, y, content, extra = {}) {
  const t = node("text", {
    x, y, fill: "var(--muted)", "font-size": 9.5,
    "font-family": "var(--mono)", ...extra,
  });
  t.textContent = content;
  return t;
}

/** A bar with its far end rounded and its near end square on the baseline. */
function barPath(x, y, w, h, r = 4) {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h} L${x},${y + rr} Q${x},${y} ${x + rr},${y} ` +
         `L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} ` +
         `L${x + w},${y + h} Z`;
}

function figure(caption, className = "chart") {
  const fig = document.createElement("figure");
  fig.className = className;
  const cap = document.createElement("figcaption");
  cap.className = "eyebrow";
  cap.textContent = caption;
  fig.appendChild(cap);
  return fig;
}

function legend(items) {
  const wrap = document.createElement("div");
  wrap.className = "chart-legend";
  wrap.innerHTML = items
    .map(({ label, colour, shape }) =>
      `<span class="lg"><i class="lg-mark lg-mark--${shape}" style="--c:${colour}"></i>${label}</span>`)
    .join("");
  return wrap;
}

// ── residual histogram ──────────────────────────────────────────────────────
function histogram(residuals, mask, threshold) {
  const fig = figure(`Reprojection error across all ${residuals.length} matches`);
  const W = 460, H = 158, L = 30, R = 58, T = 10, B = 26;

  // Rejected matches in a 1024 px patch reproject anywhere at all -- the 98th
  // percentile of this run is 1057 px -- so clipping on a percentile still
  // crushes every kept point into a single bin. The question this chart answers
  // is how tightly the KEPT points land, so the axis is scaled to them and
  // everything past it is counted into an explicit overflow bar rather than
  // quietly dropped.
  const keptResiduals = residuals.filter((_, i) => mask[i]);
  const keptSorted = keptResiduals.slice().sort((a, b) => a - b);
  const keptP98 = keptSorted[Math.floor(keptSorted.length * 0.98)] || threshold || 1;
  const hi = Math.max(threshold * 2, keptP98 * 1.15, 1);

  const BINS = 24;
  const all = new Array(BINS).fill(0);
  const inl = new Array(BINS).fill(0);
  let overflowAll = 0;
  let overflowKept = 0;
  residuals.forEach((r, i) => {
    if (r > hi) {
      overflowAll += 1;
      if (mask[i]) overflowKept += 1;
      return;
    }
    const b = Math.min(BINS - 1, Math.max(0, Math.floor((r / hi) * BINS)));
    all[b] += 1;
    if (mask[i]) inl[b] += 1;
  });
  const peak = Math.max(...all, overflowAll, 1);

  const svg = node("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-histogram", width: "100%" });
  const plotH = H - T - B;
  const bw = (W - L - R) / BINS;
  const yOf = (count) => T + plotH - (count / peak) * plotH;

  // Recessive baseline only; no gridlines competing with the marks.
  svg.appendChild(node("line", {
    x1: L, y1: T + plotH, x2: W - R, y2: T + plotH,
    stroke: "var(--rule)", "stroke-width": 1,
  }));

  for (let b = 0; b < BINS; b++) {
    if (!all[b]) continue;
    const x = L + b * bw;
    const w = Math.max(1, bw - 2);              // 2px surface gap between bars
    const yAll = yOf(all[b]);
    const yIn = yOf(inl[b]);
    const rejected = all[b] - inl[b];

    // Stacked in a fixed order: kept sits on the baseline, rejected above it,
    // so the split reads by position even with no colour at all.
    if (rejected > 0) {
      const seg = node("path", {
        class: "bar bar--rejected",
        d: barPath(x, yAll, w, Math.max(1, yIn - yAll - 2)),
        fill: "var(--bad)", opacity: 0.85,
      });
      seg.appendChild(node("title")).textContent =
        `${(b * hi / BINS).toFixed(1)}–${((b + 1) * hi / BINS).toFixed(1)} px · ${rejected} rejected`;
      svg.appendChild(seg);
    }
    if (inl[b] > 0) {
      const seg = node("path", {
        class: "bar bar--kept",
        d: barPath(x, yIn, w, T + plotH - yIn),
        fill: "var(--good)",
      });
      seg.appendChild(node("title")).textContent =
        `${(b * hi / BINS).toFixed(1)}–${((b + 1) * hi / BINS).toFixed(1)} px · ${inl[b]} kept`;
      svg.appendChild(seg);
    }
  }

  // Everything past the focused axis, stated rather than dropped.
  if (overflowAll > 0) {
    const ox = W - R + 14;
    const ow = 20;
    const yOver = yOf(overflowAll);
    const overRejected = overflowAll - overflowKept;
    if (overRejected > 0) {
      const seg = node("path", {
        class: "bar bar--rejected",
        d: barPath(ox, yOver, ow, T + plotH - yOver),
        fill: "var(--bad)", opacity: 0.85,
      });
      seg.appendChild(node("title")).textContent =
        `${overRejected} rejected beyond ${hi.toFixed(1)} px`;
      svg.appendChild(seg);
    }
    svg.appendChild(text(ox + ow / 2, H - 8, `>${hi.toFixed(0)}px`, { "text-anchor": "middle" }));
    svg.appendChild(text(ox + ow / 2, yOver - 5, String(overflowAll), {
      "text-anchor": "middle", fill: "var(--bad)",
    }));
  }

  // The RANSAC acceptance threshold: the reason the split exists at all.
  if (threshold && threshold < hi) {
    const tx = L + (threshold / hi) * (W - L - R);
    svg.appendChild(node("line", {
      x1: tx, y1: T, x2: tx, y2: T + plotH,
      stroke: "var(--signal)", "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0.5,
    }));
    svg.appendChild(text(tx + 4, T + 9, `${threshold} px threshold`, { fill: "var(--signal)", opacity: 0.75 }));
  }

  svg.appendChild(text(L, H - 8, "0"));
  svg.appendChild(text(W - R, H - 8, `${hi.toFixed(1)} px`, { "text-anchor": "end" }));
  svg.appendChild(text(L - 6, T + 8, String(peak), { "text-anchor": "end" }));

  fig.appendChild(svg);
  fig.appendChild(legend([
    { label: "kept", colour: "var(--good)", shape: "disc" },
    { label: "rejected", colour: "var(--bad)", shape: "ring" },
  ]));
  return fig;
}

// ── verification proportion ─────────────────────────────────────────────────
function verification(metrics, tiepoints) {
  const total = metrics?.n_raw_matches ?? tiepoints?.moving?.length ?? 0;
  const kept = metrics?.n_inliers ?? (tiepoints?.inlier_mask || []).filter(Boolean).length;
  const pct = total ? (kept / total) * 100 : 0;

  // Deliberately not a funnel. A funnel implies a sequence of narrowing stages;
  // here there are only two states of one population, so this is a part-to-whole
  // of a single total and a proportion bar is the honest form.
  const fig = figure("MAGSAC++ verification");
  const bar = document.createElement("div");
  bar.className = "prop";
  bar.innerHTML = `
    <div class="prop-track" role="img"
         aria-label="${kept} of ${total} candidate matches kept, ${pct.toFixed(1)} percent">
      <i class="prop-kept" style="width:${pct}%"></i>
    </div>
    <div class="prop-labels num">
      <span class="prop-kept-label">${kept.toLocaleString()} kept · ${pct.toFixed(1)}%</span>
      <span class="prop-total-label">${total.toLocaleString()} candidates</span>
    </div>`;
  fig.appendChild(bar);
  return fig;
}

// ── spatial coverage grid ───────────────────────────────────────────────────
function coverageGrid(tiepoints, patchSize, uniformity, cells) {
  const fig = figure(
    `Spatial coverage · ${(uniformity * 100).toFixed(1)}% of ${cells}×${cells} cells occupied`);

  const counts = new Array(cells * cells).fill(0);
  const size = patchSize / cells;
  (tiepoints?.ref || []).forEach(([x, y], i) => {
    if (!tiepoints.inlier_mask?.[i]) return;
    const c = Math.min(cells - 1, Math.max(0, Math.floor(x / size)));
    const r = Math.min(cells - 1, Math.max(0, Math.floor(y / size)));
    counts[r * cells + c] += 1;
  });
  const busiest = Math.max(...counts, 1);

  const grid = document.createElement("div");
  grid.className = "chart-grid";
  grid.style.setProperty("--cells", String(cells));
  counts.forEach((count, idx) => {
    const cell = document.createElement("span");
    cell.className = "grid-cell";
    if (count > 0) {
      cell.dataset.on = "1";
      // Occupancy is what the metric counts, so any inlier fills the cell; the
      // extra opacity carries density without changing what "occupied" means.
      cell.style.opacity = String(0.4 + 0.6 * (count / busiest));
    }
    cell.title = count
      ? `row ${Math.floor(idx / cells)}, col ${idx % cells} · ${count} inliers`
      : `row ${Math.floor(idx / cells)}, col ${idx % cells} · empty`;
    grid.appendChild(cell);
  });
  fig.appendChild(grid);
  fig.appendChild(legend([
    { label: "has inliers", colour: "var(--good)", shape: "disc" },
    { label: "empty", colour: "var(--rule)", shape: "ring" },
  ]));
  return fig;
}

export function renderCharts(container, result, { gridCells = 8 } = {}) {
  container.innerHTML = "";
  const tp = result.tiepoints;
  if (!tp || !tp.moving?.length) {
    container.innerHTML =
      `<p class="empty">No correspondence data for this run &mdash; the matcher did not
       reach the verification stage.</p>`;
    return;
  }
  const residuals = tp.residuals_px || [];
  const mask = tp.inlier_mask || [];
  if (residuals.length) {
    container.appendChild(histogram(residuals, mask, 3.0));
  }
  container.appendChild(verification(result.metrics, tp));
  container.appendChild(coverageGrid(
    tp, result.patch_size || 1024, result.metrics?.spatial_uniformity ?? 0, gridCells));
}
