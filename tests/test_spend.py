from datetime import date
from decimal import Decimal

from creditcard.models import Transaction
from creditcard.spend import apply_spend, spend_amount


def _txn(txn_id, when, amount, direction, txn_type, merchant="M", card="c1",
         group="Others", sub="Uncategorised"):
    return Transaction(
        txn_id=txn_id, card_id=card, statement_id="s1", txn_date=when, posting_date=None,
        merchant_raw=merchant, merchant_clean=merchant, amount=Decimal(amount),
        direction=direction, txn_type=txn_type, category_group=group, subcategory=sub,
        category_source="rule", is_forex=False, forex_ccy=None, forex_amount=None,
        source_file="f.pdf",
    )


AUG1, AUG2, AUG10 = date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 10)


def test_charges_count_their_amount():
    for kind in ("purchase", "emi", "fee", "interest"):
        assert spend_amount(_txn("t", AUG1, "100.00", "debit", kind)) == Decimal("100.00")


def test_refunds_count_negative():
    assert spend_amount(_txn("t", AUG1, "40.00", "credit", "refund")) == Decimal("-40.00")


def test_payments_cashback_and_returned_autopays_are_not_spend():
    assert spend_amount(_txn("t", AUG1, "500.00", "credit", "payment")) is None
    assert spend_amount(_txn("t", AUG1, "5.00", "credit", "cashback")) is None
    assert spend_amount(_txn("t", AUG1, "500.00", "debit", "payment_reversal")) is None


def test_emi_purchase_and_its_conversion_credit_net_to_zero_inside_emi():
    """20k debited, credited back on conversion, then billed monthly: the pair
    must cancel inside EMI, leaving only the instalments."""
    purchase = _txn("p", AUG1, "20000.00", "debit", "emi", merchant="EMI LG", group="EMI", sub="EMI")
    credit = _txn("c", AUG10, "20000.00", "credit", "refund", merchant="EMI LG CONVERTED")
    rows = {t.txn_id: t for t in apply_spend([purchase, credit]).transactions}
    assert (rows["c"].category_group, rows["c"].subcategory) == ("EMI", "EMI")
    assert rows["c"].category_source == "refund"
    assert rows["p"].spend_amount + rows["c"].spend_amount == Decimal("0")


def test_each_purchase_cancels_at_most_one_refund_nearest_first():
    older = _txn("old", date(2026, 7, 1), "300.00", "debit", "purchase", group="Shopping", sub="Amazon")
    newer = _txn("new", AUG1, "300.00", "debit", "purchase", group="Shopping", sub="Clothing")
    first = _txn("r1", AUG10, "300.00", "credit", "refund")
    second = _txn("r2", AUG10, "300.00", "credit", "refund")
    rows = {t.txn_id: t for t in apply_spend([older, newer, first, second]).transactions}
    assert rows["r1"].subcategory == "Clothing"
    assert rows["r2"].subcategory == "Amazon"


def test_refund_ignores_later_purchases_other_cards_and_old_ones():
    report = apply_spend([
        _txn("later", date(2026, 8, 20), "50.00", "debit", "purchase", sub="Amazon"),
        _txn("other", AUG1, "50.00", "debit", "purchase", card="c2", sub="Amazon"),
        _txn("old", date(2026, 4, 1), "50.00", "debit", "purchase", sub="Amazon"),
        _txn("r", AUG10, "50.00", "credit", "refund"),
    ])
    rows = {t.txn_id: t for t in report.transactions}
    assert rows["r"].subcategory == "Uncategorised"
    assert report.unmatched_refunds == 1


def test_returned_autopay_without_its_payment_is_reported():
    payment = _txn("pay", AUG1, "900.00", "credit", "payment")
    matched = _txn("rev1", AUG2, "900.00", "debit", "payment_reversal")
    stray = _txn("rev2", AUG2, "750.00", "debit", "payment_reversal")
    assert apply_spend([payment, matched, stray]).unmatched_reversals == 1


def test_rows_come_back_in_their_original_order():
    rows = [_txn("b", AUG2, "1.00", "debit", "purchase"), _txn("a", AUG1, "1.00", "debit", "purchase")]
    assert [t.txn_id for t in apply_spend(rows).transactions] == ["b", "a"]
