import csv
from datetime import date
from pathlib import Path

import pytest

from creditcard import alerts
from creditcard.alerts import MERCHANT, PROBLEM, STATEMENT, Item, Memory, items_from_files

CARDS = (
    "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
    "c1,HDFC,Millennia,0000,hdfc,C1_PW,0,1\n"
    "c2,SBI,SBI Elite,0000,sbi,C2_PW,0,1\n"
)

# Values that must never reach an email or a notification.
SECRET_AMOUNT = "48250.75"
SECRET_MERCHANT = "SECRET MERCHANT PVT"
SECRET_FILE = "4315123412341234_statement.pdf"


def _csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else ["empty"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _stmt(card, when, due="", source=None):
    return {
        "statement_id": f"{card}_{when}", "card_id": card, "statement_date": when,
        "due_date": due, "total_due": SECRET_AMOUNT,
        "source_file": source or f"statements\\{card}\\{when}.pdf",
    }


def _seed(root: Path, statements=(), ingest=(), review=(), fetch=(), unmatched=()):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "cards.csv").write_text(CARDS, encoding="utf-8")
    _csv(root / "output" / "statements.csv", list(statements))
    _csv(root / "output" / "ingest_log.csv", list(ingest))
    _csv(root / "output" / "review_queue.csv", list(review))
    _csv(root / "output" / "email_fetch_log.csv", list(fetch))
    folder = root / "statements" / "_unmatched"
    folder.mkdir(parents=True, exist_ok=True)
    for name in unmatched:
        (folder / name).write_bytes(b"%PDF")


def _by_key(items):
    return {i.key: i for i in items}


def test_each_statement_is_an_item_with_card_month_and_due_date(tmp_path):
    _seed(tmp_path, statements=[_stmt("c2", "2026-09-14", due="2026-10-04")])
    item = _by_key(items_from_files(tmp_path, date(2026, 9, 20)))["statement:c2_2026-09-14"]
    assert item.kind == STATEMENT
    assert item.text.splitlines()[0] == "SBI Elite: September statement imported."
    assert "due 4 Oct 2026 (in 14 days)" in item.text


def test_failed_and_unreconciled_files_are_problems(tmp_path):
    good = "statements\\c2\\sep.pdf"
    _seed(
        tmp_path,
        statements=[_stmt("c2", "2026-09-14", source=f"D:\\root\\{good}")],
        ingest=[
            {"source_file": f"statements\\c1\\{SECRET_FILE}", "status": "failed",
             "error": "Could not decrypt it - check the password env var"},
            {"source_file": good, "status": "warning", "error": ""},
            {"source_file": "statements\\c1\\ok.pdf", "status": "ok", "error": ""},
        ],
    )
    items = _by_key(items_from_files(tmp_path, date(2026, 9, 20)))
    failed = items[f"failed:c1/{SECRET_FILE}"]
    assert failed.kind == PROBLEM
    assert failed.text.startswith("Millennia: a statement")
    assert "wrong password?" in failed.text
    # Keyed by card folder and file name, so a manual run's relative paths
    # and the daily run's absolute ones are the same problem.
    assert items["unreconciled:c2/sep.pdf"].text.splitlines()[0] == (
        "SBI Elite: the September statement doesn't add up to its printed total."
    )
    assert not any(k.endswith("ok.pdf") for k in items)


def test_unmatched_pdf_names_the_sender_domain_and_date(tmp_path):
    _seed(
        tmp_path,
        fetch=[{"email_date": "2026-09-16", "sender": '"SBI Card" <Statements@SBICard.com>',
                "saved_path": f"D:\\x\\statements\\_unmatched\\{SECRET_FILE}",
                "status": "unmatched"}],
        unmatched=[SECRET_FILE, "dropped_by_hand.pdf"],
    )
    items = _by_key(items_from_files(tmp_path, date(2026, 9, 20)))
    assert items[f"unmatched:{SECRET_FILE}"].text.splitlines()[0] == (
        "An unrecognised statement from sbicard.com (16 Sep) is in statements/_unmatched/."
    )
    assert items["unmatched:dropped_by_hand.pdf"].text.startswith(
        "An unrecognised statement PDF saved on"
    )


@pytest.mark.parametrize(
    "last, today, late_key",
    [
        ("2026-09-18", date(2026, 10, 25), None),                 # 7 days past: not yet
        ("2026-09-18", date(2026, 10, 26), "late:c1:2026-10"),    # 8 days past: late
        ("2026-01-31", date(2026, 3, 8), "late:c1:2026-02"),      # clamps to 28 Feb
        ("2026-01-31", date(2026, 3, 7), None),
    ],
)
def test_statement_is_late_seven_days_after_the_expected_date(tmp_path, last, today, late_key):
    _seed(tmp_path, statements=[_stmt("c1", last)])
    late = [i for i in items_from_files(tmp_path, today) if i.key.startswith("late:")]
    assert [i.key for i in late] == ([late_key] if late_key else [])


