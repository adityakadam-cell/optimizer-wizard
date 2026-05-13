"""
Smoke tests for the optimizer wizard.

These run offline — no PageSpeed API calls, no SMTP. They cover the HTML
optimizer, verifier, manual-fixes builder, and Flask wiring.
"""

import pytest

from optimizer import (
    HTMLOptimizer,
    verify_html,
    get_manual_fixes,
)


# ----- verify_html -----

def test_verify_html_accepts_minimal_doc():
    ok, err = verify_html(
        "<!DOCTYPE html><html><head><title>t</title></head><body>x</body></html>"
    )
    assert ok is True
    assert err == ""


def test_verify_html_rejects_short_input():
    ok, err = verify_html("hi")
    assert ok is False
    assert "too short" in err.lower()


def test_verify_html_rejects_non_html():
    ok, err = verify_html("just some plain text without any tags here at all")
    assert ok is False


# ----- HTMLOptimizer -----

@pytest.fixture
def sample_html():
    return """<!DOCTYPE html>
<html>
<head><title>Demo</title></head>
<body>
  <img src="hero.jpg">
  <img src="thumb.png">
  <iframe src="https://www.youtube.com/embed/x"></iframe>
  <script src="https://example.com/lib.js"></script>
  <script src="https://www.googletagmanager.com/gtm.js"></script>
  <!-- old comment -->
</body>
</html>"""


def test_optimizer_applies_expected_changes(sample_html):
    opt = HTMLOptimizer(sample_html, "https://example.com")
    out = opt.optimize()

    assert "<html" in out.lower()
    assert 'loading="lazy"' in out
    assert 'fetchpriority="high"' in out
    assert "defer" in out
    assert "<picture" in out
    assert "image/webp" in out
    assert "old comment" not in out
    assert len(opt.changes) >= 5


def test_optimizer_preserves_analytics_scripts(sample_html):
    """GTM-like scripts must NOT be deferred — that breaks tracking."""
    from bs4 import BeautifulSoup

    opt = HTMLOptimizer(sample_html, "https://example.com")
    out = opt.optimize()
    soup = BeautifulSoup(out, "html.parser")

    gtm_scripts = [
        s for s in soup.find_all("script")
        if "googletagmanager" in (s.get("src") or "")
    ]
    assert len(gtm_scripts) == 1
    gtm = gtm_scripts[0]
    assert not gtm.has_attr("defer")
    assert not gtm.has_attr("async")

    other_scripts = [
        s for s in soup.find_all("script")
        if "googletagmanager" not in (s.get("src") or "") and s.get("src")
    ]
    assert any(s.has_attr("defer") for s in other_scripts)


def test_optimizer_adds_doctype_when_missing():
    html = "<html><head></head><body>x</body></html>"
    opt = HTMLOptimizer(html, "https://example.com")
    out = opt.optimize()
    assert out.lstrip().lower().startswith("<!doctype")


# ----- manual fixes table -----

def test_manual_fixes_returns_always_show_items_with_no_audit():
    fixes = get_manual_fixes(None, [])
    titles = [f["title"] for f in fixes]
    assert any("Gzip" in t or "Brotli" in t for t in titles)
    assert any("CDN" in t for t in titles)


def test_manual_fixes_dedupes_by_title():
    audit = {"opportunities": [{"id": "uses-text-compression", "savings_ms": 500}]}
    fixes = get_manual_fixes(audit, [])
    titles = [f["title"] for f in fixes]
    assert len(titles) == len(set(titles))


def test_manual_fixes_include_what_why_how_verify_keys():
    """Email composer and step4 template depend on these keys being present."""
    fixes = get_manual_fixes(None, [])
    assert len(fixes) > 0
    for f in fixes:
        assert "title" in f
        assert "category" in f
        assert "what" in f
        assert "why" in f
        assert "how" in f
        assert "verify" in f
        assert "index" in f


# ----- Flask routes -----

@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_root_redirects_to_step1(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/step1" in r.location


def test_step1_renders_with_url_and_email_fields(client):
    r = client.get("/step1")
    assert r.status_code == 200
    # Both inputs must be present — email is now a required field
    assert b'name="url"' in r.data
    assert b'name="email"' in r.data
    assert b'type="email"' in r.data


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_step5_redirects_when_no_data(client):
    r = client.get("/step5")
    assert r.status_code == 302


def test_step1_rejects_missing_email(client):
    r = client.post("/step1", data={"url": "example.com"}, follow_redirects=False)
    # Should NOT redirect to step2 — should re-render step1 with a flash
    assert r.status_code == 200
    assert b"email" in r.data.lower()


def test_step1_rejects_invalid_email(client):
    r = client.post("/step1", data={
        "url": "example.com",
        "email": "not-an-email",
    }, follow_redirects=False)
    assert r.status_code == 200


def test_step1_to_step2_flow_with_email(client):
    r = client.post("/step1", data={
        "url": "example.com",
        "email": "user@example.com",
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "/step2" in r.location

    r = client.get("/step2")
    assert r.status_code == 200
    assert b"<textarea" in r.data


def test_send_report_requires_session(client):
    """Calling /api/send-report without a completed wizard returns 400."""
    r = client.post("/api/send-report")
    assert r.status_code == 400
    assert b"session" in r.data.lower() or b"start over" in r.data.lower()
