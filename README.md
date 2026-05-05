# Website Optimization Wizard

A Flask web app that walks you through optimizing a web page for better Google PageSpeed scores. Paste your HTML, get an optimized version back, plus a checklist of server-side fixes the wizard can't apply itself.

[![CI](https://github.com/YOUR_ORG/YOUR_REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/YOUR_REPO/actions/workflows/ci.yml)

---

## What it does

A 5-step wizard:

1. **Enter URL** — your live website URL.
2. **Paste HTML** — the full HTML source (up to 5 MB).
3. **Verify & optimize** — validates HTML, runs a real PageSpeed audit on the live URL (mobile + desktop), and applies ~15 HTML-level optimizations.
4. **Review** — baseline scores, estimated "after" score, list of automatic changes, and a table of server-side fixes you still need to do.
5. **Copy code** — final optimized HTML in a textbox with Copy + Download buttons.

Automatic optimizations include: lazy-loading non-hero images and iframes, marking the hero image with `fetchpriority="high"` and preloading it, wrapping raster images in `<picture>` with WebP sources, deferring blocking scripts (with a safe list for GTM/GA/etc.), adding preconnect hints, stripping comments, and on retry — minifying whitespace.

---

## Deployment options

Pick the path that fits your team. All three start from the same repo.

### Option A — One-click deploy on Render (recommended for most teams)

The repo includes a `render.yaml` blueprint. Free tier works fine for occasional team use.

1. Push this repo to GitHub (steps below).
2. Go to [dashboard.render.com/blueprints](https://dashboard.render.com/blueprints) → **New Blueprint**.
3. Connect your GitHub repo. Render reads `render.yaml` automatically.
4. Set the `PAGESPEED_API_KEY` environment variable in the Render dashboard (Settings → Environment).
5. Deploy. You'll get a URL like `https://optimizer-wizard.onrender.com` — share that with the team.

> **Note on Render free tier:** the service spins down after 15 minutes of inactivity and takes ~30 seconds to wake up on the next request. Upgrade to the $7/mo Starter plan to remove cold starts.

### Option B — Codespaces (zero local setup, per-user instances)

Each team member opens their own Codespace from GitHub:

1. On the GitHub repo page, click **Code → Codespaces → Create codespace on main**.
2. Wait ~30 seconds for the dev container to build.
3. In the Codespace terminal: `python app.py`
4. Click the popup that says "Application running on port 5000 → Open in Browser".

The Codespace gives a shareable preview URL each time, but each user runs their own instance. Good for trying it out; not a shared service.

### Option C — Docker (deploy anywhere)

```bash
docker build -t optimizer-wizard .
docker run -p 8000:8000 -e PAGESPEED_API_KEY=your-key-here optimizer-wizard
# Open http://localhost:8000
```

The image runs Gunicorn under a non-root user with a healthcheck on `/healthz`. Push it to any container registry and run it on Cloud Run, Fly.io, AWS ECS, a VM with Docker, or your own Kubernetes cluster — all work.

### Option D — Local development

```bash
git clone https://github.com/YOUR_ORG/YOUR_REPO.git
cd YOUR_REPO
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then edit .env
python app.py                                         # http://localhost:5000
```

---

## Pushing this repo to GitHub (first time)

```bash
cd optimizer-wizard
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_ORG/YOUR_REPO.git
git push -u origin main
```

After the push, GitHub Actions runs CI (`.github/workflows/ci.yml`) automatically.

---

## Configuration (environment variables)

Set these in `.env` for local dev, or in your deployment platform's dashboard for production. **Never commit `.env`** — it's already in `.gitignore`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `PAGESPEED_API_KEY` | recommended | _(none)_ | Google PageSpeed Insights API key. Without one, Google rate-limits to ~1 query/sec/IP and audits will fail under team load. |
| `FLASK_SECRET_KEY` | recommended | random per process | Flask session secret. Set a stable value in production. |
| `MAX_HTML_BYTES` | optional | `5242880` (5 MB) | Cap on pasted HTML size. |
| `SCORE_PASS_THRESHOLD` | optional | `90` | Minimum estimated score to advance to step 5. |
| `LOG_LEVEL` | optional | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `PORT` | optional | `5000` (dev) / `8000` (Docker) | Port to bind. |

### Getting a PageSpeed API key

1. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials).
2. Create a project (or use an existing one).
3. **APIs & Services → Library**, search for "PageSpeed Insights API", click Enable.
4. **Credentials → Create Credentials → API Key**.
5. Copy the key into your `.env` or your deployment platform's env var settings.

The free tier gives 25,000 requests/day and 400 requests per 100 seconds — more than enough for team use.

---

## Architecture notes (read before scaling)

### Single-worker constraint

The app keeps wizard state (pasted HTML, optimization results) in a process-local Python dict because pasted HTML routinely exceeds the 4 KB Flask cookie limit. **This is why our Gunicorn config uses `--workers 1`** — multiple workers would route requests to different processes and lose state mid-wizard.

For a small team this is fine. If you ever need to handle high concurrent load, replace the in-memory store in `app.py` (the `_store` dict) with Redis. The relevant code is two functions, `sdata()` and `clear_sdata()`.

### `--threads 4`

Python threads still help here because PageSpeed audits spend most of their time waiting on Google's API. Gunicorn's threaded mode lets one worker handle ~4 concurrent users without blocking.

### Health check

Hit `/healthz` — returns `{"status": "ok"}`. Used by the Dockerfile's `HEALTHCHECK` and Render's health probe.

---

## Project structure

```
optimizer-wizard/
├── app.py                    Flask routes + session handling
├── wsgi.py                   Gunicorn entry point
├── optimizer.py              HTML optimization engine + score estimator
├── pagespeed.py              Google PageSpeed Insights API client
├── requirements.txt
├── Dockerfile                Production container build
├── .dockerignore
├── Procfile                  For Railway/Heroku-style platforms
├── render.yaml               Render Blueprint (one-click deploy)
├── .env.example              Template — copy to .env locally
├── .gitignore
├── LICENSE                   MIT
├── README.md
├── templates/                Jinja2 templates for each wizard step
│   ├── base.html
│   ├── step1.html            URL input
│   ├── step2.html            HTML paste with line counter
│   ├── step3.html            Processing screen (auto-calls /api/optimize)
│   ├── step4.html            Results + score cards + manual fixes
│   └── step5.html            Final code + Copy + Download
├── static/
│   └── style.css             All styling, no external framework
├── tests/
│   └── test_optimizer.py     Pytest suite (offline, no network)
├── .github/workflows/
│   └── ci.yml                Runs tests + Docker build on push
└── .devcontainer/
    └── devcontainer.json     Codespaces / VS Code dev container
```

---

## How the "90+ check" actually works

PageSpeed Insights can only audit **live, publicly-accessible URLs** — it can't audit pasted HTML. So the wizard:

- Takes the **real** PageSpeed score of your live URL as the baseline.
- Computes an **estimated** "after" score from:
  - Known point impact of each optimization applied (lazy-load ≈ +4, defer scripts ≈ +8, WebP ≈ +10, preload hero ≈ +5, etc.).
  - Scaled down by Lighthouse's actual flagged opportunities — so we can't claim a 30-point jump if PageSpeed only flagged 5 seconds of addressable savings.

If estimated ≥ 90, the wizard lets you proceed. **To get a verified 90+ score**, deploy the optimized HTML, then re-run the wizard with the same URL — the new baseline audit will reflect the real optimized score.

---

## Manual fixes the wizard can't do

Shown in the step 4 table. Common ones:

- Enable gzip/brotli compression on your web server.
- Set long `Cache-Control` headers on static assets.
- Put a CDN in front (Cloudflare's free tier alone typically adds 10–20 points).
- Minify CSS and JS in your build pipeline.
- Actually produce the `.webp` image files the optimized HTML now references.
- Anything PageSpeed flagged that's server-side (HTTP/2, TTFB, unused CSS, etc.).

---

## Running tests locally

```bash
pip install pytest
pytest -v tests/
```

Tests are offline — they don't hit the PageSpeed API.

---

## Troubleshooting

- **"PageSpeed audit failed: 429"** — you hit Google's rate limit. With an API key set, you have 400 req/100s and 25k/day; without one, ~1 req/sec/IP. Wait or set the key.
- **"HTML verification failed"** — paste the full page source including `<html>` / `<head>` / `<body>` tags, not a fragment.
- **Processing hangs at "Running PageSpeed audit"** — Lighthouse audits can take 90+ seconds for slow sites. Not hung; give it time. Our Gunicorn timeout is 200s.
- **Sessions reset between requests in production** — you're running multiple Gunicorn workers. Use `--workers 1` (it's the default in our config).
- **Render free tier is slow on the first request** — that's the cold start. Upgrade plan or hit `/healthz` periodically to keep it warm.

---

## Security checklist before going live

- [ ] `PAGESPEED_API_KEY` is set as an environment variable (never in code).
- [ ] `FLASK_SECRET_KEY` is set to a stable random value in production.
- [ ] No `.env` file committed to git (verify with `git log --all --full-history -- .env`).
- [ ] If your team is sensitive about who can use the tool, add HTTP basic auth or put it behind your SSO — the wizard has no auth of its own.

---

## License

MIT — see [LICENSE](LICENSE).
