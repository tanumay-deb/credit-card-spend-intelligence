"""What each transaction adds to spend, and which purchase a refund cancels.

Spend follows what is billed. A charge -- purchase, EMI instalment, fee, GST,
interest -- adds its amount. A refund subtracts it, and takes the category of
the purchase it cancels, so an EMI purchase and the credit that converts it net
to zero inside EMI, and a return comes off its own category. Payments, cashback
and returned autopays add nothing.

Matching is by card and exact amount, the purchase dated on or before the
refund and at most 90 days earlier, nearest first, each purchase used once. A
refund that matches nothing keeps its own category and is only counted.
"""
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal

from creditcard.models import Transaction

CHARGE_TYPES = frozenset({"purchase", "emi", "fee", "interest"})
REFUND_WINDOW = timedelta(days=90)
REVERSAL_WINDOW = timedelta(days=10)


@dataclass(frozen=True)
class SpendReport:
    transactions: list[Transaction]
    unmatched_refunds: int
    unmatched_reversals: int


def spend_amount(txn: Transaction) -> Decimal | None:
    if txn.direction == "debit" and txn.txn_type in CHARGE_TYPES:
        return txn.amount
    if txn.direction == "credit" and txn.txn_type == "refund":
        return -txn.amount
    return None


def apply_spend(transactions: list[Transaction]) -> SpendReport:
    """Set spend_amount everywhere and file each refund with its purchase."""
    by_id = {t.txn_id: replace(t, spend_amount=spend_amount(t)) for t in transactions}
    rows = list(by_id.values())

    charges = [t for t in rows if t.direction == "debit" and t.txn_type in CHARGE_TYPES]
    refunds = [t for t in rows if t.direction == "credit" and t.txn_type == "refund"]
    unmatched_refunds = 0
    used: set[str] = set()
    for refund in sorted(refunds, key=lambda t: (t.txn_date, t.txn_id)):
        purchase = _nearest_before(refund, charges, used, REFUND_WINDOW)
        if purchase is None:
            unmatched_refunds += 1
            continue
        used.add(purchase.txn_id)
        by_id[refund.txn_id] = replace(
            by_id[refund.txn_id],
            category_group=purchase.category_group,
            subcategory=purchase.subcategory,
            category_source="refund",
        )

    payments = [t for t in rows if t.direction == "credit" and t.txn_type == "payment"]
    reversals = [t for t in rows if t.txn_type == "payment_reversal"]
    unmatched_reversals = 0
    used_payments: set[str] = set()
    for reversal in sorted(reversals, key=lambda t: (t.txn_date, t.txn_id)):
        payment = _nearest_before(reversal, payments, used_payments, REVERSAL_WINDOW)
        if payment is None:
            unmatched_reversals += 1
        else:
            used_payments.add(payment.txn_id)

    return SpendReport(
        transactions=[by_id[t.txn_id] for t in transactions],
        unmatched_refunds=unmatched_refunds,
        unmatched_reversals=unmatched_reversals,
    )


def _nearest_before(
    txn: Transaction, candidates: list[Transaction], used: set[str], window: timedelta
) -> Transaction | None:
    """The unused candidate on txn's card for the same amount, dated on or
    before it and within the window. Nearest wins; the same merchant breaks a
    tie."""
    best = None
    for candidate in candidates:
        if (candidate.txn_id in used or candidate.card_id != txn.card_id
                or candidate.amount != txn.amount):
            continue
        gap = txn.txn_date - candidate.txn_date
        if gap < timedelta(0) or gap > window:
            continue
        key = (gap, candidate.merchant_clean != txn.merchant_clean, candidate.txn_id)
        if best is None or key < best[0]:
            best = (key, candidate)
    return best[1] if best else None
