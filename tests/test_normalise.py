# tests/test_normalise.py
from datetime import date
from decimal import Decimal

import pytest

from creditcard.normalise import classify_txn_type, clean_merchant, make_txn_id, make_txn_ids


def test_strips_processor_prefix_and_city_suffix():
    assert clean_merchant("SWIGGY*ORDER BANGALORE IN") == "SWIGGY ORDER"


def test_strips_reference_digits():
    assert clean_merchant("AMAZON PAY INDIA 1234567890 MUMBAI") == "AMAZON PAY INDIA"


def test_collapses_whitespace_and_uppercases():
    assert clean_merchant("  uber   india  pvt ltd  ") == "UBER INDIA"


def test_drops_corporate_suffixes():
    assert clean_merchant("ZOMATO LTD BENGALURU IN") == "ZOMATO"


def test_empty_input_returns_unknown():
    assert clean_merchant("") == "UNKNOWN"
    assert clean_merchant("   ") == "UNKNOWN"


def test_preserves_merchant_with_no_noise():
    assert clean_merchant("BIGBASKET") == "BIGBASKET"


def test_two_token_city_is_removed_as_a_unit():
    """Guards the pair check: without it DELHI pops alone and strands NEW."""
    assert clean_merchant("PIZZA HUT NEW DELHI LTD") == "PIZZA HUT"


def test_debit_defaults_to_purchase():
    assert classify_txn_type("SWIGGY", "debit") == "purchase"


def test_credit_with_payment_keyword_is_payment():
    assert classify_txn_type("PAYMENT RECEIVED THANK YOU", "credit") == "payment"
    assert classify_txn_type("NEFT PAYMENT", "credit") == "payment"


def test_credit_without_payment_keyword_is_refund():
    assert classify_txn_type("AMAZON", "credit") == "refund"


def test_cashback_is_its_own_type():
    assert classify_txn_type("CASHBACK CREDIT", "credit") == "cashback"


def test_interest_and_fees_are_debits_with_own_types():
    assert classify_txn_type("FINANCE CHARGES", "debit") == "interest"
    assert classify_txn_type("LATE PAYMENT FEE", "debit") == "fee"
    assert classify_txn_type("GST ON FEE", "debit") == "fee"


def test_emi_is_detected():
    assert classify_txn_type("EMI INSTALMENT 3 OF 12", "debit") == "emi"
    assert classify_txn_type("INSTALLMENT 3 OF 12", "debit") == "emi"


def test_emi_rows_that_never_say_emi_are_detected():
    """ICICI bills a merchant EMI's instalment as PRINCIPAL and INTEREST AMOUNT
    AMORTIZATION rows. Both are the instalment, so both are EMI."""
    assert classify_txn_type("PRINCIPAL AMOUNT AMORTIZATION", "debit") == "emi"
    assert classify_txn_type("PRINCIPAL AMOUNT AMORTIZATION 4 9 RELIANCE DIGITAL", "debit") == "emi"
    assert classify_txn_type("INTEREST AMOUNT AMORTIZATION 1 9 RELIANCE DIGITAL", "debit") == "emi"


def test_sbi_pay_in_emis_marks_eligibility_not_an_emi():
    """SBI tags a purchase that COULD be converted with PAY IN EMIS. Nothing was
    converted, so it is an ordinary purchase."""
    assert classify_txn_type("MYNTRA DESIGNS PVT LTD BANGALORE IN PAY IN EMIS", "debit") == "purchase"


def test_net_banking_credit_is_a_payment():
    """CREDIT CARD PAYMENTNET BANKING runs PAYMENT into NET, so the payment
    keyword never matched and the payment was read as a refund."""
    assert classify_txn_type("CREDIT CARD PAYMENTNET BANKING", "credit") == "payment"


def test_fin_charge_is_interest():
    assert classify_txn_type("FIN CHARGE ON RETAIL EXCL TAX 4 50", "debit") == "interest"


def test_returned_autopay_is_a_payment_reversal():
    """A bounced autopay debits back the payment it credited: not a purchase."""
    assert classify_txn_type("AUTOPAY RETURNED REF ST000000000000000000042", "debit") == "payment_reversal"


