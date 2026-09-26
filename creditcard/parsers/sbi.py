"""SBI Card credit card statement parser.

Transaction rows look like::

    05 Aug 26 PAYMENT RECEIVED 1234567DKT9VMENSWPU9H 500.00 C
    07 Aug 26 NETFLIX MUMBAI MAH 199.00 D

The date is ``DD Mon YY`` with spaces -- no slashes. A pattern built around
``DD/MM/YYYY`` finds nothing at all in an SBI statement, which is how this
difference was caught rather than shipped. **A trailing ``C`` or ``D`` states
the direction explicitly**, unlike HDFC's ``+`` prefix or ICICI's ``CR``
suffix.

Some rows carry no date of their own -- the GST line under a fee, or a charge
listed straight after another row::

    03 Oct 25 ANNUAL FEE (EXCL TAX 179.82) 999.00 D
    IGST DB @ 18.00% 179.82 D

They belong with the row above, so an undated line that directly continues a
run of rows takes its date. Only a direct continuation: an amount elsewhere on
the page that happens to end in C or D is not a transaction. Reading dated
lines alone dropped every such GST line, which is why statements carrying a fee
never reconciled.

Transaction years are two digits; the header dates are four. Both are handled
separately below rather than by one loose pattern, because a two-digit year
matched against a four-digit field silently truncates the century.

The summary is a flattened 2D table whose values land on the line below their
heading::

    Credit Limit ( ` ) (including cash) Cash Limit( ` ) ... Statement Date
    2,00,000.00 60,000.00 03 Sep 2026
    ...
    ( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )
    1,021.99 5,635.40 8,129.33 9.84 3,666.36

That last row is previous balance, credits, purchases and other debits, fees
and interest, then total outstanding -- so `purchases` is the third column and
`payments` the second.
"""
import re
from datetime import date
from decimal import Decimal

from creditcard.models import Card, ParseResult, StatementSummary, Transaction
from creditcard.normalise import classify_txn_type, clean_merchant, make_txn_ids
from creditcard.parsers.base import month_num, safe_date, to_decimal

# Text may follow the C/D marker: an overdue statement prints a notice beside its
# charges, sharing their lines. That text belongs to the other column and is
# dropped -- but it may not hold an amount of its own, so a row can never be
# read from an earlier figure while a later one on the line goes unread.
_TRAILER = r"(?:[ \t]+(?:(?!\d\.\d\d).)*)?"
_TXN_RE = re.compile(
    r"^[ \t]*(\d{1,2})[ \t]+([A-Za-z]{3})[ \t]+(\d{2})[ \t]+(.*?)[ \t]+"
    r"([\d,]+\.\d{2})[ \t]+([CD])" + _TRAILER + r"[ \t\r]*$",
    re.M,
)
_UNDATED_RE = re.compile(
    r"^[ \t]*(\S.*?)[ \t]+([\d,]+\.\d{2})[ \t]+([CD])" + _TRAILER + r"[ \t\r]*$"
)

_AMOUNT_RE = re.compile(r"(\d[\d,]*\.\d{2})")
_HEADER_DATE_RE = re.compile(r"(\d{1,2})[ \t]+([A-Za-z]{3})[a-z]*[ \t]+(\d{4})")
_PERIOD_RE = re.compile(
    r"Statement Period:[ \t]*(\d{1,2})[ \t]+([A-Za-z]{3})[ \t]+(\d{2})"
    r"[ \t]+to[ \t]+(\d{1,2})[ \t]+([A-Za-z]{3})[ \t]+(\d{2})"
)

def _label(text: str) -> re.Pattern[str]:
    """A heading matcher that tolerates SBI's inconsistent spacing.

    The two SBI cards print the same headings with different whitespace --
    "Credit Limit ( ` )" on one, "Credit Limit( ` )" on the other, and the
    same for the minimum-due line. A literal match works on one card and
    fails on the other, which is how this was found: same parser, same
    issuer, one statement reconciling and one refusing to parse at all.

    Brackets vary too: one statement prints "Interest Charges ( ` )" where the
    rest print "Interest Charges( ` )". So a bracket is a token of its own, and
    whitespace is optional between every pair of tokens whether or not the
    template shows a space there.
    """
    tokens = re.findall(r"[()]|[^\s()]+", text)
    return re.compile(r"\s*".join(re.escape(t) for t in tokens))


_LIMITS_LABEL = _label("Credit Limit ( ` ) (including cash)")
_AVAILABLE_LABEL = _label("Available Credit Limit ( ` )")
_ACCOUNT_SUMMARY_LABEL = _label("( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )")
_TOTAL_DUE_LABEL = _label("Total Amount Due ( ` )")
# No closing bracket: some PRIME statements print it at the end of the STMT No.
# line that follows, after the statement number.
_MIN_DUE_LABEL = _label("Minimum Amount Due ( `")


def _two_digit_year(yy: str) -> int:
    return 2000 + int(yy)


def _find(text: str, label: re.Pattern[str], what: str) -> re.Match[str]:
    m = label.search(text)
    if not m:
        raise ValueError(f"sbi parser: could not find {what}")
    return m


