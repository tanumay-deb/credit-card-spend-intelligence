"""Turn raw statement text into groupable, comparable values."""
import hashlib
import re
from datetime import date
from decimal import Decimal

_CITIES = {
    "BANGALORE", "BENGALURU", "MUMBAI", "DELHI", "NEW DELHI", "CHENNAI",
    "KOLKATA", "HYDERABAD", "PUNE", "GURGAON", "GURUGRAM", "NOIDA",
}
_CORPORATE = {"LTD", "LIMITED", "PVT", "PRIVATE", "IN", "INC", "LLP"}
_REF_DIGITS = re.compile(r"\b\d{6,}\b")

# Payment aggregators that prefix the real merchant, as seen on real
# statements: PYU*Swiggy Food, CAS*Swiggy, RSP*SWIGGY DINEOUT, PAYPAL *NUOSHI.
_PROCESSORS = {
    "PYU", "CAS", "RSP", "PAYU", "PAYPAL", "RAZORPAY", "BILLDESK",
    "CCAVENUE", "PAYTM", "IPAY", "SQ", "TST", "WWW",
    "RAZ", "IND", "ING", "EPC", "PTM", "PPSL", "PAY", "WL",
}

# Longest first, so BENGALURU is tried before any shorter city it might contain.
_CITIES_BY_LENGTH = sorted(_CITIES, key=len, reverse=True)


def clean_merchant(raw: str) -> str:
    """Strip processor prefixes, reference numbers, cities and corporate suffixes."""
    if not raw or not raw.strip():
        return "UNKNOWN"

    text = raw.upper().strip()

    # Statements write PROCESSOR*MERCHANT -- "PYU*Swiggy Food" -- where the name
    # is on the right; keeping the left turned every Swiggy order into a merchant
    # called "PYU". They also write MERCHANT*DETAIL -- "GOOGLE*PLAY", "Riot*
    # Dublin IE" -- where both halves matter. So the left is dropped only when it
    # is a known processor code: treating any short prefix as one threw away RIOT.
    if "*" in text:
        prefix, _, rest = text.partition("*")
        prefix = prefix.strip()
        text = rest if rest.strip() and prefix in _PROCESSORS else f"{prefix} {rest}"

    text = _REF_DIGITS.sub(" ", text)
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)

    tokens = text.split()

    # Issuers glue the city straight onto the merchant: SwiggyBengaluru,
    # TECHNOLOGIESBENGALURU. The tail-trim below works on whole tokens, so it
    # never sees these; peel the city off the last token first.
    if tokens:
        for city in _CITIES_BY_LENGTH:
            if len(tokens[-1]) > len(city) and tokens[-1].endswith(city):
                tokens[-1] = tokens[-1][: -len(city)]
                break

    # Trim noise from the tail only. A city or "LTD" mid-name is part of the
    # name; trailing ones are statement decoration. Two-token cities are checked
    # first, otherwise "DELHI" pops on its own and strands "NEW".
    while tokens:
        if len(tokens) >= 2 and " ".join(tokens[-2:]) in _CITIES:
            del tokens[-2:]
            continue
        if tokens[-1] in _CORPORATE or tokens[-1] in _CITIES:
            tokens.pop()
            continue
        break

    return " ".join(tokens) if tokens else "UNKNOWN"


def _keywords(*words: str) -> re.Pattern[str]:
    """Compile keywords bounded by non-letters.

    Letter boundaries, not word boundaries. Plain substring matching classified
    CAFE COFFEE DAY as a fee and EMIRATES as an EMI; a word boundary would fix
    those but would also stop matching EMI2400, which issuers do emit.
    """
    return re.compile("(?<![A-Z])(?:" + "|".join(words) + ")(?![A-Z])")


