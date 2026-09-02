// The scan-line strip locator.
//
// Both instruments are pushbroom line scanners: an LROC NAC strip is tens of
// thousands of single scan lines stacked in acquisition order, and the
// pipeline's hardest job is deciding which line corresponds to the
// Chandrayaan-2 patch. This rail draws the whole strip to scale, so a
// 2,105-line drift, or a strip searched end to end with nothing found, become
// things you can see rather than sentences buried in an error string.
//
// It is also draggable. Releasing anywhere on the rail fetches the imagery at
// that scan line -- one ranged read, cached -- which turns the rail from a
// readout into a way to look anywhere inside a half-gigabyte product. Nothing
// is fetched while the pointer is down, so a long drag costs one request.

const NS = "http://www.w3.org/2000/svg";
const H = 440;   // strip height in viewBox units
const W = 24;    // strip width
const X = 40;    // left inset, leaving room for the axis captions

function node(name, attrs = {}) {
  const el = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

export function clearLocator(root) {
  root.innerHTML = `
    <span class="eyebrow">Scan-line strip</span>
    <p class="rail-empty">
      Run a registration to locate the reference scan line. The rail will show the
      whole LROC strip, where the search looked, and where it locked on.
    </p>`;
  root.removeAttribute("data-coverage");
}

export function renderLocator(root, loc, { jobId, onProbe } = {}) {
  if (!loc || !loc.total_lines) return clearLocator(root);

  const total = loc.total_lines;
  const y = (line) => Math.max(0, Math.min(H, (line / total) * H));
  const lineAt = (yy) => Math.round(Math.max(0, Math.min(1, yy / H)) * total);

  const lock = loc.used_center_line ?? loc.approx_center_line ?? 0;
  const est = loc.approx_center_line ?? 0;
  const lockColour = loc.confident ? "var(--good)" : "var(--warn)";

  root.setAttribute("data-coverage", loc.whole_strip_searched ? "full" : "partial");
  root.innerHTML = `<span class="eyebrow">Scan-line strip</span>`;

  const svg = node("svg", {
    viewBox: `0 0 118 ${H + 30}`,
    width: "100%",
    class: "rail-svg",
    role: "slider",
    tabindex: "0",
    "aria-label": "Scan line",
    "aria-valuemin": "0",
    "aria-valuemax": String(total),
    "aria-valuenow": String(lock),
  });
  const g = node("g", { transform: "translate(0,14)" });

  g.appendChild(node("rect", {
    x: X, y: 0, width: W, height: H, rx: 2,
    fill: "var(--panel-2)", stroke: "var(--rule)",
  }));

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

  g.appendChild(node("line", {
    "data-mark": "estimate",
    x1: X - 6, y1: y(est), x2: X + W + 6, y2: y(est),
    stroke: "var(--muted)", "stroke-width": 1, "stroke-dasharray": "3 3",
  }));
  g.appendChild(node("line", {
    "data-mark": "lock",
    x1: X - 9, y1: y(lock), x2: X + W + 9, y2: y(lock),
    stroke: lockColour, "stroke-width": 2,
  }));
  g.appendChild(node("circle", { cx: X + W + 9, cy: y(lock), r: 3.2, fill: lockColour }));

  // The draggable probe, parked on the lock until moved.
  const probe = node("g", { "data-mark": "probe", opacity: 0 });
  const probeLine = node("line", {
    x1: X - 12, y1: y(lock), x2: X + W + 12, y2: y(lock),
    stroke: "var(--signal)", "stroke-width": 1.5,
  });
  const probeGrip = node("rect", {
    x: X - 4, y: y(lock) - 3, width: W + 8, height: 6, rx: 3,
    fill: "var(--signal)", opacity: 0.9,
  });
  probe.appendChild(probeLine);
  probe.appendChild(probeGrip);
  g.appendChild(probe);

  const cap = (yy, content) => {
    const t = node("text", {
      x: X - 8, y: yy, "text-anchor": "end",
      fill: "var(--muted)", "font-size": 9, "font-family": "var(--mono)",
    });
    t.textContent = content;
    return t;
  };
  g.appendChild(cap(4, "0"));
  g.appendChild(cap(H + 2, String(total)));

  // A hit area wider than the 24-unit strip, so the rail is easy to grab.
  const hit = node("rect", {
    x: X - 14, y: 0, width: W + 28, height: H,
    fill: "transparent", style: "cursor:ns-resize",
  });
  g.appendChild(hit);

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

  if (!jobId) return;

  const probeBox = document.createElement("div");
  probeBox.className = "rail-probe";
  probeBox.innerHTML = `<p class="rail-hint">Drag the strip to look anywhere in it.</p>`;
  root.appendChild(probeBox);

  // ── dragging ──────────────────────────────────────────────────────────────
  let dragging = false;
  let probeLineNo = lock;

  function moveProbeTo(clientY) {
    const rect = svg.getBoundingClientRect();
    // 14 units of translate plus the viewBox scale between units and pixels.
    const scale = rect.height / (H + 30);
    const local = (clientY - rect.top) / scale - 14;
    probeLineNo = lineAt(local);
    const yy = y(probeLineNo);
    probe.setAttribute("opacity", "1");
    probeLine.setAttribute("y1", yy);
    probeLine.setAttribute("y2", yy);
    probeGrip.setAttribute("y", yy - 3);
    svg.setAttribute("aria-valuenow", String(probeLineNo));
    probeBox.innerHTML =
      `<p class="rail-hint num">Line ${probeLineNo.toLocaleString()}</p>`;
  }

  function showPreview() {
    probeBox.innerHTML = `
      <p class="rail-hint num">Line ${probeLineNo.toLocaleString()}</p>
      <img class="rail-preview" alt="LROC strip at scan line ${probeLineNo}"
           src="/api/strip/${jobId}/preview.png?line=${probeLineNo}" />`;
    const img = probeBox.querySelector("img");
    img.addEventListener("error", () => {
      probeBox.innerHTML = `
        <p class="rail-hint num">Line ${probeLineNo.toLocaleString()}</p>
        <p class="rail-empty">No usable imagery at this line.</p>`;
    });
    onProbe?.(probeLineNo);
  }

  hit.addEventListener("pointerdown", (e) => {
    dragging = true;
    svg.setPointerCapture?.(e.pointerId);
    moveProbeTo(e.clientY);
    e.preventDefault();
  });
  svg.addEventListener("pointermove", (e) => {
    if (dragging) moveProbeTo(e.clientY);
  });
  const release = (e) => {
    if (!dragging) return;
    dragging = false;
    if (svg.hasPointerCapture?.(e.pointerId)) svg.releasePointerCapture(e.pointerId);
    // Fetch only on release: a long drag costs one ranged read, not dozens.
    showPreview();
  };
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", release);

  svg.addEventListener("keydown", (e) => {
    const stepBy = e.shiftKey ? Math.round(total / 20) : Math.round(total / 200);
    const moves = {
      ArrowDown: probeLineNo + stepBy,
      ArrowUp: probeLineNo - stepBy,
      Home: 0,
      End: total,
    };
    if (!(e.key in moves)) return;
    e.preventDefault();
    probeLineNo = Math.max(0, Math.min(total, moves[e.key]));
    const yy = y(probeLineNo);
    probe.setAttribute("opacity", "1");
    probeLine.setAttribute("y1", yy);
    probeLine.setAttribute("y2", yy);
    probeGrip.setAttribute("y", yy - 3);
    svg.setAttribute("aria-valuenow", String(probeLineNo));
    probeBox.innerHTML = `<p class="rail-hint num">Line ${probeLineNo.toLocaleString()}</p>`;
  });
  svg.addEventListener("keyup", (e) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) showPreview();
  });
}
