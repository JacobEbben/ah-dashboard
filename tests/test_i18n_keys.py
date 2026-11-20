"""Interface texts (static/js/core/i18n.js) match their use, both ways:

- every key the page asks for exists (else the raw key shows, e.g. "likedOnlyPill");
- every key is used somewhere (else it is a leftover of a removed feature).

A key counts as used when it appears as a quoted string in the browser code or
the templates' data-i18n* / data-title-key attributes, or as a quoted string in
the Python package (API error codes: the page translates ``err.code``).
"""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "ah_dashboard"
I18N = PKG / "static/js/core/i18n.js"
# Keys built from a prefix at run time: 'per_' + unit (core/unit.js).
DYNAMIC_PREFIXES = ("per_",)
_ATTR = re.compile(r'data-(?:i18n(?:-[a-z]+)?|title-key)="([^"{]+)"')


def _keys(lang: str) -> set[str]:
    text = I18N.read_text(encoding="utf-8")
    start = text.index(f"\n  {lang}: {{") + len(f"\n  {lang}: {{")
    end = text.index("\n  },", start)
    block = text[start:end]
    return set(re.findall(r"(?:^|[{,]|\n)\s*([A-Za-z_][A-Za-z0-9_]*):\s", block))


def _sources() -> tuple[str, str, str]:
    js = "\n".join(p.read_text(encoding="utf-8") for p in (PKG / "static/js").rglob("*.js") if p != I18N)
    templates = "\n".join(p.read_text(encoding="utf-8") for p in (PKG / "templates").rglob("*.html"))
    python = "\n".join(p.read_text(encoding="utf-8") for p in PKG.rglob("*.py"))
    return js, templates, python


def test_every_requested_text_exists():
    js, templates, _ = _sources()
    keys = _keys("en")
    asked = set(re.findall(r"\bt\('([A-Za-z_][A-Za-z0-9_]*)'[,)]", js))
    asked |= set(re.findall(r"dataset\.i18n[A-Za-z]* = '([A-Za-z_][A-Za-z0-9_]*)'", js))
    asked |= set(_ATTR.findall(templates))
    assert sorted(asked - keys) == []


def test_every_text_is_used():
    js, templates, python = _sources()
    attrs = set(_ATTR.findall(templates))
    unused = sorted(
        k for k in _keys("en")
        if k not in attrs
        and not k.startswith(DYNAMIC_PREFIXES)
        and not re.search(rf"""['"`]{k}['"`]""", js)
        and not re.search(rf"""['"]{k}['"]""", python)
    )
    assert unused == []
