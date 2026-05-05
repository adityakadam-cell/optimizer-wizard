"""
Website Optimization Wizard — Flask app entry point.

A 5-step web wizard:
  1. Enter URL
  2. Paste HTML source
  3. Verify HTML + run PageSpeed audit on the live URL + optimize HTML
  4. Show results; if estimated score >= 90, Next appears; else re-optimize
  5. Show the final optimized code with Copy + Download buttons

Local dev:
    pip install -r requirements.txt
    cp .env.example .env   # fill in PAGESPEED_API_KEY
    python app.py          # http://localhost:5000

Production: see README.md (Docker / Render / Gunicorn).
"""

import logging
import os
import secrets
import threading

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash,
)

# Optional: load .env in local dev. In production, real env vars are set by
# the platform (Render, Docker, etc.) and python-dotenv is a no-op.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from optimizer import HTMLOptimizer, verify_html, estimate_after_score, get_manual_fixes
from pagespeed import PageSpeedAuditor

# ----- Logging -----
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("optimizer")

# ----- Configuration -----
# IMPORTANT: never hardcode the PageSpeed API key. Set it via environment.
# Without a key the app still works but Google rate-limits to ~1 query/sec
# per IP, which means audits will fail quickly under team usage.
PAGESPEED_API_KEY = (os.environ.get("PAGESPEED_API_KEY") or "").strip()
if not PAGESPEED_API_KEY:
    log.warning(
        "PAGESPEED_API_KEY is not set. The app will still run but PageSpeed "
        "audits will be rate-limited. Set the env var or create a .env file."
    )

MAX_HTML_BYTES = int(os.environ.get("MAX_HTML_BYTES", 5 * 1024 * 1024))  # 5 MB default
SCORE_PASS_THRESHOLD = int(os.environ.get("SCORE_PASS_THRESHOLD", 90))

# Flask secret key. Generate a stable one for production via:
#   python -c "import secrets; print(secrets.token_hex(32))"
# and set FLASK_SECRET_KEY. Without it we generate a random one per process,
# which means sessions don't survive a restart — fine for a small team tool.
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# ----- App -----
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = MAX_HTML_BYTES + 1024

# In-memory per-session HTML store. Default Flask cookies cap at 4 KB which
# can't hold pasted HTML, so we keep blobs server-side and only put a session
# id in the cookie.
#
# CAVEAT: this is a process-local dict. If you run multiple Gunicorn workers,
# requests can land on different workers and lose their state. Run with a
# single worker (the default in our Dockerfile / render.yaml) OR swap this
# for Redis if you ever need to scale.
_store: dict[str, dict] = {}
_store_lock = threading.Lock()


def sdata() -> dict:
    """Get (or create) server-side storage for this browser session."""
    sid = session.get("sid")
    if not sid:
        sid = secrets.token_hex(16)
        session["sid"] = sid
    with _store_lock:
        return _store.setdefault(sid, {})


def clear_sdata():
    sid = session.pop("sid", None)
    if sid:
        with _store_lock:
            _store.pop(sid, None)


# ===================== Routes =====================
@app.route("/")
def home():
    clear_sdata()
    return redirect(url_for("step1"))


@app.route("/healthz")
def healthz():
    """Liveness probe for Docker / Render / Cloud Run."""
    return {"status": "ok"}, 200


# ---- Step 1: URL ----
@app.route("/step1", methods=["GET", "POST"])
def step1():
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        if not url:
            flash("Please enter a URL.", "error")
            return render_template("step1.html")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        d = sdata()
        d["url"] = url
        d["retry_count"] = 0
        return redirect(url_for("step2"))
    return render_template("step1.html")


# ---- Step 2: Paste HTML code ----
@app.route("/step2", methods=["GET", "POST"])
def step2():
    d = sdata()
    if "url" not in d:
        return redirect(url_for("step1"))

    if request.method == "POST":
        html = request.form.get("html") or ""
        if not html.strip():
            flash("Please paste your HTML code.", "error")
            return render_template("step2.html", url=d["url"])
        if len(html.encode("utf-8")) > MAX_HTML_BYTES:
            flash(f"HTML too large (max {MAX_HTML_BYTES // 1024} KB).", "error")
            return render_template("step2.html", url=d["url"], html=html)

        ok, err = verify_html(html)
        if not ok:
            flash(f"HTML verification failed: {err}", "error")
            return render_template("step2.html", url=d["url"], html=html)

        d["html"] = html
        return redirect(url_for("step3"))

    return render_template("step2.html", url=d.get("url"), html=d.get("html", ""))


# ---- Step 3: Processing ----
@app.route("/step3")
def step3():
    d = sdata()
    if "html" not in d or "url" not in d:
        return redirect(url_for("step1"))
    return render_template("step3.html", url=d["url"])


