"""Integrity checks. These catch the failures PDF parsing makes silently."""
from collections import defaultdict
from decimal import Decimal
from itertools import pairwise

from creditcard.models import Payment, StatementSummary, Transaction


def sum_check(transactions: list[Transaction], statement: StatementSummary) -> Decimal:
    """Parsed debit rows minus what the statement says it billed this cycle.

    Zero means the parser captured every row. Negative means rows were dropped;
    positive means rows were duplicated.

    What it billed is the purchases figure plus the finance charges. Issuers
    list interest and bank charges as rows but may total them outside
    purchases -- HDFC as Finance Charges, SBI in its "Fee, Taxes & Interest
    Charges" column -- so comparing rows with purchases alone flagged every
    statement that carried a charge while its rows were right. ICICI folds
    them into purchases and reports no separate figure, which this also covers.

    Reconciles on DIRECTION, not txn_type. Whichever figure an issuer totals a
    debit in, it is still a debit row, so matching on direction keeps this check
    independent of classification: reclassifying GST from purchase to fee,
    which is semantically right, would otherwise have silently broken
    reconciliation on every statement. It did, once, which is how this was
    found.
    """
    parsed = sum(
        (t.amount for t in transactions if t.direction == "debit"),
        Decimal("0"),
    )
    return parsed - (statement.purchases + statement.finance_charges)


def find_missing_cycles(
    statements: list[StatementSummary], skip: set[str] | frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """Return (card_id, 'YYYY-MM') for months with no statement between the
    earliest and latest statement held for that card. Cards in `skip` are
    billed only in months they are used, so their gaps are expected."""
    by_card: dict[str, list[StatementSummary]] = defaultdict(list)
    for stmt in statements:
        if stmt.card_id not in skip:
            by_card[stmt.card_id].append(stmt)

    missing: list[tuple[str, str]] = []
    for card_id, card_statements in sorted(by_card.items()):
        months = sorted({(s.statement_date.year, s.statement_date.month)
                         for s in card_statements})
        if len(months) < 2:
            continue
        (start_y, start_m), (end_y, end_m) = months[0], months[-1]
        cursor = start_y * 12 + (start_m - 1)
        end = end_y * 12 + (end_m - 1)
        present = {y * 12 + (m - 1) for y, m in months}
        while cursor <= end:
            if cursor not in present:
                missing.append((card_id, f"{cursor // 12:04d}-{cursor % 12 + 1:02d}"))
            cursor += 1
    return missing


def check_payments(
    statements: list[StatementSummary], payments: list[Payment]
) -> list[tuple[str, str, Decimal, Decimal]]:
    """Compare logged payments against each statement's own payments figure.

    Returns (card_id, statement_id, logged, reported) for every mismatch.

    A statement's payments figure covers the window since the previous
    statement, so the earliest statement held for a card is skipped -- there is
    no earlier boundary to bound the window with, and guessing one would
    manufacture false mismatches on the first import.
    """
    by_card: dict[str, list[StatementSummary]] = defaultdict(list)
    for stmt in statements:
        by_card[stmt.card_id].append(stmt)
    # An empty log means payments are not being recorded for that card, not
    # that none were made, so only cards with something logged are checked.
    logged_cards = {p.card_id for p in payments}

    mismatches: list[tuple[str, str, Decimal, Decimal]] = []
    for card_id, card_statements in sorted(by_card.items()):
        if card_id not in logged_cards:
            continue
        ordered = sorted(card_statements, key=lambda s: s.statement_date)
        card_payments = [p for p in payments if p.card_id == card_id]
        for previous, current in pairwise(ordered):
            logged = sum(
                (
                    p.amount
                    for p in card_payments
                    if previous.statement_date < p.payment_date <= current.statement_date
                ),
                Decimal("0"),
            )
            if logged != current.payments:
                mismatches.append(
                    (card_id, current.statement_id, logged, current.payments)
                )
    return mismatches
