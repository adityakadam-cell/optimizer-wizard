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
#  Manual fixes table — detailed step-by-step guidance
# ============================================================
#
# Each fix is a dict with the following keys:
#   title:    short label, shown in the collapsed accordion header
#   category: tag shown next to the title (e.g. "Server", "Cloudflare")
#   what:     1-2 sentence plain-English explanation of the change
#   why:      what problem it solves and why it matters for performance
#   how:      list of {env, steps} — concrete instructions per hosting environment
#   verify:   how to confirm the change actually took effect
#
# The `how` field always covers the most common environments your team is
# likely to encounter: Cloudflare, cPanel/shared hosting, WordPress, and
# Apache/Nginx. If the team uses something else (Vercel, Netlify, etc.) the
# Cloudflare/CDN advice usually translates 1:1.

_FIXES = {
    # -----------------------------------------------------------
    "uses-text-compression": {
        "title": "Enable Gzip or Brotli compression on your server",
        "category": "Server",
        "what": "Make your server send HTML, CSS, and JavaScript files as compressed (zipped) data instead of raw text. The browser unzips it on the other end automatically.",
        "why": "Compression typically cuts the size of text files by 70-80%. A 200 KB HTML page becomes ~50 KB on the wire. This is one of the highest-impact performance fixes — usually adds 5-15 PageSpeed points on its own with zero downsides.",
        "how": [
            {"env": "Cloudflare (in front of any host)",
             "steps": [
                 "Log in to your Cloudflare dashboard.",
                 "Pick your domain.",
                 "Go to Speed → Optimization → Content Optimization.",
                 "Make sure 'Brotli' is toggled ON (it usually is by default).",
                 "Cloudflare will compress responses automatically — no other change needed.",
             ]},
            {"env": "cPanel / shared hosting (GoDaddy, Bluehost, Hostinger, etc.)",
             "steps": [
                 "Log in to cPanel.",
                 "Find the 'Software' or 'Optimize Website' section.",
                 "Click 'Optimize Website' or 'Compress'.",
                 "Select 'Compress all content' → click Update Settings.",
                 "If you don't see the option, contact your host's support and ask them to enable mod_deflate or mod_brotli for your account.",
             ]},
            {"env": "WordPress (any host)",
             "steps": [
                 "Install a caching plugin like WP Rocket, W3 Total Cache, or LiteSpeed Cache (free).",
                 "In the plugin settings, find 'Gzip Compression' or 'Browser Cache' and enable it.",
                 "Save settings. The plugin writes the necessary .htaccess rules for you.",
             ]},
            {"env": "Apache (VPS / dedicated server)",
             "steps": [
                 "SSH into the server.",
                 "Run: sudo a2enmod deflate brotli",
                 "Edit /etc/apache2/conf-enabled/deflate.conf (or your site config) and add: AddOutputFilterByType DEFLATE text/html text/css application/javascript application/json image/svg+xml",
                 "Restart Apache: sudo systemctl restart apache2",
             ]},
            {"env": "Nginx (VPS / dedicated server)",
             "steps": [
                 "SSH into the server.",
                 "Edit /etc/nginx/nginx.conf — inside the http { } block add: gzip on; gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml; gzip_min_length 256;",
                 "For Brotli (better than gzip), install the brotli module first: sudo apt install nginx-module-brotli",
                 "Test config: sudo nginx -t",
                 "Reload: sudo systemctl reload nginx",
             ]},
        ],
        "verify": "Open https://www.giftofspeed.com/gzip-test/ — paste your URL — it shows whether gzip/brotli is active and how much it saved. Or in browser DevTools → Network tab → click any HTML/CSS/JS request → look for 'content-encoding: gzip' or 'br' in response headers.",
    },

    # -----------------------------------------------------------
    "uses-long-cache-ttl": {
        "title": "Set long Cache-Control headers on static assets",
        "category": "Server",
        "what": "Tell the browser to keep your images, CSS, and JS files in its local cache for a year, so returning visitors don't have to download them again.",
        "why": "Without this, the browser re-downloads your logo, stylesheets, and scripts on every visit. With it, repeat visits feel instant. PageSpeed flags assets cached for less than 30 days as a performance opportunity.",
        "how": [
            {"env": "Cloudflare",
             "steps": [
                 "Cloudflare dashboard → your domain → Caching → Configuration.",
                 "Set 'Browser Cache TTL' to '1 year' (or longest available).",
                 "Set 'Caching Level' to 'Standard'.",
                 "Save. Cloudflare will now tell browsers to cache your assets for a year.",
             ]},
            {"env": "cPanel / shared hosting",
             "steps": [
                 "cPanel → File Manager → public_html.",
                 "Show hidden files (Settings → Show Hidden Files).",
                 "Edit the file named .htaccess (create it if missing).",
                 "Add at the top: <IfModule mod_expires.c> ExpiresActive On ExpiresByType image/jpeg \"access plus 1 year\" ExpiresByType image/png \"access plus 1 year\" ExpiresByType image/webp \"access plus 1 year\" ExpiresByType text/css \"access plus 1 year\" ExpiresByType application/javascript \"access plus 1 year\" </IfModule>",
                 "Save the file. Changes take effect immediately.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Use the same caching plugin as above (WP Rocket / W3 Total Cache / LiteSpeed Cache).",
                 "Find the 'Browser Caching' or 'Cache' section.",
                 "Enable browser caching with 1 year expiry for images/CSS/JS.",
             ]},
            {"env": "Nginx",
             "steps": [
                 "Edit your site config in /etc/nginx/sites-available/yourdomain.",
                 "Inside the server block, add: location ~* \\.(jpg|jpeg|png|webp|gif|svg|ico|css|js|woff2?)$ { expires 1y; add_header Cache-Control \"public, immutable\"; }",
                 "Test: sudo nginx -t",
                 "Reload: sudo systemctl reload nginx",
             ]},
        ],
        "verify": "Open browser DevTools → Network tab → reload the page → click any image or CSS file → check the Response Headers for 'Cache-Control: public, max-age=31536000' or similar (31536000 seconds = 1 year).",
        "warning": "ONLY apply long cache to versioned/static files (e.g. style.v3.css, logo-2024.png). For HTML files use a short cache (1 hour or less) so updates show up immediately. If you long-cache an HTML file and then change it, returning visitors won't see your changes for a year.",
    },

    # -----------------------------------------------------------
    "server-response-time": {
        "title": "Reduce server response time (TTFB)",
        "category": "Server / Backend",
        "what": "TTFB ('time to first byte') is how long the browser waits after sending the request before getting the first byte of the response. Goal: under 600ms.",
        "why": "Slow TTFB means everything else is slow too — fonts, images, scripts can't even start loading until the HTML arrives. High TTFB is usually caused by slow database queries, no caching, or being on a far-away server.",
        "how": [
            {"env": "Cloudflare (works for any host)",
             "steps": [
                 "Cloudflare dashboard → Caching → Configuration → set 'Caching Level' to 'Standard'.",
                 "Speed → Optimization → enable 'Argo Smart Routing' if available (paid feature, ~$5/mo, often worth it).",
                 "Caching → Cache Rules → add a rule: 'For URLs matching example.com/* cache HTML for 2 hours'. This is huge for slow backends.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Install a page-caching plugin: WP Rocket (paid, easiest), LiteSpeed Cache (free, requires LiteSpeed server), or W3 Total Cache (free).",
                 "Enable page caching with 1-hour TTL minimum.",
                 "Enable database query caching if available.",
                 "Reduce WordPress plugins — every active plugin adds backend processing time. Aim for under 20 plugins.",
             ]},
            {"env": "Custom backend (Node, PHP, Python, Rails, etc.)",
             "steps": [
                 "Profile the slow endpoint — usually one or two database queries dominate.",
                 "Add database indexes for the columns you're querying or sorting on.",
                 "Add Redis or Memcached for caching expensive computations.",
                 "Use CDN edge caching (Cloudflare 'Cache Everything' page rule) for guest/anonymous traffic.",
             ]},
            {"env": "Shared hosting (last resort)",
             "steps": [
                 "If you've done all of the above and TTFB is still > 1 second, the host is the problem.",
                 "Move to a faster host: Hostinger, SiteGround, A2 Hosting, or any VPS provider (DigitalOcean, Vultr, Linode start at $5/mo).",
                 "A move from cheap shared hosting to a $5 VPS often cuts TTFB by 70%.",
             ]},
        ],
        "verify": "Run https://www.webpagetest.org/ — pick a server near your audience — submit your URL. The 'Time to First Byte' number should be under 600ms (good), under 800ms (acceptable), over 1000ms (problem).",
    },

    # -----------------------------------------------------------
    "uses-http2": {
        "title": "Serve assets over HTTP/2 or HTTP/3",
        "category": "Server",
        "what": "Switch from the older HTTP/1.1 protocol to HTTP/2 (or HTTP/3). Both are newer, faster ways for browsers to talk to your server.",
        "why": "HTTP/2 lets the browser download many files at once over a single connection. HTTP/1.1 is limited to ~6 parallel downloads, so pages with lots of images/CSS/JS files load slower. HTTP/3 is even faster on poor mobile connections.",
        "how": [
            {"env": "Cloudflare",
             "steps": [
                 "Cloudflare dashboard → Network.",
                 "Toggle ON: 'HTTP/2', 'HTTP/3 (with QUIC)', and '0-RTT Connection Resumption'.",
                 "These are usually on by default on free plans. Just verify they're green.",
                 "Save. That's it — Cloudflare handles the protocol negotiation with browsers.",
             ]},
            {"env": "cPanel / shared hosting",
             "steps": [
                 "Most modern shared hosts have HTTP/2 enabled automatically when SSL is on.",
                 "First make sure your site is HTTPS (cPanel → SSL/TLS Status → enable AutoSSL).",
                 "If still on HTTP/1.1 after enabling SSL, contact your host's support and ask them to enable HTTP/2 for your account.",
             ]},
            {"env": "Apache",
             "steps": [
                 "SSH in. Run: sudo a2enmod http2",
                 "Edit your SSL site config (e.g. /etc/apache2/sites-enabled/yourdomain-ssl.conf).",
                 "Inside the <VirtualHost *:443> block, add: Protocols h2 http/1.1",
                 "Restart: sudo systemctl restart apache2",
             ]},
            {"env": "Nginx",
             "steps": [
                 "Edit your SSL site config.",
                 "Find: listen 443 ssl;",
                 "Change to: listen 443 ssl http2;",
                 "For HTTP/3 (Nginx 1.25+): listen 443 quic reuseport; add_header Alt-Svc 'h3=\":443\"; ma=86400';",
                 "Test: sudo nginx -t. Reload: sudo systemctl reload nginx",
             ]},
        ],
        "verify": "Run https://tools.keycdn.com/http2-test — paste your URL. It will tell you which protocols your site supports.",
    },

    # -----------------------------------------------------------
    "render-blocking-resources": {
        "title": "Eliminate render-blocking CSS and JavaScript",
        "category": "Build pipeline",
        "what": "When the browser sees a <link rel=\"stylesheet\"> or <script src=\"...\"> in <head>, it stops rendering the page and waits for that file to finish downloading. Goal: don't make the browser wait.",
        "why": "Every blocking CSS/JS file adds to your 'first paint' time — the moment users see anything on the screen. PageSpeed measures this as Largest Contentful Paint (LCP). Common cause: a single huge CSS file in <head> blocks rendering for 800ms+.",
        "how": [
            {"env": "WordPress",
             "steps": [
                 "Install a plugin: Autoptimize (free) or WP Rocket (paid).",
                 "Autoptimize: Settings → Autoptimize → JS Options → 'Aggregate JS files' + 'Defer non-aggregated JS'. CSS Options → 'Inline and Defer CSS'.",
                 "WP Rocket: File Optimization tab → check 'Optimize CSS delivery' and 'Load JavaScript deferred'.",
                 "Test the site after — sometimes deferring breaks plugins that depend on jQuery on page load.",
             ]},
            {"env": "Static site / hand-coded HTML",
             "steps": [
                 "Inline 'critical CSS' (the styles needed for the above-the-fold content) directly in <style>...</style> in <head>.",
                 "Use the rest of your CSS as a deferred load: <link rel=\"preload\" href=\"styles.css\" as=\"style\" onload=\"this.rel='stylesheet'\">",
                 "For JS: add 'defer' or 'async' attribute to <script> tags. The wizard already does this for non-analytics scripts.",
                 "Tools to extract critical CSS automatically: critical (npm package), penthouse (npm package), Critters (Webpack plugin).",
             ]},
            {"env": "Modern build tools (Vite, Webpack, Next.js)",
             "steps": [
                 "Most modern frameworks handle this for you out of the box (code splitting + critical CSS).",
                 "Make sure you're running the production build (npm run build), not the dev server.",
                 "For Next.js: nothing to do, it inlines critical CSS automatically.",
                 "For Vite: enable the build.cssCodeSplit option (default).",
             ]},
        ],
        "verify": "Run a fresh PageSpeed test. The 'Eliminate render-blocking resources' opportunity should drop from 'X.Xs of savings' to under 0.3s, ideally disappearing from the report entirely.",
    },

    # -----------------------------------------------------------
    "unminified-css": {
        "title": "Minify your CSS files",
        "category": "Build pipeline",
        "what": "Remove whitespace, comments, and unused characters from your CSS files so they download faster.",
        "why": "Minification typically shrinks CSS by 20-40%. Combined with gzip/brotli compression, the on-the-wire saving is even bigger. Easy win.",
        "how": [
            {"env": "WordPress",
             "steps": [
                 "Use Autoptimize, WP Rocket, or LiteSpeed Cache.",
                 "In plugin settings, enable 'Minify CSS'.",
                 "Save. Plugins handle the rest, including cache invalidation when you update.",
             ]},
            {"env": "Build tools (Webpack, Vite, Gulp)",
             "steps": [
                 "Webpack: use css-minimizer-webpack-plugin in production mode.",
                 "Vite: minification is automatic in production build (npm run build).",
                 "Gulp: pipe through gulp-clean-css.",
                 "Most build tools minify automatically — just make sure you're shipping the production build, not the development one.",
             ]},
            {"env": "Hand-coded HTML/CSS",
             "steps": [
                 "Use an online tool: https://cssminifier.com/ — paste your CSS, get minified output.",
                 "Save the result as e.g. style.min.css and reference that in your HTML instead.",
                 "For automation: install a CSS minifier (clean-css-cli on npm) and run it as part of your deploy script.",
             ]},
        ],
        "verify": "View source of your CSS file. If it has no line breaks and looks like one giant line of code, it's minified.",
    },

    # -----------------------------------------------------------
    "unminified-javascript": {
        "title": "Minify your JavaScript files",
        "category": "Build pipeline",
        "what": "Same idea as CSS minification — remove whitespace, comments, and shorten variable names so JS files are smaller.",
        "why": "Modern minifiers (Terser, esbuild) shrink JS by 30-60%. JavaScript files are usually larger than CSS, so the absolute savings are bigger.",
        "how": [
            {"env": "WordPress",
             "steps": [
                 "Same plugins as for CSS minification — Autoptimize, WP Rocket, LiteSpeed Cache.",
                 "Enable 'Minify JavaScript' in the plugin settings.",
                 "Test the site afterwards — minification occasionally breaks JS that uses string-named variables. If something breaks, exclude that script in the plugin's settings.",
             ]},
            {"env": "Build tools",
             "steps": [
                 "Webpack: TerserPlugin runs by default in production mode.",
                 "Vite/Rollup: esbuild minification is automatic in production build.",
                 "Make sure you're not committing the unminified development bundle by mistake — check your dist/ or build/ folder.",
             ]},
            {"env": "Hand-coded JS",
             "steps": [
                 "Use https://terser.org/ online or install: npm install -g terser",
                 "Run: terser script.js -c -m -o script.min.js",
                 "Reference script.min.js in your HTML.",
             ]},
        ],
        "verify": "Check that your production JS files have no formatting and use single-letter variable names like 'a', 'b', 'c'.",
    },

    # -----------------------------------------------------------
    "unused-css-rules": {
        "title": "Remove unused CSS",
        "category": "Build pipeline",
        "what": "Strip CSS rules that aren't actually used on the page. Bootstrap and similar frameworks ship 200+ KB of CSS but a typical page uses 10-20 KB of it.",
        "why": "Unused CSS is downloaded, parsed, and held in memory for nothing. Removing it can save 50-150 KB on framework-heavy sites.",
        "how": [
            {"env": "Tailwind CSS",
             "steps": [
                 "Tailwind handles this automatically — its 'purge' (now 'content' option) removes unused classes during the production build.",
                 "Make sure your tailwind.config.js has the 'content' option pointing to all template files: content: ['./src/**/*.{html,js}']",
                 "Run: npm run build (or your prod build command). The output CSS will only contain classes you actually used.",
             ]},
            {"env": "Bootstrap or other framework",
             "steps": [
                 "Use PurgeCSS as a Webpack/Gulp plugin or as a standalone CLI.",
                 "Standalone: npm install -g purgecss",
                 "Run: purgecss --css bootstrap.css --content '**/*.html' --output ./dist/",
                 "PurgeCSS scans your HTML and only keeps the CSS rules that are actually used.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "WP Rocket has a 'Remove Unused CSS' feature (RUCSS) — enable it under File Optimization.",
                 "Or use a dedicated plugin: 'Asset CleanUp' lets you disable unused CSS files per page.",
             ]},
        ],
        "verify": "Re-run PageSpeed — the 'Reduce unused CSS' savings number should drop significantly.",
    },

    # -----------------------------------------------------------
    "unused-javascript": {
        "title": "Remove unused JavaScript",
        "category": "Build pipeline",
        "what": "Don't ship JavaScript code that the current page doesn't actually use. Common cause: importing the whole of a library when you only use one function.",
        "why": "JavaScript is the most expensive resource — every byte gets downloaded, parsed, AND executed. Removing 100 KB of unused JS often saves 200-500ms on mobile devices.",
        "how": [
            {"env": "Modern frameworks (React, Vue, Next.js)",
             "steps": [
                 "Use code splitting: split bundles by route so each page only loads its own JS.",
                 "React: use React.lazy() and <Suspense> to lazy-load components.",
                 "Next.js: it automatically code-splits per page. Check your bundle analyzer (next build --analyze).",
                 "Replace bulk imports with targeted ones: 'import debounce from \"lodash/debounce\"' instead of 'import _ from \"lodash\"'.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Use 'Asset CleanUp' or 'Perfmatters' to disable scripts on pages that don't need them.",
                 "Example: only load Contact Form 7's JS on the contact page, not site-wide.",
                 "Audit your plugins — every plugin tends to add JS site-wide even if it's only used on one page.",
             ]},
            {"env": "Hand-coded JS",
             "steps": [
                 "Audit each <script> tag — does this page actually need it?",
                 "Move analytics, chat widgets, and other 'nice to have' scripts to load AFTER the page is interactive (use 'defer' attribute or load via JS after window.onload).",
             ]},
        ],
        "verify": "Re-run PageSpeed — the 'Reduce unused JavaScript' savings should drop. Also check Coverage in Chrome DevTools (Cmd+Shift+P → 'Show Coverage') to see real unused-JS percentages.",
    },

    # -----------------------------------------------------------
    "modern-image-formats": {
        "title": "Convert images to WebP or AVIF format",
        "category": "Assets",
        "what": "Replace .jpg and .png images with .webp (or .avif) versions. They look identical but are 25-50% smaller.",
        "why": "Images are usually the biggest part of a page's weight. WebP cuts image size by ~30% on average, AVIF by ~50%. The wizard already inserted <picture> tags pointing to .webp files — but you still need to actually create those files.",
        "how": [
            {"env": "Cloudflare (easiest — automatic)",
             "steps": [
                 "Cloudflare dashboard → Speed → Optimization → Image Resizing.",
                 "Enable 'Polish' (free on Pro plan, $20/mo) or 'Image Resizing' (Business plan).",
                 "Cloudflare auto-converts JPEGs/PNGs to WebP for browsers that support it. No file changes on your end.",
                 "Free alternative: Cloudflare 'Mirage' for mobile.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Install ShortPixel, Imagify, or EWWW Image Optimizer plugin.",
                 "Activate WebP conversion in plugin settings.",
                 "Plugin will bulk-convert your existing media library AND auto-convert future uploads.",
                 "Most plugins also add the rewrite rules so .webp is served when supported, .jpg as fallback.",
             ]},
            {"env": "Manual conversion (any host)",
             "steps": [
                 "Use Squoosh.app — drag your image, choose WebP, download.",
                 "Or install cwebp CLI: brew install webp (Mac) / apt install webp (Linux).",
                 "Run: cwebp -q 85 input.jpg -o input.webp",
                 "Upload the .webp files alongside the originals.",
                 "Update your HTML: <picture><source srcset='image.webp' type='image/webp'><img src='image.jpg'></picture> — the wizard already did this for you.",
             ]},
        ],
        "verify": "View an image URL in browser — should serve .webp on Chrome/Edge/Firefox. Or run PageSpeed again — the 'Serve images in next-gen formats' opportunity should be gone.",
    },

    # -----------------------------------------------------------
    "uses-optimized-images": {
        "title": "Compress images to reduce file size",
        "category": "Assets",
        "what": "Reduce image file sizes (separate from format conversion) by re-encoding at appropriate quality levels. A 4 MB photo can usually become 400 KB without visible quality loss.",
        "why": "Many sites have unoptimized images straight from a phone or stock photo download. A single 4 MB hero image alone can tank your mobile score.",
        "how": [
            {"env": "Online tools (no install)",
             "steps": [
                 "TinyPNG (https://tinypng.com/) — handles PNG and JPEG, free for files under 5 MB.",
                 "Squoosh (https://squoosh.app/) — Google's tool, more control over quality settings.",
                 "ImageOptim Web (https://imageoptim.com/online) — drag and drop.",
                 "Upload your images, download the optimized versions, replace on your server.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Same plugins as WebP — ShortPixel, Imagify, EWWW Image Optimizer.",
                 "These compress AND convert in one go. Set quality to 'Glossy' or 'Lossy' (both look fine, smaller files).",
             ]},
            {"env": "Manual / build pipeline",
             "steps": [
                 "Re-export from your source: in Photoshop use 'Save for Web' → JPEG quality 75-85. In Figma use 'Export → JPG quality 80'.",
                 "Command line: install ImageMagick. Run: mogrify -quality 80 -resize 1920x1920\\> *.jpg",
                 "For automation: integrate with your build pipeline (imagemin in Webpack, gatsby-plugin-image, next/image, etc.).",
             ]},
        ],
        "verify": "Each image on your page should be under 200 KB (200,000 bytes). Hero images can be up to 500 KB. If any image is over 1 MB, that's the first thing to fix.",
    },

    # -----------------------------------------------------------
    "uses-responsive-images": {
        "title": "Serve appropriately-sized images for each device",
        "category": "HTML + Assets",
        "what": "Don't serve a 2000-pixel-wide image to a phone with a 400-pixel-wide screen. Generate multiple sizes and let the browser pick.",
        "why": "Mobile users on a 4G connection downloading a desktop-sized image waste 5-10x the bandwidth they need to. Cuts mobile LCP dramatically.",
        "how": [
            {"env": "Cloudflare",
             "steps": [
                 "Cloudflare's 'Image Resizing' (Business plan) or 'Polish' (Pro plan) does this automatically — pass an image URL with ?width=400 and Cloudflare resizes on the fly.",
                 "Free alternative: use Cloudinary or imgix CDNs (~free tier covers small sites).",
             ]},
            {"env": "WordPress",
             "steps": [
                 "WordPress core already generates multiple image sizes when you upload (thumbnail, medium, large).",
                 "Make sure your theme uses wp_get_attachment_image() or the_post_thumbnail() — they output proper srcset automatically.",
                 "If using a page builder (Elementor, Divi) check that 'srcset' or 'responsive images' is enabled in its settings.",
             ]},
            {"env": "Hand-coded HTML",
             "steps": [
                 "Generate 3 sizes per image: e.g. hero-480.jpg, hero-960.jpg, hero-1920.jpg.",
                 "Use srcset: <img src='hero-960.jpg' srcset='hero-480.jpg 480w, hero-960.jpg 960w, hero-1920.jpg 1920w' sizes='(max-width: 600px) 480px, (max-width: 1200px) 960px, 1920px' alt='...'>",
                 "Tools to generate sizes in bulk: ImageMagick (mogrify -resize 480 *.jpg), or a build plugin.",
             ]},
        ],
        "verify": "Open the page on a phone or use Chrome DevTools mobile emulator → Network tab → check that the image downloaded is the small variant, not the full-size one.",
    },

    # -----------------------------------------------------------
    "font-display": {
        "title": "Add font-display: swap to your @font-face rules",
        "category": "CSS",
        "what": "Tell the browser: while my custom font is loading, show the text in a fallback font instead of leaving it invisible.",
        "why": "Without this, custom fonts cause 'invisible text' (FOIT — Flash of Invisible Text) for up to 3 seconds while the font downloads. With font-display: swap, text is immediately visible in the fallback font, then swaps to your custom font when ready.",
        "how": [
            {"env": "Google Fonts (most common)",
             "steps": [
                 "Open the Google Fonts URL you use, e.g. https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap",
                 "Make sure '&display=swap' is at the end. If missing, add it.",
                 "Save and re-upload your HTML.",
             ]},
            {"env": "Self-hosted fonts (any platform)",
             "steps": [
                 "Find your CSS file with @font-face declarations.",
                 "Inside each @font-face { ... } block, add: font-display: swap;",
                 "Example: @font-face { font-family: 'MyFont'; src: url('myfont.woff2') format('woff2'); font-display: swap; }",
                 "Save and re-upload.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "If your theme uses Google Fonts, look for the function that enqueues them — usually in functions.php or a customizer setting.",
                 "Plugins like 'OMGF' (Optimize My Google Fonts) automatically add font-display: swap and self-host fonts for better caching.",
             ]},
        ],
        "verify": "Reload the page with cache disabled (DevTools → Network → 'Disable cache' → Cmd+R). You should see text appear immediately in a system font, then swap to your custom font when it loads. If text is invisible for a moment, font-display isn't set.",
    },

    # -----------------------------------------------------------
    "third-party-summary": {
        "title": "Reduce impact of third-party scripts",
        "category": "Marketing / Analytics",
        "what": "Audit your analytics, chat widgets, ad pixels, A/B testing tools, and similar third-party scripts. Remove what you don't strictly need.",
        "why": "Third-party scripts often add 500ms-2 seconds to page load. They run on someone else's server, so you can't make them faster — only choose to load fewer of them.",
        "how": [
            {"env": "Audit checklist",
             "steps": [
                 "List every <script> tag with a src= pointing to a third-party domain.",
                 "For each one, ask: does the business actually use the data this collects? When was it last reviewed?",
                 "Common bloat: old A/B test tools no longer in use, multiple analytics scripts (GA + GTM + Heap + Mixpanel), legacy chat widgets, abandoned marketing pixels.",
                 "Remove any script your team can't justify in one sentence.",
             ]},
            {"env": "Google Tag Manager",
             "steps": [
                 "Use GTM as a single entry point — load only GTM, then put all other tags inside it.",
                 "In GTM, set non-critical tags (Pixel, conversion tracking) to fire on 'Window Loaded' instead of 'Page View'.",
                 "Audit GTM regularly — old paused tags still count against your tag limit and slow the container load.",
             ]},
            {"env": "Defer everything possible",
             "steps": [
                 "Chat widgets (Intercom, Drift, Crisp): use their async snippet AND set it to load after window.onload.",
                 "Many chat widgets have a 'lite' or 'lazy' loading mode in their settings — turn it on.",
                 "Marketing pixels: load via GTM, fire on 'DOM Ready' or later, not 'Page View'.",
             ]},
        ],
        "verify": "Re-run PageSpeed — the 'Reduce impact of third-party code' opportunity should show smaller savings. In Chrome DevTools → Lighthouse → run a fresh audit and look at the 'Third-party usage' breakdown.",
    },

    # -----------------------------------------------------------
    "bootup-time": {
        "title": "Reduce JavaScript execution time",
        "category": "JS",
        "what": "Reduce the amount of JavaScript the browser has to parse, compile, and execute when the page loads.",
        "why": "Mobile devices are 3-5x slower at JavaScript than desktops. Heavy JS bundles cause the page to feel frozen during load — buttons don't respond, scrolling stutters. Goal: keep main-thread JS work under 2 seconds.",
        "how": [
            {"env": "Strategies",
             "steps": [
                 "Code splitting: ship one JS bundle per route, not one giant bundle for the whole site. Most modern frameworks do this automatically.",
                 "Lazy load below-the-fold features: e.g. don't load the comments-section JS until the user scrolls near it.",
                 "Move heavy work off the main thread: use Web Workers for image processing, sorting, or large data transformations.",
                 "Use lighter alternatives: if you only need 1 jQuery feature, write 5 lines of vanilla JS instead of loading 90 KB of jQuery.",
             ]},
            {"env": "WordPress",
             "steps": [
                 "Reduce active plugin count. Each plugin usually adds JS site-wide.",
                 "Use 'Asset CleanUp' or 'Perfmatters' to disable plugin JS on pages that don't need it.",
                 "Switch to lightweight themes (GeneratePress, Astra, Kadence) instead of multipurpose themes (Avada, Divi, BeTheme) which ship 200+ KB of JS.",
             ]},
        ],
        "verify": "PageSpeed report → look at 'Total Blocking Time' (TBT). Goal: under 200ms. If it's over 600ms, JavaScript is the issue.",
    },

    # -----------------------------------------------------------
    "dom-size": {
        "title": "Reduce the number of DOM elements on the page",
        "category": "HTML",
        "what": "Cut down the number of HTML elements (divs, spans, etc.) on the page. PageSpeed warns when there are more than 1500 elements.",
        "why": "Every DOM element costs memory and rendering time. Long product listings, infinite scrolls, or builder-generated pages often have thousands of unnecessary elements.",
        "how": [
            {"env": "Strategies",
             "steps": [
                 "Paginate long lists: instead of one page with 200 products, show 24 per page with 'Load more' button.",
                 "Use virtualization for very long lists: only render the rows the user can see (libraries: react-window, react-virtuoso, vue-virtual-scroller).",
                 "Audit page-builder output: Elementor / Divi / WPBakery often produce 5-10 nested divs per element. Switch to a lighter builder (GenerateBlocks, Bricks Builder) or hand-code key pages.",
                 "Remove hidden but rendered content. e.g. modal markup that's hidden but still in DOM. Lazy-mount it on demand.",
             ]},
        ],
        "verify": "Chrome DevTools → Console → run: document.querySelectorAll('*').length — should be under 1500 for a typical page, under 800 for fast mobile.",
    },

    # -----------------------------------------------------------
    # Always-show hygiene items (added even if PageSpeed didn't flag them)
    # -----------------------------------------------------------
    "_always_cdn": {
        "title": "Put a CDN in front of your site",
        "category": "Infrastructure",
        "what": "A CDN (Content Delivery Network) caches your site at servers all over the world. A user in Mumbai gets content from a Mumbai server, not your origin in Virginia.",
        "why": "Cuts latency dramatically. The free Cloudflare plan alone typically adds 10-20 PageSpeed points and protects against traffic spikes/DDoS. There is genuinely no downside for most sites.",
        "how": [
            {"env": "Cloudflare (recommended for most)",
             "steps": [
                 "Sign up free at https://dash.cloudflare.com/sign-up",
                 "Add your domain. Cloudflare will scan your DNS records.",
                 "Cloudflare gives you 2 nameservers (e.g. lina.ns.cloudflare.com, walt.ns.cloudflare.com).",
                 "Go to your domain registrar (GoDaddy, Namecheap, Google Domains, wherever you bought your domain).",
                 "Replace your existing nameservers with the Cloudflare ones. Save.",
                 "Within 24 hours (usually ~1 hour), Cloudflare is active. Visible in your dashboard as 'Active'.",
                 "In Cloudflare → SSL/TLS → set to 'Full (strict)' if your origin has SSL, otherwise 'Flexible'.",
                 "Free plan covers most needs. Pro ($20/mo) adds image optimization (Polish).",
             ]},
            {"env": "Alternatives if Cloudflare doesn't fit",
             "steps": [
                 "Bunny.net — pay-as-you-go (~$0.01/GB), faster than Cloudflare in some regions, no free tier.",
                 "Fastly — premium option, best for large sites with complex caching needs.",
                 "AWS CloudFront — for AWS-hosted sites; integrated billing.",
             ]},
        ],
        "verify": "Once active, run https://www.cdnplanet.com/tools/cdnfinder/ — paste your URL — it tells you which CDN is in front of your site. Or run a fresh PageSpeed test — TTFB should drop noticeably.",
    },
}


