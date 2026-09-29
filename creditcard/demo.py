"""Demo data: a year of made-up statements for four made-up cards.

`cards demo` writes it to demo/ in the same shape as the project itself, a
config/ folder and an output/ folder, and `cards dashboard --demo` points the
dashboard at it. Nothing in it comes from a real statement:
- the merchants are well-known brands;
- every amount comes from a fixed seed, so the demo is identical on every run
  and every PC.

The made-up transactions still go through the project's own categorisation,
spend, EMI and writer code, so the demo files have exactly the shape of the
real ones.
"""
from __future__ import annotations

import random
import shutil
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from creditcard.categorise import (
    UNCATEGORISED_GROUP,
    build_review_queue,
    category_for_type,
    resolve_category,
)
from creditcard.config import load_categories, load_rules
from creditcard.emi import add_months, latest_emi_snapshots
from creditcard.models import EmiPlan, IngestLogRow, StatementSummary, Transaction
from creditcard.spend import apply_spend
from creditcard.writers import write_outputs

CENTS = Decimal("0.01")
MONTHS = 12


@dataclass(frozen=True)
class DemoCard:
    card_id: str
    issuer: str
    name: str
    parser: str
    limit: int
    limit_group: str
    statement_day: int


CARDS = (
    DemoCard("hdfc_millennia", "HDFC", "HDFC Millennia", "hdfc", 200000, "hdfc_millennia", 18),
    DemoCard("icici_coral", "ICICI", "ICICI Coral", "icici", 150000, "icici_coral", 12),
    DemoCard("sbi_simplyclick", "SBI", "SBI SimplyCLICK", "sbi", 250000, "sbi_pool", 5),
    DemoCard("sbi_elite", "SBI", "SBI Elite", "sbi", 250000, "sbi_pool", 5),
)

# (merchant as a statement prints it, lowest amount, highest amount, times a month,
# cards it turns up on). The keyword rules in RULES file each one.
MERCHANTS = (
    ("SWIGGY BANGALORE", 180, 950, 5.0, ("hdfc_millennia", "sbi_simplyclick")),
    ("ZOMATO ONLINE ORDER", 220, 1100, 3.0, ("hdfc_millennia", "sbi_simplyclick")),
    ("SWIGGY INSTAMART", 250, 1800, 3.0, ("sbi_simplyclick",)),
    ("BLINKIT", 200, 1500, 3.0, ("sbi_simplyclick", "hdfc_millennia")),
    ("ZEPTO MARKETPLACE", 180, 1200, 2.0, ("sbi_simplyclick",)),
    ("BIGBASKET", 600, 3200, 1.0, ("hdfc_millennia",)),
    ("DMART READY", 900, 4200, 1.0, ("sbi_elite",)),
    ("DOMINOS PIZZA", 320, 950, 1.0, ("hdfc_millennia",)),
    ("STARBUCKS COFFEE", 280, 760, 1.5, ("sbi_elite",)),
    ("AMAZON PAY INDIA", 350, 7800, 4.0, ("icici_coral",)),
    ("FLIPKART INTERNET", 450, 6200, 1.0, ("sbi_simplyclick",)),
    ("MYNTRA DESIGNS", 800, 3600, 0.8, ("sbi_simplyclick", "hdfc_millennia")),
    ("CROMA RETAIL", 2500, 38000, 0.15, ("hdfc_millennia",)),
    ("UBER INDIA", 160, 720, 4.0, ("hdfc_millennia", "sbi_elite")),
    ("OLA CABS", 150, 640, 1.5, ("sbi_elite",)),
    ("IRCTC RAIL", 450, 3200, 0.5, ("sbi_elite",)),
    ("INDIGO AIRLINES", 3800, 11500, 0.2, ("sbi_elite",)),
    ("HPCL FUEL STATION", 600, 2800, 1.5, ("sbi_elite",)),
    ("OYO ROOMS", 1400, 5200, 0.2, ("sbi_elite",)),
    ("AIRTEL PAYMENTS", 499, 499, 1.0, ("icici_coral",)),
    ("BESCOM ELECTRICITY", 900, 2600, 1.0, ("icici_coral",)),
    ("NETFLIX COM", 649, 649, 1.0, ("hdfc_millennia",)),
    ("SPOTIFY INDIA", 119, 119, 1.0, ("hdfc_millennia",)),
    ("BOOK MY SHOW", 420, 1400, 0.7, ("hdfc_millennia",)),
    ("STEAM GAMES", 300, 2400, 0.3, ("hdfc_millennia",)),
    ("APOLLO PHARMACY", 220, 1600, 0.8, ("sbi_elite",)),
    ("COURSERA", 2800, 3900, 0.1, ("hdfc_millennia",)),
    ("UPI SHARMA GENERAL STORE", 80, 650, 2.0, ("sbi_simplyclick",)),
)

