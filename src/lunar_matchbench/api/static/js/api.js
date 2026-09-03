// Every network call the console makes lives here, so the view modules stay
// free of fetch details and can be reasoned about on their own.

export async function fetchCapabilities() {
  const resp = await fetch("/api/capabilities");
  if (!resp.ok) return { server_credentials: false, demo_runs: [] };
  return resp.json();
}

export async function startRun({ lat, lon, instrument, matcher, username, password }) {
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
