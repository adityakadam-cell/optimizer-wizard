"""
Gmail SMTP email sender for the optimizer wizard's "Email me the report" feature.

The actual SMTP call is in send_report(). Everything else is HTML composition,
which is intentionally testable offline (no network) — see tests/test_emailer.py.

Required env vars (set in Render dashboard or .env):
    GMAIL_USER           — full Gmail address used as the sender, e.g. seo.reports@gmail.com
    GMAIL_APP_PASSWORD   — 16-char Google App Password (NOT your normal password)
    GMAIL_FROM_NAME      — display name recipients see, e.g. "SEO Optimizer Wizard"

To create the App Password: https://myaccount.google.com/apppasswords
(2-Step Verification must already be enabled on the Google account.)
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
from email.message import EmailMessage
from html import escape

log = logging.getLogger("optimizer.emailer")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # STARTTLS port. (465 is SSL-from-the-start; we use STARTTLS.)
SMTP_TIMEOUT = 30  # seconds

# Liberal email pattern — good enough for form validation. Real validation
# happens when SMTP either delivers or bounces.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_email(addr: str) -> bool:
    """Lightweight syntactic check. Don't try to fully validate email per RFC 5322."""
    if not addr:
        return False
    addr = addr.strip()
    if len(addr) > 254:
        return False
    return bool(_EMAIL_RE.match(addr))


# ============================================================
#  HTML email composition
# ============================================================
#
# Email clients (Gmail, Outlook, Apple Mail) strip <style> blocks inconsistently
# and ignore external stylesheets entirely. So every style is inlined on the
# element. The design is intentionally simple: a single column, no media
# queries, no JavaScript. Works in everything from Gmail mobile to Outlook 2016.

# Score colour thresholds match Google's PageSpeed colour bands.
def _score_colour(score):
    if score is None:
        return "#9ca3af"  # grey
    if score >= 90:
        return "#0cce6b"  # green
    if score >= 50:
        return "#ffa400"  # amber
    return "#ff4e42"      # red


def _score_card_html(label: str, score) -> str:
    """One score tile for the email's score row."""
    colour = _score_colour(score)
    display = str(score) if score is not None else "—"
    return f"""
    <td style="padding:0 10px;vertical-align:top;text-align:center;">
        <div style="border:2px solid {colour};border-radius:12px;padding:18px 24px;display:inline-block;min-width:140px;">
            <div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;font-weight:600;">{escape(label)}</div>
            <div style="font-size:42px;font-weight:700;color:{colour};line-height:1.2;margin-top:6px;">{display}</div>
            <div style="font-size:11px;color:#9ca3af;margin-top:4px;">out of 100</div>
        </div>
    </td>"""


def _metrics_html(metrics: dict) -> str:
    """Lighthouse Core Web Vitals table."""
    if not metrics:
        return ""

    rows = []
    label_map = {
        "first-contentful-paint": "First Contentful Paint",
        "largest-contentful-paint": "Largest Contentful Paint (LCP)",
        "total-blocking-time": "Total Blocking Time (TBT)",
        "cumulative-layout-shift": "Cumulative Layout Shift (CLS)",
        "speed-index": "Speed Index",
        "interactive": "Time to Interactive",
    }

    for mid, label in label_map.items():
        if mid in metrics:
            value = metrics[mid].get("value") or "—"
            score = metrics[mid].get("score")
            colour = _score_colour(int(score * 100) if score is not None else None)
            rows.append(f"""
                <tr>
                    <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;color:#374151;">{escape(label)}</td>
                    <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;font-size:14px;color:{colour};font-weight:600;text-align:right;">{escape(str(value))}</td>
                </tr>""")

    if not rows:
        return ""

    return f"""
    <h2 style="font-size:18px;color:#111827;margin:32px 0 12px 0;">Core Web Vitals (mobile)</h2>
    <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
        {"".join(rows)}
    </table>"""


def _changes_html(changes: list) -> str:
    if not changes:
        return """
        <h2 style="font-size:18px;color:#111827;margin:32px 0 12px 0;">Automatic changes applied</h2>
        <p style="font-size:14px;color:#6b7280;margin:0;">No automatic changes were needed — the HTML is already well-optimized.</p>"""

    items = "".join(
        f'<li style="margin-bottom:6px;font-size:14px;color:#374151;">{escape(c)}</li>'
        for c in changes
    )
    return f"""
    <h2 style="font-size:18px;color:#111827;margin:32px 0 12px 0;">Automatic changes applied <span style="font-weight:normal;color:#9ca3af;font-size:14px;">({len(changes)})</span></h2>
    <ul style="margin:0;padding-left:22px;">{items}</ul>"""


