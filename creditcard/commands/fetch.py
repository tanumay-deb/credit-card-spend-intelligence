"""cards fetch: download statement PDFs from Gmail into statements/<card_id>/."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from creditcard.email_fetcher import fetch_and_process_statements
from creditcard.pipeline import run as run_pipeline


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--days",
        type=int,
        default=60,
        help="Search emails from the last N days (default: 60)",
    )
    parser.add_argument(
        "--months",
        "-m",
        type=int,
        default=None,
        help="Search emails from the last N months (e.g. --months 6)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search all emails without date restriction",
    )
    parser.add_argument(
        "--unread-only",
        action="store_true",
        help="Only search unread emails",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidate statement emails without downloading",
    )
    parser.add_argument(
        "--card",
        default=None,
        help="Filter downloads to a specific card_id",
    )
    parser.add_argument(
        "--folders",
        "--folder",
        default=None,
        help="Comma-separated list of IMAP folders to scan (e.g. 'INBOX, Bank Statements')",
    )
    parser.add_argument(
        "--list-folders",
        action="store_true",
        help="List all available folders/labels on the IMAP server and exit",
    )
    parser.add_argument(
        "--sender",
        default=None,
        help="Search emails from a specific sender (e.g. 'hdfcbank', 'icicibank', 'sbicard')",
    )
    parser.add_argument(
        "--subject",
        default=None,
        help="Search emails matching a specific subject keyword (e.g. 'statement', 'Swiggy', 'Millennia')",
    )
    parser.add_argument(
        "--search-all",
        action="store_true",
        help="Search all emails without filtering by bank senders or statement keywords",
    )
    parser.add_argument(
        "--csv-out",
        default=None,
        help="Custom path for statement export CSV file (default: output/fetched_statements[_preview].csv)",
    )
    parser.add_argument(
        "--excel-out",
        default=None,
        help="Custom path for statement export Excel file "
        "(default: output/fetched_statements[_preview].xlsx)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Run ingest.py immediately after downloading statements",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    root = args.root

    # Handle --list-folders
    if args.list_folders:
        from creditcard.email_fetcher import connect_imap, list_imap_folders, load_email_config

        print("Connecting to email to list available folders...")
        try:
            cfg = load_email_config(root / "config" / "secrets.env")
            client = connect_imap(cfg)
            try:
                available_folders = list_imap_folders(client)
                print(f"Available IMAP folder(s) ({len(available_folders)}):")
                for f in available_folders:
                    print(f"  - {f}")
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
            return 0
        except Exception as exc:
            print(f"Error listing folders: {exc}", file=sys.stderr)
            return 1

    if args.all:
        days_back = None
    elif args.months is not None:
        days_back = args.months * 30
    else:
        days_back = args.days

    print(f"Connecting to email and searching statement emails (days={days_back or 'ALL'})...")
    if args.dry_run:
        print("Running in DRY-RUN mode (no files will be saved).")

    try:
        results = fetch_and_process_statements(
            root_dir=root,
            days_back=days_back,
            unread_only=args.unread_only,
            dry_run=args.dry_run,
            card_filter=args.card,
            folders=args.folders,
            sender_filter=args.sender,
            subject_filter=args.subject,
            search_all=args.search_all,
            csv_out=args.csv_out,
            excel_out=args.excel_out,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    downloaded = [r for r in results if r.status == "downloaded"]
    skipped = [r for r in results if r.status in ("skipped_exists", "skipped_hash")]
    unmatched = [r for r in results if r.status == "unmatched"]

    print(f"Processed {len(results)} attachment(s):")
    print(f"  {len(downloaded)} downloaded")
    print(f"  {len(skipped)} skipped (already downloaded)")
    if unmatched:
        print(f"  {len(unmatched)} unrecognised (saved to statements/_unmatched/)")

    for r in downloaded:
        print(f"  + [{r.card_id}] {r.filename} ({r.email_date}) -> {r.saved_path}")

    for r in unmatched:
        print(f"  ? [UNMATCHED] {r.filename} from '{r.sender}' ('{r.subject}') -> {r.saved_path}")

    stem = "fetched_statements_preview" if args.dry_run else "fetched_statements"
    csv_file = Path(args.csv_out) if args.csv_out else root / "output" / f"{stem}.csv"
    excel_file = Path(args.excel_out) if args.excel_out else root / "output" / f"{stem}.xlsx"

    print("\nStatement manifest saved to:")
    if csv_file.exists():
        print(f"  CSV:   {csv_file}")
    if excel_file.exists():
        print(f"  Excel: {excel_file}")

    # Optional automated ingestion
    if args.ingest:
        if downloaded and not args.dry_run:
            print("\nRunning ingest pipeline on updated statements...")
            summary = run_pipeline(root=root)
            print(
                f"Ingest complete: {summary['files_processed']} file(s) processed, "
                f"{summary['transactions']} transactions."
            )
            if summary["failed"] or summary["warnings"]:
                return 1
        else:
            print("\nSkipping ingest: no new statements were downloaded.")

    return 0

