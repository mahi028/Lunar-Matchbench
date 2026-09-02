// Ambient sky: a rotating Moon, drifting stars, and the two spacecraft.
//
// This sits behind the console at low contrast on purpose. The greyscale lunar
// imagery is the actual data and has to stay the brightest thing on screen, so
// nothing here goes above roughly 30% opacity and none of it overlaps a panel's
// content -- it fills the page's empty ground rather than competing for it.
//
// The two satellites are not decoration: one is Chandrayaan-2 in --isro
// saffron, one is LRO in --nasa blue, the same two colours that mark every
// pixel's provenance everywhere else in the interface.

const STAR_COUNT = 900;
const TAU = Math.PI * 2;

function prefersReducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** A stable pseudo-random source, so the sky looks the same across reloads. */
function seeded(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function buildCraterField(size, rand) {
  // One wide offscreen tile of craters, scrolled horizontally to fake rotation.
  const tile = document.createElement("canvas");
  tile.width = size * 2;
  tile.height = size;
  const c = tile.getContext("2d");
  c.fillStyle = "#8e9199";
  c.fillRect(0, 0, tile.width, tile.height);

  for (let i = 0; i < 90; i++) {
    const r = 3 + rand() * (size * 0.06);
    const x = rand() * tile.width;
    const y = rand() * tile.height;
    const shade = 0.5 + rand() * 0.35;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fillStyle = `rgba(60,64,72,${shade * 0.55})`;
    c.fill();
    // A light rim on one side reads as a raised crater wall.
    c.beginPath();
    c.arc(x - r * 0.18, y - r * 0.18, r * 0.92, 0, TAU);
    c.strokeStyle = `rgba(190,195,205,${shade * 0.30})`;
    c.lineWidth = Math.max(1, r * 0.12);
    c.stroke();
  }
  // A couple of maria: broad dark plains, the Moon's most recognisable feature.
  for (let i = 0; i < 4; i++) {
    const r = size * (0.16 + rand() * 0.16);
    c.beginPath();
    c.arc(rand() * tile.width, rand() * tile.height, r, 0, TAU);
    c.fillStyle = "rgba(48,52,60,0.42)";
    c.fill();
  }
  return tile;
}

export function mountSky(canvas) {
  const ctx = canvas.getContext("2d");
  const rand = seeded(20260903);
  const reduced = prefersReducedMotion();

  // A real star field is mostly faint with a few bright ones, not a uniform
  // scatter -- so brightness is skewed and only the top few percent get colour
  // and diffraction spikes. Colours are the actual stellar range: cool blue-white
  // through to warm orange.
  const STAR_TINTS = ["#FFFFFF", "#DCE6FF", "#BFD4FF", "#FFF0D6", "#FFD9B0"];
  const stars = Array.from({ length: STAR_COUNT }, () => {
    const brightness = Math.pow(rand(), 2.4);      // few bright, many faint
    return {
      x: rand(), y: rand(),
      r: 0.35 + brightness * 1.9,
      mag: 0.25 + brightness * 0.95,
      tint: STAR_TINTS[Math.floor(Math.pow(rand(), 2) * STAR_TINTS.length)],
      phase: rand() * TAU,
      speed: 0.6 + rand() * 2.6,
      depth: 0.3 + rand() * 0.7,                   // parallax layer
      spike: brightness > 0.86,
    };
  });

  // A faint band of unresolved stars, the way the Milky Way reads to the eye.
  const dust = Array.from({ length: 26 }, () => ({
    x: rand(), y: rand() * 0.55, r: 0.10 + rand() * 0.26, a: 0.012 + rand() * 0.022,
  }));

  // The two spacecraft this tool is about, on different orbital planes and
  // coloured by mission -- the same saffron and blue that mark every pixel's
  // provenance everywhere else. The orbits are wide enough that both stay on
  // screen: the Moon's centre sits below the fold, so a tight orbit would spend
  // most of its period out of sight.
  // Each crosses the visible sky on its own transit rather than orbiting the
  // Moon's centre: that centre sits below the fold, so an orbit around it spends
  // most of its period off screen -- measured at zero visible frames across a
  // 15-second sample before this changed.
  const sats = [
    // Low in the frame, in the open sky below the console and near the Moon's
    // limb. Higher paths measured as present on the canvas but were completely
    // hidden behind the panels, which is the same as not drawing them.
    { colour: "#FF7A45", label: "CH2", y: 0.86, bow: -0.06, period: 26, phase: 0.15, dir: 1 },
    { colour: "#5B9CFF", label: "LRO", y: 0.95, bow: -0.04, period: 38, phase: 0.60, dir: -1 },
  ];

  const craters = buildCraterField(512, rand);
  let w = 0, h = 0, dpr = 1, raf = 0;

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth;
    h = canvas.clientHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function moonGeometry() {
    // Low and to the right, deliberately running off both edges. The console
    // column is centred and up to 1440px wide, so anywhere behind it is
    // invisible; the body has to sit in the page's own empty ground and read
    // as something much larger than the viewport.
    const radius = Math.max(260, Math.min(w, h) * 0.62);
    return { cx: w - radius * 0.52, cy: h + radius * 0.30, radius };
  }

  function drawMoon(t) {
    const { cx, cy, radius } = moonGeometry();

    ctx.save();
    ctx.globalAlpha = 0.34;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, TAU);
    ctx.clip();

    // Scroll one wide crater tile across the disc to rotate the surface. Drawn
    // as two whole images that wrap, rather than sliced into columns -- the
    // column approach left visible vertical banding at the limb.
    const spin = (t * 0.004) % 1;
    const tileW = radius * 3.1;
    const tileH = radius * 2.2;
    const originX = cx - radius - spin * tileW;
    for (let k = 0; k < 2; k++) {
      ctx.drawImage(craters, originX + k * tileW, cy - radius * 1.1, tileW, tileH);
    }

    // Limb darkening as one radial gradient: a sphere lit from the front, not
    // a flat disc.
    const limb = ctx.createRadialGradient(
      cx - radius * 0.28, cy - radius * 0.3, radius * 0.1,
      cx, cy, radius,
    );
    limb.addColorStop(0, "rgba(255,255,255,0.10)");
    limb.addColorStop(0.55, "rgba(6,9,13,0)");
    limb.addColorStop(0.86, "rgba(6,9,13,0.55)");
    limb.addColorStop(1, "rgba(6,9,13,0.95)");
    ctx.fillStyle = limb;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);

    // The terminator drifts, so the lit fraction changes over minutes.
    const term = ctx.createLinearGradient(cx - radius, 0, cx + radius, 0);
    const edge = 0.30 + 0.14 * Math.sin(t * 0.03);
    term.addColorStop(0, "rgba(6,9,13,0.90)");
    term.addColorStop(edge, "rgba(6,9,13,0.28)");
    term.addColorStop(1, "rgba(6,9,13,0)");
    ctx.fillStyle = term;
    ctx.fillRect(cx - radius, cy - radius, radius * 2, radius * 2);
    ctx.restore();

    ctx.save();
    ctx.globalAlpha = 0.20;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, TAU);
    ctx.strokeStyle = "#AAB4C4";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();
  }

  function drawDust() {
    // Broad, very low-alpha clouds. Deep space is not an empty black rectangle.
    for (const d of dust) {
      const cx = d.x * w;
      const cy = d.y * h;
      const rad = d.r * Math.max(w, h);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      g.addColorStop(0, `rgba(150,175,225,${d.a})`);
      g.addColorStop(1, "rgba(150,175,225,0)");
      ctx.fillStyle = g;
      ctx.fillRect(cx - rad, cy - rad, rad * 2, rad * 2);
    }
  }

  function drawStars(t) {
    for (const s of stars) {
      // Parallax: nearer stars drift faster, which gives the field depth.
      const x = ((s.x + t * 0.0012 * s.depth) % 1) * w;
      const y = s.y * h;
      // Sharper than a plain sine, so bright stars visibly flare rather than
      // breathing uniformly.
      const osc = 0.5 + 0.5 * Math.sin(t * s.speed + s.phase);
      const twinkle = 0.30 + 0.70 * Math.pow(osc, 2.2);
      const alpha = Math.min(1, s.mag * twinkle);

      ctx.globalAlpha = alpha;
      ctx.fillStyle = s.tint;
      ctx.beginPath();
      ctx.arc(x, y, s.r, 0, TAU);
      ctx.fill();

      if (s.spike) {
        // A soft halo and a cross, the way a bright point source reads through
        // an optic. Only the brightest few percent get it.
        const halo = ctx.createRadialGradient(x, y, 0, x, y, s.r * 7);
        halo.addColorStop(0, s.tint);
        halo.addColorStop(1, "rgba(255,255,255,0)");
        ctx.globalAlpha = alpha * 0.22;
        ctx.fillStyle = halo;
        ctx.fillRect(x - s.r * 7, y - s.r * 7, s.r * 14, s.r * 14);

        ctx.globalAlpha = alpha * 0.5;
        ctx.strokeStyle = s.tint;
        ctx.lineWidth = 0.7;
        const len = s.r * 5.5;
        ctx.beginPath();
        ctx.moveTo(x - len, y); ctx.lineTo(x + len, y);
        ctx.moveTo(x, y - len); ctx.lineTo(x, y + len);
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  function drawSats(t) {
    for (const s of sats) {
      // u sweeps 0..1 across the viewport and wraps, with a shallow bow so the
      // path reads as an arc over a curved body rather than a straight line.
      const at = (u) => {
        const uu = ((u % 1) + 1) % 1;
        const x = s.dir > 0 ? uu * (w + 120) - 60 : (1 - uu) * (w + 120) - 60;
        const y = h * s.y + Math.sin(uu * Math.PI) * h * s.bow;
        return [x, y];
      };
      const u0 = s.phase + t / s.period;

      ctx.beginPath();
      for (let k = 0; k < 40; k++) {
        const [tx, ty] = at(u0 - k * 0.0016);
        if (k === 0) ctx.moveTo(tx, ty); else ctx.lineTo(tx, ty);
      }
      ctx.strokeStyle = s.colour;
      ctx.globalAlpha = 0.28;
      ctx.lineWidth = 1.4;
      ctx.stroke();

      const [px, py] = at(u0);
      ctx.globalAlpha = 0.22;
      ctx.beginPath();
      ctx.arc(px, py, 9, 0, TAU);
      ctx.fillStyle = s.colour;
      ctx.fill();
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.arc(px, py, 3, 0, TAU);
      ctx.fill();

      // A small label, so the two dots are identifiably the two missions.
      ctx.globalAlpha = 0.5;
      ctx.font = "10px ui-monospace, SFMono-Regular, Consolas, monospace";
      ctx.fillStyle = s.colour;
      ctx.fillText(s.label, px + 12, py + 3.5);
      ctx.globalAlpha = 1;
    }
  }

  function frame(ms) {
    const t = ms / 1000;
    ctx.clearRect(0, 0, w, h);
    drawDust();
    drawStars(t);
    drawMoon(t);
    drawSats(t);
    raf = requestAnimationFrame(frame);
  }

  function start() {
    cancelAnimationFrame(raf);
    if (reduced) {
      // Still a sky, just not a moving one.
      ctx.clearRect(0, 0, w, h);
      drawDust();
      drawStars(0);
      drawMoon(0);
      drawSats(0);
      return;
    }
    raf = requestAnimationFrame(frame);
  }

  resize();
  start();
  window.addEventListener("resize", () => { resize(); if (reduced) start(); });
  // Nothing here is worth spending a background tab's CPU on.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) cancelAnimationFrame(raf);
    else start();
  });

  return { stop() { cancelAnimationFrame(raf); } };
}