def test_late_text_names_the_card_and_expected_month(tmp_path):
    _seed(tmp_path, statements=[_stmt("c1", "2026-09-18")])
    late = _by_key(items_from_files(tmp_path, date(2026, 10, 30)))["late:c1:2026-10"]
    assert late.text.splitlines()[0] == "Millennia: no October statement yet (expected around 18 Oct)."


def test_only_the_latest_statement_sets_the_expected_date(tmp_path):
    _seed(tmp_path, statements=[_stmt("c1", "2026-08-18"), _stmt("c1", "2026-09-18")])
    keys = _by_key(items_from_files(tmp_path, date(2026, 9, 30)))
    assert not any(k.startswith("late:") for k in keys)


def test_missing_month_between_statements_is_a_gap(tmp_path):
    _seed(tmp_path, statements=[_stmt("c1", "2026-06-18"), _stmt("c1", "2026-08-18")])
    gap = _by_key(items_from_files(tmp_path, date(2026, 8, 20)))["gap:c1:2026-07"]
    assert gap.text.splitlines()[0] == "Millennia: no statement for July 2026, between ones you have."


def test_each_review_queue_merchant_is_an_item_without_its_name(tmp_path):
    _seed(tmp_path, review=[{"merchant_clean": SECRET_MERCHANT, "total_amount": SECRET_AMOUNT}])
    item = _by_key(items_from_files(tmp_path, date(2026, 9, 20)))[f"merchant:{SECRET_MERCHANT}"]
    assert item.kind == MERCHANT
    assert SECRET_MERCHANT not in item.text


def test_no_item_text_carries_amounts_merchants_or_file_names(tmp_path):
    _seed(
        tmp_path,
        statements=[_stmt("c1", "2026-06-18", source=f"statements\\c1\\{SECRET_FILE}"),
                    _stmt("c1", "2026-08-18")],
        ingest=[{"source_file": f"statements\\c1\\{SECRET_FILE}", "status": "warning",
                 "error": f"row {SECRET_AMOUNT} at {SECRET_MERCHANT}"}],
        review=[{"merchant_clean": SECRET_MERCHANT, "total_amount": SECRET_AMOUNT}],
        unmatched=[SECRET_FILE],
    )
    texts = " | ".join(i.text for i in items_from_files(tmp_path, date(2026, 12, 1)))
    for secret in (SECRET_MERCHANT, SECRET_FILE, "4315123412341234"):
        assert secret not in texts


def test_missing_output_files_mean_no_items(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "cards.csv").write_text(CARDS, encoding="utf-8")
    assert items_from_files(tmp_path, date(2026, 9, 20)) == []


# --- Memory -------------------------------------------------------------------

def _p(key):
    return Item(key, PROBLEM, key)


def test_a_new_key_is_told_and_then_not_again(tmp_path):
    memory = Memory()
    current = [_p("a")]
    assert memory.fresh(current) == current
    memory.settle(current, told_now=current, delivered=True)
    assert memory.fresh(current) == []


def test_a_fixed_problem_that_returns_is_told_again():
    memory = Memory(told={"a"})
    memory.settle([], told_now=[], delivered=True)
    assert memory.fresh([_p("a")]) == [_p("a")]


def test_first_run_records_statements_and_merchants_without_telling_them():
    memory = Memory(first_run=True)
    current = [Item("statement:s1", STATEMENT, "x"), Item("merchant:m", MERCHANT, "x"), _p("a")]
    assert memory.fresh(current) == [_p("a")]
    memory.settle(current, told_now=[_p("a")], delivered=True)
    assert memory.told == {"statement:s1", "merchant:m", "a"}
    assert memory.fresh(current + [Item("statement:s2", STATEMENT, "x")]) == [
        Item("statement:s2", STATEMENT, "x")
    ]


def test_undelivered_items_are_not_remembered():
    memory = Memory()
    memory.settle([_p("a")], told_now=[_p("a")], delivered=False)
    assert memory.fresh([_p("a")]) == [_p("a")]


def test_a_partial_run_keeps_every_key_it_cannot_see():
    """A crash means the current list is incomplete; forgetting what was told
    would repeat all of it tomorrow."""
    memory = Memory(told={"a", "b"})
    memory.settle([_p("crashed:X")], told_now=[_p("crashed:X")], delivered=True, partial=True)
    assert memory.told == {"a", "b", "crashed:X"}


def test_memory_round_trips_and_a_missing_or_corrupt_file_is_a_first_run(tmp_path):
    path = tmp_path / "output" / "notified.json"
    assert Memory.load(path).first_run
    Memory(told={"a"}, fetch_failures=1, import_pending=True).save(path)
    loaded = Memory.load(path)
    assert (loaded.told, loaded.fetch_failures, loaded.import_pending, loaded.first_run) == (
        {"a"}, 1, True, False
    )
    path.write_text("{not json", encoding="utf-8")
    assert Memory.load(path).first_run


