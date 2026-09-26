"""Golden-file test for the HDFC parser, against the redacted fixture.

tests/fixtures/hdfc_sample.txt is a real HDFC statement (Swiggy card) put
through tools/redact_statement.py. Every digit in the file is replaced, and
the amounts the statement's own arithmetic ties together -- the transaction
rows and the PURCHASES/PAYMENTS figures that total them -- are regenerated
as a consistent set rather than substituted one digit at a time, which
cannot preserve a sum. So the fixture reconciles exactly, and
`test_reconciles_exactly` below is the same check the real statements are
held to.

The values pinned here are properties of this fixture. Regenerating it
(fresh random amounts every run, by design -- see the redactor's docstring)
changes them, and the assertions have to be re-read off the new file. What
does not change is the shape: 32 rows, and rows that add up.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from creditcard.models import Card
from creditcard.parsers.hdfc import hdfc_parser
from creditcard.reconcile import sum_check

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "hdfc_sample.txt"

CARD = Card(
    card_id="hdfc_regalia",
    issuer="HDFC",
    card_name="Swiggy",
    last4="0000",
    parser="hdfc",
    password_env="HDFC_REGALIA_PDF_PASSWORD",
    credit_limit=Decimal("0"),
    statement_day=1,
)


def _parse():
    text = FIXTURE.read_text(encoding="utf-8")
    return hdfc_parser.parse(text, CARD, str(FIXTURE))


def test_parses_exact_transaction_count():
    """32 lines in the fixture match the DD/MM/YYYY| HH:MM ... C <amt> l
    transaction shape: 12 on the first Domestic Transactions page, 20 on
    the second (including the two OFFUS EMI rows). Everything else in the
    file -- headers, the Cash Back Summary, the Smart EMI Loan Summary, the
    GST Summary -- does not match that shape and must not be counted."""
    result = _parse()
    assert len(result.transactions) == 32


def test_has_at_least_one_credit_and_one_debit():
    result = _parse()
    directions = {t.direction for t in result.transactions}
    assert "credit" in directions
    assert "debit" in directions


def test_payment_rows_are_credit():
    """Every row whose raw description mentions PAYMENT in this fixture is
    marked with a '+' before the C -- the only credit signal HDFC prints."""
    result = _parse()
    payment_rows = [t for t in result.transactions if "PAYMENT" in t.merchant_raw.upper()]
    assert payment_rows, "expected at least one row mentioning PAYMENT in this fixture"
    assert all(t.direction == "credit" for t in payment_rows)


def test_statement_summary_fields():
    """Values read directly off the fixture's flattened summary block:

        _ C89,406.31                                        <- total_due
        C36,055.50 C76,103.04 + C17,890.54 + C0.00 =        <- min_due's row
        ...
        C0,432.57 07 Apr, 6695                              <- min_due, due_date

    statement_date comes from the 'Statement Date 73 Apr, 6652' line.
    Redaction scrambles day-of-month digits without regard for calendar
    validity (day 73 does not exist in any month), so the parser rolls
    day/month overflow forward the way spreadsheet date arithmetic does:
    6652-04-01 + 72 days == 6652-06-12. due_date's own digits happen to
    already be in range (07 Apr, 6695), so no rollover applies there.
    """
    result = _parse()
    s = result.statement
    assert s.total_due == Decimal("89406.31")
    assert s.min_due == Decimal("432.57")
    assert s.statement_date == date(6652, 6, 12)
    assert s.due_date == date(6695, 4, 7)


def test_reconciles_exactly():
    """The check that catches a silently dropped or duplicated row.

    Zero means the rows the parser found are exactly the rows the statement
    totalled -- no more, no fewer. Anything else means the parser and the
    statement disagree about what was spent, which is the failure mode PDF
    parsing produces without ever raising.
    """
    result = _parse()
    assert sum_check(result.transactions, result.statement) == Decimal("0")


def test_credit_rows_sum_to_the_reported_payments():
    """sum_check only covers the spend side; this covers the other one, so a
    dropped payment row cannot pass unnoticed."""
    result = _parse()
    credits = sum(
        (t.amount for t in result.transactions if t.direction == "credit"),
        Decimal("0"),
    )
    assert credits == result.statement.payments


def test_every_transaction_id_is_distinct():
    """make_txn_ids must separate repeated same-day purchases; this fixture has
    several identical Swiggy orders on the same date."""
    result = _parse()
    ids = [t.txn_id for t in result.transactions]
    assert len(set(ids)) == len(ids)


def test_extracts_the_smart_emi_loan():
    """HDFC prints a Smart EMI Loan Summary with tenure, rate and the balance
    still outstanding -- the detail a transaction row alone cannot give."""
    plans = _parse().emi_plans
    assert len(plans) == 1
    plan = plans[0]
    assert plan.loan_type == "Smart EMI"
    assert plan.tenure_months > 0
    assert plan.remaining_months <= plan.tenure_months
    assert plan.interest_rate is not None
    assert plan.interest_payable is not None
    assert plan.end_date is None, "HDFC does not print a finish date"
    assert plan.outstanding > Decimal("0")


def _emi_line_plans(line: str):
    from creditcard.parsers.hdfc import _parse_emi_plans
    return _parse_emi_plans(line + "\n", CARD, "stmt", "f.pdf")


def test_emi_with_one_month_left_is_still_found():
    """HDFC prints 'Month', singular, once one instalment remains. Missing it
    made the loan vanish and get marked closed while still being billed."""
    (plan,) = _emi_line_plans(
        "134093939 15/01/2026 C71,442.59 9 Months 15% C8,120.00 C150.00 1 Month")
    assert plan.remaining_months == 1
    assert plan.outstanding == Decimal("8120.00")


def test_emi_in_its_final_month_is_still_found():
    """In the final billed month the remaining-tenure column is dropped."""
    (plan,) = _emi_line_plans(
        "134093939 15/01/2026 C71,442.59 9 Months 15% C8.40 C0.50")
    assert plan.remaining_months == 0
    assert plan.outstanding == Decimal("8.40")
