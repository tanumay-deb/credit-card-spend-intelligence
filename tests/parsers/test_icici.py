"""Golden-file test for the ICICI parser, against the redacted fixture.

The values pinned here are properties of this fixture. Regenerating it changes
them (the redactor picks fresh amounts every run, by design) and the assertions
have to be re-read off the new file. What does not change is the shape: the row
count, the direction split, and rows that add up.

Dates look absurd because redaction scrambles digits without regard for the
calendar; `safe_date` rolls the overflow forward rather than letting one
out-of-range field abort a whole statement.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from creditcard.models import Card
from creditcard.parsers.icici import icici_parser
from creditcard.reconcile import sum_check

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "icici_sample.txt"

CARD = Card(
    card_id="icici_coral",
    issuer="ICICI",
    card_name="Amazon Pay",
    last4="0000",
    parser="icici",
    password_env="ICICI_CORAL_PDF_PASSWORD",
    credit_limit=Decimal("0"),
    statement_day=1,
)


def _parse():
    return icici_parser.parse(FIXTURE.read_text(encoding="utf-8"), CARD, str(FIXTURE))


def test_parses_exact_transaction_count():
    assert len(_parse().transactions) == 52


def test_direction_split_matches_the_cr_marker():
    """A trailing CR is the only credit signal ICICI prints. If the marker were
    missed, every payment and refund would land on the spend side."""
    txns = _parse().transactions
    debits = [t for t in txns if t.direction == "debit"]
    credits = [t for t in txns if t.direction == "credit"]
    assert len(debits) == 35
    assert len(credits) == 17


def test_payment_rows_are_credit():
    txns = _parse().transactions
    payments = [t for t in txns if "Payment received" in t.merchant_raw]
    assert payments, "expected at least one 'Payment received' row in this fixture"
    assert all(t.direction == "credit" for t in payments)


def test_reward_points_are_not_mistaken_for_the_amount():
    """A reward-points integer sits between the description and the amount and
    can be negative. Reading it as the amount would be silent and wrong."""
    txns = _parse().transactions
    assert all(t.amount > Decimal("0") for t in txns)
    assert max(t.amount for t in txns) > Decimal("100")


def test_statement_summary_fields():
    s = _parse().statement
    assert s.total_due == Decimal("97120.61")
    assert s.min_due == Decimal("28051.81")
    assert s.purchases == Decimal("75321.92")
    assert s.payments == Decimal("41508.72")
    assert s.statement_date == date(4555, 3, 28)
    assert s.due_date == date(1180, 5, 5)


def test_reconciles_exactly():
    """The check that catches a silently dropped or duplicated row."""
    result = _parse()
    assert sum_check(result.transactions, result.statement) == Decimal("0")


def test_credit_rows_sum_to_the_reported_payments():
    """sum_check covers the spend side; this covers the other one."""
    result = _parse()
    credits = sum(
        (t.amount for t in result.transactions if t.direction == "credit"),
        Decimal("0"),
    )
    assert credits == result.statement.payments


def test_every_transaction_id_is_distinct():
    ids = [t.txn_id for t in _parse().transactions]
    assert len(set(ids)) == len(ids)


def test_extracts_the_merchant_emi_plans():
    """ICICI prints a finish date and the instalment amount but no rate.
    The absent field stays None rather than being inferred from HDFC's shape."""
    plans = _parse().emi_plans
    assert len(plans) == 2
    for plan in plans:
        assert plan.loan_type == "Merchant EMI"
        assert plan.end_date is not None, "ICICI does print a finish date"
        assert plan.instalment_amount is not None
        assert plan.interest_rate is None, "ICICI does not print a rate"
        assert plan.outstanding > Decimal("0")


def test_emi_outstanding_comes_before_the_monthly_instalment():
    """ICICI's header reads "... Pending Installments | Outstanding Amount* |
    Monthly Installment Amount". Read the other way round, a loan with four
    months to go showed one month's payment as all that was still owed."""
    from creditcard.parsers.icici import _parse_emi_plans
    (plan,) = _parse_emi_plans(
        "Merchant EMI 15/01/2026 15/09/2026 9 18,000.00 4 8,480.00 2,130.00\n",
        CARD, "stmt", "f.pdf",
    )
    assert plan.outstanding == Decimal("8480.00")
    assert plan.instalment_amount == Decimal("2130.00")


def test_the_printed_statement_period_is_used():
    """ICICI prints "Statement period : <date> to <date>"; the row dates only
    approximate it."""
    import re

    # The fixture's own dates are scrambled by redaction; put in a known one.
    text = re.sub(r"Statement period : .*? to \w+ \d+, \d+",
                  "Statement period : October 4, 2025 to February 3, 2026",
                  FIXTURE.read_text(encoding="utf-8"), count=1)
    statement = icici_parser.parse(text, CARD, str(FIXTURE)).statement
    assert (statement.period_start, statement.period_end) == (date(2025, 10, 4), date(2026, 2, 3))


def test_with_no_rows_and_no_printed_period_it_covers_the_month_before():
    """A closed card's last statement can have neither; it used to get 1970."""
    from datetime import timedelta

    from creditcard.emi import add_months
    from creditcard.parsers.icici import _TXN_RE

    text = "\n".join(
        line for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if "Statement period" not in line and not _TXN_RE.search(line)
    )
    statement = icici_parser.parse(text, CARD, str(FIXTURE)).statement
    assert statement.period_end == statement.statement_date
    assert statement.period_start == add_months(statement.statement_date, -1) + timedelta(days=1)
