"""
PageSpeed Insights API client.
Uses the same public Lighthouse engine that powers https://pagespeed.web.dev/.
"""

from __future__ import annotations

import requests

API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TIMEOUT = 180  # audits can take a long time


class PageSpeedAuditor:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or None

    def audit(self, url: str, strategy: str = "mobile") -> dict | None:
        params = {
            "url": url,
            "strategy": strategy,
            "category": ["performance", "accessibility", "best-practices", "seo"],
        }
        if self.api_key:
            params["key"] = self.api_key

        try:
            r = requests.get(API_URL, params=params, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"[PageSpeed] audit failed ({strategy}): {e}")
            return None

        try:
            return self._parse(r.json(), strategy)
        except Exception as e:
            print(f"[PageSpeed] parse error: {e}")
            return None

    @staticmethod
    def _parse(data: dict, strategy: str) -> dict:
        lhr = data.get("lighthouseResult", {})
        categories = lhr.get("categories", {})
        audits = lhr.get("audits", {})

        result = {
            "strategy": strategy,
            "scores": {
                name: int(round(cat["score"] * 100))
                for name, cat in categories.items()
                if cat.get("score") is not None
            },
            "metrics": {},
            "opportunities": [],
        }

        for mid in (
            "first-contentful-paint",
            "largest-contentful-paint",
            "total-blocking-time",
            "cumulative-layout-shift",
            "speed-index",
            "interactive",
        ):
            if mid in audits:
                a = audits[mid]
                result["metrics"][mid] = {
                    "value": a.get("displayValue"),
                    "score": a.get("score"),
                }

        for aid, audit in audits.items():
            details = audit.get("details", {}) or {}
            if details.get("type") == "opportunity" and (audit.get("score") or 1) < 0.9:
                savings = details.get("overallSavingsMs", 0) or 0
                if savings > 0:
                    result["opportunities"].append({
                        "id": aid,
                        "title": audit.get("title", aid),
                        "description": audit.get("description", ""),
                        "savings_ms": savings,
                    })

        result["opportunities"].sort(key=lambda x: x["savings_ms"], reverse=True)
        return result
