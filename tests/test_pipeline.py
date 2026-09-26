from datetime import date
from decimal import Decimal

from creditcard.models import Card, StatementSummary, Transaction
from creditcard.pipeline import (
    _process_one,
    file_hash,
    merge_statements,
    merge_transactions,
)


def _txn(txn_id, sub="Uncategorised"):
    return Transaction(
        txn_id=txn_id, card_id="c1", statement_id="s1", txn_date=date(2026, 8, 3),
        posting_date=None, merchant_raw="M", merchant_clean="M",
        amount=Decimal("100"), direction="debit", txn_type="purchase",
        category_group="Others", subcategory=sub, category_source="unmatched",
        is_forex=False, forex_ccy=None, forex_amount=None, source_file="f.pdf",
    )


def test_merge_keeps_both_when_ids_differ():
    merged = merge_transactions([_txn("a")], [_txn("b")])
    assert {t.txn_id for t in merged} == {"a", "b"}


def test_merge_collapses_duplicate_ids():
    merged = merge_transactions([_txn("a")], [_txn("a")])
    assert len(merged) == 1


def test_new_row_wins_so_recategorisation_applies_to_history():
    """Editing merchant_map.csv and re-running must retag old transactions."""
    prior = [_txn("a", sub="Uncategorised")]
    fresh = [_txn("a", sub="Groceries")]
    assert merge_transactions(prior, fresh)[0].subcategory == "Groceries"


def test_merge_result_is_sorted_by_date_then_id():
    merged = merge_transactions([], [_txn("z"), _txn("a")])
    assert [t.txn_id for t in merged] == ["a", "z"]


def test_file_hash_is_stable_and_content_sensitive(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")
    assert file_hash(a) == file_hash(b)
    b.write_bytes(b"different")
    assert file_hash(a) != file_hash(b)


def _stmt(statement_id, stmt_date=date(2026, 8, 18), purchases="1000"):
    return StatementSummary(
        statement_id=statement_id, card_id="c1", statement_date=stmt_date,
        due_date=date(2026, 9, 7), period_start=date(2026, 7, 19),
        period_end=stmt_date, previous_balance=Decimal("0"),
        payments=Decimal("0"), purchases=Decimal(purchases),
        total_due=Decimal(purchases), min_due=Decimal("50"),
        finance_charges=Decimal("0"), late_fee=Decimal("0"),
        credit_limit=Decimal("100000"), available_limit=Decimal("99000"),
        source_file="f.pdf",
    )


def test_merge_statements_collapses_duplicate_ids():
    """Re-importing a statement must not double it."""
    merged = merge_statements([_stmt("s1")], [_stmt("s1")])
    assert len(merged) == 1


def test_merge_statements_new_row_wins():
    """A reparse with a fixed parser must replace the old summary."""
    merged = merge_statements([_stmt("s1", purchases="900")],
                              [_stmt("s1", purchases="1000")])
    assert merged[0].purchases == Decimal("1000")


def test_merge_statements_sorted_by_date():
    merged = merge_statements(
        [], [_stmt("s2", date(2026, 9, 18)), _stmt("s1", date(2026, 8, 18))]
    )
    assert [s.statement_id for s in merged] == ["s1", "s2"]


def _card(card_id="c1"):
    return Card(
        card_id=card_id, issuer="HDFC", card_name="Regalia", last4="4321",
        parser="hdfc", password_env="HDFC_PW", credit_limit=Decimal("0"),
        statement_day=18,
    )


def test_process_one_missing_file_returns_failed_row_not_raise(tmp_path):
    """_process_one's docstring promises it never raises. A PDF that vanishes
    or is locked between run()'s glob() and processing (OneDrive sync, an
    open PDF viewer) must not kill the whole run - it must come back as a
    failed log row instead."""
    missing = tmp_path / "does-not-exist.pdf"
    row, txns, statement, emi_plans = _process_one(missing, _card(), {}, [], {})
    assert row.status == "failed"
    assert row.source_file == str(missing)
    assert txns == []
    assert statement is None
    assert emi_plans == ()
