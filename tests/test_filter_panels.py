"""Both views: the filters are a fold-out panel with a count, plus a presets bar on the page."""
from __future__ import annotations

import re


def test_bargain_filters_are_a_fold_out_panel(client):
    page = client.get("/").get_data(as_text=True)
    assert 'id="filterSheet"' not in page
    m = re.search(r'<div id="filterPanel".*?<div id="filterBody"(.*?)\n</div>\n', page, re.S)
    assert m
    panel = m.group(1)
    for el in ('id="groupBy"', 'id="sortBy"', 'id="cats"', 'id="presetSel"'):
        assert el in panel, el
    assert re.search(r'id="filterToggle"[^>]*aria-controls="filterBody"', page)
    assert 'id="filterCount"' in page
    # On the page itself: the discount buttons, presets, store chips; Remove filters always there (disabled).
    for el in ('id="minDisc"', 'id="presetBar"', 'id="storeChips"'):
        assert el in page and el not in panel, el
    assert re.search(r'id="clearFilters"[^>]*disabled', page)
    # No "liked only" filter and no product search: liking only orders the results.
    for gone in ('likedOnlyBtn', 'bonusOnlyBtn', 'productSearch', 'searchBar"'):
        assert gone not in page, gone


def test_bonus_filters_are_categories_and_presets_like_the_bargains(client):
    page = client.get("/").get_data(as_text=True)
    m = re.search(r'<div id="bonusFilterPanel".*?<div id="bonusFilterBody"(.*?)\n</div>\n', page, re.S)
    assert m
    panel = m.group(1)
    for el in ('id="bonusCatChips"', 'id="bonusCatAll"', 'id="bonusPresetSel"', 'id="bonusPresetSave"'):
        assert el in panel, el
    assert re.search(r'id="bonusFilterToggle"[^>]*aria-controls="bonusFilterBody"', page)
    for el in ('id="bonusPresetBar"', 'id="bonusPresetAddBtn"', 'id="bonusLikedBtn"'):
        assert el in page and el not in panel, el
    # No more category groups or one-category-at-a-time pill row, and no deal or sort filters.
    for gone in ("bonusGroup", "bonusCats", "bonusMinSave", "bonusSort", "bonusTypes"):
        assert gone not in page, gone