_PAYMENT = _keywords("PAYMENT", "PMT RECEIVED", "NEFT", "IMPS", "UPI CR", "AUTOPAY", "BANKING")
_CASHBACK = _keywords("CASHBACK", "CASH BACK", "REWARD CREDIT")
# SBI writes FIN CHARGE where HDFC writes FINANCE CHARGES.
_INTEREST = _keywords("FIN(?:ANCE)? CHARGES?", "INTEREST")
_FEE = _keywords("FEES?", "CHARGES", "GST", "IGST", "CGST", "SGST", "SURCHARGE", "DCC")
# ICICI bills a merchant EMI's instalment as PRINCIPAL AMOUNT AMORTIZATION and
# INTEREST AMOUNT AMORTIZATION rows; neither says EMI. SBI's PAY IN EMIS is not
# here: it only marks a purchase as eligible for conversion.
_EMI = _keywords("EMI", "INSTAL?LMENT", "(?:PRINCIPAL|INTEREST) AMOUNT AMORTI[SZ]ATION")
# A bounced autopay: the bank debits back the payment it had credited.
# Issuers word a bounced autopay differently: AUTOPAY RETURNED, or ICICI's
# AUTO DR RETN INSUFF FUND.
_PAYMENT_REVERSAL = _keywords("AUTOPAY RETURNED", "AUTO DR RETN")


def classify_txn_type(merchant_clean: str, direction: str) -> str:
    """Classify a row so spend measures can exclude non-spend movement.

    Check order is load-bearing: cashback before payment, because a cashback row
    often also says CREDIT; payment reversal first among debits, because
    AUTOPAY RETURNED is neither a purchase nor a fee; interest before fee,
    because FINANCE CHARGES contains CHARGES.
    """
    normalised = (direction or "").strip().lower()
    if normalised not in ("debit", "credit"):
        raise ValueError(
            f"direction must be 'debit' or 'credit', got {direction!r}"
        )

    text = merchant_clean.upper()

    if normalised == "credit":
        if _CASHBACK.search(text):
            return "cashback"
        if _PAYMENT.search(text):
            return "payment"
        return "refund"

    if _PAYMENT_REVERSAL.search(text):
        return "payment_reversal"
    if _EMI.search(text):
        return "emi"
    if _INTEREST.search(text):
        return "interest"
    if _FEE.search(text):
        return "fee"
    return "purchase"


def make_txn_id(
    card_id: str,
    txn_date: date,
    amount: Decimal,
    merchant_raw: str,
    occurrence: int = 0,
) -> str:
    """Content hash of a transaction. Re-parsing the same statement yields the
    same id, so duplicate imports collapse instead of double-counting.

    `occurrence` distinguishes genuinely repeated rows -- two separate 120-rupee
    Uber rides on the same day are different transactions, not a double import.
    Prefer make_txn_ids(), which assigns it for you.
    """
    # quantize to paise so Decimal("482.5") and Decimal("482.50") agree
    normalised_amount = format(amount.quantize(Decimal("0.01")), "f")
    payload = "|".join(
        [
            card_id,
            txn_date.isoformat(),
            normalised_amount,
            merchant_raw.strip().upper(),
            str(occurrence),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_txn_ids(
    card_id: str, rows: list[tuple[date, Decimal, str]]
) -> list[str]:
    """Content hashes for one statement's rows, in statement order.

    Rows identical in date, amount and merchant are separated by their position
    among that identical set. Two 120-rupee Uber rides on the same day get
    different ids, while re-parsing the statement reproduces both exactly --
    the rows arrive in the same order, so they get the same ordinals.
    """
    counts: dict[tuple[date, Decimal, str], int] = {}
    ids: list[str] = []
    for txn_date, amount, merchant_raw in rows:
        key = (txn_date, amount.quantize(Decimal("0.01")), merchant_raw.strip().upper())
        occurrence = counts.get(key, 0)
        counts[key] = occurrence + 1
        ids.append(make_txn_id(card_id, txn_date, amount, merchant_raw, occurrence))
    return ids
