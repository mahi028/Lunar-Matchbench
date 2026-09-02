// What the registration actually did, geometrically.
//
// The homography is the answer the whole pipeline produces, and until now it
// reached the screen only as nine raw numbers in the provenance blob. Nobody
// reads a 3x3 matrix and pictures a rotation. This decomposes it into the
// physical motions it encodes -- shift, rotation, scale, shear, perspective --
// and draws a square grid pushed through the transform so the distortion is
// visible rather than described.
//
// Every number here is derived from the fitted matrix; nothing is illustrative.

const NS = "http://www.w3.org/2000/svg";

function node(name, attrs = {}) {
  const el = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

/**
 * Split a homography into interpretable parts.
 *
 * The upper-left 2x2 is decomposed by QR into a rotation and an upper-triangular
 * matrix, which is the standard way to separate "turned" from "stretched and
 * skewed". Translation is the third column; the bottom row is the perspective
 * term, which for a near-planar lunar surface should be almost zero -- a large
 * value there is a warning that the fit is doing something unphysical.
 */
export function decomposeHomography(H) {
  const [a, b, tx] = H[0];
  const [c, d, ty] = H[1];
  const [p, q] = H[2];

  // QR by Gram-Schmidt on the columns of [[a,b],[c,d]].
  const sx = Math.hypot(a, c) || 1e-9;
  const r11 = a / sx;
  const r21 = c / sx;
  const shearNum = r11 * b + r21 * d;
  const by = b - shearNum * r11;
  const dy = d - shearNum * r21;
  const sy = Math.hypot(by, dy) || 1e-9;

  return {
    shiftX: tx,
    shiftY: ty,
    shift: Math.hypot(tx, ty),
    rotationDeg: (Math.atan2(r21, r11) * 180) / Math.PI,
    scaleX: sx,
    scaleY: sy,
    scale: (sx + sy) / 2,
    shear: shearNum / sy,
    perspective: Math.hypot(p, q),
  };
}

function fmt(value, digits = 2) {
  return Number(value).toFixed(digits);
}

export function renderTransform(container, result) {
  const H = result.homography;
  if (!H || H.length !== 3) {
    container.innerHTML = "";
    return;
  }

  const size = result.patch_size || 1024;
  const t = decomposeHomography(H);

  const fig = document.createElement("figure");
  fig.className = "chart chart-transform";
  fig.innerHTML = `<figcaption class="eyebrow">The transform that was fitted</figcaption>`;

  // ── the warped grid ───────────────────────────────────────────────────────
  const VB = 240;
  const svg = node("svg", { viewBox: `0 0 ${VB} ${VB}`, class: "tf-grid", width: "100%" });
  const project = (x, y) => {
    const w = H[2][0] * x + H[2][1] * y + H[2][2];
    return [
      ((H[0][0] * x + H[0][1] * y + H[0][2]) / w) * (VB / size),
      ((H[1][0] * x + H[1][1] * y + H[1][2]) / w) * (VB / size),
    ];
  };

  const N = 6;
  const step = size / N;

  // The original patch outline, for reference.
  svg.appendChild(node("rect", {
    x: 0, y: 0, width: VB, height: VB,
    fill: "none", stroke: "var(--rule)", "stroke-dasharray": "3 3",
  }));

  // The same square grid after the transform.
  for (let i = 0; i <= N; i++) {
    const along = (fixed, horizontal) => {
      const pts = [];
      for (let j = 0; j <= N; j++) {
        const [x, y] = horizontal
          ? project(j * step, fixed * step)
          : project(fixed * step, j * step);
        pts.push(`${x.toFixed(2)},${y.toFixed(2)}`);
      }
      return pts.join(" ");
    };
    const edge = i === 0 || i === N;
    for (const horizontal of [true, false]) {
      svg.appendChild(node("polyline", {
        points: along(i, horizontal),
        fill: "none",
        stroke: edge ? "var(--isro)" : "var(--nasa)",
        "stroke-width": edge ? 1.6 : 0.8,
        opacity: edge ? 0.95 : 0.45,
      }));
    }
  }
  fig.appendChild(svg);

  const legend = document.createElement("div");
  legend.className = "chart-legend";
  legend.innerHTML = `
    <span class="lg"><i class="lg-mark lg-mark--disc" style="--c:var(--rule)"></i>original patch</span>
    <span class="lg"><i class="lg-mark lg-mark--disc" style="--c:var(--isro)"></i>after transform</span>`;
  fig.appendChild(legend);

  // ── the numbers ───────────────────────────────────────────────────────────
  // Thresholds are what a plausible cross-mission fit looks like: a lunar patch
  // is effectively planar, so strong perspective or shear means the homography
  // is absorbing error rather than describing real geometry.
  const flags = [
    {
      label: "Shift",
      value: `${fmt(t.shift, 1)} px`,
      note: `${fmt(t.shiftX, 1)} across, ${fmt(t.shiftY, 1)} down. How far the two ` +
            `images were offset before alignment.`,
      ok: true,
    },
    {
      label: "Rotation",
      value: `${fmt(t.rotationDeg, 2)}°`,
      note: `Relative turn between the two passes, from their different orbital ` +
            `tracks over the same ground.`,
      ok: Math.abs(t.rotationDeg) < 25,
    },
    {
      label: "Scale",
      value: `${fmt(t.scale, 3)}×`,
      note: `${fmt(t.scaleX, 3)} across against ${fmt(t.scaleY, 3)} down. Near 1.0 ` +
            `means the resolution matching was right; far from it means the assumed ` +
            `pixel sizes disagree with the imagery.`,
      ok: t.scale > 0.75 && t.scale < 1.35,
    },
    {
      label: "Shear",
      value: fmt(t.shear, 3),
      note: `Squareness. A rigid ground truth should be near zero; large shear ` +
            `means the fit is bending the frame to absorb mismatched points.`,
      ok: Math.abs(t.shear) < 0.15,
    },
    {
      label: "Perspective",
      value: t.perspective.toExponential(1),
      note: `Out-of-plane tilt. A lunar patch this size is effectively flat, so ` +
            `anything above about 1e-4 is a sign the fit is unphysical.`,
      ok: t.perspective < 1e-4,
    },
  ];

  const table = document.createElement("dl");
  table.className = "tf-table";
  table.innerHTML = flags.map((f) => `
    <div class="tf-row${f.ok ? "" : " tf-row--warn"}">
      <dt>${f.label}</dt>
      <dd class="num">${f.value}</dd>
      <dd class="tf-note">${f.note}</dd>
    </div>`).join("");
  fig.appendChild(table);

  const suspicious = flags.filter((f) => !f.ok);
  const verdict = document.createElement("p");
  verdict.className = "chart-explain";
  verdict.textContent = suspicious.length
    ? `The grid shows the shape the fitted transform imposes on a square patch. ` +
      `${suspicious.map((f) => f.label.toLowerCase()).join(" and ")} ` +
      `${suspicious.length === 1 ? "is" : "are"} outside what a flat lunar surface ` +
      `should need, so treat this alignment with caution even if the error looks low.`
    : `The grid shows the shape the fitted transform imposes on a square patch. ` +
      `Shear and perspective are both small here, so the fit is a plain shift, ` +
      `turn and rescale — which is what registering two views of flat ground ` +
      `should produce.`;
  fig.appendChild(verdict);

  container.innerHTML = "";
  container.appendChild(fig);
}
