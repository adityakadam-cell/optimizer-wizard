"""
Tests for the Gmail email composer.

Everything here runs offline — no SMTP, no network. We test:
  * Email syntactic validation
  * HTML report composition (the actual byte output)
  * Text fallback composition
  * Missing-config error path

The SMTP send itself is NOT tested in unit tests — that requires real Gmail
credentials. Manual test = run the wizard end-to-end against a real Render
deploy with GMAIL_USER/GMAIL_APP_PASSWORD set.
"""

import os
import pytest

from emailer import (
    is_valid_email,
    build_report_html,
    build_report_text,
    send_report,
    EmailNotConfigured,
    EmailSendFailed,
)


# ----- email validation -----

@pytest.mark.parametrize("addr", [
    "user@example.com",
    "first.last@example.co.in",
    "a@b.io",
    "name+tag@gmail.com",
    "WITH.CAPS@DOMAIN.COM",
])
def test_is_valid_email_accepts_well_formed(addr):
    assert is_valid_email(addr) is True


@pytest.mark.parametrize("addr", [
    "",
    "  ",
    "no-at-sign.com",
    "missing-tld@host",
    "two@@signs.com",
    "spaces in@addr.com",
    None,
])
def test_is_valid_email_rejects_malformed(addr):
    assert is_valid_email(addr) is False


def test_is_valid_email_rejects_excessive_length():
    long_addr = "a" * 255 + "@example.com"
    assert is_valid_email(long_addr) is False


# ----- sample fixtures -----

@pytest.fixture
def sample_fixes():
    """Realistic-looking manual fixes the email composer will receive."""
    return [
        {
            "index": 1,
            "title": "Enable Gzip or Brotli compression on your server",
            "category": "Server",
            "what": "Make your server send HTML/CSS/JS as compressed data.",
            "why": "Compression typically cuts text file sizes by 70-80%.",
            "how": [
                {"env": "Cloudflare", "steps": ["Toggle Brotli on in the dashboard."]},
                {"env": "Nginx", "steps": ["Add gzip on; to nginx.conf", "Reload nginx"]},
            ],
            "verify": "DevTools → Network → check content-encoding header.",
            "warning": None,
            "impact_ms": 1500,
        },
        {
            "index": 2,
            "title": "Set long Cache-Control headers on static assets",
            "category": "Server",
            "what": "Tell browsers to cache images/CSS/JS for a year.",
            "why": "Returning visitors get instant page loads.",
            "how": [
                {"env": "Cloudflare", "steps": ["Set Browser Cache TTL to 1 year."]},
            ],
            "verify": "Check Cache-Control: max-age=31536000 in response headers.",
            "warning": "Only apply long cache to versioned static files, never HTML.",
            "impact_ms": 0,
        },
    ]


@pytest.fixture
def sample_metrics():
    return {
        "first-contentful-paint": {"value": "1.2 s", "score": 0.95},
        "largest-contentful-paint": {"value": "3.5 s", "score": 0.50},
        "total-blocking-time": {"value": "250 ms", "score": 0.75},
        "cumulative-layout-shift": {"value": "0.08", "score": 0.85},
    }


# ----- build_report_html -----

def test_build_report_html_includes_url_and_scores(sample_fixes):
    html = build_report_html(
        url="https://example.com",
        mobile_score=65,
        desktop_score=82,
        metrics={},
        changes=["Added defer to 3 blocking scripts."],
        manual_fixes=sample_fixes,
    )
    assert "example.com" in html
    assert "65" in html
    assert "82" in html
    # Score band colours by threshold
    assert "#ffa400" in html or "#0cce6b" in html or "#ff4e42" in html


def test_build_report_html_handles_none_scores(sample_fixes):
    """When PageSpeed returned nothing, we should render '—' not crash."""
    html = build_report_html(
        url="https://example.com",
        mobile_score=None,
        desktop_score=None,
        metrics={},
        changes=[],
        manual_fixes=sample_fixes,
    )
    assert "—" in html
    assert "example.com" in html