RULES = (
    (5, "SWIGGY INSTAMART", "Quick Commerce"), (10, "SWIGGY", "Swiggy"),
    (11, "ZOMATO", "Zomato"), (12, "BLINKIT", "Quick Commerce"),
    (13, "ZEPTO", "Quick Commerce"), (14, "BIGBASKET", "Quick Commerce"),
    (15, "DMART", "Groceries"), (16, "DOMINOS", "Restaurants"),
    (17, "STARBUCKS", "Restaurants"), (20, "AMAZON", "Amazon"),
    (21, "FLIPKART", "Flipkart"), (22, "MYNTRA", "Clothing"),
    (23, "CROMA", "Electronics"), (30, "UBER", "Cabs"), (31, "OLA", "Cabs"),
    (32, "IRCTC", "Travel"), (33, "INDIGO", "Flights"), (34, "HPCL", "Fuel"),
    (35, "OYO", "Hotels"), (40, "AIRTEL", "Telecom"), (41, "BESCOM", "Utilities"),
    (42, "NETFLIX", "Streaming"), (43, "SPOTIFY", "Streaming"),
    (44, "BOOK MY SHOW", "Events"), (45, "STEAM", "Gaming"),
    (46, "APOLLO PHARMACY", "Health"), (47, "COURSERA", "Education"),
)


def build(root: Path, today: date | None = None, seed: int = 7) -> Path:
    """Write demo/config and demo/output under root. Returns the demo folder."""
    root = Path(root)
    demo = root / "demo"
    config = demo / "config"
    config.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / "config" / "categories.csv", config / "categories.csv")
    _write_config(config)

    categories = load_categories(config / "categories.csv")
    rules = load_rules(config / "category_rules.csv")
    rng = random.Random(seed)
    today = today or date.today()

    transactions: list[Transaction] = []
    statements: list[StatementSummary] = []
    plans: list[EmiPlan] = []
    for card in CARDS:
        card_txns, card_statements, card_plans = _card_year(card, today, rng)
        transactions += card_txns
        statements += card_statements
        plans += card_plans

    transactions = [_categorised(t, categories, rules) for t in transactions]
    spend = apply_spend(sorted(transactions, key=lambda t: (t.txn_date, t.txn_id)))
    statements.sort(key=lambda s: (s.statement_date, s.statement_id))
    write_outputs(
        demo / "output",
        transactions=spend.transactions,
        statements=statements,
        review=build_review_queue(spend.transactions),
        ingest_log=[_log_row(s) for s in statements],
        emi_plans=latest_emi_snapshots(plans, statements),
        payments=[],
    )
    return demo


