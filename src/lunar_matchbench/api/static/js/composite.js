// The alignment compositor.
//
// One canvas, several ways of putting the registered Chandrayaan-2 patch and
// the LROC reference on top of each other. Each mode answers the same question
// -- did this actually line up -- but they fail differently, which is the point:
// a swipe hides a small rotation that a checkerboard makes obvious, and an edge
// overlay catches sub-pixel drift that a difference image washes out.
//
// All of it is computed from the two patches in the browser, so the cell size
// and blend are live rather than baked into a rendered picture.
//
// Colour keeps its meaning: --isro marks Chandrayaan-2 pixels, --nasa marks
// LROC. Where a mode needs a two-channel false colour it uses those same two
// hues, so a fringe still tells you which mission is which.

import { patchUrl } from "./api.js";

const ISRO = [255, 122, 69];
const NASA = [91, 156, 255];

export const MODES = [
  { id: "swipe", label: "Swipe", hint: "Drag the divider. Terrain should run straight through the seam." },
  { id: "checker", label: "Checker", hint: "Alternating tiles from each image. Craters should cross tile edges unbroken." },
  { id: "overlay", label: "Overlay", hint: "Both at once in mission colours, brightness ranges matched. Neutral grey means agreement; saffron or blue fringes mean one image sits off the other." },
  { id: "edges", label: "Edges", hint: "Ridges and crater rims from each image. Overlapping outlines mean a tight fit." },
  { id: "difference", label: "Difference", hint: "One subtracted from the other after matching their brightness ranges, so what is left is structural. Dark is agreement; bright outlines are edges that did not land on top of each other." },
  { id: "triptych", label: "Side by side", hint: "Moving, reference and registered result, in that order." },
];

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`could not load ${src}`));
    img.src = src;
  });
}

function toGray(img, size) {
  const c = document.createElement("canvas");
  c.width = size;
  c.height = size;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, size, size);
  const d = ctx.getImageData(0, 0, size, size).data;
  const out = new Uint8ClampedArray(size * size);
  const valid = new Uint8Array(size * size);
  for (let i = 0, p = 0; i < d.length; i += 4, p++) {
    // The warped patch carries its footprint in alpha: outside it there is no
    // CH2 data at all, which must never be compared against the reference.
    valid[p] = d[i + 3] > 8 ? 1 : 0;
    out[p] = valid[p] ? (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) : 0;
  }
  return { gray: out, valid };
}

/** Sobel gradient magnitude -- crater rims and ridges, not brightness. */
function edges(gray, size) {
  const out = new Uint8ClampedArray(size * size);
  for (let y = 1; y < size - 1; y++) {
    for (let x = 1; x < size - 1; x++) {
      const i = y * size + x;
      const gx =
        -gray[i - size - 1] - 2 * gray[i - 1] - gray[i + size - 1] +
        gray[i - size + 1] + 2 * gray[i + 1] + gray[i + size + 1];
      const gy =
        -gray[i - size - 1] - 2 * gray[i - size] - gray[i - size + 1] +
        gray[i + size - 1] + 2 * gray[i + size] + gray[i + size + 1];
      out[i] = Math.min(255, Math.hypot(gx, gy) * 0.5);
    }
  }
  return out;
}

/**
 * Put two images on the same radiometric footing over their shared area.
 *
 * TMC-2 and NAC are different sensors with different response and different sun
 * angles, so a raw subtraction is dominated by that offset -- the whole frame
 * comes out uniformly hot and says nothing about alignment. Matching mean and
 * spread over the overlapping pixels first leaves structural disagreement,
 * which is what a difference view is actually for.
 */
function matchRadiometry(a, b, valid) {
  let n = 0, sa = 0, sb = 0;
  for (let i = 0; i < a.length; i++) {
    if (!valid[i]) continue;
    n++; sa += a[i]; sb += b[i];
  }
  if (!n) return { a, b };
  const ma = sa / n;
  const mb = sb / n;
  let va = 0, vb = 0;
  for (let i = 0; i < a.length; i++) {
    if (!valid[i]) continue;
    va += (a[i] - ma) ** 2;
    vb += (b[i] - mb) ** 2;
  }
  const sda = Math.sqrt(va / n) || 1;
  const sdb = Math.sqrt(vb / n) || 1;
  const outA = new Uint8ClampedArray(a.length);
  const outB = new Uint8ClampedArray(b.length);
  for (let i = 0; i < a.length; i++) {
    outA[i] = 128 + ((a[i] - ma) / sda) * 48;
    outB[i] = 128 + ((b[i] - mb) / sdb) * 48;
  }
  return { a: outA, b: outB };
}

