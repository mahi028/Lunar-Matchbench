import { fetchResult } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";
import { mountComparator } from "./comparator.js";
import { mountTiePoints } from "./tiepoints.js";

const locator = document.getElementById("locator");
const stage = document.getElementById("stage");
const stageBody = document.getElementById("stage-body");
const stageTools = document.getElementById("stage-tools");

clearLocator(locator);

function renderStage(jobId, data) {
  stage.hidden = false;
  stageBody.innerHTML = "";
  stageTools.innerHTML = `
    <button type="button" class="tool" data-mode="swipe" aria-pressed="true">Swipe</button>
    <button type="button" class="tool" data-mode="fade" aria-pressed="false">Fade</button>`;

  // Two square views of the same ground; side by side on wide screens so the
  // alignment and the correspondences can be read against each other without
  // scrolling between them.
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
}

// Rehydrating from ?job= makes a finished run linkable and survivable across a
// reload, and is how the UI tests drive real payloads without touching the
// network.
const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchResult(jobFromUrl)
    .then((data) => {
      renderLocator(locator, data?.provenance?.lroc_localization);
      renderStage(jobFromUrl, data);
    })
    .catch(() => clearLocator(locator));
}