# Always-show hygiene fix IDs (shown even if PageSpeed didn't flag them)
_ALWAYS_SHOW_IDS = ["uses-text-compression", "_always_cdn", "uses-long-cache-ttl"]


def get_manual_fixes(audit, applied_changes: list[str]) -> list[dict]:
    """
    Build the list of manual fixes the wizard could NOT apply automatically.

    Each fix dict contains:
      index, title, category, impact_ms — for the collapsed accordion header
      what, why, how, verify, [warning] — for the expanded detail panel

    Backwards-compat:
      details — short summary string, kept so any old templates still render

    The function is dedup'd on `title` so the same fix doesn't appear twice
    if it was both flagged by Lighthouse and on the always-show list.
    """
    fixes: list[dict] = []
    seen_titles: set[str] = set()

    def _add(fix_id: str, impact_ms: int = 0):
        fix = _FIXES.get(fix_id)
        if not fix:
            return
        if fix["title"] in seen_titles:
            return
        seen_titles.add(fix["title"])
        # Build a short "details" summary for backwards compatibility
        # (older templates that don't know about how/why/verify)
        short = fix.get("what", "").split(". ")[0][:200]
        fixes.append({
            "index": len(fixes) + 1,
            "title": fix["title"],
            "category": fix["category"],
            "what": fix.get("what", ""),
            "why": fix.get("why", ""),
            "how": fix.get("how", []),
            "verify": fix.get("verify", ""),
            "warning": fix.get("warning"),  # may be None
            "details": short,  # legacy field
            "impact_ms": impact_ms,
        })

    # 1. Fixes derived from real PageSpeed opportunities
    if audit and audit.get("opportunities"):
        for opp in audit["opportunities"]:
            aid = opp.get("id")
            if aid in _FIXES:
                _add(aid, opp.get("savings_ms", 0))

    # 2. Always-show hygiene items (Gzip, CDN, Cache-Control)
    for fid in _ALWAYS_SHOW_IDS:
        _add(fid, 0)

    return fixes
