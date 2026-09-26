"""Writes the four script-owned CSVs. Always writes a header, even when empty,
so Power BI can bind a schema before the first statement exists.

Written as utf-8-sig. review_queue.csv is opened by hand in Excel to copy
merchant names into merchant_map.csv, and Excel garbles UTF-8 without a BOM.
Power BI auto-detects the BOM, and Task 13 reads these files back with the same
encoding.
"""
import csv
import os
from dataclasses import fields
from datetime import date
from decimal import Decimal
from pathlib import Path

from creditcard.models import (
    EmiPlan,
    IngestLogRow,
    Payment,
    StatementSummary,
    Transaction,
)

ENCODING = "utf-8-sig"

TRANSACTION_COLUMNS = [f.name for f in fields(Transaction)]
STATEMENT_COLUMNS = [f.name for f in fields(StatementSummary)]
INGEST_LOG_COLUMNS = [f.name for f in fields(IngestLogRow)]
EMI_COLUMNS = [f.name for f in fields(EmiPlan)]
PAYMENT_COLUMNS = [f.name for f in fields(Payment)]
REVIEW_QUEUE_COLUMNS = [
    "merchant_clean", "times_seen", "total_amount", "example_txn_date", "example_card",
]
WORKBOOK = "credit_card_data.xlsx"


class OutputLocked(Exception):
    """An output file is open in another program, so it cannot be replaced."""

    def __init__(self, path: Path):
        self.path = Path(path)
        super().__init__(f"{self.path.name} is open in another program")


def is_locked(path: Path) -> bool:
    """True when another program holds path open against writing. Excel does,
    for every workbook and CSV it has open."""
    if not Path(path).exists():
        return False
    try:
        with open(path, "a"):
            return False
    except PermissionError:
        return True


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _write(rows: list[dict], columns: list[str], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding=ENCODING) as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_cell(row.get(col)) for col in columns])


def _as_dict(obj) -> dict:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def write_transactions(transactions: list[Transaction], path: Path) -> None:
    _write([_as_dict(t) for t in transactions], TRANSACTION_COLUMNS, path)


def write_statements(statements: list[StatementSummary], path: Path) -> None:
    _write([_as_dict(s) for s in statements], STATEMENT_COLUMNS, path)


def write_review_queue(rows: list[dict], path: Path) -> None:
    _write(rows, REVIEW_QUEUE_COLUMNS, path)


def write_ingest_log(rows: list[IngestLogRow], path: Path) -> None:
    _write([_as_dict(r) for r in rows], INGEST_LOG_COLUMNS, path)


def write_payments(payments: list[Payment], path: Path) -> None:
    """Copy the user's payment log into output/ so Power BI reads one folder."""
    _write([_as_dict(p) for p in payments], PAYMENT_COLUMNS, path)


def write_emi_plans(plans: list[EmiPlan], path: Path) -> None:
    _write([_as_dict(p) for p in plans], EMI_COLUMNS, path)


def _excel_val(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def write_excel_workbook(
    transactions: list[Transaction],
    statements: list[StatementSummary],
    emi_plans: list[EmiPlan],
    payments: list[Payment],
    review: list[dict],
    ingest_log: list[IngestLogRow],
    path: Path,
) -> Path | None:
    """Write all pipeline outputs into a single multi-sheet Excel workbook.

    Sheets:
      - Transactions: All parsed & categorized credit card transactions
      - Statements: Statement cycle summaries, dues, and credit limits
      - EMI_Plans: Active EMI plans detected across cards
      - Payments: Settled payments logged
      - Review_Queue: Uncategorized merchants needing review
      - Ingest_Log: Execution log of parsed statement files
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pandas as pd
        from openpyxl.styles import Font, PatternFill

        sheets = {
            "Transactions": ([_as_dict(t) for t in transactions], TRANSACTION_COLUMNS),
            "Statements": ([_as_dict(s) for s in statements], STATEMENT_COLUMNS),
            "EMI_Plans": ([_as_dict(p) for p in emi_plans], EMI_COLUMNS),
            "Payments": ([_as_dict(p) for p in payments], PAYMENT_COLUMNS),
            "Review_Queue": (review, REVIEW_QUEUE_COLUMNS),
            "Ingest_Log": ([_as_dict(r) for r in ingest_log], INGEST_LOG_COLUMNS),
        }

        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, (rows, columns) in sheets.items():
                formatted_rows = [
                    {col: _excel_val(row.get(col)) for col in columns}
                    for row in rows
                ]
                df = pd.DataFrame(formatted_rows, columns=columns)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                ws = writer.sheets[sheet_name]

                # Style header row (dark blue navy with bold white text)
                header_font = Font(bold=True, color="FFFFFF")
                header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
                for cell in ws[1]:
                    cell.font = header_font
                    cell.fill = header_fill

                # Auto-adjust column widths
                for col in ws.columns:
                    col_letter = col[0].column_letter
                    max_len = max(len(str(cell.value or "")) for cell in col)
                    ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 45)
        return path
    except Exception:
        return None


def _staged(path: Path) -> Path:
    return path.with_name(f"{path.stem}.tmp{path.suffix}")


def write_outputs(
    out: Path,
    *,
    transactions: list[Transaction],
    statements: list[StatementSummary],
    review: list[dict],
    ingest_log: list[IngestLogRow],
    emi_plans: list[EmiPlan],
    payments: list[Payment],
) -> dict:
    """Write every output, or leave every CSV as it was.

    Each file goes to a temporary name first, and they are swapped in only once
    all exist and no CSV is held open elsewhere. Writing them one by one let a
    CSV open in Excel crash the run halfway, leaving this run's transactions
    beside last run's statements. The workbook is a convenience copy: if it
    alone is open, the CSVs Power BI reads are still replaced and the workbook
    is skipped.

    Raises OutputLocked, having changed nothing, when a CSV is open elsewhere.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    csv_writers = {
        "transactions.csv": lambda p: write_transactions(transactions, p),
        "statements.csv": lambda p: write_statements(statements, p),
        "review_queue.csv": lambda p: write_review_queue(review, p),
        "ingest_log.csv": lambda p: write_ingest_log(ingest_log, p),
        "emi_plans.csv": lambda p: write_emi_plans(emi_plans, p),
        "payments.csv": lambda p: write_payments(payments, p),
    }
    workbook = out / WORKBOOK
    workbook_tmp = _staged(workbook)
    staged: dict[Path, Path] = {}
    try:
        for name, write in csv_writers.items():
            target = out / name
            staged[target] = _staged(target)
            write(staged[target])
        workbook_written = write_excel_workbook(
            transactions, statements, emi_plans, payments, review, ingest_log, workbook_tmp
        ) is not None

        for target in staged:
            if is_locked(target):
                raise OutputLocked(target)
        for target, tmp in staged.items():
            os.replace(tmp, target)

        if not workbook_written:
            return {"excel_file": None, "workbook_skipped": False}
        if is_locked(workbook):
            return {"excel_file": None, "workbook_skipped": True}
        os.replace(workbook_tmp, workbook)
        return {"excel_file": workbook, "workbook_skipped": False}
    finally:
        for tmp in [*staged.values(), workbook_tmp]:
            tmp.unlink(missing_ok=True)