def _fix_steps_html(env_block: dict) -> str:
    env_label = escape(env_block.get("env", ""))
    steps = env_block.get("steps", [])
    step_items = "".join(
        f'<li style="margin-bottom:5px;color:#374151;">{escape(s)}</li>'
        for s in steps
    )
    return f"""
    <div style="margin:10px 0 10px 0;padding:10px 14px;background:#f9fafb;border-left:3px solid #6366f1;border-radius:4px;">
        <div style="font-weight:600;font-size:13px;color:#4f46e5;margin-bottom:6px;">{env_label}</div>
        <ol style="margin:0;padding-left:18px;font-size:13px;line-height:1.5;">{step_items}</ol>
    </div>"""


def _fix_card_html(fix: dict) -> str:
    """One manual-fix block, fully expanded (no accordion — this is email)."""
    title = escape(fix.get("title", ""))
    category = escape(fix.get("category", ""))
    impact_ms = fix.get("impact_ms", 0) or 0
    impact_label = (
        f"~{impact_ms / 1000:.1f}s saving" if impact_ms > 0 else "Recommended"
    )

    what = escape(fix.get("what", ""))
    why = escape(fix.get("why", ""))
    verify = escape(fix.get("verify", ""))
    warning = fix.get("warning")

    how_blocks = "".join(_fix_steps_html(env) for env in fix.get("how", []))

    warning_html = ""
    if warning:
        warning_html = f"""
        <div style="margin:10px 0;padding:10px 14px;background:#fef3c7;border-left:3px solid #f59e0b;border-radius:4px;font-size:13px;color:#92400e;">
            <strong>⚠ Important:</strong> {escape(warning)}
        </div>"""

    return f"""
    <div style="margin-bottom:24px;padding:16px 18px;border:1px solid #e5e7eb;border-radius:8px;background:#ffffff;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;">
            <h3 style="font-size:16px;color:#111827;margin:0;flex:1;">
                <span style="color:#6366f1;margin-right:6px;">#{fix.get("index", 0)}</span>
                {title}
            </h3>
        </div>
        <div style="font-size:12px;color:#6b7280;margin-bottom:12px;">
            <span style="background:#eef2ff;color:#4338ca;padding:2px 8px;border-radius:10px;font-weight:600;margin-right:8px;">{category}</span>
            <span style="color:#6b7280;">{impact_label}</span>
        </div>

        <div style="margin-bottom:10px;">
            <div style="font-size:13px;font-weight:600;color:#374151;margin-bottom:4px;">What this is</div>
            <div style="font-size:14px;color:#4b5563;line-height:1.5;">{what}</div>
        </div>

        <div style="margin-bottom:10px;">
            <div style="font-size:13px;font-weight:600;color:#374151;margin-bottom:4px;">Why it matters</div>
            <div style="font-size:14px;color:#4b5563;line-height:1.5;">{why}</div>
        </div>

        {warning_html}

        <div style="margin-bottom:10px;">
            <div style="font-size:13px;font-weight:600;color:#374151;margin:8px 0 4px 0;">How to do it</div>
            <div style="font-size:12px;color:#6b7280;margin-bottom:6px;">Pick the environment matching your setup. Skip the others.</div>
            {how_blocks}
        </div>

        <div>
            <div style="font-size:13px;font-weight:600;color:#374151;margin-bottom:4px;">How to verify it worked</div>
            <div style="font-size:14px;color:#4b5563;line-height:1.5;">{verify}</div>
        </div>
    </div>"""


def _manual_fixes_html(manual_fixes: list, mobile_score, desktop_score) -> str:
    if not manual_fixes:
        return ""

    below_90 = (
        (mobile_score is not None and mobile_score < 90)
        or (desktop_score is not None and desktop_score < 90)
    )

    intro = (
        "Below 90 on mobile or desktop. Apply the fixes below to reach 90+:"
        if below_90
        else "Your site is already at 90+ on both. These fixes are tune-ups for diminishing returns:"
    )

    cards = "".join(_fix_card_html(f) for f in manual_fixes)

    return f"""
    <h2 style="font-size:18px;color:#111827;margin:32px 0 12px 0;">
        The perfect solution <span style="font-weight:normal;color:#9ca3af;font-size:14px;">({len(manual_fixes)} fixes)</span>
    </h2>
    <p style="font-size:14px;color:#4b5563;margin:0 0 16px 0;">{intro}</p>
    {cards}"""


