import { fetchResult } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";
import { mountComparator } from "./comparator.js";

const locator = document.getElementById("locator");
const stage = document.getElementById("stage");
const stageBody = document.getElementById("stage-body");
const stageTools = document.getElementById("stage-tools");

clearLocator(locator);

function renderStage(jobId) {
  stage.hidden = false;
  stageBody.innerHTML = "";
  stageTools.innerHTML = `
    <button type="button" class="tool" data-mode="swipe" aria-pressed="true">Swipe</button>
    <button type="button" class="tool" data-mode="fade" aria-pressed="false">Fade</button>`;

  const cmpHost = document.createElement("div");
  stageBody.appendChild(cmpHost);
  const comparator = mountComparator(cmpHost, { jobId, mode: "swipe" });

  stageTools.onclick = (e) => {
    const btn = e.target.closest(".tool");
    if (!btn) return;
    comparator.setMode(btn.dataset.mode);
    stageTools.querySelectorAll(".tool").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
  };
}

// Rehydrating from ?job= makes a finished run linkable and survivable across a
// reload, and is how the UI tests drive real payloads without touching the
// network.
const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchResult(jobFromUrl)
    .then((data) => {
      renderLocator(locator, data?.provenance?.lroc_localization);
      renderStage(jobFromUrl);
    })
    .catch(() => clearLocator(locator));
}