def _write_config(config: Path) -> None:
    (config / "cards.csv").write_text(
        "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day,"
        "limit_group,monthly,closed\n"
        + "".join(f"{c.card_id},{c.issuer},{c.name},0000,{c.parser},DEMO_PDF_PASSWORD,"
                  f"{c.limit},{c.statement_day},{c.limit_group},yes,\n" for c in CARDS),
        encoding="utf-8",
    )
    (config / "category_rules.csv").write_text(
        "priority,pattern,subcategory\n"
        + "".join(f"{p},{pattern},{sub}\n" for p, pattern, sub in RULES),
        encoding="utf-8",
    )
    (config / "merchant_map.csv").write_text("merchant_clean,subcategory\n", encoding="utf-8")
    (config / "payments.csv").write_text("card_id,payment_date,amount,note\n", encoding="utf-8")


def _card_year(card: DemoCard, today: date, rng: random.Random):
    """Twelve monthly statements for one card, with their transactions."""
    last = date(today.year, today.month, min(card.statement_day, 28))
    if last >= today:
        last = add_months(last, -1)
    cycle_ends = [add_months(last, -k) for k in range(MONTHS - 1, -1, -1)]
    emi = _emi_plan(card, cycle_ends[0])

    transactions: list[Transaction] = []
    statements: list[StatementSummary] = []
    plans: list[EmiPlan] = []
    previous_due = Decimal("0")
    for number, end in enumerate(cycle_ends):
        start = add_months(end, -1) + timedelta(days=1)
        sid = f"{card.card_id}_{end.isoformat()}"
        rows: list[Transaction] = []

        def add(when: date, text: str, amount: Decimal, direction: str, kind: str,
                sid: str = sid, rows: list[Transaction] = rows) -> None:
            rows.append(_txn(card, sid, when, text, amount, direction, kind, len(rows)))

        for merchant, low, high, per_month, cards in MERCHANTS:
            if card.card_id not in cards:
                continue
            for _ in range(_count(per_month, rng)):
                when = start + timedelta(days=rng.randrange((end - start).days + 1))
                add(when, merchant, _amount(low, high, rng), "debit", "purchase")
        # One order in six comes back: a refund of the same amount, a few days on.
        for original in [r for r in rows if r.txn_type == "purchase"]:
            if rng.random() < 1 / 6 and original.amount > 300:
                when = min(original.txn_date + timedelta(days=rng.randint(2, 9)), end)
                add(when, original.merchant_clean, original.amount, "credit", "refund")
        if emi and number >= emi["first"] and number < emi["first"] + emi["months"]:
            left = emi["first"] + emi["months"] - number - 1
            add(end - timedelta(days=2), f"EMI {emi['months'] - left} OF {emi['months']} "
                f"{emi['name']}", emi["instalment"], "debit", "emi")
            plans.append(_emi_snapshot(card, sid, emi, left))
        if previous_due > 0:
            add(start + timedelta(days=rng.randint(8, 16)), "PAYMENT RECEIVED THANK YOU",
                previous_due, "credit", "payment")
        if card.card_id == "sbi_simplyclick":
            online = sum((r.amount for r in rows if r.txn_type == "purchase"), Decimal("0"))
            add(end, "CASHBACK CREDIT", (online * Decimal("0.05")).quantize(CENTS),
                "credit", "cashback")
        if number == MONTHS - 5 and card.card_id == "hdfc_millennia":
            add(end - timedelta(days=1), "ANNUAL FEE", Decimal("1000.00"), "debit", "fee")
            add(end - timedelta(days=1), "IGST ON ANNUAL FEE", Decimal("180.00"), "debit", "fee")

        debits = sum((r.amount for r in rows if r.direction == "debit"), Decimal("0"))
        paid = sum((r.amount for r in rows if r.txn_type == "payment"), Decimal("0"))
        credits = sum((r.amount for r in rows
                       if r.direction == "credit" and r.txn_type != "payment"), Decimal("0"))
        total_due = max(previous_due - paid - credits + debits, Decimal("0"))
        statements.append(StatementSummary(
            statement_id=sid, card_id=card.card_id, statement_date=end,
            due_date=end + timedelta(days=20), period_start=start, period_end=end,
            previous_balance=previous_due, payments=paid, purchases=debits,
            total_due=total_due, min_due=max((total_due * Decimal("0.05")).quantize(
                Decimal("1"), ROUND_HALF_UP), Decimal("200")) if total_due else Decimal("0"),
            finance_charges=Decimal("0"), late_fee=Decimal("0"),
            credit_limit=Decimal(card.limit),
            available_limit=Decimal(card.limit) - total_due, source_file=f"demo/{sid}.pdf",
        ))
        transactions += rows
        previous_due = total_due
    return transactions, statements, plans


