"""Security headers: strict CSP, no software banner, extra hardening headers."""

import json
import re
from pathlib import Path

from ah_dashboard.headers import build_csp

STATIC = Path(__file__).resolve().parent.parent / "ah_dashboard"


def directives(resp):
    csp = resp.headers["Content-Security-Policy"]
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in csp.split(";")) if d}


def test_csp_is_strict(client):
    d = directives(client.get("/"))
    assert d["default-src"] == ["'none'"]
    assert d["script-src"] == ["'self'"]
    assert d["style-src"] == ["'self'"]
    assert d["img-src"] == ["'self'", "data:", "https://static.ah.nl"]
    assert d["frame-ancestors"] == ["'none'"] and d["base-uri"] == ["'none'"]
    assert d["font-src"] == ["'self'"]            # only the app's own font files


def test_self_hosted_fonts_are_served(client):
    for name in ("RedHatDisplay-latin.woff2", "RedHatText-latin.woff2"):
        resp = client.get(f"/static/fonts/{name}")
        assert resp.status_code == 200 and resp.data[:4] == b"wOF2"
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_csp_on_every_response(client):
    for path in ("/static/css/app.css", "/api/session", "/does-not-exist"):
        assert "default-src 'none'" in client.get(path).headers["Content-Security-Policy"]


def test_pages_are_never_cached_but_static_files_may_be(client):
    for path in ("/", "/privacy", "/voorwaarden", "/does-not-exist"):
        resp = client.get(path)
        assert resp.mimetype == "text/html", path
        assert resp.headers["Cache-Control"] == "no-store", path
    assert client.get("/static/css/app.css").headers.get("Cache-Control") != "no-store"


def test_permissions_policy_has_no_deprecated_features(client):
    policy = client.get("/").headers["Permissions-Policy"]
    assert "browsing-topics" not in policy and "interest-cohort" not in policy


def test_csp_image_hosts_must_be_https():
    csp = build_csp("https://img.example http://evil.example javascript:x")
    assert "https://img.example" in csp and "http://evil" not in csp and "javascript:" not in csp


def test_page_gets_the_csp_image_hosts(app):
    from conftest import configure
    configure(app, CSP_IMAGE_HOSTS="https://static.ah.nl http://evil.example https://*.img.example")
    resp = app.test_client().get("/")
    m = re.search(r'id="app-config">(.*?)</script>', resp.get_data(as_text=True), re.S)
    assert json.loads(m.group(1))["imageHosts"] == ["https://static.ah.nl", "https://*.img.example"]
    # The CSP is built from the same list.
    assert "img-src 'self' data: https://static.ah.nl https://*.img.example;" in build_csp(
        "https://static.ah.nl http://evil.example https://*.img.example")


def test_no_software_banner_and_hardening_headers(client):
    h = client.get("/").headers
    assert "Server" not in h and "X-Powered-By" not in h
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"
    assert "camera=()" in h["Permissions-Policy"] and "geolocation=()" in h["Permissions-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"


def test_no_inline_styles_or_scripts_in_templates_and_js():
    """The CSP blocks inline style attributes and inline scripts; make sure
    none are (re)introduced."""
    for path in list((STATIC / "templates").rglob("*.html")) + list((STATIC / "static" / "js").rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        # HTML attribute syntax (space, name, ="...), as in templates and html`` strings.
        assert not re.search(r"\sstyle=[\"']", text), f"inline style attribute in {path.name}"
        assert not re.search(r"\son[a-z]+=[\"']", text), f"inline event handler in {path.name}"
    for path in (STATIC / "templates").rglob("*.html"):
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", path.read_text(encoding="utf-8"), re.S):
            attrs, body = m.groups()
            assert "src=" in attrs or "application/json" in attrs or not body.strip(), \
                f"inline script in {path.name}"


def test_favicon_is_linked_and_served(client):
    page = client.get("/").get_data(as_text=True)
    for href in ("icons/favicon.svg", "icons/favicon-32.png", "icons/apple-touch-icon.png"):
        assert href in page, href
        assert client.get("/static/" + href).status_code == 200, href
    ico = client.get("/favicon.ico")
    assert ico.status_code == 200 and ico.mimetype == "image/png"
    assert ico.data.startswith(b"\x89PNG")