def test_the_fee_for_a_returned_payment_is_still_a_fee():
    assert classify_txn_type("PAYMENT RETURN FEE REF", "debit") == "fee"


def test_merchant_names_containing_keywords_are_not_misclassified():
    """Plain substring matching made COFFEE a fee and EMIRATES an EMI."""
    assert classify_txn_type("CAFE COFFEE DAY", "debit") == "purchase"
    assert classify_txn_type("STARBUCKS COFFEE", "debit") == "purchase"
    assert classify_txn_type("SHREE CHEMIST", "debit") == "purchase"
    assert classify_txn_type("EMIRATES", "debit") == "purchase"
    assert classify_txn_type("AIR INDIA PREMIER ECONOMY", "debit") == "purchase"
    assert classify_txn_type("BSNL MOBILE RECHARGES", "debit") == "purchase"


def test_keyword_glued_to_digits_still_matches():
    """Issuers glue codes onto keywords: EMI2400 is still an EMI."""
    assert classify_txn_type("EMI2400 OF 12", "debit") == "emi"
    assert classify_txn_type("GST18 ON FEE", "debit") == "fee"


def test_direction_is_case_and_whitespace_insensitive():
    """Four parsers are still unwritten; none of them can get this wrong."""
    assert classify_txn_type("AMAZON", "Credit") == "refund"
    assert classify_txn_type("AMAZON", "CREDIT") == "refund"
    assert classify_txn_type("AMAZON", " credit ") == "refund"
    assert classify_txn_type("SWIGGY", "DEBIT") == "purchase"


def test_unrecognised_direction_raises():
    """A garbage direction must fail loudly, not silently become spend."""
    with pytest.raises(ValueError):
        classify_txn_type("AMAZON", "")
    with pytest.raises(ValueError):
        classify_txn_type("AMAZON", "cr")
    with pytest.raises(ValueError):
        classify_txn_type("AMAZON", None)


def test_fee_words_inside_merchant_names_are_not_fees():
    """EV charging merchants and annual subscriptions are purchases."""
    assert classify_txn_type("TATA POWER EZ CHARGE", "debit") == "purchase"
    assert classify_txn_type("CHARGE2GO EV NETWORK", "debit") == "purchase"
    assert classify_txn_type("NETFLIX ANNUAL SUBSCRIPTION", "debit") == "purchase"
    assert classify_txn_type("AMAZON PRIME ANNUAL MEMBERSHIP", "debit") == "purchase"


def test_real_annual_fee_is_still_a_fee():
    """Dropping the ANNUAL keyword must not lose the actual annual fee."""
    assert classify_txn_type("ANNUAL FEE", "debit") == "fee"
    assert classify_txn_type("ANNUAL FEES", "debit") == "fee"
    assert classify_txn_type("JOINING FEE", "debit") == "fee"


def test_same_inputs_always_give_same_id():
    args = ("hdfc_regalia", date(2026, 8, 3), Decimal("482.50"), "SWIGGY*ORDER")
    assert make_txn_id(*args) == make_txn_id(*args)


def test_different_card_gives_different_id():
    a = make_txn_id("hdfc_regalia", date(2026, 8, 3), Decimal("482.50"), "SWIGGY")
    b = make_txn_id("icici_amazon", date(2026, 8, 3), Decimal("482.50"), "SWIGGY")
    assert a != b


def test_amount_precision_matters():
    a = make_txn_id("c", date(2026, 8, 3), Decimal("482.50"), "M")
    b = make_txn_id("c", date(2026, 8, 3), Decimal("482.05"), "M")
    assert a != b


def test_decimal_trailing_zeros_do_not_change_id():
    """482.5 and 482.50 are the same amount and must hash identically."""
    a = make_txn_id("c", date(2026, 8, 3), Decimal("482.5"), "M")
    b = make_txn_id("c", date(2026, 8, 3), Decimal("482.50"), "M")
    assert a == b


def test_id_is_short_hex():
    txn_id = make_txn_id("c", date(2026, 8, 3), Decimal("1"), "M")
    assert len(txn_id) == 16
    int(txn_id, 16)  # raises if not hex


