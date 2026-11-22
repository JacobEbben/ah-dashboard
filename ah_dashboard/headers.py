"""HTTP security headers.

Strict Content-Security-Policy: everything is denied by default and only what
the app uses is allowed. Scripts and styles come from our own origin only (no
inline scripts, no inline styles); store colours are CSS classes. Product
images come from AH's image CDN (CSP_IMAGE_HOSTS); "data:" is for the small
SVG icons inside app.css.
"""

from __future__ import annotations

from flask import Flask, request


def build_csp(image_hosts: str, form_targets: str = "") -> str:
    """``form_targets``: the login service's origin. Signing out is a POST that redirects
    there, and browsers apply form-action to that redirect."""
    hosts = " ".join(h for h in str(image_hosts or "").split() if h.startswith("https://"))
    return "; ".join([
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        f"img-src 'self' data: {hosts}".strip(),
        "connect-src 'self'",
        "font-src 'self'",          # self-hosted woff2 files (static/fonts)
        "manifest-src 'self'",
        "base-uri 'none'",
        f"form-action 'self' {form_targets}".strip(),
        "frame-ancestors 'none'",
    ])


# Browser features the app never uses. (No "browsing-topics": the Topics API
# is deprecated, and header analysers flag it.)
PERMISSIONS_POLICY = ", ".join(f"{f}=()" for f in (
    "accelerometer", "autoplay", "camera", "display-capture", "encrypted-media", "fullscreen",
    "geolocation", "gyroscope", "magnetometer", "microphone", "midi", "payment",
    "picture-in-picture", "publickey-credentials-get", "screen-wake-lock", "usb",
    "xr-spatial-tracking",
))


def register(app: Flask, *, image_hosts: str, hsts: bool, form_targets=lambda: "") -> None:
    """``form_targets()``: the login service's origin (it can change in the admin panel)."""
    """Add the security headers to every response. ``hsts``: served via HTTPS
    (ADMIN_COOKIE_SECURE)."""


    @app.after_request
    def _headers(resp):
        h = resp.headers
        h.setdefault("Content-Security-Policy", build_csp(image_hosts, form_targets()))
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        h.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if hsts:
            # Only when served via HTTPS (ADMIN_COOKIE_SECURE): browsers then
            # refuse plain HTTP for this host for a year.
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.path.startswith("/api/"):
            # Responses can depend on the caller's session: never cache them.
            h["Cache-Control"] = "no-store"
        elif resp.mimetype == "text/html" and "Cache-Control" not in h:
            # Pages carry the visitor's session (CSRF token and account in
            # app-config): never store them in any cache either.
            h["Cache-Control"] = "no-store"
        # Don't advertise the software (Waitress' own header is turned off in serve.py).
        h.pop("Server", None)
        h.pop("X-Powered-By", None)
        return resp
