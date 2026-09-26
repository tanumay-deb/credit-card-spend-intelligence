import os
from decimal import Decimal
from pathlib import Path

import pytest

from creditcard.config import (
    load_cards,
    load_categories,
    load_merchant_map,
    load_rules,
    load_secrets,
)


def test_load_cards_keys_by_card_id(tmp_path):
    p = tmp_path / "cards.csv"
    p.write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
        "hdfc_regalia,HDFC,Regalia,4321,hdfc,HDFC_PW,200000,18\n",
        encoding="utf-8",
    )
    cards = load_cards(p)
    assert cards["hdfc_regalia"].issuer == "HDFC"
    assert cards["hdfc_regalia"].credit_limit == Decimal("200000")
    assert cards["hdfc_regalia"].statement_day == 18


def test_load_categories_maps_subcategory_to_group(tmp_path):
    p = tmp_path / "categories.csv"
    p.write_text(
        "category_group,subcategory,is_discretionary,sort_order\n"
        "Food,Swiggy,1,11\nOthers,Uncategorised,1,90\n",
        encoding="utf-8",
    )
    cats = load_categories(p)
    assert cats["Swiggy"] == "Food"
    assert cats["Uncategorised"] == "Others"


def test_load_rules_sorted_by_priority(tmp_path):
    p = tmp_path / "rules.csv"
    p.write_text(
        "priority,pattern,subcategory\n"
        "20,AMAZON,Amazon\n10,SWIGGY,Swiggy\n",
        encoding="utf-8",
    )
    rules = load_rules(p)
    assert [r.priority for r in rules] == [10, 20]
    assert rules[0].subcategory == "Swiggy"


def test_load_merchant_map_uppercases_keys(tmp_path):
    p = tmp_path / "map.csv"
    p.write_text("merchant_clean,subcategory\nblinkit,Groceries\n", encoding="utf-8")
    assert load_merchant_map(p)["BLINKIT"] == "Groceries"


def test_missing_optional_file_returns_empty(tmp_path):
    assert load_merchant_map(tmp_path / "nope.csv") == {}


def test_missing_required_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_cards(tmp_path / "nope.csv")


def test_seed_cards_file_loads():
    """The committed seed config must actually parse."""
    cards = load_cards(Path("config/cards.csv"))
    assert set(cards) == {
        "hdfc_millennia", "hdfc_regalia", "icici_coral", "icici_rubyx",
        "icici_sapphiro", "sbi_elite", "sbi_bpcl",
    }
    # Two HDFC cards and two SBI cards share a parser each; that sharing is the
    # whole reason statement folders are keyed by card_id rather than issuer.
    assert cards["hdfc_millennia"].parser == "hdfc"
    assert cards["hdfc_regalia"].parser == "hdfc"
    assert cards["sbi_elite"].parser == "sbi"
    assert cards["sbi_bpcl"].parser == "sbi"


def test_cards_share_a_password_env_only_within_one_issuer():
    """ICICI locks every card's statement with one password, so its cards share
    one name. Sharing across issuers would decrypt one bank's PDF with
    another's password."""
    cards = load_cards(Path("config/cards.csv"))
    issuers: dict[str, set[str]] = {}
    for card in cards.values():
        issuers.setdefault(card.password_env, set()).add(card.issuer)
    assert all(len(found) == 1 for found in issuers.values())


def test_monthly_defaults_to_yes_and_reads_no(tmp_path):
    path = tmp_path / "cards.csv"
    path.write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day,monthly\n"
        "a,HDFC,A,0000,hdfc,A_PW,0,1,\n"
        "b,ICICI,B,0000,icici,B_PW,0,1,no\n",
        encoding="utf-8",
    )
    cards = load_cards(path)
    assert (cards["a"].monthly, cards["b"].monthly) == (True, False)


def test_the_icici_cards_used_now_and_then_are_not_monthly():
    cards = load_cards(Path("config/cards.csv"))
    assert not cards["icici_rubyx"].monthly and not cards["icici_sapphiro"].monthly
    assert cards["icici_coral"].monthly


def test_seed_categories_file_loads():
    """Every subcategory the pipeline falls back to must exist."""
    cats = load_categories(Path("config/categories.csv"))
    assert cats["Uncategorised"] == "Others"
    assert cats["Swiggy"] == "Food"


