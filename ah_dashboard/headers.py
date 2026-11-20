"""HTTP security headers.

Strict Content-Security-Policy: everything is denied by default and only what
the app uses is allowed. Scripts and styles come from our own origin only (no
inline scripts, no inline styles); store colours are CSS classes. Product
images come from AH's image CDN (CSP_IMAGE_HOSTS); "data:" is for the small
SVG icons inside app.css.
"""

from __future__ import annotations

from flask import Flask, request


def build_csp(image_hosts: str) -> str:
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
        "form-action 'self'",
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


def register(app: Flask, *, image_hosts: str, hsts: bool) -> None:
    """Add the security headers to every response. ``hsts``: served via HTTPS
    (ADMIN_COOKIE_SECURE)."""
    csp = build_csp(image_hosts)

    @app.after_request
    def _headers(resp):
        h = resp.headers
        h.setdefault("Content-Security-Policy", csp)
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