def test_run_items_have_stable_keys():
    assert alerts.fetch_auth().key == "fetch_auth"
    assert alerts.fetch_network(2, "TimeoutError").key == "fetch_network"
    assert "TimeoutError" in alerts.fetch_network(2, "TimeoutError").text
    assert alerts.locked("transactions.csv").key == "locked:transactions.csv"
    assert alerts.workbook_locked().key == "workbook_locked"
    assert alerts.refresh_failed().key == "refresh_failed"
    assert alerts.crashed("KeyError").key == "crashed:KeyError"
    assert all(i.kind == PROBLEM for i in (alerts.fetch_auth(), alerts.crashed("E")))


def test_a_card_billed_only_when_used_gets_no_late_or_gap_notes(tmp_path):
    """ICICI sends a statement only in months the card is used."""
    _seed(tmp_path, statements=[_stmt("c1", "2026-06-18"), _stmt("c1", "2026-08-18")])
    cards = tmp_path / "config" / "cards.csv"
    cards.write_text(CARDS.replace("statement_day\n", "statement_day,monthly\n")
                     .replace("C1_PW,0,1\n", "C1_PW,0,1,no\n")
                     .replace("C2_PW,0,1\n", "C2_PW,0,1,\n"), encoding="utf-8")
    keys = _by_key(items_from_files(tmp_path, date(2026, 12, 1)))
    assert not any(k.startswith(("late:c1", "gap:c1")) for k in keys)


def test_rupees_use_indian_grouping():
    from decimal import Decimal

    from creditcard.alerts import rupees

    assert rupees(Decimal("999")) == "₹999"
    assert rupees(Decimal("1000")) == "₹1,000"
    assert rupees(Decimal("100000")) == "₹1,00,000"
    assert rupees(Decimal("1234567.5")) == "₹12,34,568"
    assert rupees(Decimal("-1500")) == "-₹1,500"


def _txn_row(statement_id, subcategory, spend, merchant="SHOP"):
    return {"statement_id": statement_id, "subcategory": subcategory,
            "spend_amount": spend, "merchant_clean": merchant}


def test_a_statement_item_carries_the_money_and_where_it_went(tmp_path):
    stmt = {**_stmt("c2", "2026-09-14", due="2026-10-04"), "total_due": "12345.60",
            "min_due": "620", "period_start": "2026-08-15"}
    sid = stmt["statement_id"]
    _seed(tmp_path, statements=[stmt])
    _csv(tmp_path / "output" / "transactions.csv", [
        _txn_row(sid, "Restaurants", "500", SECRET_MERCHANT), _txn_row(sid, "Restaurants", "300"),
        _txn_row(sid, "Groceries", "1000"), _txn_row(sid, "EMI", "2000"),
        _txn_row(sid, "Groceries", "-100"), _txn_row(sid, "Payment", ""),
        _txn_row("another_statement", "Restaurants", "9999"),
    ])
    item = _by_key(items_from_files(tmp_path, date(2026, 9, 26)))[f"statement:{sid}"]
    assert item.text.splitlines() == [
        "SBI Elite: September statement imported (15 Aug to 14 Sep).",
        "Total due ₹12,346, minimum ₹620, due 4 Oct 2026 (in 8 days).",
        "Spent ₹3,700 in 4 transactions: EMI ₹2,000 (54%), Groceries ₹900 (24%), "
        "Restaurants ₹800 (22%).",
    ]
    assert SECRET_MERCHANT not in item.text


def test_a_past_due_date_and_an_unread_period_are_left_out(tmp_path):
    stmt = {**_stmt("c2", "2026-02-03", due="2026-02-03"), "period_start": "1970-01-01"}
    _seed(tmp_path, statements=[stmt])
    text = _by_key(items_from_files(tmp_path, date(2026, 9, 26)))["statement:c2_2026-02-03"].text
    assert text.splitlines()[0] == "SBI Elite: February statement imported."
    assert "due 3 Feb 2026." in text and "days" not in text
    assert "No spending this cycle." in text


def test_each_problem_says_how_to_fix_it(tmp_path):
    _seed(
        tmp_path,
        statements=[_stmt("c1", "2026-06-18"), _stmt("c1", "2026-08-18")],
        ingest=[{"source_file": r"statements\c1\x.pdf", "status": "failed",
                 "error": "Could not decrypt x.pdf"}],
        unmatched=["u.pdf"],
    )
    items = _by_key(items_from_files(tmp_path, date(2026, 10, 1)))
    assert "cards vault set C1_PW" in items["failed:c1/x.pdf"].text
    assert "config/cards.csv" in items["unmatched:u.pdf"].text
    assert "monthly=no" in items["late:c1:2026-09"].text
    assert "statements/c1/" in items["gap:c1:2026-07"].text