def test_repeated_rows_in_one_statement_get_different_ids():
    """Two separate 120-rupee Uber rides on one day are two transactions."""
    rows = [
        (date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP"),
        (date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP"),
    ]
    assert len(set(make_txn_ids("hdfc_regalia", rows))) == 2


def test_reparsing_a_statement_reproduces_the_same_ids():
    """Idempotence must survive the occurrence ordinal."""
    rows = [
        (date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP"),
        (date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP"),
        (date(2026, 8, 4), Decimal("50.00"), "METRO"),
    ]
    assert make_txn_ids("c", rows) == make_txn_ids("c", rows)


def test_first_occurrence_matches_the_plain_single_row_id():
    rows = [(date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP")]
    assert make_txn_ids("c", rows) == [
        make_txn_id("c", date(2026, 8, 3), Decimal("120.00"), "UBER*TRIP")
    ]


def test_processor_prefix_is_dropped_and_merchant_kept():
    """Real HDFC statements write PROCESSOR*MERCHANT.

    Keeping the left of the star turned every Swiggy order into a merchant
    called PYU and dumped 34 of 36 real transactions into Uncategorised.
    """
    assert clean_merchant("PYU*Swiggy FoodBangalore") == "SWIGGY FOOD"
    assert clean_merchant("CAS*SwiggyBengaluru") == "SWIGGY"
    assert clean_merchant("RSP*SWIGGY DINEOUTBENGALURU") == "SWIGGY DINEOUT"
    assert clean_merchant("PAYPAL *LUMISHOPDIP") == "LUMISHOPDIP"


def test_merchant_first_star_form_still_keeps_the_merchant():
    """A merchant on the left of the star keeps both halves -- the detail on
    the right is what told GOOGLE*PLAY apart from plain GOOGLE."""
    assert clean_merchant("SWIGGY*ORDER BANGALORE IN") == "SWIGGY ORDER"


def test_city_glued_onto_the_merchant_is_stripped():
    """The tail trim works on whole tokens, so it never sees a glued city."""
    assert clean_merchant("BUNDL TECHNOLOGIESBENGALURU") == "BUNDL TECHNOLOGIES"
    assert clean_merchant("SwiggyBANGALORE") == "SWIGGY"
    assert clean_merchant("Swiggy INBangalore") == "SWIGGY"


def test_short_brand_before_the_star_is_kept():
    """Riot is four letters; treating every short prefix as a processor code
    kept only the city."""
    assert clean_merchant("Riot* Dublin IE") == "RIOT DUBLIN IE"


def test_detail_after_a_merchant_star_is_kept():
    """GOOGLE*PLAY is Google Play; keeping only the left lost the product."""
    assert clean_merchant("GOOGLE*PLAY SUPPORT.GOOGL CA 25.00 USD").startswith("GOOGLE PLAY")


def test_processor_codes_seen_on_statements_are_still_dropped():
    assert clean_merchant("RAZ*SwiggyBangalore") == "SWIGGY"
    assert clean_merchant("IND*AMAZON HTTP://WWW.AM IN") == "AMAZON HTTP WWW AM"
    assert clean_merchant("ING*MYNTRA DESIGNS PVT MUMBAI IN") == "MYNTRA DESIGNS"
    assert clean_merchant("EPC*EPIC GAMES STORELUZERN") == "EPIC GAMES STORELUZERN"
    assert clean_merchant("PTM*SWIGGY INBANGALORE") == "SWIGGY"
    assert clean_merchant("PPSL*SwiggyBangalore") == "SWIGGY"
    assert clean_merchant("PAY*WWW SWIGGY INBANGALORE") == "WWW SWIGGY"
    assert clean_merchant("WL *ETS*Testing Exam 953-6813000 US") == "ETS TESTING EXAM 953 US"


def test_an_autopay_returned_for_insufficient_funds_is_a_payment_reversal():
    """ICICI words a bounced autopay differently from HDFC; it adds no spend either."""
    assert classify_txn_type("AUTO DR RETN INSUFF FUND", "debit") == "payment_reversal"