@app.route("/api/optimize", methods=["POST"])
def api_optimize():
    d = sdata()
    if "html" not in d or "url" not in d:
        return jsonify({"error": "session expired"}), 400

    try:
        url = d["url"]
        html = d["html"]
        retry = d.get("retry_count", 0)

        # 1. Baseline PageSpeed audit on the LIVE url
        # auditor = PageSpeedAuditor(api_key=PAGESPEED_API_KEY or None)
        # mobile = auditor.audit(url, "mobile")
        # desktop = auditor.audit(url, "desktop")
        from concurrent.futures import ThreadPoolExecutor
        auditor = PageSpeedAuditor(api_key=PAGESPEED_API_KEY or None)
        # Run mobile + desktop audits in parallel to stay under Render's proxy timeout.
        with ThreadPoolExecutor(max_workers=2) as pool:
            mobile_future = pool.submit(auditor.audit, url, "mobile")
            desktop_future = pool.submit(auditor.audit, url, "desktop")
            mobile = mobile_future.result()
            desktop = desktop_future.result()

        # Use the lower of mobile/desktop as our baseline (harder case to beat)
        baseline_scores = []
        audit_for_opps = mobile or desktop
        if mobile and mobile["scores"].get("performance") is not None:
            baseline_scores.append(mobile["scores"]["performance"])
        if desktop and desktop["scores"].get("performance") is not None:
            baseline_scores.append(desktop["scores"]["performance"])
        baseline = min(baseline_scores) if baseline_scores else 50

        # 2. Optimize the HTML (more aggressive on retry)
        aggressive = retry > 0
        opt = HTMLOptimizer(html, url, aggressive=aggressive)
        optimized = opt.optimize()

        # 3. Estimate improvement
        estimated = estimate_after_score(baseline, opt.changes, audit_for_opps)

        # 4. Manual fixes (things the script cannot fix by itself)
        manual_fixes = get_manual_fixes(audit_for_opps, opt.changes)

        # Persist
        d["optimized_html"] = optimized
        d["baseline_mobile"] = mobile["scores"].get("performance") if mobile else None
        d["baseline_desktop"] = desktop["scores"].get("performance") if desktop else None
        d["estimated_score"] = estimated
        d["changes"] = opt.changes
        d["manual_fixes"] = manual_fixes
        d["opportunities"] = audit_for_opps["opportunities"] if audit_for_opps else []
        d["metrics"] = audit_for_opps["metrics"] if audit_for_opps else {}

        return jsonify({"ok": True, "redirect": url_for("step4")})
    except Exception as e:
        log.exception("optimize failed")
        return jsonify({"error": str(e)}), 500


# ---- Step 4: Results ----
@app.route("/step4")
def step4():
    d = sdata()
    if "optimized_html" not in d:
        return redirect(url_for("step1"))

    passed = (d.get("estimated_score") or 0) >= SCORE_PASS_THRESHOLD
    return render_template(
        "step4.html",
        url=d["url"],
        baseline_mobile=d.get("baseline_mobile"),
        baseline_desktop=d.get("baseline_desktop"),
        estimated=d.get("estimated_score"),
        changes=d.get("changes", []),
        manual_fixes=d.get("manual_fixes", []),
        metrics=d.get("metrics", {}),
        passed=passed,
        retry_count=d.get("retry_count", 0),
        threshold=SCORE_PASS_THRESHOLD,
    )


@app.route("/reoptimize", methods=["POST"])
def reoptimize():
    d = sdata()
    if "html" not in d:
        return redirect(url_for("step1"))
    # Feed the already-optimized HTML into the next pass so improvements compound.
    if "optimized_html" in d:
        d["html"] = d["optimized_html"]
    d["retry_count"] = d.get("retry_count", 0) + 1
    return redirect(url_for("step3"))


@app.route("/force-proceed", methods=["POST"])
def force_proceed():
    d = sdata()
    if "optimized_html" not in d:
        return redirect(url_for("step1"))
    d["forced"] = True
    return redirect(url_for("step5"))


# ---- Step 5: Final code + copy ----
@app.route("/step5")
def step5():
    d = sdata()
    if "optimized_html" not in d:
        return redirect(url_for("step1"))

    passed = (d.get("estimated_score") or 0) >= SCORE_PASS_THRESHOLD
    forced = d.get("forced", False)
    if not passed and not forced:
        return redirect(url_for("step4"))

    return render_template(
        "step5.html",
        url=d["url"],
        html=d["optimized_html"],
        estimated=d.get("estimated_score"),
        passed=passed,
        forced=forced,
    )


@app.route("/restart")
def restart():
    clear_sdata()
    return redirect(url_for("step1"))


# ===================== Main (local dev only) =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "=" * 60)
    print(" Website Optimization Wizard (development server)")
    print(f" Open in your browser:  http://localhost:{port}")
    print(" For production, use:   gunicorn wsgi:app")
    print("=" * 60 + "\n")
    # 127.0.0.1 by default so the dev server isn't exposed on the LAN.
    # Set FLASK_HOST=0.0.0.0 if you want LAN access during local testing.
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
