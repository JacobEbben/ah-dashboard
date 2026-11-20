"""The filter controls live in two sheets (modal dialogs), opened by a button with a count."""
from __future__ import annotations

import re


def _dialog(page: str, dialog_id: str) -> str:
    m = re.search(rf'<dialog id="{dialog_id}"[^>]*>(.*?)</dialog>', page, re.S)
    assert m, dialog_id
    return m.group(1)


def test_bargain_filters_are_in_the_sheet(client):
    page = client.get("/").get_data(as_text=True)
    sheet = _dialog(page, "filterSheet")
    for el in ('id="likedOnlyBtn"', 'id="bonusOnlyBtn"', 'id="groupBy"', 'id="sortBy"', 'id="cats"',
               'id="presetSel"', 'id="sheetClear"'):
        assert el in sheet, el
    # The opener says it opens a dialog and has a place for the count.
    assert re.search(r'id="filterMenuBtn"[^>]*aria-haspopup="dialog"[^>]*aria-controls="filterSheet"', page)
    assert 'id="filterCount"' in page
    # The quick switches stay on the page itself, outside the sheet: the discount dropdown and the presets.
    for el in ('id="minDiscSel"', 'id="presetBar"', 'id="clearFilters"'):
        assert el in page and el not in sheet, el


def test_bonus_filters_are_in_the_sheet(client):
    page = client.get("/").get_data(as_text=True)
    sheet = _dialog(page, "bonusFilterSheet")
    for el in ('id="bonusMinSave"', 'id="bonusSort"', 'id="bonusTypes"', 'id="bonusFiltersReset"'):
        assert el in sheet, el
    assert re.search(r'id="bonusFilterBtn"[^>]*aria-controls="bonusFilterSheet"', page)


def test_every_sheet_can_be_closed_with_a_button(client):
    page = client.get("/").get_data(as_text=True)
    for dialog_id in ("filterSheet", "bonusFilterSheet"):
        sheet = _dialog(page, dialog_id)
        assert sheet.count("data-close") >= 2, dialog_id   # the ✕ and "Show results"
        assert 'class="sheetclose"' in sheet
