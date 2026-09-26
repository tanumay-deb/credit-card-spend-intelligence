from datetime import date
from decimal import Decimal

from creditcard.categorise import build_review_queue, resolve_category
from creditcard.models import Rule, Transaction

CATEGORIES = {
    "Swiggy": "Food",
    "Amazon": "Shopping",
    "Groceries": "Food",
    "Uncategorised": "Others",
}
RULES = [Rule(10, "SWIGGY", "Swiggy"), Rule(20, "AMAZON", "Amazon")]


def test_merchant_map_wins_over_rules():
    group, sub, source = resolve_category("SWIGGY", {"SWIGGY": "Groceries"}, RULES, CATEGORIES)
    assert (group, sub, source) == ("Food", "Groceries", "map")


def test_rule_matches_within_a_longer_merchant_name():
    group, sub, source = resolve_category("SWIGGY INSTAMART", {}, RULES, CATEGORIES)
    assert (group, sub, source) == ("Food", "Swiggy", "rule")


def test_first_matching_rule_wins():
    """Rules arrive priority-sorted from load_rules; the first match wins.

    load_rules does the sorting (covered by its own test); resolve_category
    honours the order it is handed rather than re-sorting per transaction.
    """
    rules = [Rule(10, "AMAZON PAY", "Swiggy"), Rule(20, "PAY", "Amazon")]
    _, sub, _ = resolve_category("AMAZON PAY LATER", {}, rules, CATEGORIES)
    assert sub == "Swiggy"


def test_rule_pattern_does_not_match_inside_a_word():
    """OLA is a seed rule, and CHOCOLATE contains OLA."""
    rules = [Rule(10, "OLA", "Cabs")]
    cats = {"Cabs": "Travel", "Uncategorised": "Others"}
    _, sub, source = resolve_category("CHOCOLATE FACTORY", {}, rules, cats)
    assert (sub, source) == ("Uncategorised", "unmatched")


def test_long_brand_with_a_city_glued_on_still_matches():
    """Issuers run the city into the name: ZOMATONEWDELHI, AMAZONGURGOAN,
    MICROSOFTRS. A brand of six letters or more is distinctive enough to match
    with letters trailing it."""
    rules = [Rule(10, "ZOMATO", "Zomato")]
    cats = {"Zomato": "Food", "Uncategorised": "Others"}
    _, sub, source = resolve_category("ZOMATONEWDELHI", {}, rules, cats)
    assert (sub, source) == ("Zomato", "rule")


def test_short_pattern_still_needs_a_boundary_after_it():
    """LIC is a seed rule for insurance, and LICIOUS sells meat."""
    rules = [Rule(10, "LIC", "Insurance")]
    cats = {"Insurance": "Bills", "Uncategorised": "Others"}
    _, sub, source = resolve_category("LICIOUS", {}, rules, cats)
    assert (sub, source) == ("Uncategorised", "unmatched")


def test_long_pattern_still_needs_a_boundary_before_it():
    """Letters may trail a brand, never lead it: a brand starts a name."""
    rules = [Rule(10, "AMAZON", "Amazon")]
    _, _, source = resolve_category("PAMAZONIA", {}, rules, CATEGORIES)
    assert source == "unmatched"


def test_emi_rows_form_their_own_category():
    """EMI booking and instalment rows carry the bank's wording rather than a
    merchant a rule could place, so they are a category of their own."""
    from creditcard.categorise import category_for_type
    assert category_for_type("emi") == "EMI"


def test_unrecognised_upi_payment_goes_to_upi():
    """UPI payments to people and small shops match no rule; they get their own
    bucket instead of flooding Uncategorised."""
    cats = {"UPI": "UPI", "Uncategorised": "Others"}
    assert resolve_category("UPI MR SOME PAYEE", {}, [], cats) == ("UPI", "UPI", "upi")


def test_a_rule_still_beats_the_upi_fallback():
    """UPI is how something was paid for, not what was bought."""
    rules = [Rule(10, "SUBWAY", "Restaurants")]
    cats = {"Restaurants": "Food", "UPI": "UPI", "Uncategorised": "Others"}
    assert resolve_category("UPI TFS SUBWAY", {}, rules, cats)[:2] == ("Food", "Restaurants")


def test_returned_autopays_are_filed_with_payments():
    from creditcard.categorise import category_for_type
    assert category_for_type("payment_reversal") == "Misc"


def test_rule_pattern_with_regex_characters_is_treated_literally():
    """A user may type anything into the rules file; it must not crash."""
    rules = [Rule(10, "C++ ACADEMY", "Misc")]
    cats = {"Misc": "Others", "Uncategorised": "Others"}
    _, sub, _ = resolve_category("C++ ACADEMY FEES", {}, rules, cats)
    assert sub == "Misc"


def test_unmatched_falls_through_to_uncategorised():
    group, sub, source = resolve_category("SOME NEW SHOP", {}, RULES, CATEGORIES)
    assert (group, sub, source) == ("Others", "Uncategorised", "unmatched")


def _txn(merchant, amount, source="unmatched", sub="Uncategorised"):
    return Transaction(
        txn_id="x" + merchant + str(amount), card_id="c1", statement_id="s1",
        txn_date=date(2026, 8, 1), posting_date=None,
        merchant_raw=merchant, merchant_clean=merchant,
        amount=Decimal(amount), direction="debit", txn_type="purchase",
        category_group="Others", subcategory=sub, category_source=source,
        is_forex=False, forex_ccy=None, forex_amount=None, source_file="f.pdf",
    )


def test_review_queue_only_contains_unmatched():
    rows = build_review_queue([_txn("NEW SHOP", "100"), _txn("SWIGGY", "50", "rule", "Swiggy")])
    assert [r["merchant_clean"] for r in rows] == ["NEW SHOP"]


def test_review_queue_aggregates_and_sorts_by_value():
    rows = build_review_queue([
        _txn("SMALL", "10"), _txn("BIG", "500"), _txn("BIG", "500"),
    ])
    assert rows[0]["merchant_clean"] == "BIG"
    assert rows[0]["times_seen"] == 2
    assert rows[0]["total_amount"] == Decimal("1000")


def test_a_zero_amount_row_stays_out_of_the_review_queue():
    """ICICI prints its notice of a bounced autopay as a ₹0 credit."""
    from dataclasses import replace
    from datetime import date
    from decimal import Decimal

    from creditcard.categorise import build_review_queue
    from creditcard.models import Transaction

    base = Transaction(
        txn_id="t1", card_id="c1", statement_id="s1", txn_date=date(2026, 9, 1),
        posting_date=None, merchant_raw="X", merchant_clean="AUTO DR RETN INSUFF FUND",
        amount=Decimal("0.00"), direction="credit", txn_type="refund",
        category_group="Others", subcategory="Uncategorised", category_source="unmatched",
        is_forex=False, forex_ccy=None, forex_amount=None, source_file="f.pdf",
    )
    real = replace(base, txn_id="t2", merchant_clean="SOME SHOP", amount=Decimal("120"))
    assert [row["merchant_clean"] for row in build_review_queue([base, real])] == ["SOME SHOP"]
