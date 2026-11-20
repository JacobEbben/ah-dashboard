"""Colour theme: light by default, dark only when chosen; never automatic."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "ah_dashboard"


def test_the_system_setting_never_switches_the_theme():
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "prefers-color-scheme" not in css                 # no automatic dark mode
    assert ':root[data-theme="dark"]' in css                 # dark exists, when chosen
    for path in (ROOT / "static" / "js").rglob("*.js"):
        assert "matchMedia" not in path.read_text(encoding="utf-8"), path


def test_theme_menu_offers_light_and_dark_only(client):
    page = client.get("/").get_data(as_text=True)
    menu = page[page.index('<select id="theme"'):]
    menu = menu[:menu.index("</select>")]
    assert 'value="light"' in menu and 'value="dark"' in menu and 'value="auto"' not in menu
    assert menu.index('value="light"') < menu.index('value="dark"')   # light first: the default


def test_early_script_applies_only_a_chosen_dark_theme():
    init = (ROOT / "static" / "js" / "theme-init.js").read_text(encoding="utf-8")
    assert "localStorage.getItem('ah_theme') === 'dark'" in init
    assert "setAttribute('data-theme', 'dark')" in init and "matchMedia" not in init
