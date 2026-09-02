// Every network call the console makes lives here, so the view modules stay
// free of fetch details and can be reasoned about on their own.

export async function startRun({ lat, lon, instrument, matcher }) {
  const resp = await fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lat, lon, instrument, matcher }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || "Could not start the run.");
  }
  return resp.json();
}

export async function fetchStatus(jobId) {
  const resp = await fetch(`/api/status/${jobId}`);
  if (!resp.ok) throw new Error("Lost contact with the run.");
  return resp.json();
}

export async function fetchResult(jobId) {
  const resp = await fetch(`/api/result/${jobId}`);
  if (!resp.ok) throw new Error("Could not read the result.");
  return resp.json();
}

export function patchUrl(jobId, which) {
  return `/api/patch/${jobId}/${which}.png`;
}
