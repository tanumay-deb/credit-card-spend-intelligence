"""The dashboard's report pages, checked as the files Power BI opens."""
import json
from pathlib import Path

import pytest

PAGES = Path(__file__).resolve().parent.parent / "dashboard.Report" / "definition" / "pages"
PAGE_ORDER = json.loads((PAGES / "pages.json").read_text(encoding="utf-8-sig"))["pageOrder"]

SLICERS = {
    "month": ("dim_date[Year]", "dim_date[Month Short]"),
    "card": ("dim_card[card_name]",),
    "category": ("dim_category[category_group]",),
}
DATES = {"fact_statements[statement_date]", "fact_transactions[txn_date]"}


def _visuals(page: str) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8-sig"))["visual"]
        for path in sorted((PAGES / page / "visuals").glob("*/visual.json"))
    ]


def _name(field: dict) -> str:
    body = next(iter(field.values()))
    return f"{body['Expression']['SourceRef']['Entity']}[{body['Property']}]"


def _fields(visual: dict) -> tuple[str, ...]:
    return tuple(_name(p["field"]) for p in visual["query"]["queryState"]["Values"]["projections"])


def _sync_group(page: str, fields: tuple[str, ...]) -> dict:
    for visual in _visuals(page):
        if visual["visualType"] == "slicer" and _fields(visual) == fields:
            return visual.get("syncGroup", {})
    raise AssertionError(f"{page} has no slicer on {fields}")


@pytest.mark.parametrize("fields", SLICERS.values(), ids=SLICERS.keys())
def test_a_slicer_choice_carries_to_every_page(fields):
    """Every page has its own copy of each slicer. Left unsynced, a month
    picked on one page is forgotten on the next."""
    groups = {page: _sync_group(page, fields) for page in PAGE_ORDER}
    for page, group in groups.items():
        assert group.get("filterChanges") is True, page
        assert group.get("fieldChanges") is True, page
    assert len({group["groupName"] for group in groups.values()}) == 1


def test_slicers_on_different_fields_stay_independent():
    """A group name shared by two fields would tie the month to the card."""
    names = [_sync_group(PAGE_ORDER[0], fields).get("groupName") for fields in SLICERS.values()]
    assert None not in names
    assert len(set(names)) == len(names)


def test_tables_that_lead_with_a_date_list_the_newest_first():
    """Power BI sorts an unsorted table oldest first, which puts a statement
    that arrived today on the last row of a table nobody scrolls."""
    dated = [
        (page, visual)
        for page in PAGE_ORDER
        for visual in _visuals(page)
        if visual["visualType"] == "tableEx" and _fields(visual)[0] in DATES
    ]
    assert len(dated) == 3
    for page, visual in dated:
        sort = visual["query"].get("sortDefinition", {}).get("sort", [])
        order = [(_name(by["field"]), by["direction"]) for by in sort]
        assert order == [(_fields(visual)[0], "Descending")], page
