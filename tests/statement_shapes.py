"""Where each issuer's statement keeps the numbers that have to agree.

Every statement in this project carries the same internal relation: the
transaction rows it lists add up to the summary figures it prints. Debit
rows total the "purchases" figure; credit rows total the "payments" figure.

This module is the one place that says how to find those, so the fixture
guards and the redactor's own tests cannot drift apart. It is deliberately
independent of `creditcard/parsers/` and of `tools/redact_statement.py`: a
test that reads a statement using the same code as the thing under test
proves only that the code agrees with itself.

Each relation below was confirmed against the real statements it describes
-- the rows sum to the printed figure to the paisa on every statement held
for that issuer, including the two HDFC cards and the two SBI cards.

Patterns tolerate a trailing carriage return, because the extracted text is
kept with CRLF endings and `$` in multiline mode matches before the "\\n".
"""
import re
from dataclasses import dataclass
from decimal import Decimal


def _amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


@dataclass(frozen=True)
class Rows:
    debits: list[Decimal]
    credits: list[Decimal]

    @property
    def debit_total(self) -> Decimal:
        return sum(self.debits, Decimal("0"))

    @property
    def credit_total(self) -> Decimal:
        return sum(self.credits, Decimal("0"))


@dataclass(frozen=True)
class Totals:
    purchases: Decimal
    payments: Decimal


def _line_after(text: str, label: str) -> str:
    return text[text.index(label):].splitlines()[1]


# ---------------------------------------------------------------------------
# HDFC -- "+" immediately before the rupee glyph is the only credit marker
# ---------------------------------------------------------------------------
_HDFC_ROW = re.compile(
    r"^[ \t]*\d{2}/\d{2}/\d{4}\|[ \t]*\d{2}:\d{2}[ \t]+"
    r".*?[ \t]*(\+)?[ \t]*C[ \t]*([\d,]+\.\d{2})[ \t]*l[ \t\r]*$",
    re.M,
)
_HDFC_SUMMARY_AMOUNT = re.compile(r"C[ \t]*(\d[\d,]*(?:\.\d{1,2})?)")


def hdfc_rows(text: str) -> Rows:
    found = list(_HDFC_ROW.finditer(text))
    return Rows(
        debits=[_amount(m.group(2)) for m in found if not m.group(1)],
        credits=[_amount(m.group(2)) for m in found if m.group(1)],
    )


def hdfc_totals(text: str) -> Totals:
    """The summary table is a 2D block pdfplumber flattens; its amounts come
    out in a fixed order -- total due, previous dues, payments, purchases,
    finance charges -- however the lines happen to wrap."""
    start = text.index("PAYMENTS/CREDITS")
    ends = [
        i
        for i in (text.find("Purchase Indicator", start), text.find("DATE & TIME", start))
        if i != -1
    ]
    block = text[start:min(ends)] if ends else text[start:]
    amounts = [_amount(m.group(1)) for m in _HDFC_SUMMARY_AMOUNT.finditer(block)]
    return Totals(purchases=amounts[3], payments=amounts[2])


# ---------------------------------------------------------------------------
# ICICI -- a trailing "CR" marks a credit
# ---------------------------------------------------------------------------
# Rows are matched from their date rather than from the line start: on page
# one the flattened two-column layout can bleed a fragment of the rewards
# column ("5% ") onto the front of a transaction line.
_ICICI_ROW = re.compile(
    r"\d{2}/\d{2}/\d{4}[ \t]+\d{6,}[ \t]+.*?[ \t]+-?\d+[ \t]+"
    r"([\d,]+\.\d{2})([ \t]+CR)?[ \t\r]*$",
    re.M,
)
_ICICI_SUMMARY_LABEL = (
    "Previous Balance Purchases / Charges Cash Advances Payments / Credits"
)


def icici_rows(text: str) -> Rows:
    found = list(_ICICI_ROW.finditer(text))
    return Rows(
        debits=[_amount(m.group(1)) for m in found if not m.group(2)],
        credits=[_amount(m.group(1)) for m in found if m.group(2)],
    )


def icici_totals(text: str) -> Totals:
    line = _line_after(text, _ICICI_SUMMARY_LABEL)
    amounts = [_amount(m) for m in re.findall(r"`[ \t]*([\d,]+\.\d{2})", line)]
    return Totals(purchases=amounts[1], payments=amounts[3])


# ---------------------------------------------------------------------------
# SBI -- a trailing "C" or "D" marks the direction
# ---------------------------------------------------------------------------
_SBI_ROW = re.compile(
    r"^[ \t]*\d{1,2}[ \t]+[A-Z][a-z]{2}[ \t]+\d{2}[ \t]+.*?[ \t]+"
    r"([\d,]+\.\d{2})[ \t]+([CD])[ \t\r]*$",
    re.M,
)
_SBI_SUMMARY_LABEL = "( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )"


def sbi_rows(text: str) -> Rows:
    found = list(_SBI_ROW.finditer(text))
    return Rows(
        debits=[_amount(m.group(1)) for m in found if m.group(2) == "D"],
        credits=[_amount(m.group(1)) for m in found if m.group(2) == "C"],
    )


def sbi_totals(text: str) -> Totals:
    """Columns are: previous balance, credits, purchases & other debits,
    fees/taxes/interest, total outstanding."""
    line = _line_after(text, _SBI_SUMMARY_LABEL)
    amounts = [_amount(m) for m in re.findall(r"([\d,]+\.\d{2})", line)]
    return Totals(purchases=amounts[2], payments=amounts[1])


SHAPES = {
    "hdfc": (hdfc_rows, hdfc_totals),
    "icici": (icici_rows, icici_totals),
    "sbi": (sbi_rows, sbi_totals),
}

FIXTURE_ISSUER = {
    "hdfc_sample.txt": "hdfc",
    "icici_sample.txt": "icici",
    "sbi_sample.txt": "sbi",
}
