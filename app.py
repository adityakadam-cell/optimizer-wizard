"""
Website Optimization Wizard — Flask app entry point.

A 5-step web wizard:
  1. Enter URL + email (email used by Step 4's "Email me the report" button)
  2. Paste HTML source
  3. Verify HTML + run PageSpeed audit on the live URL + optimize HTML
  4. Show real mobile/desktop scores from PageSpeed + manual fixes
     ("the perfect solution"). User can email themselves the full report.
  5. Show the final optimized code with Copy + Download buttons.

Local dev:
    pip install -r requirements.txt
    cp .env.example .env   # fill in PAGESPEED_API_KEY + GMAIL_* vars
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

from optimizer import HTMLOptimizer, verify_html, get_manual_fixes
from pagespeed import PageSpeedAuditor
from emailer import (
    is_valid_email, send_report,
    EmailNotConfigured, EmailSendFailed,
)

# ----- Logging -----
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("optimizer")

# ----- Configuration -----
PAGESPEED_API_KEY = (os.environ.get("PAGESPEED_API_KEY") or "").strip()
if not PAGESPEED_API_KEY:
    log.warning(
        "PAGESPEED_API_KEY is not set. The app will still run but PageSpeed "
        "audits will be rate-limited. Set the env var or create a .env file."
    )

# Warn early if Gmail isn't configured. The /api/send-report route handles
# the error gracefully, but a startup warning makes the misconfiguration
# obvious in Render logs.
if not os.environ.get("GMAIL_USER") or not os.environ.get("GMAIL_APP_PASSWORD"):
    log.warning(
        "GMAIL_USER and/or GMAIL_APP_PASSWORD are not set. The 'Email me the "
        "report' button will fail until both are configured. See README.md "
        "for the Gmail App Password setup."
    )

MAX_HTML_BYTES = int(os.environ.get("MAX_HTML_BYTES", 5 * 1024 * 1024))  # 5 MB default

FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

# ----- App -----
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# IMPORTANT: form-encoded HTML bodies inflate ~30–60% over raw size because
# every <, >, ", and newline becomes a 3-char escape. A 5 MB HTML can produce
# an 8 MB POST body. Allow 3x the raw cap + headroom for headers/other fields
# so users hit our friendly "HTML too large" flash, not Flask's bare 413.
app.config["MAX_CONTENT_LENGTH"] = MAX_HTML_BYTES * 3 + 64 * 1024

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


@app.errorhandler(413)
def handle_too_large(e):
    """Flask raises this BEFORE the view runs when the request body exceeds
    MAX_CONTENT_LENGTH. Redirect to step 2 with a friendly flash instead of
    showing the bare HTML 413 page."""
    flash(
        f"The pasted HTML is too large to upload. The limit is "
        f"{MAX_HTML_BYTES // (1024 * 1024)} MB of HTML.",
        "error",
    )
    return redirect(url_for("step2"))


# ---- Step 1: URL + Email ----
@app.route("/step1", methods=["GET", "POST"])
def step1():
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        email = (request.form.get("email") or "").strip()

        if not url:
            flash("Please enter a URL.", "error")
            return render_template("step1.html", url=url, email=email)
        if not email:
            flash("Please enter your email address.", "error")
            return render_template("step1.html", url=url, email=email)
        if not is_valid_email(email):
            flash("That email address doesn't look valid.", "error")
            return render_template("step1.html", url=url, email=email)

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        d = sdata()
        d["url"] = url
        d["email"] = email
        # New URL — clear any cached optimization from a previous run.
        d.pop("optimized_html", None)
        d.pop("html", None)
        return redirect(url_for("step2"))

    # Pre-fill from session if user hit Back.
    d = sdata()
    return render_template("step1.html", url=d.get("url", ""), email=d.get("email", ""))


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
            flash(f"HTML too large (max {MAX_HTML_BYTES // (1024 * 1024)} MB).", "error")
            return render_template("step2.html", url=d["url"], html=html)

        ok, err = verify_html(html)
        if not ok:
            flash(f"HTML verification failed: {err}", "error")
            return render_template("step2.html", url=d["url"], html=html)

        d["html"] = html
        # New paste invalidates any cached optimization from earlier.
        d.pop("optimized_html", None)
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

    # Idempotency: if the user refreshes step 3 the script re-fires this fetch.
    # Don't pay for a second PageSpeed audit — return the cached result.
    if d.get("optimized_html"):
        return jsonify({"ok": True, "redirect": url_for("step4")})

    try:
        url = d["url"]
        html = d["html"]

        # 1. Baseline PageSpeed audit on the LIVE url — mobile + desktop in
        # parallel so we stay under Render's proxy timeout.
        from concurrent.futures import ThreadPoolExecutor
        auditor = PageSpeedAuditor(api_key=PAGESPEED_API_KEY or None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            mobile_future = pool.submit(auditor.audit, url, "mobile")
            desktop_future = pool.submit(auditor.audit, url, "desktop")
            mobile = mobile_future.result()
            desktop = desktop_future.result()

        # If BOTH audits failed, fail honestly — don't fabricate a result.
        if mobile is None and desktop is None:
            return jsonify({
                "error": (
                    "PageSpeed audit failed for both mobile and desktop. "
                    "Check that the URL is publicly reachable and try again. "
                    "(If you hit Google's rate limit, set PAGESPEED_API_KEY.)"
                ),
            }), 502

        audit_for_opps = mobile or desktop

        # 2. Optimize the HTML
        opt = HTMLOptimizer(html, url, aggressive=False)
        optimized = opt.optimize()

        # 3. Manual fixes — "the perfect solution" shown when below 90
        manual_fixes = get_manual_fixes(audit_for_opps, opt.changes)

        # Persist everything Step 4 / Step 5 / email-sending will need
        d["optimized_html"] = optimized
        d["baseline_mobile"] = mobile["scores"].get("performance") if mobile else None
        d["baseline_desktop"] = desktop["scores"].get("performance") if desktop else None
        d["changes"] = opt.changes
        d["manual_fixes"] = manual_fixes
        d["opportunities"] = audit_for_opps["opportunities"] if audit_for_opps else []
        d["metrics"] = audit_for_opps["metrics"] if audit_for_opps else {}
        # Reset the "email already sent" flag so a fresh optimization can
        # be emailed again.
        d.pop("email_sent_to", None)

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

    mobile = d.get("baseline_mobile")
    desktop = d.get("baseline_desktop")
    # "Below 90" — the trigger to show manual fixes as "the perfect solution"
    below_90 = (
        (mobile is not None and mobile < 90)
        or (desktop is not None and desktop < 90)
    )

    return render_template(
        "step4.html",
        url=d["url"],
        email=d.get("email", ""),
        mobile=mobile,
        desktop=desktop,
        below_90=below_90,
        changes=d.get("changes", []),
        manual_fixes=d.get("manual_fixes", []),
        metrics=d.get("metrics", {}),
        email_sent_to=d.get("email_sent_to"),
    )


@app.route("/api/send-report", methods=["POST"])
def api_send_report():
    """Email the full Step 4 report to the address captured at Step 1.

    Synchronous send (~1-3s on Gmail). Returns JSON for the AJAX button —
    on success the button flips to '✓ Sent'; on failure the message comes
    back inline.
    """
    d = sdata()
    if "optimized_html" not in d:
        return jsonify({"error": "session expired — please start over"}), 400

    to_addr = d.get("email")
    if not to_addr:
        return jsonify({"error": "no email captured in step 1"}), 400

    try:
        send_report(
            to_addr=to_addr,
            url=d["url"],
            mobile_score=d.get("baseline_mobile"),
            desktop_score=d.get("baseline_desktop"),
            metrics=d.get("metrics", {}),
            changes=d.get("changes", []),
            manual_fixes=d.get("manual_fixes", []),
            optimized_html=d.get("optimized_html", ""),
        )
        d["email_sent_to"] = to_addr
        log.info("Report emailed to %s for %s", to_addr, d["url"])
        return jsonify({"ok": True, "sent_to": to_addr})
    except EmailNotConfigured as e:
        log.warning("Email send attempted but Gmail is not configured: %s", e)
        return jsonify({"error": str(e)}), 503
    except EmailSendFailed as e:
        return jsonify({"error": str(e)}), 502


# ---- Step 5: Final code + copy ----
@app.route("/step5")
def step5():
    d = sdata()
    if "optimized_html" not in d:
        return redirect(url_for("step1"))

    return render_template(
        "step5.html",
        url=d["url"],
        html=d["optimized_html"],
        mobile=d.get("baseline_mobile"),
        desktop=d.get("baseline_desktop"),
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
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