def _amount_after(text: str, label: re.Pattern[str], what: str) -> Decimal:
    """First amount at or after a label.

    Not simply "the next line": the minimum-due heading is followed by a
    ``STMT No.`` line before its figure arrives. Searching for the next
    amount-shaped token skips that, and the statement number cannot be
    mistaken for one because it carries no decimal point.
    """
    m = _AMOUNT_RE.search(text, _find(text, label, what).end())
    if not m:
        raise ValueError(f"sbi parser: no amount after {what}")
    return to_decimal(m.group(1))


def _values_below(text: str, label: re.Pattern[str], what: str) -> str:
    """The line of figures under a heading.

    Normally the very next line. Some PRIME statements wrap the heading and
    push its closing ")" onto a line of its own, which left the figures one
    line further down than expected -- so take the first of the next few lines
    that carries an amount.
    """
    for line in text[_find(text, label, what).end():].splitlines()[1:4]:
        if _AMOUNT_RE.search(line):
            return line
    raise ValueError(f"sbi parser: no figures below the {what}")


def _date_in(line: str, what: str) -> date:
    m = _HEADER_DATE_RE.search(line)
    if not m:
        raise ValueError(f"sbi parser: no date in the {what}")
    day, mon, year = m.groups()
    return safe_date(int(year), month_num(mon), int(day))


def _parse_period(text: str, fallback):
    m = _PERIOD_RE.search(text)
    if not m:
        return fallback
    d1, m1, y1, d2, m2, y2 = m.groups()
    return (
        safe_date(_two_digit_year(y1), month_num(m1), int(d1)),
        safe_date(_two_digit_year(y2), month_num(m2), int(d2)),
    )


def _parse_summary(text: str, card: Card, source_file: str) -> StatementSummary:
    limits_line = _values_below(text, _LIMITS_LABEL, "credit limit block")
    available_line = _values_below(text, _AVAILABLE_LABEL, "available credit block")

    statement_date = _date_in(limits_line, "credit limit block")
    # An overdue account's due date reads IMMEDIATE: the amount is due now.
    if "IMMEDIATE" in available_line.upper() and not _HEADER_DATE_RE.search(available_line):
        due_date = statement_date
    else:
        due_date = _date_in(available_line, "available credit block")

    limits = [to_decimal(a) for a in _AMOUNT_RE.findall(limits_line)]
    available = [to_decimal(a) for a in _AMOUNT_RE.findall(available_line)]

    summary = [to_decimal(a) for a in _AMOUNT_RE.findall(
        _values_below(text, _ACCOUNT_SUMMARY_LABEL, "account summary"))]
    if len(summary) < 5:
        raise ValueError(
            f"sbi parser: expected 5 columns in the account summary, got {len(summary)}"
        )
    previous_balance, payments, purchases, fees, _total = summary[:5]

    return StatementSummary(
        statement_id=f"{card.card_id}_{statement_date.isoformat()}",
        card_id=card.card_id,
        statement_date=statement_date,
        due_date=due_date,
        period_start=statement_date,
        period_end=statement_date,
        previous_balance=previous_balance,
        payments=payments,
        purchases=purchases,
        total_due=_amount_after(text, _TOTAL_DUE_LABEL, "total amount due"),
        min_due=_amount_after(text, _MIN_DUE_LABEL, "minimum amount due"),
        finance_charges=fees,
        late_fee=Decimal("0"),
        credit_limit=limits[0] if limits else Decimal("0"),
        available_limit=available[0] if available else Decimal("0"),
        source_file=source_file,
    )


def _parse_transactions(
    text: str, card: Card, statement_id: str, source_file: str
) -> list[Transaction]:
    rows = []
    run_date = None  # date of the run of rows the next line could continue
    for line in text.splitlines():
        dated = _TXN_RE.match(line)
        undated: re.Match[str] | None = (
            None if dated or run_date is None else _UNDATED_RE.match(line)
        )
        if dated:
            dd, mon, yy, desc, amount_s, marker = dated.groups()
            run_date = safe_date(_two_digit_year(yy), month_num(mon), int(dd))
        elif undated and run_date is not None:
            desc, amount_s, marker = undated.groups()
        else:
            run_date = None
            continue
        rows.append((
            run_date,
            to_decimal(amount_s),
            desc.strip(),
            "credit" if marker == "C" else "debit",
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


class SbiParser:
    name = "sbi"

    def parse(self, text: str, card: Card, source_file: str) -> ParseResult:
        statement = _parse_summary(text, card, source_file)
        start, end = _parse_period(
            text, (statement.statement_date, statement.statement_date)
        )
        statement = StatementSummary(
            **{**{f: getattr(statement, f) for f in statement.__dataclass_fields__},
               "period_start": start, "period_end": end}
        )
        return ParseResult(
            statement=statement,
            transactions=_parse_transactions(
                text, card, statement.statement_id, source_file
            ),
        )


sbi_parser = SbiParser()
