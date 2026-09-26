# tests/test_models.py
from datetime import date
from decimal import Decimal

from creditcard.models import Card, StatementSummary, Transaction


def test_card_holds_parser_and_password_env_not_password():
    card = Card(
        card_id="hdfc_regalia",
        issuer="HDFC",
        card_name="Regalia",
        last4="4321",
        parser="hdfc",
        password_env="HDFC_REGALIA_PDF_PASSWORD",
        credit_limit=Decimal("200000"),
        statement_day=18,
    )
    assert card.parser == "hdfc"
    assert card.password_env == "HDFC_REGALIA_PDF_PASSWORD"
    assert not hasattr(card, "password")


def test_transaction_amount_is_positive_and_direction_carries_sign():
    txn = Transaction(
        txn_id="abc123",
        card_id="hdfc_regalia",
        statement_id="hdfc_regalia_2026-08-18",
        txn_date=date(2026, 8, 3),
        posting_date=date(2026, 8, 4),
        merchant_raw="SWIGGY*ORDER BANGALORE IN",
        merchant_clean="SWIGGY",
        amount=Decimal("482.50"),
        direction="debit",
        txn_type="purchase",
        category_group="Food",
        subcategory="Swiggy",
        category_source="rule",
        is_forex=False,
        forex_ccy=None,
        forex_amount=None,
        source_file="statements/hdfc/2026-08.pdf",
    )
    assert txn.amount > 0
    assert txn.direction == "debit"


def test_statement_summary_carries_due_and_limit_fields():
    stmt = StatementSummary(
        statement_id="hdfc_regalia_2026-08-18",
        card_id="hdfc_regalia",
        statement_date=date(2026, 8, 18),
        due_date=date(2026, 9, 7),
        period_start=date(2026, 7, 19),
        period_end=date(2026, 8, 18),
        previous_balance=Decimal("0"),
        payments=Decimal("12000"),
        purchases=Decimal("18450.75"),
        total_due=Decimal("18450.75"),
        min_due=Decimal("930"),
        finance_charges=Decimal("0"),
        late_fee=Decimal("0"),
        credit_limit=Decimal("200000"),
        available_limit=Decimal("181549.25"),
        source_file="statements/hdfc/2026-08.pdf",
    )
    assert stmt.total_due == Decimal("18450.75")
