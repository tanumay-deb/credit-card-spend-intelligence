import csv
from datetime import date
from decimal import Decimal

import pytest

from creditcard.config import load_payments
from creditcard.writers import write_payments


def test_load_payments_parses_rows(tmp_path):
    p = tmp_path / "payments.csv"
    p.write_text(
        "card_id,payment_date,amount,note\n"
        "hdfc_regalia,2026-09-05,32000,paid in full via UPI\n",
        encoding="utf-8",
    )
    payments = load_payments(p)
    assert payments[0].card_id == "hdfc_regalia"
    assert payments[0].payment_date == date(2026, 9, 5)
    assert payments[0].amount == Decimal("32000")
    assert payments[0].note == "paid in full via UPI"


def test_missing_file_returns_empty_list(tmp_path):
    assert load_payments(tmp_path / "nope.csv") == []


def test_header_only_file_returns_empty_list(tmp_path):
    p = tmp_path / "payments.csv"
    p.write_text("card_id,payment_date,amount,note\n", encoding="utf-8")
    assert load_payments(p) == []


def test_blank_note_is_allowed(tmp_path):
    p = tmp_path / "payments.csv"
    p.write_text(
        "card_id,payment_date,amount,note\nc1,2026-09-05,100,\n", encoding="utf-8"
    )
    assert load_payments(p)[0].note == ""


def test_write_payments_round_trips(tmp_path):
    from creditcard.models import Payment

    out = tmp_path / "payments.csv"
    write_payments(
        [Payment("c1", date(2026, 9, 5), Decimal("32000"), "note")], out
    )
    row = list(csv.DictReader(out.open(encoding="utf-8")))[0]
    assert row["payment_date"] == "2026-09-05"
    assert row["amount"] == "32000"


def test_write_payments_empty_still_writes_header(tmp_path):
    out = tmp_path / "payments.csv"
    write_payments([], out)
    assert out.read_text(encoding="utf-8-sig").startswith("card_id,payment_date")


def test_bom_prefixed_payments_file_loads(tmp_path):
    """payments.csv is the file the user edits most; Excel writes a BOM."""
    p = tmp_path / "payments.csv"
    p.write_text(
        "card_id,payment_date,amount,note\nc1,2026-09-05,32000,paid\n",
        encoding="utf-8-sig",
    )
    assert load_payments(p)[0].card_id == "c1"


def test_comma_in_payment_amount_is_rejected_not_truncated(tmp_path):
    """32,000 must not silently become 32."""
    p = tmp_path / "payments.csv"
    p.write_text(
        "card_id,payment_date,amount,note\n"
        "c1,2026-09-05,32,000,paid in full\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_payments(p)
    assert "comma" in str(exc.value).lower()


def test_quoted_thousands_separator_is_accepted(tmp_path):
    """Properly quoted, a comma is legitimate CSV - but Decimal still rejects it,
    so the user gets a clear per-row error rather than a wrong number."""
    p = tmp_path / "payments.csv"
    p.write_text(
        'card_id,payment_date,amount,note\nc1,2026-09-05,"32,000",paid\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_payments(p)


def test_malformed_payment_date_names_file_and_line(tmp_path):
    """Unlike load_cards and load_rules, load_payments used to have no
    try/except around row construction, so a bad date gave a bare
    'ValueError: Invalid isoformat string' with no file or line number."""
    p = tmp_path / "payments.csv"
    p.write_text(
        "card_id,payment_date,amount,note\n"
        "c1,not-a-date,100,paid\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_payments(p)
    assert str(p) in str(exc.value)
    assert "line 2" in str(exc.value)
