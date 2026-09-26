import csv
import shutil
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from creditcard import demo
from creditcard.__main__ import main
from creditcard.config import load_cards

TODAY = date(2026, 9, 26)
REAL_CONFIG = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture
def root(tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copyfile(REAL_CONFIG / "categories.csv", tmp_path / "config" / "categories.csv")
    return tmp_path


def _rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_writes_a_demo_project_with_config_and_outputs(root):
    folder = demo.build(root, today=TODAY)
    assert folder == root / "demo"
    for name in ("cards.csv", "categories.csv", "category_rules.csv"):
        assert (folder / "config" / name).exists()
    for name in ("transactions.csv", "statements.csv", "emi_plans.csv", "credit_card_data.xlsx"):
        assert (folder / "output" / name).exists()
    assert len(load_cards(folder / "config" / "cards.csv")) == 4
    assert len(_rows(folder / "output" / "statements.csv")) == 4 * demo.MONTHS


def test_every_demo_statement_reconciles_like_a_real_one(root):
    folder = demo.build(root, today=TODAY)
    debits = defaultdict(Decimal)
    for t in _rows(folder / "output" / "transactions.csv"):
        if t["direction"] == "debit":
            debits[t["statement_id"]] += Decimal(t["amount"])
    for s in _rows(folder / "output" / "statements.csv"):
        assert debits[s["statement_id"]] == Decimal(s["purchases"]) + Decimal(s["finance_charges"])


def test_every_demo_merchant_is_categorised_and_refunds_find_their_purchase(root):
    folder = demo.build(root, today=TODAY)
    txns = _rows(folder / "output" / "transactions.csv")
    assert not [t for t in txns if t["subcategory"] == "Uncategorised"]
    refunds = [t for t in txns if t["txn_type"] == "refund"]
    assert refunds and all(t["category_source"] == "refund" for t in refunds)


def test_the_demo_has_one_running_emi(root):
    folder = demo.build(root, today=TODAY)
    active = [p for p in _rows(folder / "output" / "emi_plans.csv") if p["status"] == "active"]
    assert len(active) == 1 and int(active[0]["remaining_months"]) == 3


def test_the_demo_is_the_same_every_time(root, tmp_path_factory):
    first = (demo.build(root, today=TODAY) / "output" / "transactions.csv").read_bytes()
    assert (demo.build(root, today=TODAY) / "output" / "transactions.csv").read_bytes() == first


def test_cards_demo_builds_it(root, capsys):
    assert main(["demo", "--root", str(root), "--as-of", "2026-09-26"]) == 0
    assert (root / "demo" / "output" / "transactions.csv").exists()
    assert "cards dashboard --demo" in capsys.readouterr().out