def _emi_plan(card: DemoCard, first_cycle: date) -> dict | None:
    """A phone bought on 12 monthly instalments, starting in month 4."""
    if card.card_id != "hdfc_millennia":
        return None
    principal, months, rate = Decimal("48000"), 12, Decimal("14")
    monthly = float(rate) / 1200
    instalment = Decimal(repr(float(principal) * monthly / (1 - (1 + monthly) ** -months)))
    return {"first": 3, "months": months, "principal": principal, "rate": rate,
            "instalment": instalment.quantize(CENTS, ROUND_HALF_UP),
            "start": add_months(first_cycle, 3), "name": "SMARTPHONE EMI"}


def _emi_snapshot(card: DemoCard, sid: str, emi: dict, left: int) -> EmiPlan:
    monthly = float(emi["rate"]) / 1200
    e = float(emi["instalment"])
    principal_left = Decimal(repr(e * (1 - (1 + monthly) ** -left) / monthly)) if left else Decimal(0)
    principal_left = principal_left.quantize(CENTS, ROUND_HALF_UP)
    return EmiPlan(
        card_id=card.card_id, statement_id=sid, loan_ref="DEMO-EMI-1", loan_type="Smart EMI",
        start_date=emi["start"], end_date=add_months(emi["start"], emi["months"] - 1),
        principal=emi["principal"], tenure_months=emi["months"], remaining_months=left,
        interest_rate=emi["rate"], instalment_amount=emi["instalment"],
        interest_payable=(emi["instalment"] * left - principal_left).quantize(CENTS),
        outstanding=principal_left, source_file=f"demo/{sid}.pdf",
    )


def _txn(card: DemoCard, sid: str, when: date, text: str, amount: Decimal, direction: str,
         kind: str, n: int) -> Transaction:
    return Transaction(
        txn_id=f"{sid}_{n:03d}", card_id=card.card_id, statement_id=sid, txn_date=when,
        posting_date=when, merchant_raw=text, merchant_clean=text, amount=amount,
        direction=direction, txn_type=kind, category_group=UNCATEGORISED_GROUP,
        subcategory="Uncategorised", category_source="unmatched", is_forex=False,
        forex_ccy=None, forex_amount=None, source_file=f"demo/{sid}.pdf",
    )


def _categorised(txn: Transaction, categories, rules) -> Transaction:
    forced = category_for_type(txn.txn_type)
    if forced is not None:
        return replace(txn, category_group=categories.get(forced, UNCATEGORISED_GROUP),
                       subcategory=forced, category_source="type")
    group, sub, source = resolve_category(txn.merchant_clean, {}, rules, categories)
    return replace(txn, category_group=group, subcategory=sub, category_source=source)


def _log_row(statement: StatementSummary) -> IngestLogRow:
    return IngestLogRow(
        source_file=statement.source_file, file_hash="demo", parser="demo",
        run_timestamp=f"{statement.statement_date.isoformat()}T12:00:00", rows_found=0,
        statement_purchases=statement.purchases, sum_check_delta=Decimal("0"),
        status="ok", error="",
    )


def _count(per_month: float, rng: random.Random) -> int:
    whole = int(per_month)
    return whole + (1 if rng.random() < per_month - whole else 0) + rng.choice((-1, 0, 0, 1)) * (whole > 2)


def _amount(low: int, high: int, rng: random.Random) -> Decimal:
    return Decimal(str(round(rng.uniform(low, high), 2))).quantize(CENTS)
