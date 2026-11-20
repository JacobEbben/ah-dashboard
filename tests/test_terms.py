"""The terms of use page ("Voorwaarden", /voorwaarden)."""

import pathlib

from conftest import configure

from ah_dashboard.web.terms import TERMS_UPDATED


def text_of(resp) -> str:
    """The page with whitespace collapsed, as a reader sees it (the template
    wraps sentences over several lines)."""
    return " ".join(resp.get_data(as_text=True).split())


def test_footer_links_to_the_terms_on_every_page(client):
    for path in ("/", "/login?error=googleFailed", "/privacy", "/voorwaarden"):
        page = client.get(path).get_data(as_text=True)
        assert 'href="/voorwaarden"' in page and 'href="/privacy"' in page, path


def test_terms_page_in_both_languages(client):
    resp = client.get("/voorwaarden")
    page = text_of(resp)
    assert resp.status_code == 200
    assert "<h1>Voorwaarden</h1>" in page and "<h1>Terms of use</h1>" in page
    assert "Set-Cookie" not in resp.headers                      # reading it sets no cookie
    # The points it has to make, in both languages.
    for text in ("niet verbonden aan en niet goedgekeurd door Albert Heijn",
                 "De prijs in de winkel of op ah.nl is altijd leidend",
                 "Geen garantie op beschikbaarheid", "ingrediënten, allergenen",
                 "Nederlands recht",
                 "not owned by, affiliated with or endorsed by Albert Heijn",
                 "The price in the store or on ah.nl always applies",
                 "No guarantee of availability", "Dutch law applies"):
        assert text in page, text
    assert f'datetime="{TERMS_UPDATED.isoformat()}"' in page    # the version date is shown
    assert 'href="/privacy"' in page                             # refers to the privacy statement


def test_terms_show_the_contact_when_set(app, client):
    assert "Questions about these terms can go" in client.get("/voorwaarden").get_data(as_text=True)
    configure(app, PRIVACY_CONTACT="info@example.org")
    assert "info@example.org" in client.get("/voorwaarden").get_data(as_text=True)


def test_english_address_redirects(client):
    resp = client.get("/terms")
    assert resp.status_code == 301 and resp.headers["Location"].endswith("/voorwaarden")


def test_privacy_statement_links_to_the_terms(client):
    page = client.get("/privacy").get_data(as_text=True)
    assert page.count('href="/voorwaarden"') >= 3             # footer + NL + EN statement


def test_terms_cover_trademarks_objections_licence_and_court(client):
    page = text_of(client.get("/voorwaarden"))
    for text in ("geen commerciële relatie met", "geen officiële logo's of huisstijl",
                 "voedselverspilling", "MIT-licentie", "SIL Open Font License",
                 "Bezwaar als rechthebbende?", "bevoegde rechter in Nederland",
                 "no commercial relationship", "no official Albert Heijn logos or house style",
                 "food waste", "MIT licence", "Objection as a rights holder?", "competent court in the Netherlands"):
        assert text in page, text
    # The objection route points to the contact section in the same language.
    assert 'href="#contact-nl"' in page and 'id="contact-nl"' in page
    assert 'href="#contact-en"' in page and 'id="contact-en"' in page


def test_licence_files_ship_with_the_code_and_the_fonts():
    """The terms promise an MIT licence; the bundled fonts may only be
    redistributed (repository, image) together with their SIL OFL text."""
    root = pathlib.Path(__file__).resolve().parent.parent
    licence = (root / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "AH Dashboard contributors" in licence
    fonts = root / "ah_dashboard" / "static" / "fonts"
    assert list(fonts.glob("*.woff2")), "no fonts?"
    ofl = (fonts / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in ofl and "The Red Hat Project Authors" in ofl
