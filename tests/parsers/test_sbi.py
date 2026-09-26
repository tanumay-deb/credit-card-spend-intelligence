"""Golden-file test for the SBI parser, against the redacted fixture.

The fixture comes from the PRIME card, which had a light month -- two rows. That
is thin coverage on its own, so the reconciliation assertions matter more here
than the count: they are what would catch a dropped row if the pattern drifted.
The Cashback card exercises the same parser against a busier statement.

The values pinned here are properties of this fixture and change if it is
regenerated. Dates look absurd because redaction scrambles digits without regard
for the calendar.

The fees column in the account summary was set to 0.00 by hand, and the HDFC
fixture's finance-charges figure likewise. Redaction scrambles every figure
outside the rows-and-totals set it regenerates, which turned each month's real
0.00 into a random charge no row accounted for -- unnoticed while reconciliation
compared rows with purchases alone, and a failure once it rightly added the
statement's finance charges. A regenerated fixture needs the same correction.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

from creditcard.models import Card
from creditcard.parsers.sbi import _parse_summary, _parse_transactions, sbi_parser
from creditcard.reconcile import sum_check

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sbi_sample.txt"

CARD = Card(
    card_id="sbi_elite",
    issuer="SBI",
    card_name="PRIME",
    last4="0000",
    parser="sbi",
    password_env="SBI_ELITE_PDF_PASSWORD",
    credit_limit=Decimal("0"),
    statement_day=1,
)


def _parse():
    return sbi_parser.parse(FIXTURE.read_text(encoding="utf-8"), CARD, str(FIXTURE))


def test_parses_exact_transaction_count():
    assert len(_parse().transactions) == 2


def test_trailing_c_and_d_set_the_direction():
    """SBI states direction explicitly, unlike HDFC's '+' prefix or ICICI's
    trailing CR. Reading the marker backwards would invert every row."""
    txns = _parse().transactions
    assert {t.direction for t in txns} == {"debit", "credit"}
    payment = next(t for t in txns if "PAYMENT RECEIVED" in t.merchant_raw)
    assert payment.direction == "credit"


def test_two_digit_transaction_years_are_this_century():
    """Rows carry DD Mon YY while the header carries four digits. Feeding a
    two-digit year into a four-digit field silently truncates the century."""
    assert all(t.txn_date.year >= 2000 for t in _parse().transactions)


def test_statement_summary_fields():
    s = _parse().statement
    assert s.total_due == Decimal("102.41")
    assert s.min_due == Decimal("182.95")
    assert s.purchases == Decimal("129.33")
    assert s.payments == Decimal("635.40")
    assert s.statement_date == date(1238, 10, 18)
    assert s.due_date == date(3293, 10, 11)


def test_reconciles_exactly():
    result = _parse()
    assert sum_check(result.transactions, result.statement) == Decimal("0")


def test_credit_rows_sum_to_the_reported_payments():
    result = _parse()
    credits = sum(
        (t.amount for t in result.transactions if t.direction == "credit"),
        Decimal("0"),
    )
    assert credits == result.statement.payments


def test_every_transaction_id_is_distinct():
    ids = [t.txn_id for t in _parse().transactions]
    assert len(set(ids)) == len(ids)


_HEADING = "Credit Limit ( ` ) (including cash) Cash Limit( ` ) (30% of credit limit) Statement Date"


_SUMMARY_HEADING = "( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )"
_MIN_DUE = "Minimum Amount Due ( ` )\nSTMT No. : X12345678901\n200.00"


def _statement_text(heading: str = _HEADING, due: str = "23 Sep 2026",
                    summary_heading: str = _SUMMARY_HEADING, min_due: str = _MIN_DUE) -> str:
    return f"""{heading}
