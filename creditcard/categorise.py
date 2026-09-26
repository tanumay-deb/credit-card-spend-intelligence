"""Category resolution and the review queue that shrinks Uncategorised."""
import re
from collections import defaultdict
from decimal import Decimal
from functools import cache

from creditcard.models import Rule, Transaction

UNCATEGORISED = "Uncategorised"
UNCATEGORISED_GROUP = "Others"
UPI = "UPI"

# Rows whose category comes from what they ARE, not who they are with. A GST
# line, a finance charge or a bill payment has no merchant worth tagging, and
# leaving them Uncategorised buries the merchants that do need attention -- and
# inflates the Uncategorised % KPI with rows nobody will ever categorise.
#
# EMI rows are the same. HDFC books a conversion as "EMI <merchant>" and ICICI
# bills instalments as PRINCIPAL AMOUNT AMORTIZATION, so a merchant rule rarely
# places them -- they were most of Uncategorised. EMI is a category of its own.
TYPE_SUBCATEGORY = {
    "fee": "Fees & Interest",
    "interest": "Fees & Interest",
    "payment": "Misc",
    "cashback": "Misc",
    "emi": "EMI",
    "payment_reversal": "Misc",
}


def category_for_type(txn_type: str) -> str | None:
    """Subcategory implied by txn_type, or None to fall through to the merchant.

    purchase and refund are deliberately absent: those DO have a merchant worth
    categorising, and a refund should land in the same category as the purchase
    it reverses.
    """
    return TYPE_SUBCATEGORY.get(txn_type)


# A pattern whose last word has at least this many letters may have letters
# trailing it.
_GLUE_MIN_LETTERS = 6


@cache
def _pattern_re(pattern: str) -> re.Pattern[str]:
    """Compile a rule pattern, letter-bounded and escaped.

    Letter-bounded because plain substring matching would tag CHOCOLATE FACTORY
    as a cab ride -- the seed rules contain OLA. A wrong category is silent and
    permanent; a missed one lands in the review queue where you will see it.

    The trailing bound is dropped for a long final word, though: issuers run the
    city into the name -- ZOMATONEWDELHI, AMAZONGURGOAN -- and a brand of six
    letters or more is distinctive enough to match with letters after it. Short
    patterns keep it, or LIC would tag LICIOUS as insurance. The leading bound
    always stays, because a brand starts a name.

    Escaped because the rules file is hand-edited, and a pattern like "C++"
    would otherwise raise re.error and abort the whole run.

    Cached because this is called once per rule per transaction.
    """
    words = pattern.split()
    last = words[-1] if words else ""
    tail = "" if last.isalpha() and len(last) >= _GLUE_MIN_LETTERS else "(?![A-Z])"
    return re.compile("(?<![A-Z])" + re.escape(pattern) + tail)


def resolve_category(
    merchant_clean: str,
    merchant_map: dict[str, str],
    rules: list[Rule],
    categories: dict[str, str],
) -> tuple[str, str, str]:
    """Return (category_group, subcategory, category_source).

    Order: explicit merchant map, then first matching rule, then UPI for an
    unrecognised UPI payment, then Others/Uncategorised. `rules` must already
    be priority-sorted -- load_rules does that, so the cost is paid once per
    run rather than per transaction.
    """
    key = merchant_clean.upper()

    if key in merchant_map:
        sub = merchant_map[key]
        return categories.get(sub, UNCATEGORISED_GROUP), sub, "map"

    for rule in rules:
        if _pattern_re(rule.pattern).search(key):
            return (
                categories.get(rule.subcategory, UNCATEGORISED_GROUP),
                rule.subcategory,
                "rule",
            )

    # UPI is how something was paid for, not what was bought, so it is a
    # fallback: a UPI payment no map entry or rule recognises -- mostly people
    # and small shops -- goes to UPI instead of flooding Uncategorised.
    if key.startswith("UPI "):
        return categories.get(UPI, UNCATEGORISED_GROUP), UPI, "upi"

    return UNCATEGORISED_GROUP, UNCATEGORISED, "unmatched"


def build_review_queue(transactions: list[Transaction]) -> list[dict]:
    """One row per unrecognised merchant, highest value first."""
    seen: dict[str, dict] = defaultdict(
        lambda: {"times_seen": 0, "total_amount": Decimal("0"), "example_txn_date": None,
                 "example_card": ""}
    )
    for txn in transactions:
        # A zero-amount row, such as ICICI's notice of a bounced autopay, moves no
        # money, so it needs no category.
        if txn.category_source != "unmatched" or txn.amount == 0:
            continue
        entry = seen[txn.merchant_clean]
        entry["times_seen"] += 1
        entry["total_amount"] += txn.amount
        if entry["example_txn_date"] is None:
            entry["example_txn_date"] = txn.txn_date
            entry["example_card"] = txn.card_id

    rows = [{"merchant_clean": name, **data} for name, data in seen.items()]
    return sorted(rows, key=lambda r: r["total_amount"], reverse=True)
