"""HDFC Bank credit card statement parser.

Transaction rows look like::

    06/03/2026| 22:48 PYU*Swiggy FoodBangalore C 184.00 l
    28/03/2026| 19:02 BPPY CC PAYMENT DP1234abcd + C 25,000.00 l

`C` is the rupee glyph as pdfplumber extracts it, not a currency code. A
trailing `l` is the Purchase-Indicator bullet. **A `+` immediately before
`C` is the only credit marker** -- its absence means debit. The table
repeats per page under a `DATE & TIME TRANSACTION DESCRIPTION AMOUNT PI`
header; only lines matching the full row shape are treated as transactions,
so header/page-break lines and unrelated tables (Cash Back Summary, Smart
EMI Loan Summary, GST Summary) are skipped for free.

The statement summary is a 2D table pdfplumber has flattened, with labels
and values landing on different lines and out of order:

    PAYMENTS/CREDITS PURCHASES/DEBIT
    PREVIOUS STATEMENT DUES FINANCE CHARGES TOTAL AMOUNT DUE
    RECEIVED (Current Billing Cycle)
    _ C<total amount due>
    C<previous dues> C<payments> + C<purchases> + C<finance charges> =
    TOTAL CREDIT LIMIT
    (Including Cash) AVAILABLE CREDIT LIMIT AVAILABLE CASH LIMIT MINIMUM DUE DUE DATE
    C<minimum due> <due date, e.g. 18 Apr, 2026>
    C<credit limit> C<available credit> C<available cash>

The physical line breaks in that block differ between cards (observed
directly on hdfc_millennia vs hdfc_regalia), so nothing here is keyed off line
numbers. Instead we anchor on the fixed label text "PAYMENTS/CREDITS" and
then read the amounts and the one date in this block off in the order the
statement always prints them -- which is stable across both cards even
though the line wrapping is not.
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from creditcard.categorise import UNCATEGORISED, UNCATEGORISED_GROUP
from creditcard.models import (
    Card,
    EmiPlan,
    ParseResult,
    StatementSummary,
    Transaction,
)
from creditcard.normalise import classify_txn_type, clean_merchant, make_txn_ids

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# DD/MM/YYYY| HH:MM <description> [+] C <amount> l
_TXN_RE = re.compile(
    r"^(\d{2})/(\d{2})/(\d{4})\|\s*\d{2}:\d{2}\s+"
    r"(.*?)\s*(\+)?\s*C\s*([\d,]+\.\d{2})\s*l\s*$"
)

# One token of the summary block: either a rupee amount (C1,234.56, or
# C7,94,142 with no paise) or a "DD Mon, YYYY"-shaped date.
_SUMMARY_TOKEN_RE = re.compile(
    r"C\s*(\d[\d,]*(?:\.\d{1,2})?)"
    r"|(\d{1,2})\s+([A-Za-z]{3})[a-zA-Z]*,?\s*(\d{2,4})"
)

_DATE_LABEL_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-zA-Z]*,?\s*(\d{2,4})")


def _month_num(abbrev: str) -> int:
    return _MONTHS[abbrev[:3].lower()]


def _safe_date(year: int, month: int, day: int) -> date:
    """Build a date from DD/MM/YYYY components without assuming they are
    already in range.

    Every genuine statement's dates are valid, so the plain `date(y, m, d)`
    construction below is what actually runs in production and is used
    whenever it succeeds. The fallback exists purely so that one
    malformed/out-of-range field -- the only source of this text is
    pdfplumber's own extraction -- can never take down parsing of an entire
    statement; on failure it rolls the day/month forward the way spreadsheet
    date arithmetic does, which always yields *some* valid date.
    """
    try:
        return date(year, month, day)
    except ValueError:
        total_months = year * 12 + (month - 1)
        norm_year, norm_month0 = divmod(total_months, 12)
        return date(norm_year, norm_month0 + 1, 1) + timedelta(days=day - 1)


def _find_or_raise(text: str, label: str) -> int:
    idx = text.find(label)
    if idx == -1:
        raise ValueError(f"hdfc parser: could not find {label!r} in statement text")
    return idx


def _parse_label_date(text: str, label: str) -> date:
    idx = _find_or_raise(text, label)
    m = _DATE_LABEL_RE.search(text, idx + len(label))
    if not m:
        raise ValueError(f"hdfc parser: no date found after {label!r}")
    day, mon, year = m.groups()
    return _safe_date(int(year), _month_num(mon), int(day))


def _parse_period(text: str) -> tuple[date, date]:
    idx = _find_or_raise(text, "Billing Period")
    matches = list(_DATE_LABEL_RE.finditer(text, idx + len("Billing Period")))
    if len(matches) < 2:
        raise ValueError("hdfc parser: expected two dates after 'Billing Period'")
    (d1, mon1, y1), (d2, mon2, y2) = (m.groups() for m in matches[:2])
    start = _safe_date(int(y1), _month_num(mon1), int(d1))
    end = _safe_date(int(y2), _month_num(mon2), int(d2))
    return start, end


def _summary_block(text: str) -> str:
    """The slice of text spanning the flattened summary table.

    Bounded at the start by the fixed label 'PAYMENTS/CREDITS' and at the
    end by whichever comes first of 'Purchase Indicator' or the transaction
    table's 'DATE & TIME' header (one card's statement carries the former,
    the other does not) -- both of these sit safely after the summary block
    and before any transaction row, so the amounts collected in between can
    never spill into actual transaction data even if a label goes missing.
    """
    start = _find_or_raise(text, "PAYMENTS/CREDITS")
    end_candidates = [
        i for i in (text.find("Purchase Indicator", start), text.find("DATE & TIME", start))
        if i != -1
    ]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _parse_summary(text: str, card: Card, source_file: str) -> StatementSummary:
    statement_date = _parse_label_date(text, "Statement Date")
    period_start, period_end = _parse_period(text)

    block = _summary_block(text)
    amounts: list[Decimal] = []
    due_date_parts: tuple[int, int, int] | None = None
    for m in _SUMMARY_TOKEN_RE.finditer(block):
        amount_s, day, mon, year = m.groups()
        if amount_s is not None:
            amounts.append(Decimal(amount_s.replace(",", "")))
        elif due_date_parts is None:
            due_date_parts = (int(year), _month_num(mon), int(day))

    if len(amounts) < 9:
        raise ValueError(
            "hdfc parser: expected 9 amounts in the summary block "
            f"(total due, previous balance, payments, purchases, finance "
            f"charges, minimum due, credit limit, available credit, "
            f"available cash), found {len(amounts)}"
        )
    if due_date_parts is None:
        raise ValueError("hdfc parser: could not find the due date in the summary block")

    (total_due, previous_balance, payments, purchases, finance_charges,
     min_due, credit_limit, available_limit, _available_cash) = amounts[:9]
    due_date = _safe_date(*due_date_parts)

    statement_id = f"{card.card_id}_{statement_date.isoformat()}"

    return StatementSummary(
        statement_id=statement_id,
        card_id=card.card_id,
        statement_date=statement_date,
        due_date=due_date,
        period_start=period_start,
        period_end=period_end,
        previous_balance=previous_balance,
        payments=payments,
        purchases=purchases,
        total_due=total_due,
        min_due=min_due,
        finance_charges=finance_charges,
        # Not printed anywhere in the observed HDFC layout as its own
        # labelled figure (the "Past Dues (if any)" aging table is a
        # different breakdown entirely) -- zero is the correct value on
        # every statement that has not actually incurred one, and there is
        # no field to read a real one from if it ever does.
        late_fee=Decimal("0"),
        credit_limit=credit_limit,
        available_limit=available_limit,
        source_file=source_file,
    )


_REF_TAIL_RE = re.compile(r"^[\d\s]*\)$")
_REF_HEAD_RE = re.compile(r"\s*\(Ref#\s*$")


def _description_above(lines: list[str], index: int) -> str:
    """Recover a description that wrapped onto the line above the amount.

    The Millennia card carries an extra "Base NeuCoins" column, which pushes the
    description off the amount's line entirely; the Swiggy card keeps it inline.
    Same issuer, same parser, two layouts -- so when the amount line has no
    description, look upward for it.

    Refuses anything that is itself a transaction row, a column header, or the
    previous row's wrapped reference number, so a row can never steal its
    neighbour's description.
    """
    for j in range(index - 1, max(index - 4, -1), -1):
        candidate = lines[j].strip()
        if not candidate or _REF_TAIL_RE.match(candidate):
            continue
        if _TXN_RE.match(candidate) or "DATE & TIME" in candidate:
            break
        return _REF_HEAD_RE.sub("", candidate)
    return ""


def _parse_transactions(
    text: str, card: Card, statement_id: str, source_file: str
) -> list[Transaction]:
    lines = text.splitlines()
    parsed_rows = []
    for index, line in enumerate(lines):
        m = _TXN_RE.match(line.strip())
        if not m:
            continue
        dd, mm, yyyy, desc, credit_flag, amount_s = m.groups()
        txn_date = _safe_date(int(yyyy), int(mm), int(dd))
        amount = Decimal(amount_s.replace(",", ""))
        direction = "credit" if credit_flag else "debit"
        merchant_raw = desc.strip() or _description_above(lines, index)
        parsed_rows.append((txn_date, amount, merchant_raw, direction))

    txn_ids = make_txn_ids(
        card.card_id, [(d, a, m) for d, a, m, _ in parsed_rows]
    )

    transactions = []
    for txn_id, (txn_date, amount, merchant_raw, direction) in zip(txn_ids, parsed_rows, strict=True):
        merchant_clean = clean_merchant(merchant_raw)
        txn_type = classify_txn_type(merchant_clean, direction)
        transactions.append(
            Transaction(
                txn_id=txn_id,
                card_id=card.card_id,
                statement_id=statement_id,
                txn_date=txn_date,
                posting_date=None,
                merchant_raw=merchant_raw,
                merchant_clean=merchant_clean,
                amount=amount,
                direction=direction,
                txn_type=txn_type,
                category_group=UNCATEGORISED_GROUP,
                subcategory=UNCATEGORISED,
                category_source="unmatched",
                is_forex=False,
                forex_ccy=None,
                forex_amount=None,
                source_file=source_file,
            )
        )
    return transactions


# Smart EMI Loan Summary rows, in the three shapes a loan takes over its life:
#   134093939 15/01/2026 C71,442.59 9 Months 15% C42,000.00 C3,200.00 4 Months
#   134093939 15/01/2026 C71,442.59 9 Months 15% C8,120.00 C150.00 1 Month
#   134093939 15/01/2026 C71,442.59 9 Months 15% C8.40 C0.50
# loan no, booked date, amount, tenure, rate p.a., principal outstanding,
# interest payable, months remaining. With one month left "Months" turns
# singular, and in the final billed month the remaining-tenure column is dropped
# entirely. Matching only the first shape made every loan vanish two months
# early -- and the pipeline then marked it closed while it was still billing.
_EMI_RE = re.compile(
    r"^[ \t]*(\d{6,})[ \t]+(\d{2})/(\d{2})/(\d{4})[ \t]+C[ \t]*([\d,]+\.\d{2})"
    r"[ \t]+(\d+)[ \t]+Months?[ \t]+([\d.]+)[ \t]*%[ \t]+C[ \t]*([\d,]+\.\d{2})"
    r"[ \t]+C[ \t]*([\d,]+\.\d{2})(?:[ \t]+(\d+)[ \t]+Months?)?",
    re.M,
)


def _parse_emi_plans(
    text: str, card: Card, statement_id: str, source_file: str
) -> tuple[EmiPlan, ...]:
    """Smart EMI loans, if the statement carries any. Absent on most cycles."""
    plans = []
    for m in _EMI_RE.finditer(text):
        (loan_no, dd, mm, yyyy, amount, tenure, rate,
         principal_out, interest_payable, remaining) = m.groups()
        plans.append(EmiPlan(
            card_id=card.card_id,
            statement_id=statement_id,
            loan_ref=loan_no,
            loan_type="Smart EMI",
            start_date=_safe_date(int(yyyy), int(mm), int(dd)),
            end_date=None,
            principal=Decimal(amount.replace(",", "")),
            tenure_months=int(tenure),
            remaining_months=int(remaining) if remaining else 0,
            interest_rate=Decimal(rate),
            instalment_amount=None,
            interest_payable=Decimal(interest_payable.replace(",", "")),
            outstanding=Decimal(principal_out.replace(",", "")),
            source_file=source_file,
        ))
    return tuple(plans)


class HdfcParser:
    name = "hdfc"

    def parse(self, text: str, card: Card, source_file: str) -> ParseResult:
        statement = _parse_summary(text, card, source_file)
        transactions = _parse_transactions(
            text, card, statement.statement_id, source_file
        )
        return ParseResult(
            statement=statement,
            transactions=transactions,
            emi_plans=_parse_emi_plans(
                text, card, statement.statement_id, source_file
            ),
        )


hdfc_parser = HdfcParser()
