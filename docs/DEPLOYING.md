# Deploying Lunar-MatchBench

## The credential problem, first

Chandrayaan-2 data comes from ISRO's ISSDC archive, which requires an account.
That creates a decision no public deployment can avoid:

**Do not deploy publicly with your own ISSDC credentials in the environment.**
Every visitor's registration would run on your personal account. It will get
throttled or suspended, it reads badly against ISSDC's terms — you would be
proxying authenticated archive access to anonymous users — and every click
spends bandwidth you are accountable for.

This build is designed so you do not have to.

| Deployment | `PRADAN_*` set? | What a visitor can do |
|---|---|---|
| **Public static Space** *(what is live today)* | **No** — cannot use them | Run the four preset coordinates, replayed from recorded runs. Anything else points at the container. |
| Public container | **No** | Presets from cache; any coordinate live once the visitor enters their own ISSDC login. |
| Private / local container | Yes, `LMB_DEMO_ONLY=0` | Any coordinate, live, on your account. |

`LMB_DEMO_ONLY=1` is the **default in the container image**, so a public deploy
is safe even if a credential reaches the environment by accident. Turn it off
deliberately for a private one.

## What "recorded run" means

The four presets in `demo/` are **real runs** against the live ISSDC and NASA
archives — real metrics, real tie-points, real imagery, including two that
genuinely fail to converge for different reasons. What is cached is the
*fetching*, not the answer.

| Preset | Outcome |
|---|---|
| Oceanus Procellarum | 523 inliers / 1280, RMSE 1.532 px, 62.5% coverage |
| Rayed crater 5.2°N | 261 inliers / 856, RMSE 1.798 px, 50.0% coverage |
| Sinus Aestuum | Fails — entire strip searched, genuine mismatch |
| Known failure 3.6°N | Fails — only 4 inliers, localisation not confident |

The console labels every replayed run in the status chip and at the top of the
evidence panel. **Do not remove that labelling.** A judge who later discovers a
number was cached and was not told will discount every other number on the page.

Re-bake after pipeline changes, on a machine that has credentials:

```
uv run lunar-matchbench bake-demo --instrument tmc --matcher xfeat
```

---

## Hugging Face Spaces (what the public link runs)

**Docker Spaces are not free.** As of September 2026 hosting a Docker or Gradio
Space on `cpu-basic` requires a PRO subscription; only **static** Spaces are free.
Creating one without PRO fails with `402 Payment Required`. Earlier versions of
this document said Docker Spaces were free. They were; they are not now.

So the public deployment is a **static build**, and the container remains the way
to run anything live.

### The static build

The four baked runs need no server to replay - they are JSON and PNGs - so the
whole console runs in the browser:

```
uv run python scripts/build_static_site.py
```

That writes `site/` (about 6.4 MB). The API responses in it are not
reimplemented in JavaScript: the script drives the real FastAPI app through its
real endpoints in demo-only mode and records what it returned, so the static
site serves the server's own answers. `api.js` reads files instead of making
requests when `window.LMB_STATIC_BASE` is set, which `index.html` does only in
this build.

What the static build cannot do, and says so on the page rather than failing
quietly:

- run an arbitrary coordinate (no server can reach ISSDC)
- accept visitor ISSDC credentials (same reason - the panel shows the container
  command instead of collecting a password it cannot use)
- render the draggable strip preview at an arbitrary scan line (that is a ranged
  read of a 529 MB product)

Everything else - the presets, metrics, tie-points, composites, charts, transform
decomposition, footprint map - is fully present.

### Publishing it

```
uv run python scripts/build_static_site.py
hf auth login                      # device flow; the token never lands in a shell
```

Then create the Space with `sdk: static` and upload `site/`. `site/README.md`
carries the front matter Spaces reads. `huggingface_hub`'s `upload_folder` does
both:

```python
from huggingface_hub import HfApi
api = HfApi()
sid = f"{api.whoami()['name']}/lunar-matchbench"
api.create_repo(sid, repo_type="space", space_sdk="static", exist_ok=True)
api.upload_folder(repo_id=sid, repo_type="space", folder_path="site")
```

Live at:

- <https://huggingface.co/spaces/Nitya-Prakash-Pandey/lunar-matchbench>
- <https://nitya-prakash-pandey-lunar-matchbench.static.hf.space> (direct)

Rebuild and re-upload after any UI or demo-bundle change; the bundle is a
snapshot, not a live view of the source.

**Do not add `PRADAN_USERNAME` or `PRADAN_PASSWORD` as Space secrets.** A static
Space cannot use them, and a Docker one must not.

### If you do get PRO

The Dockerfile already targets Spaces (uid 1000, `$PORT`, `LMB_DEMO_ONLY=1`).
Push the repo to the Space's git remote and it runs with the live-credentials
panel working.

## Railway

`railway.json` is present and the same image works unchanged.

1. New project → Deploy from GitHub repo → this repository.
2. Leave `PRADAN_*` unset. Railway injects `$PORT`, which the image honours.

Trial credits, then roughly $5/month.

## Render

Works, but the free tier is 512 MB RAM and sleeps after 15 minutes of
inactivity, so a judge's first click waits through a cold start on a ~1 GB
image. Acceptable on the paid tier.

## Vercel

Not usable. Vercel is serverless with a 250 MB bundle limit; PyTorch, OpenCV and
matplotlib exceed it, and there is no long-running process for the pipeline.

---

## Running the container locally

```
docker build -t lunar-matchbench .
docker run --rm -p 7860:7860 lunar-matchbench
```

Open <http://127.0.0.1:7860>. This is exactly what the Space runs — nothing
depends on the machine that built it.

To run live against your own account locally:

```
docker run --rm -p 7860:7860 \
  -e LMB_DEMO_ONLY=0 \
  -e PRADAN_USERNAME=... -e PRADAN_PASSWORD=... \
  lunar-matchbench
```

## Before you deploy

- [ ] `PRADAN_*` unset for anything public, `LMB_DEMO_ONLY` left at its default
- [ ] `demo/` committed and populated (`demo/manifest.json` and `demo/runs/`)
- [ ] `.env` still gitignored and never committed
- [ ] HTTPS if the visitor-credentials panel is reachable — Spaces and Railway
      both provide it
- [ ] `uv run pytest` green
- [ ] for the static Space: `site/` rebuilt from the current source, and the
      published page checked in a browser (a preset run, a failing preset, and a
      non-preset coordinate) rather than only fetched
