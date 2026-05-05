"""
Smoke tests for the optimizer wizard.

These run offline — no PageSpeed API calls. They cover the HTML optimizer,
verifier, score estimator, and Flask wiring.
"""

import pytest

from optimizer import (
    HTMLOptimizer,
    verify_html,
    estimate_after_score,
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

    # Output is non-empty HTML
    assert "<html" in out.lower()

    # Lazy load was added to the non-hero image
    assert 'loading="lazy"' in out

    # Hero image got fetchpriority=high
    assert 'fetchpriority="high"' in out

    # Defer was added to a non-analytics script
    assert "defer" in out

    # Picture wrapper for raster image with WebP source
    assert "<picture" in out
    assert "image/webp" in out

    # Comment was stripped
    assert "old comment" not in out

    # Changes list has entries
    assert len(opt.changes) >= 5


def test_optimizer_preserves_analytics_scripts(sample_html):
    """GTM-like scripts must NOT be deferred — that breaks tracking."""
    from bs4 import BeautifulSoup

    opt = HTMLOptimizer(sample_html, "https://example.com")
    out = opt.optimize()
    soup = BeautifulSoup(out, "html.parser")

    # Find the GTM script tag specifically and check it has neither defer nor async
    gtm_scripts = [
        s for s in soup.find_all("script")
        if "googletagmanager" in (s.get("src") or "")
    ]
    assert len(gtm_scripts) == 1, "expected exactly one GTM script in output"
    gtm = gtm_scripts[0]
    assert not gtm.has_attr("defer"), "GTM script must not be deferred"
    assert not gtm.has_attr("async"), "GTM script must not be async (in non-aggressive mode)"

    # Sanity: a non-analytics script SHOULD have defer
    other_scripts = [
        s for s in soup.find_all("script")
        if "googletagmanager" not in (s.get("src") or "") and s.get("src")
    ]
    assert any(s.has_attr("defer") for s in other_scripts), "non-analytics scripts should be deferred"


def test_optimizer_adds_doctype_when_missing():
    html = "<html><head></head><body>x</body></html>"
    opt = HTMLOptimizer(html, "https://example.com")
    out = opt.optimize()
    assert out.lstrip().lower().startswith("<!doctype")


def test_optimizer_aggressive_minifies_whitespace():
    html = """<!DOCTYPE html><html><head></head><body>
    <p>hello       world</p>
    </body></html>"""
    opt = HTMLOptimizer(html, "https://example.com", aggressive=True)
    out = opt.optimize()
    # Multiple internal spaces should collapse to one
    assert "hello world" in out
    assert "hello       world" not in out


# ----- score estimator -----

def test_estimate_score_caps_at_100():
    fake_changes = [f"change {i}" for i in range(50)]
    score = estimate_after_score(50, fake_changes, None)
    assert 0 <= score <= 100


def test_estimate_score_never_below_zero():
    score = estimate_after_score(0, [], None)
    assert score >= 0


def test_estimate_score_baseline_with_no_changes():
    score = estimate_after_score(85, [], None)
    # No changes means no gain — score should be ~baseline
    assert score == 85


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


def test_step1_renders(client):
    r = client.get("/step1")
    assert r.status_code == 200
    assert b"<form" in r.data


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_step5_redirects_when_no_data(client):
    r = client.get("/step5")
    assert r.status_code == 302


def test_step1_to_step2_flow(client):
    r = client.post("/step1", data={"url": "example.com"}, follow_redirects=False)
    assert r.status_code == 302
    assert "/step2" in r.location

    r = client.get("/step2")
    assert r.status_code == 200
    assert b"<textarea" in r.data