def build_report_html(
    *,
    url: str,
    mobile_score,
    desktop_score,
    metrics: dict,
    changes: list,
    manual_fixes: list,
) -> str:
    """Compose the full HTML email body. Pure function — no I/O."""
    mobile_card = _score_card_html("Mobile", mobile_score)
    desktop_card = _score_card_html("Desktop", desktop_score)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>PageSpeed Report — {escape(url)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#111827;">
<table cellpadding="0" cellspacing="0" style="width:100%;background:#f3f4f6;">
<tr><td style="padding:24px 12px;">

<table cellpadding="0" cellspacing="0" style="max-width:680px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.06);">
<tr><td style="padding:32px 32px 0 32px;">

<div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;font-weight:600;margin-bottom:6px;">⚡ Website Optimizer · Full Report</div>
<h1 style="font-size:22px;color:#111827;margin:0 0 6px 0;line-height:1.3;">PageSpeed audit for</h1>
<a href="{escape(url)}" style="font-size:16px;color:#6366f1;text-decoration:none;word-break:break-all;">{escape(url)}</a>

<h2 style="font-size:18px;color:#111827;margin:32px 0 16px 0;">Live PageSpeed scores</h2>
<table cellpadding="0" cellspacing="0" style="margin:0 auto;"><tr>{mobile_card}{desktop_card}</tr></table>
<p style="font-size:12px;color:#9ca3af;text-align:center;margin:8px 0 0 0;">Fetched directly from Google PageSpeed Insights for your live URL.</p>

{_metrics_html(metrics)}

{_changes_html(changes)}

{_manual_fixes_html(manual_fixes, mobile_score, desktop_score)}

<div style="margin:32px 0 0 0;padding:16px;background:#eef2ff;border-radius:8px;">
    <div style="font-size:14px;color:#3730a3;font-weight:600;margin-bottom:4px;">Optimized HTML attached</div>
    <div style="font-size:13px;color:#4338ca;">The optimized HTML file is attached to this email as <code>index-optimized.html</code>. Deploy it after applying the fixes above, then re-test at <a href="https://pagespeed.web.dev/analysis?url={escape(url)}" style="color:#4f46e5;">pagespeed.web.dev</a>.</div>
</div>

</td></tr>
<tr><td style="padding:24px 32px 32px 32px;background:#f9fafb;border-top:1px solid #e5e7eb;margin-top:24px;">
    <p style="font-size:12px;color:#9ca3af;margin:0;text-align:center;">
        Generated by Website Optimizer Wizard · Powered by Google PageSpeed Insights
    </p>
</td></tr>
</table>

</td></tr>
</table>
</body>
</html>"""


def build_report_text(
    *,
    url: str,
    mobile_score,
    desktop_score,
    changes: list,
    manual_fixes: list,
) -> str:
    """Plain-text fallback for clients that won't render HTML. Required by RFC 2046."""
    lines = [
        f"WEBSITE OPTIMIZER — FULL REPORT",
        f"=" * 60,
        f"",
        f"URL audited: {url}",
        f"",
        f"LIVE PAGESPEED SCORES",
        f"  Mobile:  {mobile_score if mobile_score is not None else '—'} / 100",
        f"  Desktop: {desktop_score if desktop_score is not None else '—'} / 100",
        f"",
    ]

    if changes:
        lines.append(f"AUTOMATIC CHANGES APPLIED ({len(changes)})")
        for c in changes:
            lines.append(f"  • {c}")
        lines.append("")

    if manual_fixes:
        lines.append(f"THE PERFECT SOLUTION ({len(manual_fixes)} fixes)")
        lines.append(f"-" * 60)
        for fix in manual_fixes:
            lines.append("")
            lines.append(f"#{fix.get('index', 0)} — {fix.get('title', '')}")
            lines.append(f"  Category: {fix.get('category', '')}")
            if fix.get("what"):
                lines.append(f"  What: {fix['what']}")
            if fix.get("why"):
                lines.append(f"  Why:  {fix['why']}")
            if fix.get("warning"):
                lines.append(f"  ⚠ Warning: {fix['warning']}")
            if fix.get("how"):
                lines.append(f"  How:")
                for env in fix["how"]:
                    lines.append(f"    [{env.get('env', '')}]")
                    for step in env.get("steps", []):
                        lines.append(f"      - {step}")
            if fix.get("verify"):
                lines.append(f"  Verify: {fix['verify']}")
        lines.append("")

    lines.extend([
        f"-" * 60,
        f"The optimized HTML is attached as index-optimized.html.",
        f"Deploy it after applying the fixes above, then re-test at:",
        f"  https://pagespeed.web.dev/analysis?url={url}",
        f"",
    ])

    return "\n".join(lines)


# ============================================================
#  SMTP send
# ============================================================
class EmailNotConfigured(RuntimeError):
    """Raised when GMAIL_USER/GMAIL_APP_PASSWORD are missing — caller should
    show a friendly 'email is not configured on the server' error to the user."""


class EmailSendFailed(RuntimeError):
    """Raised when SMTP rejected the send (auth, bad address, server timeout).
    Caller should show the message to the user — it's already human-readable."""


def _get_smtp_config() -> tuple[str, str, str]:
    user = (os.environ.get("GMAIL_USER") or "").strip()
    # Google displays App Passwords with spaces every 4 chars for readability;
    # the SMTP server doesn't care, but strip them for consistency.
    pwd = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    from_name = (os.environ.get("GMAIL_FROM_NAME") or "Website Optimizer").strip()

    if not user or not pwd:
        raise EmailNotConfigured(
            "Email sending is not configured on this server. "
            "Ask the admin to set GMAIL_USER and GMAIL_APP_PASSWORD."
        )
    return user, pwd, from_name


def send_report(
    *,
    to_addr: str,
    url: str,
    mobile_score,
    desktop_score,
    metrics: dict,
    changes: list,
    manual_fixes: list,
    optimized_html: str,
) -> None:
    """Send the report email via Gmail SMTP. Raises EmailSendFailed on failure."""
    if not is_valid_email(to_addr):
        raise EmailSendFailed(f"'{to_addr}' is not a valid email address.")

    user, pwd, from_name = _get_smtp_config()

    msg = EmailMessage()
    # Subject summarises the most important fact: scores and the URL host.
    mob_str = mobile_score if mobile_score is not None else "—"
    desk_str = desktop_score if desktop_score is not None else "—"
    msg["Subject"] = f"PageSpeed report — {url} (Mobile {mob_str} / Desktop {desk_str})"
    msg["From"] = f"{from_name} <{user}>"
    msg["To"] = to_addr

    # Plain-text body first (RFC says it should appear before HTML).
    msg.set_content(build_report_text(
        url=url,
        mobile_score=mobile_score,
        desktop_score=desktop_score,
        changes=changes,
        manual_fixes=manual_fixes,
    ))
    msg.add_alternative(
        build_report_html(
            url=url,
            mobile_score=mobile_score,
            desktop_score=desktop_score,
            metrics=metrics,
            changes=changes,
            manual_fixes=manual_fixes,
        ),
        subtype="html",
    )

    # Attach the optimized HTML so the user gets the full deliverable in one email.
    if optimized_html:
        msg.add_attachment(
            optimized_html.encode("utf-8"),
            maintype="text",
            subtype="html",
            filename="index-optimized.html",
        )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        # Almost always: typo in GMAIL_APP_PASSWORD, or 2FA not enabled, or
        # the App Password was revoked because the Google password changed.
        log.exception("Gmail SMTP auth failed")
        raise EmailSendFailed(
            "Gmail rejected the login. The App Password may be wrong or revoked. "
            "Ask the admin to regenerate it at myaccount.google.com/apppasswords."
        ) from e
    except smtplib.SMTPRecipientsRefused as e:
        log.exception("Gmail rejected recipient")
        raise EmailSendFailed(f"Gmail wouldn't deliver to '{to_addr}'.") from e
    except (smtplib.SMTPException, OSError) as e:
        log.exception("SMTP send failed")
        raise EmailSendFailed(f"Email send failed: {e}") from e
