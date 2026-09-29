"""ICICI Bank credit card statement parser.

Transaction rows look like::

    02/03/2026 12966547415 Auto Dr.Retn-Insuff. fund 0 0.00 CR
    14/03/2026 12966547419 AMAZON PAY IN E COMMERC BANGALORE 21 1,499.00
    19/03/2026 12966547433 AMAZON PAY IN E COMMERC BANGALORE -11 899.00 CR

Date, then an 11-digit reference, then the description, then a reward-points
integer that may be negative, then the amount. **A trailing ``CR`` is the only
credit marker** -- its absence means debit. Get that wrong and every payment
and refund flips sign.

Rows are matched from the date rather than from the start of the line: on the
first page the flattened two-column layout sometimes bleeds a fragment of the
rewards column ("5% ") onto the front of a transaction line.

The summary is a flattened 2D table. Two of its labels come through with every
letter doubled -- pdfplumber renders bold headings that way -- so
``STATEMENT DATE`` may arrive as ``SSTTAATTEEMMEENNTT DDAATTEE``. The label
patterns below allow each character once or twice for that reason.

The rupee glyph extracts as a backtick here, not as the ``C`` that HDFC
produces.
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from creditcard.emi import add_months
from creditcard.models import (
    Card,
    EmiPlan,
    ParseResult,
    StatementSummary,
    Transaction,
)
from creditcard.normalise import classify_txn_type, clean_merchant, make_txn_ids
from creditcard.parsers.base import (
    DATE_MONTH_FIRST_RE,
    month_num,
    safe_date,
    to_decimal,
)

_TXN_RE = re.compile(
    r"(\d{2})/(\d{2})/(\d{4})[ \t]+\d{6,}[ \t]+(.*?)[ \t]+-?\d+[ \t]+"
    r"([\d,]+\.\d{2})([ \t]+CR)?[ \t\r]*$",
    re.M,
)

_AMOUNT_RE = re.compile(r"`[ \t]*([\d,]+\.\d{2})")

_TOTALS_LABEL = "Previous Balance Purchases / Charges Cash Advances Payments / Credits"
_LIMITS_LABEL = "Credit Limit (Including cash) Available Credit (Including cash)"


def _doubled(label: str) -> re.Pattern[str]:
    """Match a heading whether or not pdfplumber doubled every letter."""
    return re.compile("".join(
        re.escape(ch) + "+" if ch.strip() else r"\s+" for ch in label
    ))


_STATEMENT_DATE_RE = _doubled("STATEMENT DATE")
_DUE_DATE_RE = _doubled("PAYMENT DUE DATE")
_PERIOD_RE = re.compile(
    r"Statement\s+period\s*:\s*([A-Za-z]+\s+\d{1,2},?\s*\d{4})\s+to\s+"
    r"([A-Za-z]+\s+\d{1,2},?\s*\d{4})",
    re.IGNORECASE,
)


def _date_after(text: str, pattern: re.Pattern[str], what: str):
    m = pattern.search(text)
    if not m:
        raise ValueError(f"icici parser: could not find {what} label")
    d = DATE_MONTH_FIRST_RE.search(text, m.end())
    if not d:
        raise ValueError(f"icici parser: no date after {what} label")
    mon, day, year = d.groups()
    return safe_date(int(year), month_num(mon), int(day))


def _amounts_after(text: str, label: str, what: str, search_lines: int = 4) -> list[Decimal]:
    """Amounts from the first line below a heading that actually carries any.

    The values do not reliably sit on the very next line: the credit-limit
    heading is followed by a wrapped sentence ("Interest will be charged if
    your / total amount due is not paid") before its four figures arrive.
    """
    idx = text.find(label)
    if idx == -1:
        raise ValueError(f"icici parser: could not find {what} block")
    for line in text[idx:].splitlines()[1:search_lines + 1]:
        amounts = [to_decimal(m) for m in _AMOUNT_RE.findall(line)]
        if amounts:
            return amounts
    raise ValueError(f"icici parser: no amounts within {search_lines} lines of {what}")


def _amount_after_label(text: str, label: str, what: str) -> Decimal:
    """First rupee amount at or after a label, searching later lines if the
    label's own line carries none -- ICICI puts several of these on the line
    below their heading."""
    idx = text.find(label)
    if idx == -1:
        raise ValueError(f"icici parser: could not find {what!r}")
    m = _AMOUNT_RE.search(text, idx + len(label))
    if not m:
        raise ValueError(f"icici parser: no amount after {what!r}")
    return to_decimal(m.group(1))


def _month_first(value: str) -> date:
    m = DATE_MONTH_FIRST_RE.search(value)
    if m is None:
        raise ValueError(f"icici parser: unreadable date {value!r}")
    mon, day, year = m.groups()
    return safe_date(int(year), month_num(mon), int(day))


def _printed_period(text: str) -> tuple[date, date] | None:
    m = _PERIOD_RE.search(text)
    return (_month_first(m.group(1)), _month_first(m.group(2))) if m else None


def _parse_summary(text: str, card: Card, source_file: str,
                   period: tuple | None) -> StatementSummary:
    statement_date = _date_after(text, _STATEMENT_DATE_RE, "statement date")
    due_date = _date_after(text, _DUE_DATE_RE, "payment due date")
    if period is None:
        # Neither printed nor any rows to take it from - a closed card's last
        # statement can have neither - so it covers the month before its date.
        period = (add_months(statement_date, -1) + timedelta(days=1), statement_date)

    # previous balance, purchases/charges, cash advances, payments/credits
    totals = _amounts_after(text, _TOTALS_LABEL, "spends summary")
    if len(totals) < 4:
        raise ValueError(
            f"icici parser: expected 4 amounts in the spends summary, got {len(totals)}"
        )
    previous_balance, purchases, _cash_advances, payments = totals[:4]

    # credit limit, available credit, cash limit, available cash
    limits = _amounts_after(text, _LIMITS_LABEL, "credit summary")
    credit_limit = limits[0] if limits else Decimal("0")
    available_limit = limits[1] if len(limits) > 1 else Decimal("0")

    return StatementSummary(
        statement_id=f"{card.card_id}_{statement_date.isoformat()}",
        card_id=card.card_id,
        statement_date=statement_date,
        due_date=due_date,
        period_start=period[0],
        period_end=period[1],
        previous_balance=previous_balance,
        payments=payments,
        purchases=purchases,
        total_due=_amount_after_label(text, "Total Amount due", "Total Amount due"),
        min_due=_amount_after_label(text, "Minimum Amount due", "Minimum Amount due"),
        finance_charges=Decimal("0"),
        late_fee=Decimal("0"),
        credit_limit=credit_limit,
        available_limit=available_limit,
        source_file=source_file,
    )


def _parse_transactions(
    text: str, card: Card, statement_id: str, source_file: str
) -> list[Transaction]:
    rows = []
    for m in _TXN_RE.finditer(text):
        dd, mm, yyyy, desc, amount_s, credit_flag = m.groups()
        rows.append((
            safe_date(int(yyyy), int(mm), int(dd)),
            to_decimal(amount_s),
            desc.strip(),
            "credit" if credit_flag else "debit",
        ))

    txn_ids = make_txn_ids(card.card_id, [(d, a, m) for d, a, m, _ in rows])

    transactions = []
    for txn_id, (txn_date, amount, merchant_raw, direction) in zip(txn_ids, rows, strict=True):
        merchant_clean = clean_merchant(merchant_raw)
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
                txn_type=classify_txn_type(merchant_clean, direction),
                category_group="Others",
                subcategory="Uncategorised",
                category_source="unmatched",
                is_forex=False,
                forex_ccy=None,
                forex_amount=None,
                source_file=source_file,
            )
        )
    return transactions


# EMI / PERSONAL LOAN ON CREDIT CARDS rows:
#   Merchant EMI 15/01/2026 15/07/2026 6 60,000.00 4 42,000.00 10,500.00
# type, creation date, finish date, instalments, loan amount, pending
# instalments, outstanding, monthly instalment -- the order the header prints
# them in. Read the other way round, the balance showed one month's payment.
_EMI_RE = re.compile(
    r"^[ 	]*([A-Za-z][A-Za-z /]*?EMI)[ 	]+(\d{2})/(\d{2})/(\d{4})[ 	]+"
    r"(\d{2})/(\d{2})/(\d{4})[ 	]+(\d+)[ 	]+([\d,]+\.\d{2})[ 	]+"
    r"(\d+)[ 	]+([\d,]+\.\d{2})[ 	]+([\d,]+\.\d{2})",
    re.M,
)


def _parse_emi_plans(
    text: str, card: Card, statement_id: str, source_file: str
) -> tuple[EmiPlan, ...]:
    """Instalment plans, if the statement lists any.

    ICICI prints a finish date and the instalment amount but no interest rate;
    HDFC prints the rate but no finish date. Neither is inferred from the
    other -- an absent field stays None.
    """
    plans = []
    for m in _EMI_RE.finditer(text):
        (loan_type, sd, sm, sy, ed, em, ey, tenure,
         principal, pending, outstanding, instalment) = m.groups()
        plans.append(EmiPlan(
            card_id=card.card_id,
            statement_id=statement_id,
            loan_ref=f"{loan_type.strip()} {sd}/{sm}/{sy}",
            loan_type=loan_type.strip(),
            start_date=safe_date(int(sy), int(sm), int(sd)),
            end_date=safe_date(int(ey), int(em), int(ed)),
            principal=to_decimal(principal),
            tenure_months=int(tenure),
            remaining_months=int(pending),
            interest_rate=None,
            instalment_amount=to_decimal(instalment),
            interest_payable=None,
            outstanding=to_decimal(outstanding),
            source_file=source_file,
        ))
    return tuple(plans)


class IciciParser:
    name = "icici"

    def parse(self, text: str, card: Card, source_file: str) -> ParseResult:
        dates = [
            safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            for m in _TXN_RE.finditer(text)
        ]
        # ICICI prints "Statement period : <date> to <date>". Without it, the
        # period is taken from the rows, which only approximates it.
        period = _printed_period(text) or ((min(dates), max(dates)) if dates else None)
        statement = _parse_summary(text, card, source_file, period)
        return ParseResult(
            statement=statement,
            transactions=_parse_transactions(
                text, card, statement.statement_id, source_file
            ),
            emi_plans=_parse_emi_plans(
                text, card, statement.statement_id, source_file
            ),
        )


icici_parser = IciciParser()