def test_build_report_html_renders_all_fix_content(sample_fixes):
    """The email must include what/why/how/verify for each fix — that's the point."""
    html = build_report_html(
        url="https://example.com",
        mobile_score=70,
        desktop_score=80,
        metrics={},
        changes=[],
        manual_fixes=sample_fixes,
    )

    for fix in sample_fixes:
        assert fix["title"] in html
        assert fix["what"] in html
        assert fix["why"] in html
        assert fix["verify"] in html
        for env_block in fix["how"]:
            assert env_block["env"] in html
            for step in env_block["steps"]:
                assert step in html


def test_build_report_html_renders_warning_when_present(sample_fixes):
    html = build_report_html(
        url="https://example.com",
        mobile_score=70, desktop_score=80,
        metrics={}, changes=[], manual_fixes=sample_fixes,
    )
    # Fix #2 has a warning
    assert "Only apply long cache to versioned static files" in html


def test_build_report_html_below_90_shows_solution_intro(sample_fixes):
    """When below 90, intro tells user to apply fixes to reach 90+."""
    html = build_report_html(
        url="https://example.com",
        mobile_score=65, desktop_score=82,
        metrics={}, changes=[], manual_fixes=sample_fixes,
    )
    assert "90+" in html


def test_build_report_html_at_or_above_90_shows_tune_up_intro(sample_fixes):
    """When 90+ on both, intro frames fixes as optional."""
    html = build_report_html(
        url="https://example.com",
        mobile_score=92, desktop_score=95,
        metrics={}, changes=[], manual_fixes=sample_fixes,
    )
    assert "tune-up" in html.lower() or "diminishing" in html.lower()


def test_build_report_html_renders_metrics_when_provided(sample_fixes, sample_metrics):
    html = build_report_html(
        url="https://example.com",
        mobile_score=70, desktop_score=80,
        metrics=sample_metrics, changes=[], manual_fixes=sample_fixes,
    )
    assert "Largest Contentful Paint" in html
    assert "3.5 s" in html


def test_build_report_html_escapes_url_to_prevent_injection():
    """Pasted URLs go into the email — make sure < > don't break out as HTML."""
    html = build_report_html(
        url="https://example.com/?x=<script>alert(1)</script>",
        mobile_score=70, desktop_score=80,
        metrics={}, changes=[], manual_fixes=[],
    )
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


# ----- build_report_text -----

def test_build_report_text_includes_essentials(sample_fixes):
    text = build_report_text(
        url="https://example.com",
        mobile_score=65, desktop_score=82,
        changes=["Added defer to 3 blocking scripts."],
        manual_fixes=sample_fixes,
    )
    assert "https://example.com" in text
    assert "Mobile" in text
    assert "65" in text
    assert "Desktop" in text
    assert "82" in text
    for fix in sample_fixes:
        assert fix["title"] in text


def test_build_report_text_handles_empty_lists():
    text = build_report_text(
        url="https://example.com",
        mobile_score=None, desktop_score=None,
        changes=[], manual_fixes=[],
    )
    assert "https://example.com" in text


# ----- send_report config validation -----

def test_send_report_raises_when_gmail_not_configured(monkeypatch):
    """If env vars are missing, we get a clean EmailNotConfigured error,
    not an SMTP traceback the user can't act on."""
    monkeypatch.delenv("GMAIL_USER", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)

    with pytest.raises(EmailNotConfigured):
        send_report(
            to_addr="user@example.com",
            url="https://example.com",
            mobile_score=70, desktop_score=80,
            metrics={}, changes=[], manual_fixes=[],
            optimized_html="<html></html>",
        )


def test_send_report_rejects_invalid_recipient(monkeypatch):
    """Invalid recipient short-circuits before we touch SMTP."""
    monkeypatch.setenv("GMAIL_USER", "sender@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")

    with pytest.raises(EmailSendFailed):
        send_report(
            to_addr="not-an-email",
            url="https://example.com",
            mobile_score=70, desktop_score=80,
            metrics={}, changes=[], manual_fixes=[],
            optimized_html="<html></html>",
        )