function autoGain(channel) {
  // Edge magnitudes are mostly small; without a stretch the overlay is black.
  let hi = 1;
  for (let i = 0; i < channel.length; i += 7) if (channel[i] > hi) hi = channel[i];
  return 235 / hi;
}

export function mountComposite(container, { jobId, patchSize = 1024, hasWarped = true }) {
  const size = patchSize;
  container.innerHTML = `
    <div class="cmp" data-mode="swipe" style="--split:.5">
      <canvas class="cmp-canvas" width="${size}" height="${size}"></canvas>
      <div class="cmp-handle" role="slider" tabindex="0" aria-label="Comparison split"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="50"></div>
      <span class="cmp-tag cmp-tag--l">CH2 TMC-2</span>
      <span class="cmp-tag cmp-tag--r">LROC NAC</span>
      <p class="cmp-loading">Loading patches&hellip;</p>
    </div>
    <div class="cmp-controls">
      <label class="cmp-ctl" data-for="checker">
        <span class="eyebrow">Tile size</span>
        <input type="range" id="cmp-cells" min="2" max="16" step="1" value="8" />
      </label>
      <p class="cmp-hint" id="cmp-hint"></p>
    </div>`;

  const cmp = container.querySelector(".cmp");
  const canvas = container.querySelector(".cmp-canvas");
  const handle = container.querySelector(".cmp-handle");
  const loading = container.querySelector(".cmp-loading");
  const hint = container.querySelector("#cmp-hint");
  const cellsInput = container.querySelector("#cmp-cells");
  const ctx = canvas.getContext("2d");

  let mode = "swipe";
  let split = 0.5;
  let cells = 8;
  let data = null;      // { moving, reference, registered, edgeReg, edgeRef }

  function paintMessage(text) {
    ctx.fillStyle = "#0E141B";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#7A8794";
    ctx.font = `${Math.round(size / 34)}px ui-monospace, monospace`;
    ctx.textAlign = "center";
    ctx.fillText(text, canvas.width / 2, canvas.height / 2);
  }

  function draw() {
    if (!data) return;
    const { moving, reference, registered, valid, edgeReg, edgeRef } = data;

    if (mode === "triptych") {
      // Three panels: what went in, what it was matched against, what came out.
      canvas.width = size * 3 + 16;
      canvas.height = size;
      ctx.fillStyle = "#0E141B";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const put = (gray, x) => {
        const im = ctx.createImageData(size, size);
        for (let i = 0, p = 0; p < gray.length; p++, i += 4) {
          im.data[i] = im.data[i + 1] = im.data[i + 2] = gray[p];
          im.data[i + 3] = 255;
        }
        ctx.putImageData(im, x, 0);
      };
      put(moving, 0);
      put(reference, size + 8);
      put(registered, size * 2 + 16);
      return;
    }

    canvas.width = size;
    canvas.height = size;
    const im = ctx.createImageData(size, size);
    const out = im.data;
    const gainReg = mode === "edges" ? autoGain(edgeReg) : 1;
    const gainRef = mode === "edges" ? autoGain(edgeRef) : 1;
    // Overlay and difference compare brightness directly, so they need the two
    // sensors on the same footing first; swipe and checker show each image
    // as-is and must not be altered.
    const matched = (mode === "overlay" || mode === "difference")
      ? matchRadiometry(registered, reference, valid)
      : null;
    const cellPx = size / cells;
    const splitPx = split * size;

    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const p = y * size + x;
        const i = p * 4;
        const a = matched ? matched.a[p] : registered[p];   // registered CH2
        const b = matched ? matched.b[p] : reference[p];    // LROC reference
        let r, g, bl;

        // No CH2 coverage here, so there is nothing to compare. Rendered as a
        // flat slate rather than a bright difference, which would read as a
        // catastrophic misalignment that never happened.
        if (!valid[p] && mode !== "swipe" && mode !== "checker") {
          out[i] = 22; out[i + 1] = 28; out[i + 2] = 36; out[i + 3] = 255;
          continue;
        }

        switch (mode) {
          case "swipe": {
            const v = x < splitPx ? a : b;
            r = g = bl = v;
            break;
          }
          case "checker": {
            const odd = (Math.floor(x / cellPx) + Math.floor(y / cellPx)) % 2 === 0;
            const v = odd ? a : b;
            r = g = bl = v;
            break;
          }
          case "overlay": {
            // Each image drives one mission-coloured channel set. Where they
            // agree the two tints sum to neutral; where they do not, the offset
            // shows as a saffron/blue fringe on every edge.
            r = (a * ISRO[0] + b * NASA[0]) / 510;
            g = (a * ISRO[1] + b * NASA[1]) / 510;
            bl = (a * ISRO[2] + b * NASA[2]) / 510;
            r *= 2; g *= 2; bl *= 2;
            break;
          }
          case "edges": {
            const ea = edgeReg[p] * gainReg;
            const eb = edgeRef[p] * gainRef;
            r = (ea * ISRO[0] + eb * NASA[0]) / 510;
            g = (ea * ISRO[1] + eb * NASA[1]) / 510;
            bl = (ea * ISRO[2] + eb * NASA[2]) / 510;
            r *= 2; g *= 2; bl *= 2;
            break;
          }
          default: {  // difference
            // Radiometry is already matched, so what is left is structural.
            const d = Math.abs(a - b);
            r = Math.min(255, d * 3.4);
            g = Math.min(255, d * 1.7);
            bl = Math.min(255, d * 0.8);
            break;
          }
        }
        out[i] = r;
        out[i + 1] = g;
        out[i + 2] = bl;
        out[i + 3] = 255;
      }
    }
    ctx.putImageData(im, 0, 0);
  }

  function applyMode(next) {
    mode = next;
    cmp.dataset.mode = next;
    const meta = MODES.find((m) => m.id === next);
    hint.textContent = meta ? meta.hint : "";
    container.querySelector('[data-for="checker"]').hidden = next !== "checker";
    draw();
  }

  // ── split handling, shared by the swipe mode ──────────────────────────────
  function setSplit(fraction) {
    split = Math.max(0, Math.min(1, fraction));
    cmp.style.setProperty("--split", String(split));
    handle.setAttribute("aria-valuenow", String(Math.round(split * 100)));
    if (mode === "swipe") draw();
  }

  let dragging = false;
  const pointToSplit = (clientX) => {
    const rect = cmp.getBoundingClientRect();
    return (clientX - rect.left) / rect.width;
  };
  cmp.addEventListener("pointerdown", (e) => {
    if (mode !== "swipe") return;
    dragging = true;
    cmp.setPointerCapture?.(e.pointerId);
    setSplit(pointToSplit(e.clientX));
  });
  cmp.addEventListener("pointermove", (e) => { if (dragging) setSplit(pointToSplit(e.clientX)); });
  const stop = (e) => {
    dragging = false;
    if (cmp.hasPointerCapture?.(e.pointerId)) cmp.releasePointerCapture(e.pointerId);
  };
  cmp.addEventListener("pointerup", stop);
  cmp.addEventListener("pointercancel", stop);
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 0.1 : 0.02;
    const moves = { ArrowLeft: split - step, ArrowRight: split + step, Home: 0, End: 1 };
    if (e.key in moves) { setSplit(moves[e.key]); e.preventDefault(); }
  });

  cellsInput.addEventListener("input", () => {
    cells = Number(cellsInput.value);
    draw();
  });

  // ── load ──────────────────────────────────────────────────────────────────
  paintMessage("Loading patches…");
  Promise.all([
    loadImage(patchUrl(jobId, "ch2")),
    loadImage(patchUrl(jobId, "lroc")),
    // A run that never converged produced no warped patch. Asking for one
    // anyway is a guaranteed 404 -- harmless, but it puts a red error in
    // the console of a public page for something working as intended.
    hasWarped ? loadImage(patchUrl(jobId, "warped")).catch(() => null) : null,
  ])
    .then(([movingImg, refImg, warpedImg]) => {
      const moving = toGray(movingImg, size).gray;
      const reference = toGray(refImg, size).gray;
      // A failed run has no warped patch; fall back to the unregistered moving
      // image so the comparison still shows what was attempted.
      const reg = warpedImg
        ? toGray(warpedImg, size)
        : { gray: moving, valid: new Uint8Array(size * size).fill(1) };
      data = {
        moving, reference,
        registered: reg.gray,
        valid: reg.valid,
        edgeReg: edges(reg.gray, size),
        edgeRef: edges(reference, size),
      };
      loading.remove();
      // Signals that the pixels are in hand: every mode is a no-op until then,
      // so both the UI and its tests need to know when compositing can happen.
      cmp.dataset.ready = "1";
      applyMode(mode);
    })
    .catch(() => {
      loading.remove();
      cmp.dataset.ready = "error";
      paintMessage("Patches unavailable for this run");
    });

  return {
    setMode: applyMode,
    setSplit,
    destroy() { container.innerHTML = ""; },
  };
}
