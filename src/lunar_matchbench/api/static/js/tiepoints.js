// Tie-point overlay drawn from the pipeline's own correspondence arrays,
// plotted over the Chandrayaan-2 patch the keypoints were detected in.
//
// The matcher's output used to reach the screen only as a picture rendered
// server-side. Drawing it here means a viewer can filter it, hover a single
// point and read its reprojection error -- the difference between being shown
// a result and being able to interrogate one.
//
// Colour here is the inlier verdict, not mission provenance: both endpoints of
// every line belong to both missions at once, so the only thing a colour can
// honestly encode is whether MAGSAC++ kept the correspondence. That verdict is
// also carried by shape, because green-vs-red is the classic colour-vision trap.

const KEPT = "#3FD68C";      // --good
const REJECTED = "#FF5C5C";  // --bad

import { patchUrl } from "./api.js";

export function mountTiePoints(container, { tiepoints, patchSize, jobId }) {
  const moving = tiepoints?.moving || [];
  const ref = tiepoints?.ref || [];
  const mask = tiepoints?.inlier_mask || [];
  const residuals = tiepoints?.residuals_px || [];
  const n = moving.length;
  const kept = mask.filter(Boolean).length;

  container.innerHTML = `
    <div class="tp" data-filter="all">
      <div class="tp-bar">
        <div class="tp-legend num">
          <span class="tp-key tp-key--in">${kept} kept</span>
          <span class="tp-key tp-key--out">${n - kept} rejected</span>
          <span class="tp-total">of ${n}</span>
        </div>
        <div class="tp-filters">
          <button type="button" class="tool" data-filter="all" aria-pressed="true">All</button>
          <button type="button" class="tool" data-filter="inliers" aria-pressed="false">Kept</button>
          <button type="button" class="tool" data-filter="outliers" aria-pressed="false">Rejected</button>
          <button type="button" class="tool tp-vectors" aria-pressed="false">Vectors</button>
        </div>
      </div>
      <div class="tp-frame">
        ${jobId ? `<img class="tp-base" alt="" aria-hidden="true"
             src="${patchUrl(jobId, "ch2")}" />` : ""}
        <canvas class="tp-canvas" width="${patchSize}" height="${patchSize}"></canvas>
        <output class="tp-readout num" aria-live="polite"></output>
      </div>
    </div>`;

  const root = container.querySelector(".tp");
  const canvas = container.querySelector(".tp-canvas");
  const readout = container.querySelector(".tp-readout");
  const ctx = canvas.getContext("2d");
  let filter = "all";
  // Drawing all 1280 displacement lines at once is an unreadable hairball, so
  // the flow field is opt-in and hovering always draws the single line you are
  // pointing at. The points themselves carry the verdict.
  let showVectors = false;

  const visible = (i) =>
    filter === "all" || (filter === "inliers") === !!mask[i];

  function draw(highlight = -1) {
    ctx.clearRect(0, 0, patchSize, patchSize);
    for (let i = 0; i < n; i++) {
      if (!visible(i)) continue;
      const [x, y] = moving[i];
      const [rx, ry] = ref[i] || [x, y];
      const colour = mask[i] ? KEPT : REJECTED;
      const hot = i === highlight;

      // The displacement each correspondence claims, moving endpoint to
      // reference endpoint. A long line is a match that disagrees with its
      // neighbours, which is exactly what RANSAC threw out.
      if (showVectors || hot) {
        ctx.strokeStyle = colour;
        ctx.globalAlpha = hot ? 1 : 0.28;
        ctx.lineWidth = hot ? 3 : 1;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(rx, ry);
        ctx.stroke();
      }

      // Shape, not just colour. The kept/rejected pair is green vs red, which
      // the palette validator puts at deutan dE 8.5 -- above the >=8 target but
      // only just -- so the verdict is also carried by fill: kept is a solid
      // disc, rejected is a hollow ring.
      ctx.globalAlpha = 1;
      const r = hot ? 6 : 3.2;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      if (mask[i]) {
        ctx.fillStyle = colour;
        ctx.fill();
      } else {
        ctx.strokeStyle = colour;
        ctx.lineWidth = hot ? 2.4 : 1.4;
        ctx.stroke();
      }
    }
  }

  function nearest(px, py) {
    let best = -1;
    let bestDist = 26 * 26;
    for (let i = 0; i < n; i++) {
      if (!visible(i)) continue;
      const dx = moving[i][0] - px;
      const dy = moving[i][1] - py;
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
    readout.textContent = i < 0
      ? ""
      : `#${i} · ${mask[i] ? "kept" : "rejected"} · ` +
        `${(residuals[i] ?? 0).toFixed(2)} px reprojection error`;
    draw(i);
  });
  canvas.addEventListener("pointerleave", () => {
    readout.textContent = "";
    draw();
  });

  root.querySelector(".tp-filters").addEventListener("click", (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    if (btn.classList.contains("tp-vectors")) {
      showVectors = !showVectors;
      btn.setAttribute("aria-pressed", String(showVectors));
      draw();
      return;
    }
    filter = btn.dataset.filter;
    root.dataset.filter = filter;
    root.querySelectorAll(".tp-filters .tool[data-filter]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    draw();
  });

  draw();
  return {
    setFilter(f) { filter = f; root.dataset.filter = f; draw(); },
    destroy() { container.innerHTML = ""; },
  };
}