2,00,000.00 60,000.00 03 Sep 2026
Available Credit Limit ( ` ) Available Cash Limit ( ` ) Payment Due Date
1,90,000.00 60,000.00 {due}
ACCOUNT SUMMARY
Previous Balance Payments & other Purchases & Other Fee, Taxes & Total Outstanding
{summary_heading}
1,021.99 5,635.40 8,129.33 9.84 3,666.36
Total Amount Due ( ` )
3,666.36
{min_due}
"""


def test_statement_date_is_found_when_the_heading_wraps():
    """Some PRIME statements push the ")" closing "Cash Limit( ` )" onto a line
    of its own, leaving the figures two lines under the heading, not one."""
    wrapped = "Credit Limit ( ` ) (including cash) Cash Limit( ` (30% of credit limit) Statement Date\n)"
    s = _parse_summary(_statement_text(heading=wrapped), CARD, "f.pdf")
    assert s.statement_date == date(2026, 9, 3)
    assert s.credit_limit == Decimal("200000.00")


def test_immediate_due_date_is_the_statement_date():
    """An overdue account's due date reads IMMEDIATE instead of a date."""
    s = _parse_summary(_statement_text(due="IMMEDIATE"), CARD, "f.pdf")
    assert s.due_date == s.statement_date


def test_undated_rows_share_the_date_of_the_row_above():
    """SBI prints a date once per group, so the GST line under a fee has none.
    Skipping those lines dropped the GST, and any charge posted the same day as
    a payment."""
    rows = _parse_transactions(
        "03 Oct 25 ANNUAL FEE (EXCL TAX 179.82) 999.00 D\n"
        "IGST DB @ 18.00% 179.82 D\n",
        CARD, "stmt", "f.pdf",
    )
    assert [(t.txn_date, t.amount, t.direction) for t in rows] == [
        (date(2025, 10, 3), Decimal("999.00"), "debit"),
        (date(2025, 10, 3), Decimal("179.82"), "debit"),
    ]
    assert rows[1].txn_type == "fee"


def test_an_amount_line_away_from_the_rows_is_not_a_transaction():
    """Only a line continuing a run of rows is read as one, so a figure
    elsewhere on the page that happens to end in C or D is left alone."""
    rows = _parse_transactions(
        "03 Oct 25 NETFLIX MUMBAI 199.00 D\n"
        "Reward Points Summary\n"
        "Points adjusted 50.00 C\n",
        CARD, "stmt", "f.pdf",
    )
    assert len(rows) == 1


def test_account_summary_is_found_with_a_space_before_a_bracket():
    """One statement prints "Interest Charges ( ` )" where the others print
    "Interest Charges( ` )", and the parser found no account summary at all."""
    spaced = "( ` ) Credits( ` ) Debits( ` ) Interest Charges ( ` ) ( ` )"
    s = _parse_summary(_statement_text(summary_heading=spaced), CARD, "f.pdf")
    assert s.purchases == Decimal("8129.33")
    assert s.finance_charges == Decimal("9.84")


def test_minimum_due_is_found_when_its_bracket_lands_past_the_statement_number():
    """Some PRIME statements close "Minimum Amount Due ( `" only at the end of
    the STMT No. line that follows it."""
    split = "**Minimum Amount Due ( `\nSTMT No. : X12345678901 )\n200.00"
    s = _parse_summary(_statement_text(min_due=split), CARD, "f.pdf")
    assert s.min_due == Decimal("200.00")


def test_a_row_is_read_when_the_next_column_runs_on_after_it():
    """On an overdue statement the charges share their lines with a notice
    printed beside them, so text follows the C or D marker."""
    rows = _parse_transactions(
        "05 May 26 FEE - LATE PAYMENT (EXCL TAX 90.00) 500.00 D Please pay the\n"
        "IGST DB @ 18.00% 90.00 D total amount due\n",
        CARD, "stmt", "f.pdf",
    )
    assert [(t.amount, t.direction) for t in rows] == [
        (Decimal("500.00"), "debit"),
        (Decimal("90.00"), "debit"),
    ]
    assert rows[0].merchant_raw == "FEE - LATE PAYMENT (EXCL TAX 90.00)"
