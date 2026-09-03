// Every network call the console makes lives here, so the view modules stay
// free of fetch details and can be reasoned about on their own.
//
// There are two backends behind this one surface. Normally the calls go to the
// FastAPI server. When the page is built as a *static* bundle -- which is how
// the public Hugging Face Space is hosted, because Docker Spaces are no longer
// free -- `window.LMB_STATIC_BASE` is set and the same calls read pre-rendered
// files instead. Those files are not hand-written: they are the real server's
// own responses, captured at build time by driving the actual app. So the two
// backends cannot drift apart in what they return, only in where it comes from.
//
// The static backend can replay the baked preset runs and nothing else. A live
// coordinate needs a server that can reach ISSDC, which is what the container
// is for.

const STATIC_BASE =
  typeof window !== "undefined" && window.LMB_STATIC_BASE ? window.LMB_STATIC_BASE : null;

export const isStatic = () => STATIC_BASE !== null;

// Matches core/demo.py's MATCH_TOLERANCE_DEG: presets are entered by clicking a
// button, so this only has to absorb float formatting.
const MATCH_TOLERANCE_DEG = 1e-4;

// core/app.py's _replay_demo paces the baked steps at this interval. Kept
// identical so a replay on the static site advances exactly as it does on the
// server -- the console's progress view behaves the same in both.
const REPLAY_STEP_MS = 450;

const NO_SERVER_DETAIL =
  "This deployment has no ISSDC account, so it can only replay the preset " +
  "coordinates. Enter your own ISSDC credentials to run any coordinate live.";

const NO_LIVE_ON_STATIC =
  "This is the static public build, which replays the recorded preset runs and " +
  "cannot reach ISSDC. To run any coordinate live against your own account, run " +
  "the container: docker run -p 7860:7860 -e LMB_DEMO_ONLY=0 lunar-matchbench";

let manifestPromise = null;
const jobs = new Map();

function staticJson(path) {
  return fetch(`${STATIC_BASE}/${path}`).then((r) => {
    if (!r.ok) throw new Error(`Missing ${path} in the static bundle.`);
    return r.json();
  });
}

function manifest() {
  if (manifestPromise === null) manifestPromise = staticJson("index.json");
  return manifestPromise;
}

export async function fetchCapabilities() {
  if (isStatic()) return staticJson("capabilities.json").catch(() => ({
    server_credentials: false, demo_runs: [],
  }));
  const resp = await fetch("/api/capabilities");
  if (!resp.ok) return { server_credentials: false, demo_runs: [] };
  return resp.json();
}

export async function startRun({ lat, lon, instrument, matcher, username, password }) {
  if (isStatic()) {
    if (username && password) throw new Error(NO_LIVE_ON_STATIC);
    const runs = await manifest();
    const hit = runs.find(
      (r) =>
        Math.abs(r.lat - lat) <= MATCH_TOLERANCE_DEG &&
        Math.abs(r.lon - lon) <= MATCH_TOLERANCE_DEG &&
        r.instrument === instrument &&
        r.matcher === matcher,
    );
    if (!hit) throw new Error(NO_SERVER_DETAIL);
    const steps = await staticJson(`runs/${hit.slug}/steps.json`);
    jobs.set(hit.slug, { startedAt: Date.now(), steps });
    return { job_id: hit.slug, status: "queued" };
  }

  const body = { lat, lon, instrument, matcher };
  // Sent for this request only. Never stored in the page, never put in the URL,
  // and dropped by the server before the job is written anywhere.
  if (username && password) {
    body.issdc_username = username;
    body.issdc_password = password;
  }
  const resp = await fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || "Could not start the run.");
  }
  return resp.json();
}

export async function fetchStatus(jobId) {
  if (isStatic()) {
    const final = await staticJson(`runs/${jobId}/result.json`);
    const job = jobs.get(jobId);
    // No local job means the page was reloaded against ?job=<slug>. The server
    // rehydrates a finished job from disk in that case, so do the same here
    // rather than replaying the progress a second time.
    if (!job) return { job_id: jobId, status: final.status, progress_step: 8,
                       progress_total: 8, progress_msg: "", step_image_urls: {},
                       transfer: final.transfer || {} };
    const idx = Math.floor((Date.now() - job.startedAt) / REPLAY_STEP_MS);
    if (idx >= job.steps.length) {
      jobs.delete(jobId);
      return { job_id: jobId, status: final.status, progress_step: 8,
               progress_total: 8, progress_msg: "", step_image_urls: {},
               transfer: final.transfer || {} };
    }
    const step = job.steps[idx];
    return {
      job_id: jobId,
      status: "running",
      progress_step: step.step || 0,
      progress_total: 8,
      progress_msg: step.msg || "",
      step_image_urls: {},
      transfer: step.transfer || {},
    };
  }
  const resp = await fetch(`/api/status/${jobId}`);
  if (!resp.ok) throw new Error("Lost contact with the run.");
  return resp.json();
}

export async function fetchResult(jobId) {
  if (isStatic()) return staticJson(`runs/${jobId}/result.json`);
  const resp = await fetch(`/api/result/${jobId}`);
  if (!resp.ok) throw new Error("Could not read the result.");
  return resp.json();
}

export function patchUrl(jobId, which) {
  if (isStatic()) return `${STATIC_BASE}/runs/${jobId}/${which}.png`;
  return `/api/patch/${jobId}/${which}.png`;
}
