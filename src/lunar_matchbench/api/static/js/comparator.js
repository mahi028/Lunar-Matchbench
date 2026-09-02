// Swipe/fade comparator between the registered CH2 patch and the LROC reference.
//
// This is the claim the whole tool makes -- "these two images of the same
// ground line up" -- so it has to be inspectable rather than asserted. Both
// layers occupy the same grid cell, so the frame height tracks the imagery and
// switching modes never shifts the layout.

import { patchUrl } from "./api.js";

export function mountComparator(container, { jobId, mode = "swipe" }) {
  container.innerHTML = `
    <div class="cmp" data-mode="${mode}" style="--split:.5">
      <img class="cmp-img" data-layer="reference" alt="LROC NAC reference patch"
           src="${patchUrl(jobId, "lroc")}" />
      <img class="cmp-img" data-layer="moving"
           alt="Chandrayaan-2 patch registered onto the LROC frame"
           src="${patchUrl(jobId, "warped")}" />
      <div class="cmp-handle" role="slider" tabindex="0" aria-label="Comparison split"
           aria-valuemin="0" aria-valuemax="100" aria-valuenow="50"></div>
      <!-- The clip reveals the moving layer from the left edge, so the left of
           the split is Chandrayaan-2 and the right is the LROC reference. -->
      <span class="cmp-tag cmp-tag--l">CH2 TMC-2</span>
      <span class="cmp-tag cmp-tag--r">LROC NAC</span>
    </div>`;

  const cmp = container.querySelector(".cmp");
  const handle = container.querySelector(".cmp-handle");
  let dragging = false;

  function setSplit(fraction) {
    const f = Math.max(0, Math.min(1, fraction));
    cmp.style.setProperty("--split", String(f));
    handle.setAttribute("aria-valuenow", String(Math.round(f * 100)));
  }

  const pointToSplit = (clientX) => {
    const rect = cmp.getBoundingClientRect();
    return (clientX - rect.left) / rect.width;
  };

  cmp.addEventListener("pointerdown", (e) => {
    if (cmp.dataset.mode !== "swipe") return;
    dragging = true;
    cmp.setPointerCapture?.(e.pointerId);
    setSplit(pointToSplit(e.clientX));
  });
  cmp.addEventListener("pointermove", (e) => {
    if (dragging) setSplit(pointToSplit(e.clientX));
  });
  const stop = (e) => {
    dragging = false;
    if (cmp.hasPointerCapture?.(e.pointerId)) cmp.releasePointerCapture(e.pointerId);
  };
  cmp.addEventListener("pointerup", stop);
  cmp.addEventListener("pointercancel", stop);

  // Keyboard parity: the split is a real slider, not a mouse-only affordance.
  handle.addEventListener("keydown", (e) => {
    const current = parseFloat(cmp.style.getPropertyValue("--split")) || 0.5;
    const stepBy = e.shiftKey ? 0.1 : 0.02;
    const moves = {
      ArrowLeft: current - stepBy,
      ArrowRight: current + stepBy,
      Home: 0,
      End: 1,
    };
    if (e.key in moves) {
      setSplit(moves[e.key]);
      e.preventDefault();
    }
  });

  return {
    setMode(next) { cmp.dataset.mode = next; },
    setSplit,
    destroy() { container.innerHTML = ""; },
  };
}
