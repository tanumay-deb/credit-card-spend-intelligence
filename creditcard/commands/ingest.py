"""cards ingest: import every statement PDF into output/."""
import argparse
import logging
import sys

from creditcard.pipeline import run as run_pipeline
from creditcard.writers import OutputLocked


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch new statement PDFs from email before ingesting",
    )
    parser.add_argument(
        "--fetch-days",
        type=int,
        default=60,
        help="Days to look back when fetching from email (default: 60)",
    )
    parser.add_argument(
        "--fetch-months",
        type=int,
        default=None,
        help="Months to look back when fetching from email (e.g. --fetch-months 6)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    root = args.root

    if args.fetch:
        from creditcard.email_fetcher import fetch_and_process_statements

        days = args.fetch_months * 30 if args.fetch_months is not None else args.fetch_days
        print(f"Fetching statement emails before ingestion (looking back {days} days)...")
        try:
            fetch_results = fetch_and_process_statements(root_dir=root, days_back=days)
            downloaded = sum(1 for r in fetch_results if r.status == "downloaded")
            print(f"Downloaded {downloaded} new statement(s) from email.")
        except Exception as exc:
            print(f"Warning: email fetch failed ({exc}). Continuing with local files.",
                  file=sys.stderr)

    try:
        summary = run_pipeline(root=root)
    except OutputLocked as exc:
        print(
            f"output/{exc.path.name} is open in another program (probably Excel). "
            "Close it and run again - no output file was changed.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Processed {summary['files_processed']} file(s): "
        f"{summary['transactions']} transactions, "
        f"{summary['statements']} statements."
    )
    if summary.get("excel_file"):
        print(f"Generated CSVs and consolidated Excel workbook at: {summary['excel_file']}")
    if summary.get("workbook_skipped"):
        print(
            "  credit_card_data.xlsx is open in another program, so it was not "
            "updated - the CSVs were"
        )
    if summary["failed"]:
        print(f"  {summary['failed']} file(s) FAILED - see output/ingest_log.csv")
    if summary["warnings"]:
        print(
            f"  {summary['warnings']} file(s) did not reconcile - "
            "check sum_check_delta in output/ingest_log.csv"
        )
    if summary["unmatched_merchants"]:
        print(
            f"  {summary['unmatched_merchants']} merchant(s) need tagging - "
            "see output/review_queue.csv"
        )
    if summary["unmatched_refunds"]:
        print(
            f"  {summary['unmatched_refunds']} refund(s) matched no earlier purchase - "
            "they keep their own category"
        )
    if summary["unmatched_reversals"]:
        print(
            f"  {summary['unmatched_reversals']} returned autopay(s) matched no payment - "
            "check output/transactions.csv"
        )
    for card_id, statement_id, logged, reported in summary["payment_mismatches"]:
        print(
            f"  {card_id}: logged {logged} in payments.csv but statement "
            f"{statement_id} reports {reported}"
        )
    for card_id, month in summary["missing_cycles"]:
        print(f"  no statement for {card_id} in {month}")
    for folder in summary["unknown_folders"]:
        print(
            f"  statements/{folder}/ has PDFs but matches no card_id in "
            "config/cards.csv - nothing in it was imported"
        )

    # A failed parse means data is missing outright, and a non-zero
    # sum_check_delta means a statement reconciled against itself and lost -
    # both are silent data loss unless the exit code says so.
    return 1 if (summary["failed"] or summary["warnings"]) else 0
