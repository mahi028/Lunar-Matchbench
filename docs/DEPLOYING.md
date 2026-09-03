# Deploying Lunar-MatchBench

## The credential problem, first

Chandrayaan-2 data comes from ISRO's ISSDC archive, which requires an account.
That creates a decision no deployment can avoid:

**Do not deploy publicly with your own ISSDC credentials in the environment.**
Every visitor's registration would run on your personal account. It will get
throttled or suspended, it is a poor reading of ISSDC's terms — you would be
proxying authenticated archive access to anonymous users — and every click
spends real bandwidth you are accountable for.

This build is designed so you do not have to.

| Deployment | `PRADAN_*` set? | What a visitor can do |
|---|---|---|
| **Public demo** *(recommended)* | **No** | Run the four preset coordinates, replayed from recorded runs. Anything else needs their own account. |
| Private / local | Yes | Any coordinate, live, on your account. |
| Public + visitor accounts | No | Presets from cache; any coordinate live once the visitor enters their own ISSDC login. |

With no `PRADAN_USERNAME` / `PRADAN_PASSWORD` in the environment, the app
automatically serves the presets from `demo/` and refuses other coordinates with
an explanation. There is no flag to forget.

## What "recorded run" means

The four presets in `demo/` are **real runs** against the live ISSDC and NASA
archives — real metrics, real tie-points, real imagery, including two that
genuinely fail to converge. What is cached is the *fetching*, not the answer.

The console labels every replayed run in the status chip and at the top of the
evidence panel. Do not remove that labelling. A judge who later discovers a
number was cached and was not told will discount every other number on the page.

Re-bake after pipeline changes, on a machine that does have credentials:

```
uv run lunar-matchbench bake-demo --instrument tmc --matcher xfeat
```

## Railway

`Dockerfile` and `railway.json` are already in the repo. The Dockerfile installs
the CPU-only PyTorch wheel first, which keeps the image near 1 GB instead of the
~6.7 GB the default CUDA build produces — pointless on CPU hosting.

1. New project → Deploy from GitHub repo → this repository.
2. Leave `PRADAN_USERNAME` and `PRADAN_PASSWORD` **unset** for a public deploy.
3. Railway injects `$PORT`; `railway.json`'s start command already binds it.

Resources: XFeat on CPU needs roughly **2 GB RAM**, and a live registration takes
about 1–9 s of CPU after the fetching. Replayed runs need almost nothing, so a
demo-only deployment is cheap.

## Anywhere else

Any host that runs the container works. The requirements are:

- ~2 GB RAM (PyTorch CPU + OpenCV)
- Writable `outputs/` for job records and generated imagery
- **HTTPS**, if visitors will enter their own ISSDC credentials. The console
  detects plain HTTP on a non-local host and warns against typing a password,
  but that is a guard rail, not a substitute for TLS.

## Static hosting

Not possible as-is. The console calls a live API for job status, patches and
strip previews. A static export would need those baked to files too — worth
doing if you want GitHub Pages, but it is not what this build does today.

## Before you deploy

- [ ] `PRADAN_*` unset for anything public
- [ ] `demo/` committed and populated (`demo/manifest.json` plus `demo/runs/`)
- [ ] `.env` still gitignored and never committed
- [ ] HTTPS if the visitor-credentials panel is reachable
- [ ] `uv run pytest` green
