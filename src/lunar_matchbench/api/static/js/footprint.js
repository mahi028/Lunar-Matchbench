// The geographic footprint map.
//
// Before any pixel comparison means anything, the two images have to cover the
// same ground. This draws that check: the LROC NAC strip, the Chandrayaan-2
// patch, and the part they share.
//
// It replaces a matplotlib PNG that was rendered in the poster's print palette
// -- cream background, desaturated slate -- which inside the dark console read
// as grey boxes on white and said nothing about which footprint belonged to
// which mission. Drawn here instead, it uses the same orange-for-ISRO and
// blue-for-NASA the rest of the console uses, stays crisp at any size, and
// costs no image bytes.
//
// Geometry is plotted in degrees at equal scale on both axes, so the shapes and
// their proportions are honest: an LROC strip really is that long and narrow
// next to a patch. Longitude is not stretched by 1/cos(lat) -- below ~20 deg
// that is a sub-4% distortion, and faking a projection would be a worse lie
// than the small one.

const NS = "http://www.w3.org/2000/svg";

const el = (name, attrs = {}) => {
  const n = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
  return n;
};

const fmtLat = (v) => `${v.toFixed(2)}°N`;
const fmtLon = (v) => `${v.toFixed(2)}°E`;

// A five-pointed star, so the target is identifiable by shape and not only by
// colour -- the same reason kept tie-points as discs versus rings.
function starPath(cx, cy, outer, inner) {
  const pts = [];
  for (let i = 0; i < 10; i++) {
    const r = i % 2 ? inner : outer;
    const a = (Math.PI / 5) * i - Math.PI / 2;
    pts.push(`${(cx + r * Math.cos(a)).toFixed(2)},${(cy + r * Math.sin(a)).toFixed(2)}`);
  }
  return `M${pts.join("L")}Z`;
}