def test_real_config_places_the_merchants_reviewed_by_hand():
    """The merchants reviewed one by one land where they were decided to go."""
    from creditcard.categorise import resolve_category
    from creditcard.config import load_merchant_map, load_rules

    cats = load_categories(Path("config/categories.csv"))
    rules = load_rules(Path("config/category_rules.csv"))
    merchant_map = load_merchant_map(Path("config/merchant_map.csv"))

    def place(merchant):
        return resolve_category(merchant, merchant_map, rules, cats)[:2]

    assert place("RIOT DUBLIN IE") == ("Entertainment", "Gaming")
    assert place("BOOK MY SHOW SMART G") == ("Entertainment", "Events")
    assert place("UPI BIGTREE ENTERTAINMENT PRI") == ("Entertainment", "Events")
    assert place("UPI DISTRICT D") == ("Entertainment", "Events")
    assert place("RELIANCE DIGITAL NASHIK") == ("Shopping", "Electronics")
    assert place("GOOGLE PLAY SUPPORT GOOGL CA 25 00 USD") == ("Bills", "Subscriptions")
    assert place("GOOGLE CHROME 855 836 3987 CA 5 00 USD") == ("Bills", "Subscriptions")
    assert place("ACMI AHMEDNAGAR AHMEDNAGAR IN PAY IN EMIS") == ("Education", "Education")
    assert place("UPI UNIV SETTLEMENT ACCOUNT") == ("Education", "Education")
    assert place("ETS TESTING EXAM 953 US") == ("Education", "Education")
    assert place("NOVA SCHOOL OF BUS") == ("Education", "Education")
    assert place("SKILLNEST LEARNING GURGAON HA") == ("Education", "Education")
    assert place("1MG HEALTHCARE SOLU") == ("Health", "Health")
    assert place("UPI FRESHMART SUPER MARKET N") == ("Food", "Groceries")
    assert place("ASSPL BANGALORE IN PAY IN EMIS") == ("Shopping", "Amazon")
    assert place("VOLTRONIX") == ("Shopping", "Electronics")
    assert place("RAZORPAY PAYMENTSBANGALORE") == ("Others", "Misc")
    assert place("SALE DOMESTIC NOTONUS 7") == ("Others", "Misc")
    assert place("LUMISHOPORDER0000000001") == ("Others", "Misc")
    assert place("UPI TFS SUBWAY") == ("Food", "Restaurants")
    assert place("UPI SPICE HOUSE") == ("Food", "Restaurants")
    assert place("UPI CITYBITE BURGER GALAXY MAL") == ("Food", "Restaurants")
    assert place("UPI GREEN LEAF CAFEPVT") == ("Food", "Restaurants")
    assert place("UPI SOMEONE NEW") == ("UPI", "UPI")


def test_bom_prefixed_cards_file_loads(tmp_path):
    """Excel on Windows writes a UTF-8 BOM. It must not break the loader."""
    p = tmp_path / "cards.csv"
    p.write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
        "hdfc_regalia,HDFC,Regalia,4321,hdfc,HDFC_PW,200000,18\n",
        encoding="utf-8-sig",
    )
    assert load_cards(p)["hdfc_regalia"].issuer == "HDFC"


def test_bom_prefixed_secrets_file_sets_the_real_key(tmp_path, monkeypatch):
    """A BOM used to set an invisible key while the real one stayed unset."""
    p = tmp_path / "secrets.env"
    p.write_text("HDFC_PW=hunter2\n", encoding="utf-8-sig")
    monkeypatch.delenv("HDFC_PW", raising=False)
    load_secrets(p)
    assert os.environ["HDFC_PW"] == "hunter2"


def test_secrets_file_overrides_existing_env_var(tmp_path, monkeypatch):
    """The file is the source of truth, not a stale shell export."""
    p = tmp_path / "secrets.env"
    p.write_text("HDFC_PW=from_file\n", encoding="utf-8")
    monkeypatch.setenv("HDFC_PW", "stale_from_shell")
    load_secrets(p)
    assert os.environ["HDFC_PW"] == "from_file"


def test_bad_credit_limit_names_the_card_and_line(tmp_path):
    p = tmp_path / "cards.csv"
    p.write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
        "hdfc_regalia,HDFC,Regalia,4321,hdfc,HDFC_PW,not_a_number,18\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_cards(p)
    assert "hdfc_regalia" in str(exc.value)
    assert "line 2" in str(exc.value)


def test_duplicate_subcategory_raises(tmp_path):
    """A spreadsheet edit reusing a name would silently overwrite the mapping."""
    p = tmp_path / "categories.csv"
    p.write_text(
        "category_group,subcategory,is_discretionary,sort_order\n"
        "Food,Coffee,1,10\nShopping,Coffee,1,20\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_categories(p)
    assert "Coffee" in str(exc.value)


def test_comma_in_credit_limit_is_rejected_not_truncated(tmp_path):
    p = tmp_path / "cards.csv"
    p.write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
        "hdfc_regalia,HDFC,Regalia,4321,hdfc,HDFC_PW,2,00,000,18\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_cards(p)
    assert "line 2" in str(exc.value)
    assert "comma" in str(exc.value).lower()


def test_comma_in_category_group_is_rejected_not_truncated(tmp_path):
    """The same restkey overflow that would hit cards.csv can hit any
    hand-edited CSV; categories.csv gets the same guard."""
    p = tmp_path / "categories.csv"
    p.write_text(
        "category_group,subcategory,is_discretionary,sort_order\n"
        "Bills,Electricity,1,10,20\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_categories(p)
    assert "line 2" in str(exc.value)
    assert "comma" in str(exc.value).lower()


def test_comma_in_rule_priority_is_rejected_not_truncated(tmp_path):
    p = tmp_path / "rules.csv"
    p.write_text(
        "priority,pattern,subcategory\n"
        "10,AMAZON,Amazon,Extra\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_rules(p)
    assert "line 2" in str(exc.value)
    assert "comma" in str(exc.value).lower()


def test_comma_in_merchant_map_is_rejected_not_truncated(tmp_path):
    p = tmp_path / "map.csv"
    p.write_text(
        "merchant_clean,subcategory\n"
        "blinkit,Groceries,Extra\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        load_merchant_map(p)
    assert "line 2" in str(exc.value)
    assert "comma" in str(exc.value).lower()
