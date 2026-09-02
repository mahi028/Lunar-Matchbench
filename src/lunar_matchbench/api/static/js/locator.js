// The scan-line strip locator.
//
// Both instruments are pushbroom line scanners: an LROC NAC strip is tens of
// thousands of single scan lines stacked in acquisition order, and the
// pipeline's hardest job is deciding which line corresponds to the
// Chandrayaan-2 patch. This rail draws the whole strip to scale, so
// "the geometry estimate was 2,105 lines off" and "the entire strip was
// searched and nothing matched" become things you can see rather than
// sentences buried in an error string.

const NS = "http://www.w3.org/2000/svg";

function node(name, attrs = {}) {
  const el = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

export function clearLocator(root) {
  root.innerHTML = `
    <span class="eyebrow">Scan-line strip</span>
    <p class="rail-empty">Run a registration to locate the reference scan line.</p>`;
  root.removeAttribute("data-coverage");
}

export function renderLocator(root, loc) {
  if (!loc || !loc.total_lines) return clearLocator(root);

  const H = 440;          // strip height in viewBox units
  const W = 24;           // strip width
  const X = 40;           // left inset, leaving room for the axis captions
  const total = loc.total_lines;
  const y = (line) => Math.max(0, Math.min(H, (line / total) * H));

  root.setAttribute("data-coverage", loc.whole_strip_searched ? "full" : "partial");
  root.innerHTML = `<span class="eyebrow">Scan-line strip</span>`;

  const lock = loc.used_center_line ?? loc.approx_center_line ?? 0;
  const est = loc.approx_center_line ?? 0;
  const lockColour = loc.confident ? "var(--good)" : "var(--warn)";

  const svg = node("svg", {
    viewBox: `0 0 118 ${H + 30}`,
    width: "100%",
    role: "img",
    "aria-label":
      `LROC strip of ${total} scan lines. ${loc.lines_searched} lines searched. ` +
      `Reference line ${lock}, ${loc.confident ? "visually verified" : "geometry estimate only"}.`,
  });
  const g = node("g", { transform: "translate(0,14)" });

  // The strip itself, drawn to scale top (line 0) to bottom (last line).
  g.appendChild(node("rect", {
    x: X, y: 0, width: W, height: H, rx: 2,
    fill: "var(--panel-2)", stroke: "var(--rule)",
  }));

  // The span actually examined. Windows are contiguous around the centre the
  // search settled on, so one band is a truthful summary of the sweep.
  const searched = Math.min(loc.lines_searched || 0, total);
  if (searched > 0) {
    const lo = Math.max(0, Math.min(total - searched, lock - searched / 2));
    g.appendChild(node("rect", {
      "data-mark": "searched",
      x: X, y: y(lo), width: W,
      height: Math.max(2, y(lo + searched) - y(lo)),
      fill: "var(--nasa)", opacity: 0.22,
    }));
  }

  // Where the pushbroom geometry said to look.
  g.appendChild(node("line", {
    "data-mark": "estimate",
    x1: X - 6, y1: y(est), x2: X + W + 6, y2: y(est),
    stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "3 3",
  }));

  // Where correlation actually put it.
  g.appendChild(node("line", {
    "data-mark": "lock",
    x1: X - 9, y1: y(lock), x2: X + W + 9, y2: y(lock),
    stroke: lockColour, "stroke-width": 2,
  }));
  g.appendChild(node("circle", {
    cx: X + W + 9, cy: y(lock), r: 3.2, fill: lockColour,
  }));

  // Endpoint captions, so the rail reads as a coordinate axis rather than a bar.
  const cap = (yy, text, anchor = "end", xx = X - 8) => {
    const t = node("text", {
      x: xx, y: yy, "text-anchor": anchor,
      fill: "var(--muted)", "font-size": 9, "font-family": "var(--mono)",
    });
    t.textContent = text;
    return t;
  };
  g.appendChild(cap(4, "0"));
  g.appendChild(cap(H + 2, String(total)));

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
