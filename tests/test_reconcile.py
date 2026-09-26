from dataclasses import replace
from datetime import date
from decimal import Decimal

from creditcard.models import Payment, StatementSummary, Transaction
from creditcard.reconcile import check_payments, find_missing_cycles, sum_check


def _stmt(card="c1", stmt_date=date(2026, 8, 18), purchases="1000", finance="0"):
    return StatementSummary(
        statement_id=f"{card}_{stmt_date}", card_id=card, statement_date=stmt_date,
        due_date=date(2026, 9, 7), period_start=date(2026, 7, 19), period_end=stmt_date,
        previous_balance=Decimal("0"), payments=Decimal("0"),
        purchases=Decimal(purchases), total_due=Decimal(purchases),
        min_due=Decimal("50"), finance_charges=Decimal(finance), late_fee=Decimal("0"),
        credit_limit=Decimal("100000"), available_limit=Decimal("99000"),
        source_file="f.pdf",
    )


_CREDIT_TYPES = {"payment", "refund", "cashback"}


def _txn(amount, txn_type="purchase"):
    """direction follows txn_type, because sum_check reconciles on direction."""
    return Transaction(
        txn_id="t" + amount + txn_type, card_id="c1", statement_id="s1",
        txn_date=date(2026, 8, 1), posting_date=None, merchant_raw="M",
        merchant_clean="M", amount=Decimal(amount),
        direction="credit" if txn_type in _CREDIT_TYPES else "debit",
        txn_type=txn_type, category_group="Others", subcategory="Misc",
        category_source="rule", is_forex=False, forex_ccy=None,
        forex_amount=None, source_file="f.pdf",
    )


def test_sum_check_zero_when_rows_match_statement():
    assert sum_check([_txn("600"), _txn("400")], _stmt(purchases="1000")) == Decimal("0")


def test_sum_check_negative_when_parser_dropped_a_row():
    assert sum_check([_txn("600")], _stmt(purchases="1000")) == Decimal("-400")


def test_sum_check_ignores_credit_rows():
    """Payments and refunds are credits; the issuer's purchases figure excludes them."""
    rows = [_txn("1000"), _txn("500", "payment"), _txn("50", "refund")]
    assert sum_check(rows, _stmt(purchases="1000")) == Decimal("0")


def test_sum_check_counts_fees_and_interest():
    """An issuer bills GST and finance charges as debits, so its purchases
    total includes them. Excluding them here broke reconciliation on every
    real statement the moment GST was correctly reclassified as a fee."""
    rows = [_txn("600"), _txn("300", "fee"), _txn("100", "interest")]
    assert sum_check(rows, _stmt(purchases="1000")) == Decimal("0")


def test_sum_check_adds_the_statements_finance_charges():
    """Issuers list interest and charges as rows but total them outside
    purchases -- HDFC as Finance Charges, SBI in its fees column. Comparing rows
    with purchases alone flagged every statement that carried one."""
    rows = [_txn("1000"), _txn("50", "interest")]
    assert sum_check(rows, _stmt(purchases="1000", finance="50")) == Decimal("0")


def test_sum_check_counts_emi_as_spend():
    """Total Spend counts purchase + emi, so the reconciliation must too."""
    rows = [_txn("600"), _txn("400", "emi")]
    assert sum_check(rows, _stmt(purchases="1000")) == Decimal("0")


def test_sum_check_with_no_rows_reports_the_full_shortfall():
    assert sum_check([], _stmt(purchases="1000")) == Decimal("-1000")


def test_find_missing_cycles_detects_a_skipped_month():
    stmts = [
        _stmt(stmt_date=date(2026, 6, 18)),
        _stmt(stmt_date=date(2026, 8, 18)),
    ]
    assert find_missing_cycles(stmts) == [("c1", "2026-07")]


def test_find_missing_cycles_spans_a_year_boundary():
    stmts = [
        _stmt(stmt_date=date(2025, 11, 18)),
        _stmt(stmt_date=date(2026, 2, 18)),
    ]
    assert find_missing_cycles(stmts) == [("c1", "2025-12"), ("c1", "2026-01")]


def test_no_gaps_for_consecutive_months():
    stmts = [_stmt(stmt_date=date(2026, 7, 18)), _stmt(stmt_date=date(2026, 8, 18))]
    assert find_missing_cycles(stmts) == []


def test_single_statement_has_no_gaps():
    assert find_missing_cycles([_stmt()]) == []


def test_gaps_are_reported_per_card():
    stmts = [
        _stmt(card="c1", stmt_date=date(2026, 6, 18)),
        _stmt(card="c1", stmt_date=date(2026, 8, 18)),
        _stmt(card="c2", stmt_date=date(2026, 6, 18)),
        _stmt(card="c2", stmt_date=date(2026, 7, 18)),
    ]
    assert find_missing_cycles(stmts) == [("c1", "2026-07")]


def _pay(amount, day, card="c1"):
    return Payment(card_id=card, payment_date=date(2026, 8, day),
                   amount=Decimal(amount), note="")


def _stmt_pay(stmt_date, payments):
    s = _stmt(stmt_date=stmt_date)
    return replace(s, payments=Decimal(payments),
                   statement_id=f"c1_{stmt_date}")


def test_logged_payment_matching_the_statement_reports_nothing():
    stmts = [_stmt_pay(date(2026, 7, 18), "0"), _stmt_pay(date(2026, 8, 18), "5000")]
    assert check_payments(stmts, [_pay("5000", 5)]) == []


def test_forgotten_payment_is_reported():
    """Once a card's payments are logged, a statement window with nothing logged
    against a reported payment is a forgotten entry."""
    stmts = [_stmt_pay(date(2026, 7, 18), "0"), _stmt_pay(date(2026, 8, 18), "5000"),
             _stmt_pay(date(2026, 9, 18), "3000")]
    result = check_payments(stmts, [_pay("5000", 5)])
    assert result == [("c1", "c1_2026-09-18", Decimal("0"), Decimal("3000"))]


def test_card_with_no_logged_payments_is_not_checked():
    """An empty log means payments are not being recorded, not that none were
    made; checking it warned on every statement."""
    stmts = [_stmt_pay(date(2026, 7, 18), "0"), _stmt_pay(date(2026, 8, 18), "5000")]
    assert check_payments(stmts, []) == []
    assert check_payments(stmts, [_pay("5000", 5, card="c2")]) == []


def test_double_logged_payment_is_reported():
    stmts = [_stmt_pay(date(2026, 7, 18), "0"), _stmt_pay(date(2026, 8, 18), "5000")]
    result = check_payments(stmts, [_pay("5000", 5), _pay("5000", 6)])
    assert result[0][2] == Decimal("10000")


def test_earliest_statement_per_card_is_skipped():
    """Nothing bounds the window before the first statement."""
    assert check_payments([_stmt_pay(date(2026, 8, 18), "5000")], []) == []


def test_payments_outside_the_window_are_not_counted():
    stmts = [_stmt_pay(date(2026, 7, 18), "0"), _stmt_pay(date(2026, 8, 18), "5000")]
    late = Payment(card_id="c1", payment_date=date(2026, 9, 2),
                   amount=Decimal("5000"), note="")
    result = check_payments(stmts, [late])
    assert result[0][2] == Decimal("0")


def test_cards_billed_only_when_used_can_be_left_out():
    stmts = [_stmt(card="c1", stmt_date=date(2026, 6, 18)),
             _stmt(card="c1", stmt_date=date(2026, 8, 18))]
    assert find_missing_cycles(stmts, skip={"c1"}) == []
