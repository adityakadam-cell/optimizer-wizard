"""
HTML optimization engine.

- verify_html()           : basic validation of pasted HTML
- HTMLOptimizer           : applies safe performance tweaks to HTML
- estimate_after_score()  : rough heuristic for expected PageSpeed score after fixes
- get_manual_fixes()      : returns remaining things the user must do manually
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment


# ============================================================
#  HTML verification
# ============================================================
def verify_html(html: str) -> tuple[bool, str]:
    """
    Lightweight sanity-check on pasted HTML.
    Returns (is_valid, error_message_or_empty_string).
    """
    html = html.strip()
    if len(html) < 30:
        return False, "Content is too short to be a complete HTML page."

    lowered = html.lower()

    # Common signals that this is HTML
    looks_like_html = any(
        tag in lowered for tag in ("<html", "<head", "<body", "<!doctype")
    )
    if not looks_like_html:
        return False, "Could not find <html>, <head>, <body>, or <!DOCTYPE>. Is this really an HTML file?"

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return False, f"Parser error: {e}"

    if not soup.find():
        return False, "No HTML tags detected."

    # Unbalanced-ish basic check: at least one major structural tag
    if not (soup.find("html") or soup.find("body") or soup.find("head")):
        return False, "Missing <html>, <head> or <body>."

    return True, ""


# ============================================================
#  HTML Optimizer
# ============================================================
class HTMLOptimizer:
    """Applies safe-but-impactful performance optimisations to HTML."""

    # Scripts that must run synchronously (never defer these)
    NEVER_DEFER = (
        "gtag", "gtm.js", "googletagmanager",
        "fbevents", "hotjar", "clarity.ms",
        "recaptcha", "turnstile",
    )

    # Fonts we can pre-connect to for free perf wins
    COMMON_FONT_DOMAINS = (
        "https://fonts.googleapis.com",
        "https://fonts.gstatic.com",
    )

    def __init__(self, html: str, base_url: str, aggressive: bool = False):
        self.base_url = base_url
        self.base_domain = urlparse(base_url).netloc
        self.aggressive = aggressive
        # Use html.parser (stdlib, no extra deps)
        self.soup = BeautifulSoup(html, "html.parser")
        self.changes: list[str] = []

    def optimize(self) -> str:
        self._ensure_doctype()
        self._ensure_head()
        self._add_charset()
        self._add_viewport()
        self._optimize_images()
        self._lazy_iframes()
        self._defer_scripts()
        self._optimize_stylesheets()
        self._preload_hero_image()
        self._add_preconnect()
        self._strip_comments()
        if self.aggressive:
            self._aggressive_minify()
            self._remove_empty_attrs()

        return self._render()

    # --------------------------------------------------------
    #  Individual optimisations
    # --------------------------------------------------------
    def _ensure_doctype(self):
        first = str(self.soup).lstrip()[:15].lower()
        if not first.startswith("<!doctype"):
            # BeautifulSoup doesn't let us insert a doctype easily; we prepend at render time
            self._needs_doctype = True
            self.changes.append("Added missing <!DOCTYPE html>.")
        else:
            self._needs_doctype = False

    def _ensure_head(self):
        if self.soup.find("head"):
            return
        html_tag = self.soup.find("html") or self.soup
        head = self.soup.new_tag("head")
        if html_tag.name == "html":
            html_tag.insert(0, head)
        else:
            self.soup.insert(0, head)
        self.changes.append("Inserted missing <head> tag.")

    def _add_charset(self):
        head = self.soup.find("head")
        if not head:
            return
        if not head.find("meta", attrs={"charset": True}):
            # Also check http-equiv variant
            http_eq = head.find("meta", attrs={"http-equiv": lambda v: v and v.lower() == "content-type"})
            if not http_eq:
                head.insert(0, self.soup.new_tag("meta", attrs={"charset": "utf-8"}))
                self.changes.append("Added <meta charset=\"utf-8\">.")

    def _add_viewport(self):
        head = self.soup.find("head")
        if not head:
            return
        if not head.find("meta", attrs={"name": "viewport"}):
            tag = self.soup.new_tag(
                "meta",
                attrs={"name": "viewport",
                       "content": "width=device-width, initial-scale=1"},
            )
            head.insert(1, tag)
            self.changes.append("Added mobile viewport meta tag (+mobile score).")

    def _optimize_images(self):
        n_lazy = n_dec = n_webp = n_hero = 0
        for idx, img in enumerate(self.soup.find_all("img")):
            src = img.get("src") or img.get("data-src")
            if not src or src.startswith("data:"):
                continue

            # Hero: first image (assumed above-the-fold)
            if idx == 0:
                if img.get("loading") == "lazy":
                    del img["loading"]
                img["fetchpriority"] = img.get("fetchpriority", "high")
                img["decoding"] = img.get("decoding", "async")
                n_hero = 1
            else:
                if not img.get("loading"):
                    img["loading"] = "lazy"
                    n_lazy += 1
                if not img.get("decoding"):
                    img["decoding"] = "async"
                    n_dec += 1

            # Wrap raster images in <picture> with a WebP alternative
            # (the user will convert the actual image files separately)
            if not self._is_raster(src):
                continue
            if img.parent and img.parent.name == "picture":
                continue

            webp_src = self._to_webp_path(src)
            picture = self.soup.new_tag("picture")
            source = self.soup.new_tag(
                "source", attrs={"srcset": webp_src, "type": "image/webp"}
            )
            img.wrap(picture)
            picture.insert(0, source)
            n_webp += 1

        if n_hero:
            self.changes.append("Marked hero image with fetchpriority=\"high\" (+LCP).")
        if n_lazy:
            self.changes.append(f"Added loading=\"lazy\" to {n_lazy} images.")
        if n_dec:
            self.changes.append(f"Added decoding=\"async\" to {n_dec} images.")
        if n_webp:
            self.changes.append(
                f"Wrapped {n_webp} images in <picture> with WebP source "
                f"(requires converting the image files — see manual fixes)."
            )

    def _lazy_iframes(self):
        n = 0
        for iframe in self.soup.find_all("iframe"):
            if not iframe.get("loading"):
                iframe["loading"] = "lazy"
                n += 1
        if n:
            self.changes.append(f"Added loading=\"lazy\" to {n} iframes.")

    def _defer_scripts(self):
        n_defer = n_async = 0
        for s in self.soup.find_all("script"):
            src = (s.get("src") or "").lower()
            if not src:
                continue
            if s.get("async") or s.get("defer"):
                continue
            if s.get("type") == "module":
                continue  # modules are deferred by default
            if any(kw in src for kw in self.NEVER_DEFER):
                # analytics-like scripts: use async instead (safer than sync)
                if self.aggressive:
                    s["async"] = ""
                    n_async += 1
                continue
            s["defer"] = ""
            n_defer += 1

        if n_defer:
            self.changes.append(f"Added defer to {n_defer} blocking scripts (+TBT).")
        if n_async:
            self.changes.append(f"Added async to {n_async} analytics scripts.")

    def _optimize_stylesheets(self):
        """Mark print stylesheets as print media (frees main thread)."""
        n = 0
        for link in self.soup.find_all("link", rel="stylesheet"):
            href = (link.get("href") or "").lower()
            if "print" in href and link.get("media", "all") == "all":
                link["media"] = "print"
                n += 1
        if n:
            self.changes.append(f"Scoped {n} print stylesheet(s) to media=\"print\".")

    def _preload_hero_image(self):
        """Preload the hero image so LCP improves."""
        head = self.soup.find("head")
        if not head:
            return
        # Don't add duplicate preload
        for l in head.find_all("link", rel="preload"):
            if l.get("as") == "image":
                return

        first_img = self.soup.find("img")
        if not first_img:
            return
        src = first_img.get("src")
        if not src or src.startswith("data:"):
            return

        attrs = {"rel": "preload", "as": "image", "href": src, "fetchpriority": "high"}
        if self._is_raster(src):
            attrs["href"] = self._to_webp_path(src)
            attrs["type"] = "image/webp"
        head.append(self.soup.new_tag("link", attrs=attrs))
        self.changes.append("Preloaded hero image (+LCP).")

    def _add_preconnect(self):
        head = self.soup.find("head")
        if not head:
            return

        existing = {l.get("href") for l in head.find_all("link", rel="preconnect")}
        domains: set[str] = set()

        for tag in self.soup.find_all(["script", "link", "img"]):
            u = tag.get("src") or tag.get("href")
            if not u:
                continue
            parsed = urlparse(urljoin(self.base_url, u))
            if parsed.netloc and parsed.netloc != self.base_domain:
                domains.add(f"{parsed.scheme}://{parsed.netloc}")

        # Always add font providers if referenced anywhere
        full_html = str(self.soup).lower()
        for font_domain in self.COMMON_FONT_DOMAINS:
            if urlparse(font_domain).netloc in full_html:
                domains.add(font_domain)

        added = 0
        # Too many preconnects hurt perf; cap at 4
        for d in sorted(domains)[:4]:
            if d in existing:
                continue
            link = self.soup.new_tag(
                "link", rel="preconnect", href=d, crossorigin="anonymous"
            )
            head.append(link)
            added += 1

        if added:
            self.changes.append(f"Added preconnect hints for {added} external domains.")

    def _strip_comments(self):
        comments = [c for c in self.soup.find_all(string=lambda t: isinstance(t, Comment))
                    if not str(c).strip().startswith("[if ")]  # keep IE conditionals
        for c in comments:
            c.extract()
        if comments:
            self.changes.append(f"Removed {len(comments)} HTML comments.")

    def _aggressive_minify(self):
        """Collapse whitespace between tags (safe on HTML, not on <pre>/<textarea>/<script>/<style>)."""
        # Protect content inside these tags
        protected_tags = {"pre", "textarea", "script", "style"}
        for t in self.soup.find_all(string=True):
            if t.parent and t.parent.name in protected_tags:
                continue
            # Collapse runs of whitespace (but keep single spaces between words)
            new = re.sub(r"\s+", " ", str(t))
            if new != str(t):
                t.replace_with(new)
        self.changes.append("Collapsed whitespace in HTML (aggressive).")

    def _remove_empty_attrs(self):
        n = 0
        for tag in self.soup.find_all(True):
            to_del = [k for k, v in list(tag.attrs.items())
                      if v in ("", [], None) and k not in ("defer", "async", "required", "disabled", "hidden", "open", "checked", "selected")]
            for k in to_del:
                del tag[k]
                n += 1
        if n:
            self.changes.append(f"Removed {n} empty attributes.")

    # --------------------------------------------------------
    #  Helpers
    # --------------------------------------------------------
    def _render(self) -> str:
        body = str(self.soup)
        if getattr(self, "_needs_doctype", False) and not body.lstrip().lower().startswith("<!doctype"):
            body = "<!DOCTYPE html>\n" + body
        return body

    @staticmethod
    def _is_raster(src: str) -> bool:
        s = src.lower().split("?")[0].split("#")[0]
        return s.endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"))

    @staticmethod
    def _to_webp_path(src: str) -> str:
        # Replace file extension with .webp, keeping query/hash
        m = re.match(r"^(.*?\.)(jpg|jpeg|png|bmp|tif|tiff)(\?.*|#.*|$)", src, re.I)
        if not m:
            return src
        return f"{m.group(1)}webp{m.group(3)}"


# ============================================================
#  Score estimation
# ============================================================

# Approx Lighthouse points gained when a given change is applied.
# These are rough heuristics — real numbers depend on site specifics.
_SCORE_IMPACT = {
    "viewport meta": 6,
    "charset": 2,
    "loading=\"lazy\"": 4,
    "decoding=\"async\"": 1,
    "defer to": 8,       # defer blocking scripts
    "async to": 3,
    "preload": 5,        # preload hero
    "preconnect": 3,
    "WebP": 10,          # if they actually convert the files
    "picture": 0,        # per-image, already covered
    "hero image with fetchpriority": 4,
    "comments": 1,
    "whitespace": 2,     # minification
    "print stylesheet": 1,
}


def estimate_after_score(baseline: int, changes: list[str], audit) -> int:
    """
    Heuristic "after" score. Sums per-change impact, capped by:
      * The headroom left to 100
      * What opportunities PageSpeed actually flagged (addressing them gives back savings)
    """
    gain = 0.0
    seen = set()
    for change in changes:
        for key, pts in _SCORE_IMPACT.items():
            if key.lower() in change.lower() and key not in seen:
                gain += pts
                seen.add(key)
                break

    # Diminishing returns: each successive gain is smaller
    effective_gain = gain * (1 - baseline / 200)  # softer as baseline rises

    # Cap using PageSpeed opportunities: if they said "saves 1.5s" total and we addressed them,
    # scale our gain accordingly
    if audit and audit.get("opportunities"):
        total_savings_ms = sum(o["savings_ms"] for o in audit["opportunities"])
        if total_savings_ms > 0:
            # 3000ms of addressable savings ~= ~20 score points; roughly linear
            savings_cap = min(25, total_savings_ms / 150)
            effective_gain = min(effective_gain, savings_cap * 1.2)

    estimated = int(round(baseline + effective_gain))
    return max(0, min(100, estimated))


# ============================================================
#  Manual fixes table
# ============================================================

# Mapping of PageSpeed audit ids to human-friendly manual-fix rows
_OPP_TO_FIX = {
    "uses-text-compression":
        ("Enable Gzip or Brotli compression on your server",
         "Server",
         "Add `gzip on;` (Nginx) or enable mod_deflate/mod_brotli (Apache). For shared hosting, turn it on in cPanel → Optimize Website."),
    "uses-long-cache-ttl":
        ("Set long Cache-Control headers for static assets",
         "Server",
         "Return `Cache-Control: public, max-age=31536000, immutable` for images, CSS, JS (use versioned filenames)."),
    "server-response-time":
        ("Reduce server response time (TTFB)",
         "Server / Backend",
         "Add page caching, database query caching, or move to a faster host/CDN. Target TTFB < 600ms."),
    "uses-http2":
        ("Serve assets over HTTP/2 or HTTP/3",
         "Server",
         "Enable HTTP/2 in your web server config. All modern hosts support it with a single toggle."),
    "render-blocking-resources":
        ("Eliminate render-blocking CSS/JS",
         "Build pipeline",
         "Inline critical CSS (< 14KB) in <head>; load the rest async. Tools: Critical, PurgeCSS."),
    "unminified-css":
        ("Minify your CSS files",
         "Build pipeline",
         "Use your build tool (Vite/Webpack/Gulp) to minify. For WordPress: Autoptimize / WP Rocket."),
    "unminified-javascript":
        ("Minify your JavaScript files",
         "Build pipeline",
         "Use Terser / esbuild / your bundler's production mode."),
    "unused-css-rules":
        ("Remove unused CSS",
         "Build pipeline",
         "Use PurgeCSS / Tailwind purge to strip unused selectors."),
    "unused-javascript":
        ("Remove unused JavaScript",
         "Build pipeline",
         "Code-split by route, tree-shake, lazy-load non-critical modules."),
    "modern-image-formats":
        ("Convert images to WebP or AVIF",
         "Assets",
         "The wizard already inserted <picture> tags referencing .webp files. You still need to produce the .webp files (e.g. `cwebp` CLI, Squoosh, or the standalone script included)."),
    "efficiently-encode-images":
        ("Re-encode images at appropriate quality",
         "Assets",
         "Re-export JPEGs at 75-85% quality and PNGs with pngquant."),
    "offscreen-images":
        ("Lazy-load off-screen images",
         "HTML",
         "✓ Done automatically by the wizard (`loading=\"lazy\"`)."),
    "uses-optimized-images":
        ("Compress images properly",
         "Assets",
         "Images > 100KB should be optimized. Use ImageOptim, Squoosh, or TinyPNG."),
    "uses-responsive-images":
        ("Serve appropriately-sized images",
         "HTML + Assets",
         "Generate 2-3 sizes per image and use <img srcset=\"... 480w, ... 1200w\" sizes=\"...\">."),
    "efficient-animated-content":
        ("Replace animated GIFs with video",
         "Assets",
         "Use <video autoplay muted loop playsinline> with MP4/WebM. GIFs are ~10x larger."),
    "font-display":
        ("Add font-display: swap to @font-face",
         "CSS",
         "Ensure every @font-face rule has `font-display: swap;` so text stays visible while fonts load."),
    "third-party-summary":
        ("Reduce third-party script impact",
         "Marketing / Analytics",
         "Audit your analytics, chat widgets, ad pixels. Remove what you don't strictly need."),
    "bootup-time":
        ("Reduce JavaScript execution time",
         "JS",
         "Split large bundles, lazy-load non-critical JS, move heavy work to Web Workers."),
    "mainthread-work-breakdown":
        ("Minimize main-thread work",
         "JS",
         "Same as above — less JS parse/compile/execute on page load."),
    "dom-size":
        ("Avoid excessive DOM size",
         "HTML",
         "Large DOMs (>1500 nodes) slow rendering. Paginate or virtualize long lists."),
}

# Always-useful hygiene items if audit didn't return them
_ALWAYS_SHOW = [
    ("Enable Gzip or Brotli compression on your server",
     "Server",
     "Massive win — typically cuts HTML/CSS/JS transfer size by 70-80%."),
    ("Put a CDN in front of your site",
     "Infrastructure",
     "Cloudflare's free tier alone typically adds 10-20 points. Also: Bunny.net, Fastly."),
    ("Set long Cache-Control headers for static assets",
     "Server",
     "`Cache-Control: public, max-age=31536000, immutable` for versioned assets."),
]


def get_manual_fixes(audit, applied_changes: list[str]) -> list[dict]:
    """
    Build the pointer table of manual fixes the script could NOT apply.
    Each fix is {index, title, category, details, impact_ms}.
    """
    fixes: list[dict] = []
    seen_titles: set[str] = set()

    def _add(title, category, details, impact_ms=0):
        if title in seen_titles:
            return
        seen_titles.add(title)
        fixes.append({
            "index": len(fixes) + 1,
            "title": title,
            "category": category,
            "details": details,
            "impact_ms": impact_ms,
        })

    # Fixes based on real PageSpeed opportunities
    if audit and audit.get("opportunities"):
        for opp in audit["opportunities"]:
            aid = opp.get("id")
            if aid in _OPP_TO_FIX:
                title, cat, det = _OPP_TO_FIX[aid]
                _add(title, cat, det, opp.get("savings_ms", 0))

    # Always-useful hygiene items
    for title, cat, det in _ALWAYS_SHOW:
        _add(title, cat, det, 0)

    return fixes
