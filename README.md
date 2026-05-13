# Website Optimization Wizard

A Flask web app that walks you through optimizing a web page for better Google PageSpeed scores. Paste your HTML, get an optimized version back, plus a checklist of server-side fixes the wizard can't apply itself.

[![CI](https://github.com/YOUR_ORG/YOUR_REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/YOUR_REPO/actions/workflows/ci.yml)

---

## What it does

A 5-step wizard:

1. **Enter URL and email** — your live website URL and the email address that should receive the full report.
2. **Paste HTML** — the full HTML source (up to 5 MB).
3. **Verify & optimize** — validates HTML, runs a real PageSpeed audit on the live URL (mobile + desktop), and applies ~15 HTML-level optimizations.
4. **Review** — real PageSpeed scores for mobile and desktop fetched live from Google. If either is below 90, the page shows "the perfect solution" — a detailed list of manual fixes with step-by-step instructions per hosting environment. One click on **"Send me on email"** delivers the full report (scores, automatic changes, every manual fix's what/why/how/verify, plus the optimized HTML as an attachment) to the email captured at Step 1.
5. **Copy code** — final optimized HTML in a textbox with Copy + Download buttons.

Automatic optimizations include: lazy-loading non-hero images and iframes, marking the hero image with `fetchpriority="high"` and preloading it, wrapping raster images in `<picture>` with WebP sources, deferring blocking scripts (with a safe list for GTM/GA/etc.), adding preconnect hints, and stripping comments.

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
| `GMAIL_USER` | required for email | _(none)_ | Full Gmail address used as the sender for the "Email me the report" feature. |
| `GMAIL_APP_PASSWORD` | required for email | _(none)_ | 16-char Google App Password. NOT your regular Gmail password. See "Email setup (Gmail)" below. |
| `GMAIL_FROM_NAME` | optional | `Website Optimizer` | Display name recipients see in their inbox. |
| `FLASK_SECRET_KEY` | recommended | random per process | Flask session secret. Set a stable value in production. |
| `MAX_HTML_BYTES` | optional | `5242880` (5 MB) | Cap on pasted HTML size. |
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

## Email setup (Gmail)

Step 4's **"Send me on email"** button sends the full report to the email captured at Step 1. We use Gmail's SMTP server via a Google App Password — no third-party service, no domain verification.

### Step 1 — Enable 2-Step Verification on the Google account

App Passwords only exist for accounts with 2FA enabled. Go to [myaccount.google.com/security](https://myaccount.google.com/security), find **2-Step Verification**, and turn it on if it isn't already. You'll add your phone number for SMS codes or use the Google Authenticator app. Takes 2 minutes.

### Step 2 — Create the App Password

Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (must be signed in). If the page says "the setting you're looking for is not available," it's almost always because Step 1 isn't fully done — go back and confirm 2-Step is fully active.

On the App Passwords page, in the "App name" field type something descriptive (e.g. `Optimizer Wizard`) and click **Create**. Google shows a yellow box with a 16-character password like `abcd efgh ijkl mnop`. **Copy it immediately — Google never shows it again.** The spaces don't matter; the code strips them.

### Step 3 — Decide which Gmail account sends the reports

Two reasonable options:

- **Use your own Gmail address as the sender.** Reports show up as "from Your Name <you@gmail.com>". Fine for a team-internal tool. Gmail's free-tier sending limit is ~500 emails/day, well past anything team usage will hit.
- **Create a dedicated Gmail account** (e.g. `seo.reports.yourcompany@gmail.com`), turn on 2FA there, and generate the App Password from that account. Reports come from a neutral address, your personal inbox stays out of it, and revoking access is just deleting that account. Recommended if the wizard is used by more than a couple of people.

Either way works — only the env var values change.

### Step 4 — Set the three environment variables

In Render: **Dashboard → your service → Environment** tab → **Add Environment Variable** three times:

| Variable | Value |
|---|---|
| `GMAIL_USER` | The full Gmail address you're sending from (e.g. `seo.reports@gmail.com`) |
| `GMAIL_APP_PASSWORD` | The 16-character App Password from Step 2 |
| `GMAIL_FROM_NAME` | The display name recipients see (e.g. `SEO Optimizer Wizard`) |

Click **Save Changes** — Render redeploys automatically (~1 minute).

For local dev, put the same three values in your `.env` file. See `.env.example` for the format.

### Step 5 — Test it

Run the wizard end-to-end with your own email as the Step 1 input. On Step 4 click **"Send me on email"**. You should get the report in your Gmail inbox within 10-30 seconds.

If it doesn't arrive: **check the spam folder first** — Gmail occasionally flags app-sent mail until it learns the pattern. Marking it "Not spam" once usually fixes future sends. If you see `SMTP authentication failed` in Render's logs, the App Password has a typo — regenerate it from Step 2 and re-paste in Render's env vars.

### Things worth knowing

- App Passwords don't expire, but they ARE revoked automatically if you change the main Google password. You'll have to generate a new one and update Render.
- If you hit Gmail's daily sending limit (~500/day on free tier), that's the signal to move to a transactional service like Resend or SendGrid — but team-internal usage is nowhere near that.
- Ignore any "less secure app" warnings you may have seen elsewhere — that's a different (deprecated) Google feature unrelated to App Passwords.

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
├── optimizer.py              HTML optimization engine + manual-fixes catalogue
├── pagespeed.py              Google PageSpeed Insights API client
├── emailer.py                Gmail SMTP sender + HTML report composer
├── requirements.txt
├── Dockerfile                Production container build
├── Procfile                  For Railway/Heroku-style platforms
├── render.yaml               Render Blueprint (one-click deploy)
├── .env.example              Template — copy to .env locally
├── LICENSE                   MIT
├── README.md
├── templates/                Jinja2 templates for each wizard step
│   ├── base.html
│   ├── step1.html            URL + email input
│   ├── step2.html            HTML paste with line counter
│   ├── step3.html            Processing screen (auto-calls /api/optimize)
│   ├── step4.html            Real mobile/desktop scores + "the perfect solution" + email button
│   └── step5.html            Final code + Copy + Download
├── static/
│   └── style.css             All styling, no external framework
└── tests/
    ├── test_optimizer.py     Optimizer + verifier + Flask route tests (offline)
    └── test_emailer.py       Email composition + SMTP config tests (offline)
```

---

## How the 90+ check actually works

PageSpeed Insights audits **live, publicly-accessible URLs** — it can't audit pasted HTML. So the wizard:

- Fetches the **real** PageSpeed scores for mobile and desktop on your live URL. These are the only numbers shown — there is no fabricated "estimated after" number.
- If either mobile or desktop is **below 90**, Step 4 shows "The perfect solution" — a list of the manual fixes you need to apply to reach 90+, each with what / why / how-per-environment / verify content.
- If both are already **90+**, the same list is shown labelled as optional tune-ups.

To verify a real 90+ score after applying the fixes: deploy the optimized HTML + apply the manual fixes, then re-run the wizard with the same URL. The new mobile/desktop numbers will reflect the improvements.

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
- **"Send me on email" button shows an error about App Password** — typo in `GMAIL_APP_PASSWORD`, or 2-Step Verification got disabled, or you changed your Google password (which auto-revokes existing App Passwords). Regenerate the password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and re-paste in Render's env vars.
- **Email arrives in spam** — common on the first few sends from a new Gmail App Password. Mark one as "Not spam" and Gmail learns. Recipients on the same Google Workspace as the sender almost never see this.
- **Sessions reset between requests in production** — you're running multiple Gunicorn workers. Use `--workers 1` (it's the default in our config).
- **Render free tier is slow on the first request** — that's the cold start. Upgrade plan or hit `/healthz` periodically to keep it warm.

---

## Security checklist before going live

- [ ] `PAGESPEED_API_KEY` is set as an environment variable (never in code).
- [ ] `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `GMAIL_FROM_NAME` are set in Render env vars (never in code).
- [ ] `FLASK_SECRET_KEY` is set to a stable random value in production.
- [ ] No `.env` file committed to git (verify with `git log --all --full-history -- .env`).
- [ ] The Gmail account used for sending has 2-Step Verification enabled (App Passwords require it).
- [ ] If your team is sensitive about who can use the tool, add HTTP basic auth or put it behind your SSO — the wizard has no auth of its own.

---

## License

MIT — see [LICENSE](LICENSE).