export function renderFootprint(host, fp) {
  if (!host || !fp || !fp.ch2_bbox || !fp.lroc_bbox) return false;

  const ch2 = { lat: fp.ch2_bbox.lat, lon: fp.ch2_bbox.lon };
  const lroc = { lat: fp.lroc_bbox.lat, lon: fp.lroc_bbox.lon };
  const target = fp.target || {};

  const latLo = Math.min(ch2.lat[0], lroc.lat[0]);
  const latHi = Math.max(ch2.lat[1], lroc.lat[1]);
  const lonLo = Math.min(ch2.lon[0], lroc.lon[0]);
  const lonHi = Math.max(ch2.lon[1], lroc.lon[1]);
  const padLat = (latHi - latLo) * 0.12 || 0.05;
  const padLon = (lonHi - lonLo) * 0.12 || 0.05;
  const y0 = latLo - padLat, y1 = latHi + padLat;
  const x0 = lonLo - padLon, x1 = lonHi + padLon;

  // Equal degrees per unit on both axes; the plot area takes whichever shape
  // the geometry actually is.
  const PLOT = 300;
  const spanLon = x1 - x0, spanLat = y1 - y0;
  const k = PLOT / Math.max(spanLon, spanLat);
  const w = spanLon * k, h = spanLat * k;
  // The right margin holds the "CH2 patch" leader label. Without it the text
  // overflowed the SVG and printed on top of the readout beside the map.
  const LABEL_W = 62, LEADER = 26;
  const M = { l: 52, r: 14 + LEADER + LABEL_W, t: 14, b: 34 };
  const W = w + M.l + M.r, H = h + M.t + M.b;

  const px = (lon) => M.l + (lon - x0) * k;
  const py = (lat) => M.t + (y1 - lat) * k;   // latitude increases upward

  const svg = el("svg", {
    viewBox: `0 0 ${W.toFixed(1)} ${H.toFixed(1)}`,
    width: W.toFixed(1), height: H.toFixed(1),
    class: "fp-svg", role: "img",
    "aria-label":
      `Footprint map. The Chandrayaan-2 patch spans ${fmtLat(ch2.lat[0])} to ` +
      `${fmtLat(ch2.lat[1])}; the LROC image spans ${fmtLat(lroc.lat[0])} to ` +
      `${fmtLat(lroc.lat[1])}. ${fp.ch2_overlap_pct}% of the patch is covered.`,
  });

  svg.appendChild(el("rect", {
    x: M.l, y: M.t, width: w, height: h, class: "fp-plot",
  }));

  // Tick density comes from the pixels available, not a fixed count. At true
  // scale an LROC strip's longitude span is a few dozen pixels wide, and four
  // eight-character labels there run together into an unreadable smear.
  // ~52px of label plus breathing room. At w/76 the end label (anchored to
  // the axis start) ended exactly where the next one began.
  const nX = Math.max(1, Math.min(3, Math.round(w / 98)));
  const nY = Math.max(1, Math.min(3, Math.round(h / 54)));

  for (let i = 0; i <= nY; i++) {
    const lat = y0 + (spanLat * i) / nY;
    svg.appendChild(el("line", {
      x1: M.l, x2: M.l + w, y1: py(lat).toFixed(1), y2: py(lat).toFixed(1), class: "fp-grid",
    }));
    const ty = el("text", { x: M.l - 7, y: (py(lat) + 3.5).toFixed(1), class: "fp-tick fp-tick--y" });
    ty.textContent = fmtLat(lat);
    svg.appendChild(ty);
  }
  const NARROW = w < 150;   // not enough room for two labels side by side
  for (let i = 0; i <= nX; i++) {
    const lon = x0 + (spanLon * i) / nX;
    svg.appendChild(el("line", {
      y1: M.t, y2: M.t + h, x1: px(lon).toFixed(1), x2: px(lon).toFixed(1), class: "fp-grid",
    }));
    if (NARROW) continue;
    // The end labels would otherwise hang past the plot and collide with the
    // axis or the panel beside it.
    const anchor = i === 0 ? "start" : i === nX ? "end" : "middle";
    const tx = el("text", {
      x: px(lon).toFixed(1), y: M.t + h + 15, class: "fp-tick", "text-anchor": anchor,
    });
    tx.textContent = fmtLon(lon);
    svg.appendChild(tx);
  }
  if (NARROW) {
    const span = el("text", {
      x: (M.l + w / 2).toFixed(1), y: M.t + h + 15, class: "fp-tick", "text-anchor": "middle",
    });
    span.textContent = `${x0.toFixed(2)}–${x1.toFixed(2)}°E`;
    svg.appendChild(span);
  }

  const box = (b, cls) => el("rect", {
    x: px(b.lon[0]).toFixed(1), y: py(b.lat[1]).toFixed(1),
    width: Math.max(1, (b.lon[1] - b.lon[0]) * k).toFixed(1),
    height: Math.max(1, (b.lat[1] - b.lat[0]) * k).toFixed(1),
    class: cls,
  });

  const defs = el("defs");
  const pat = el("pattern", {
    id: "fp-hatch", width: 6, height: 6, patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  pat.appendChild(el("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "fp-hatch-line" }));
  defs.appendChild(pat);
  svg.appendChild(defs);

  svg.appendChild(box(lroc, "fp-lroc"));
  svg.appendChild(box(ch2, "fp-ch2"));
  if (fp.intersection_bbox) svg.appendChild(box(fp.intersection_bbox, "fp-inter"));

  if (typeof target.lat === "number" && typeof target.lon === "number") {
    svg.appendChild(el("path", {
      d: starPath(px(target.lon), py(target.lat), 7, 2.9), class: "fp-target",
    }));
  }

  // The CH2 patch is a few kilometres inside a strip tens of kilometres long,
  // so at true scale it is a small square. A leader line names it rather than
  // leaving the reader to infer which of three rectangles it is.
  const cx = px((ch2.lon[0] + ch2.lon[1]) / 2);
  const cRight = px(ch2.lon[1]);
  const cyTop = py(ch2.lat[1]);
  const lx = Math.min(cRight + LEADER, M.l + w + LEADER);
  const ly = Math.max(M.t + 12, cyTop - 10);
  svg.appendChild(el("line", { x1: cx, y1: cyTop, x2: lx - 3, y2: ly + 3, class: "fp-leader" }));
  const lbl = el("text", { x: lx, y: ly, class: "fp-label", "text-anchor": "start" });
  lbl.textContent = "CH2 patch";
  svg.appendChild(lbl);

  const pct = fp.ch2_overlap_pct;
  const partial = pct < 99.5;
  host.innerHTML = `
    <figure class="fp">
      <figcaption class="eyebrow">Geographic footprint overlap</figcaption>
      <div class="fp-body">
        <div class="fp-map"></div>
        <div class="fp-side">
          <ul class="fp-key">
            <li><span class="fp-sw fp-sw--lroc"></span>LROC NAC image
              <em>${fp.lroc_filename || ""}</em></li>
            <li><span class="fp-sw fp-sw--ch2"></span>Chandrayaan-2 patch
              <em>&plusmn;${(fp.ch2_half_deg ?? 0.085).toFixed(3)}&deg;</em></li>
            <li><span class="fp-sw fp-sw--inter"></span>Shared ground
              <em>what can be compared</em></li>
            <li><span class="fp-sw fp-sw--target"></span>Target coordinate
              <em>${typeof target.lat === "number"
                    ? `${fmtLat(target.lat)}, ${fmtLon(target.lon)}` : ""}</em></li>
          </ul>
          <dl class="fp-stats">
            <div><dt>Patch covered</dt>
              <dd class="num ${partial ? "is-warn" : "is-good"}">${pct}%</dd></div>
            <div><dt>Shared area</dt>
              <dd class="num">${fp.overlap_area_km2} km&sup2;</dd></div>
            <div><dt>Footprint IoU</dt>
              <dd class="num">${fp.iou}</dd></div>
          </dl>
        </div>
      </div>
      <p class="chart-explain fp-note">${partial
        ? `Only <b>${pct}%</b> of the Chandrayaan-2 patch falls inside this LROC image. ` +
          `The rest has no reference to match against, which caps how well the two can ever agree.`
        : `The Chandrayaan-2 patch sits <b>entirely inside</b> the LROC image&rsquo;s footprint, ` +
          `so the two cover the same ground and a pixel comparison is meaningful.`}</p>
    </figure>`;
  host.querySelector(".fp-map").appendChild(svg);
  return true;
}
