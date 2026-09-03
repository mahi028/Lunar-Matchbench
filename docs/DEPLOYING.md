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
| **Public demo** *(recommended)* | **No** | Run the four preset coordinates, replayed from recorded runs. Any other coordinate needs their own account. |
| Private / local | Yes, `LMB_DEMO_ONLY=0` | Any coordinate, live, on your account. |
| Public + visitor accounts | No | Presets from cache; any coordinate live once the visitor enters their own ISSDC login. |

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

## Hugging Face Spaces (recommended)

Free, 2 vCPU and 16 GB RAM, a permanent public URL, no payment details, and
Docker is a first-class option. The repo is already configured for it: the
front matter at the top of `README.md` declares `sdk: docker` and
`app_port: 7860`, and the Dockerfile binds that port and runs as uid 1000.

1. Sign in at <https://huggingface.co> and go to **New → Space**.
2. Name it (for example `lunar-matchbench`), pick **Docker → Blank**, and set
   visibility **Public**.
3. Create it, then push this repository to the Space's git remote:

   ```
   git remote add space https://huggingface.co/spaces/<your-username>/lunar-matchbench
   git push space feat/streaming-and-interactive-ui:main
   ```

   HF asks for your username and an access token as the password. Create the
   token at <https://huggingface.co/settings/tokens> with **write** scope. Use a
   token, not your account password.
4. The Space builds automatically. First build takes roughly 5–10 minutes
   because of the PyTorch wheel; later pushes reuse the layer cache.

Your URL will be:

```
https://huggingface.co/spaces/<your-username>/lunar-matchbench
```

**Do not add `PRADAN_USERNAME` or `PRADAN_PASSWORD` as Space secrets.** The
image already defaults to demo mode; adding them would put your account behind
a public button.

### Verifying the deployment

- The landing panel says the deployment runs the preset coordinates from
  recorded results.
- Clicking **Oceanus Procellarum → Run registration** completes and shows
  `Recorded run · succeeded` in the status chip.
- Clicking **Sinus Aestuum** completes and reports a genuine mismatch.
- Typing an arbitrary coordinate is refused with an explanation.

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
