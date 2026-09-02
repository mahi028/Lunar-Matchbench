import { fetchResult } from "./api.js";
import { clearLocator, renderLocator } from "./locator.js";

const locator = document.getElementById("locator");
clearLocator(locator);

// Rehydrating from ?job= makes a finished run linkable and survivable across a
// reload, and is how the UI tests drive real payloads without touching the
// network.
const jobFromUrl = new URLSearchParams(location.search).get("job");
if (jobFromUrl) {
  fetchResult(jobFromUrl)
    .then((data) => renderLocator(locator, data?.provenance?.lroc_localization))
    .catch(() => clearLocator(locator));
}
