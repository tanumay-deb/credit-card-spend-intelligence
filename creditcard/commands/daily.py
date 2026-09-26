"""cards daily: the daily check, which Task Scheduler runs at noon.

It fetches new statements from Gmail, imports them, refreshes the open
dashboard, backs up, and emails you - with a Windows notification - about new
statements and anything that needs attention. Each thing is told once; see
output/logs/daily_run.log for every run.
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from creditcard import daily, notify
from creditcard.config import load_secrets


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preview",
        action="store_true",
        help="print what a check would tell you now; fetch, import, send and remember nothing",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="send one test email and one test Windows notification",
    )


def _log_to_file(root: Path) -> None:
    logs = root / "output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(logs / "daily_run.log", encoding="utf-8")
    ]
    if sys.stderr is not None:  # None under pythonw, which has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def run(args: argparse.Namespace) -> int:
    root = args.root

    if args.preview:
        # The message carries ₹, which the Windows console's legacy code page lacks.
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        message = daily.preview(root, date.today())
        print(f"{message.subject}\n\n{message.body}" if message else "Nothing new to tell.")
        return 0

    _log_to_file(root)
    if args.test_notify:
        load_secrets(root / "config" / "secrets.env")
        notify.send_email(notify.Message(
            subject=f"{notify.TITLE}: test",
            body="A test from `cards daily --test-notify`. If you can read this, "
                 "email alerts work.\n",
            summary="test",
        ))
        notify.show_toast(notify.TITLE, ["Test notification",
                                         "If you can see this, notifications work."])
        print("Sent a test email and a test notification.")
        return 0

    try:
        report = daily.run(root, date.today())
    except Exception:  # noqa: BLE001 - leave a trace for Task Scheduler's run history
        logging.getLogger("daily_run").exception("The daily check failed before it could report")
        return 1
    return 1 if report.crashed else 0
