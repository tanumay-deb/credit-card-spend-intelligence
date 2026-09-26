"""Guards on the committed statement fixtures.

The fixtures stand in for real credit card statements, so three things must
hold. They must carry nothing personally identifying. They must preserve the
exact line structure of the statements they were derived from -- that
structure is what the parsers key off, so a fixture with different column
widths would be testing a layout that does not exist. And they must keep the
statement's own arithmetic: the transaction rows have to add up to the
summary figures printed above them.

That last one is the whole point of `creditcard.reconcile.sum_check`, which
compares what a parser summed from the rows against what the statement
claimed. A fixture that cannot reconcile cannot exercise it, and the parser
built against that fixture ships with its most important integrity check
never having run against anything.

It is checked here, for every fixture, rather than only in each issuer's
golden-file test, because two of the three fixtures have no parser yet.
Whoever writes the ICICI and SBI parsers should find their fixture already
able to reconcile, instead of discovering it cannot and working around it.
"""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from tests.statement_shapes import FIXTURE_ISSUER, SHAPES

FIXTURE_DIR = Path("tests/fixtures")
FIXTURES = ["hdfc_sample.txt", "icici_sample.txt", "sbi_sample.txt"]


def _read(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_exists_and_is_substantial(name):
    path = FIXTURE_DIR / name
    assert path.exists(), f"{name} missing"
    assert len(_read(name)) > 1000


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_carries_no_real_email_address(name):
    """Placeholders are padded to the original width to keep columns aligned,
    so they appear as redactedxxxx@example.com rather than a fixed string."""
    placeholder = re.compile(r"redactedx*@example\.com")
    found = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", _read(name))
    leftovers = [e for e in found if not placeholder.fullmatch(e)]
    assert not leftovers, f"{name} still contains {len(leftovers)} real address(es)"


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_carries_no_personal_name(name):
    """The cardholder and co-applicant names must not survive redaction."""
    text = _read(name).upper()
    for needle in ("TANUMAY", "GOSWAMI"):
        assert needle not in text, f"{name} still contains {needle}"


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_still_looks_like_a_statement(name):
    """Redaction must not have destroyed what the parsers match on.

    Amounts have to remain syntactically valid numbers, or the fixture stops
    exercising the parser it exists for.
    """
    text = _read(name)
    amounts = re.findall(r"\d+\.\d{2}", text)
    assert len(amounts) >= 10, f"{name} has only {len(amounts)} amount-shaped tokens"


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_debit_rows_sum_to_its_own_purchases_figure(name):
    """Reconciliation has to survive redaction.

    It does not survive it for free. Substituting digits one at a time
    cannot preserve a sum -- addition carries between digit positions and a
    per-position substitution does not -- so the rows and the total have to
    be regenerated together as a consistent set. See
    `tools/redact_statement.py`.
    """
    read_rows, read_totals = SHAPES[FIXTURE_ISSUER[name]]
    text = _read(name)
    rows = read_rows(text)
    assert rows.debits, f"{name}: no debit rows found; the row pattern has drifted"
    assert rows.debit_total == read_totals(text).purchases


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_credit_rows_sum_to_its_own_payments_figure(name):
    read_rows, read_totals = SHAPES[FIXTURE_ISSUER[name]]
    text = _read(name)
    rows = read_rows(text)
    assert rows.credits, f"{name}: no credit rows found; the row pattern has drifted"
    assert rows.credit_total == read_totals(text).payments


@pytest.mark.parametrize("name", FIXTURES)
def test_fixture_amounts_are_not_all_zero(name):
    """A reconciling fixture is trivial to fake by zeroing every amount."""
    read_rows, _ = SHAPES[FIXTURE_ISSUER[name]]
    rows = read_rows(_read(name))
    assert rows.debit_total > Decimal("0")
    assert rows.credit_total > Decimal("0")
