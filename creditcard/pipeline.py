"""Stage orchestration: accumulate, dedup, and rewrite outputs."""
import hashlib
import logging
import os
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from creditcard import config as config_mod
from creditcard import writers
from creditcard.categorise import (
    UNCATEGORISED_GROUP,
    build_review_queue,
    category_for_type,
    resolve_category,
)
from creditcard.emi import latest_emi_snapshots
from creditcard.models import Card, EmiPlan, IngestLogRow, Rule, StatementSummary, Transaction
from creditcard.parsers.base import PdfPasswordError, extract_text
from creditcard.parsers.registry import UnknownParserError, get_parser
from creditcard.reconcile import check_payments, find_missing_cycles, sum_check
from creditcard.spend import apply_spend

log = logging.getLogger(__name__)


def file_hash(path: Path) -> str:
    """SHA-256 of file bytes, recorded in ingest_log.csv for traceability so a
    row can be traced back to the exact bytes it came from."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def merge_transactions(
    prior: list[Transaction], fresh: list[Transaction]
) -> list[Transaction]:
    """Union by txn_id. Fresh rows win, so re-running after editing
    merchant_map.csv retags historical transactions."""
    by_id = {t.txn_id: t for t in prior}
    by_id.update({t.txn_id: t for t in fresh})
    return sorted(by_id.values(), key=lambda t: (t.txn_date, t.txn_id))


def merge_statements(
    prior: list[StatementSummary], fresh: list[StatementSummary]
) -> list[StatementSummary]:
    by_id = {s.statement_id: s for s in prior}
    by_id.update({s.statement_id: s for s in fresh})
    return sorted(by_id.values(), key=lambda s: (s.statement_date, s.statement_id))


def run(root: Path) -> dict:
    """Execute the full pipeline. Returns a small summary dict.

    Every PDF present is reparsed on every run. At five cards this costs
    seconds, and it means the outputs are always a pure function of what is in
    statements/ plus config/ -- no partial state to drift or repair. It is also
    what makes retagging work: edit merchant_map.csv, re-run, and history is
    recategorised along with everything else.
    """
    root = Path(root)
    cfg, out = root / "config", root / "output"
    out.mkdir(parents=True, exist_ok=True)

    config_mod.load_secrets(cfg / "secrets.env")
    cards = config_mod.load_cards(cfg / "cards.csv")
    categories = config_mod.load_categories(cfg / "categories.csv")
    rules = config_mod.load_rules(cfg / "category_rules.csv")
    merchant_map = config_mod.load_merchant_map(cfg / "merchant_map.csv")
    payments = config_mod.load_payments(cfg / "payments.csv")

    parsed: list[Transaction] = []
    summaries: list[StatementSummary] = []
    emi_plans: list[EmiPlan] = []
    log_rows: list[IngestLogRow] = []

    for card in sorted(cards.values(), key=lambda c: c.card_id):
        # Keyed by card_id, not issuer: two SBI cards would otherwise each
        # claim the other's statements and mislabel half the rows.
        card_dir = root / "statements" / card.card_id
        if not card_dir.exists():
            continue
        for pdf_path in sorted(card_dir.glob("*.pdf")):
            row, txns, statement, plans = _process_one(
                pdf_path, card, categories, rules, merchant_map
            )
            log_rows.append(row)
            parsed.extend(txns)
            emi_plans.extend(plans)
            if statement is not None:
                summaries.append(statement)

    # A folder name that matches no card_id (an easy typo -- sbi_bpcl vs
    # sbi_simplyclick) is never visited by the loop above, so its PDFs would
    # otherwise never be imported, silently. Nothing in statements/ is allowed
    # to be silently invisible, so anything left over is reported here.
    statements_dir = root / "statements"
    unknown_folders = sorted(
        entry.name
        for entry in (statements_dir.iterdir() if statements_dir.exists() else [])
        if entry.is_dir() and entry.name not in cards and any(entry.glob("*.pdf"))
    )

    # Merging against an empty list still dedups by id, which is what catches
    # the same statement saved twice under different filenames.
    transactions = merge_transactions([], parsed)
    # Refunds cancel the purchases they reverse, so spend is settled before
    # anything reads categories or totals.
    spend = apply_spend(transactions)
    transactions = spend.transactions
    statements = merge_statements([], summaries)
    # Every statement re-lists every running plan, so a year of statements
    # would report a nine-month loan nine times. One row per loan.
    emi_plans = latest_emi_snapshots(emi_plans, statements)
    review = build_review_queue(transactions)
    gaps = find_missing_cycles(
        statements, skip={c.card_id for c in cards.values() if not c.monthly}
    )
    payment_mismatches = check_payments(statements, payments)

    # All or nothing: raises OutputLocked, having changed nothing, when a CSV
    # is open in another program.
    written = writers.write_outputs(
        out,
        transactions=transactions,
        statements=statements,
        review=review,
        ingest_log=log_rows,
        emi_plans=emi_plans,
        payments=payments,
    )

    return {
        "files_processed": len(log_rows),
        "failed": sum(1 for r in log_rows if r.status == "failed"),
        "warnings": sum(1 for r in log_rows if r.status == "warning"),
        "transactions": len(transactions),
        "statements": len(statements),
        "emi_plans": len(emi_plans),
        "unmatched_merchants": len(review),
        "missing_cycles": gaps,
        "payments": len(payments),
        "payment_mismatches": payment_mismatches,
        "unknown_folders": unknown_folders,
        "excel_file": written["excel_file"],
        "workbook_skipped": written["workbook_skipped"],
        "unmatched_refunds": spend.unmatched_refunds,
        "unmatched_reversals": spend.unmatched_reversals,
    }


def _failed(base: dict, error: str) -> IngestLogRow:
    return IngestLogRow(
        **base,
        rows_found=0,
        statement_purchases=None,
        sum_check_delta=None,
        status="failed",
        error=error,
    )


def _categorise(
    txn: Transaction,
    categories: dict[str, str],
    rules: list[Rule],
    merchant_map: dict[str, str],
) -> Transaction:
    forced = category_for_type(txn.txn_type)
    if forced is not None:
        return replace(
            txn,
            category_group=categories.get(forced, UNCATEGORISED_GROUP),
            subcategory=forced,
            category_source="type",
        )

    group, sub, source = resolve_category(
        txn.merchant_clean, merchant_map, rules, categories
    )
    return replace(
        txn, category_group=group, subcategory=sub, category_source=source
    )


def _process_one(
    pdf_path: Path,
    card: Card,
    categories: dict[str, str],
    rules: list[Rule],
    merchant_map: dict[str, str],
) -> tuple[IngestLogRow, list[Transaction], StatementSummary | None, tuple]:
    """Parse one PDF. Never raises - failures are recorded and the run continues."""
    base = dict(
        source_file=str(pdf_path),
        parser=card.parser,
        run_timestamp=datetime.now().isoformat(timespec="seconds"),
    )
    try:
        # Computed here, inside the try, not while building base above: a file
        # that vanishes or is locked between run()'s glob() and this call
        # (routine on Windows with OneDrive sync or an open PDF viewer) must
        # be recorded as a failed row, not crash the whole run.
        base["file_hash"] = file_hash(pdf_path)
        text = extract_text(pdf_path, os.environ.get(card.password_env, ""))
        result = get_parser(card.parser).parse(text, card, str(pdf_path))
        tagged = [
            _categorise(t, categories, rules, merchant_map)
            for t in result.transactions
        ]
        delta = sum_check(tagged, result.statement)
        return (
            IngestLogRow(
                **base,
                rows_found=len(tagged),
                statement_purchases=result.statement.purchases,
                sum_check_delta=delta,
                status="ok" if delta == Decimal("0") else "warning",
                error="",
            ),
            tagged,
            result.statement,
            result.emi_plans,
        )
    except (PdfPasswordError, UnknownParserError) as exc:
        base.setdefault("file_hash", "")
        return _failed(base, str(exc)), [], None, ()
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
        log.exception("Failed to parse %s", pdf_path)
        base.setdefault("file_hash", "")
        return _failed(base, f"{type(exc).__name__}: {exc}"), [], None, ()
