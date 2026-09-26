"""One daily check: fetch, import, refresh, tell, remember.

Task Scheduler runs this at noon through daily_run.py. Every outside
dependency -- Gmail, the import, Power BI, the email and the notification --
is a parameter, so the tests drive the whole sequence with fakes.
"""
import csv
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from creditcard import alerts, dashboard, notify
from creditcard.alerts import Item, Memory, source_key
from creditcard.backup import back_up, destinations
from creditcard.config import load_cards, load_secrets
from creditcard.email_fetcher import EmailAuthError, fetch_and_process_statements
from creditcard.pipeline import run as run_import
from creditcard.writers import OutputLocked

log = logging.getLogger(__name__)

FETCH_DAYS = 60
# A network error usually clears by itself; two failed runs in a row is worth
# telling. A rejected login never clears by itself and is told at once.
NETWORK_FAILURES_TO_TELL = 2
DETAILS_IN_EMAIL = "Details are in your email."
EMAIL_FAILED = "The email couldn't be sent: see output/logs/daily_run.log"


@dataclass(frozen=True)
class RunReport:
    downloaded: int
    imported: bool
    refresh: str | None
    told: tuple[Item, ...]
    delivered: bool
    crashed: str | None


def _fetch(root: Path):
    return fetch_and_process_statements(root_dir=root, days_back=FETCH_DAYS)


def _backup(root: Path) -> list[Path]:
    return back_up(root, destinations()).failed


def run(
    root: Path,
    today: date,
    *,
    fetch=_fetch,
    import_=run_import,
    refresh=dashboard.refresh,
    backup=_backup,
    send_email=notify.send_email,
    show_toast=notify.show_toast,
) -> RunReport:
    root = Path(root)
    memory_path = root / "output" / "notified.json"
    memory = Memory.load(memory_path)
    run_items: list[Item] = []
    downloaded, imported, refreshed, crashed = 0, False, None, None
    try:
        load_secrets(root / "config" / "secrets.env")
        downloaded = _fetch_step(root, fetch, memory, run_items)
        if downloaded or memory.import_pending or _unimported_pdfs(root):
            imported = _import_step(root, import_, memory, run_items)
        if imported:
            refreshed = refresh()
            if refreshed == dashboard.FAILED:
                run_items.append(alerts.refresh_failed())
        run_items += _backup_step(root, backup)
        current = alerts.items_from_files(root, today) + run_items
    except Exception as exc:  # noqa: BLE001 - a crash must still be told
        crashed = type(exc).__name__
        log.exception("The daily check crashed")
        current = run_items + [alerts.crashed(crashed)]

    fresh = memory.fresh(current)
    delivered = _tell(fresh, refreshed, send_email, show_toast) if fresh else True
    memory.settle(current, fresh, delivered, partial=crashed is not None)
    memory.save(memory_path)
    log.info(
        "Checked: %d downloaded, imported %s, Power BI %s, told %d item(s)%s%s",
        downloaded, "yes" if imported else "no", refreshed or "untouched", len(fresh),
        "" if delivered else " but the email failed", f", crashed with {crashed}" if crashed else "",
    )
    return RunReport(downloaded, imported, refreshed, tuple(fresh), delivered, crashed)


def preview(root: Path, today: date) -> notify.Message | None:
    """What a check would tell now, from the files as they stand. Fetches,
    imports, sends and remembers nothing."""
    memory = Memory.load(Path(root) / "output" / "notified.json")
    fresh = memory.fresh(alerts.items_from_files(root, today))
    return notify.compose(fresh, None) if fresh else None


def _fetch_step(root: Path, fetch, memory: Memory, run_items: list[Item]) -> int:
    try:
        results = fetch(root)
    except EmailAuthError:
        log.warning("Gmail rejected the login")
        memory.fetch_failures = 0
        run_items.append(alerts.fetch_auth())
        return 0
    except Exception as exc:  # noqa: BLE001 - counted, told on the second in a row
        memory.fetch_failures += 1
        log.warning("Couldn't fetch from Gmail: %s: %s (%d run(s) in a row)",
                    type(exc).__name__, exc, memory.fetch_failures)
        if memory.fetch_failures >= NETWORK_FAILURES_TO_TELL:
            run_items.append(alerts.fetch_network(memory.fetch_failures, type(exc).__name__))
        return 0
    memory.fetch_failures = 0
    return sum(1 for r in results if r.status == "downloaded")


def _backup_step(root: Path, backup) -> list[Item]:
    """Back up on every run. A failed backup is told, and never stops the check."""
    try:
        failed = backup(root)
    except Exception as exc:  # noqa: BLE001
        log.warning("The backup stopped: %s: %s", type(exc).__name__, exc)
        return [alerts.backup_failed("every destination")]
    return [alerts.backup_failed(str(dest)) for dest in failed]


def _unimported_pdfs(root: Path) -> int:
    """PDFs in statements/<card_id>/ that the last import didn't read.

    The fetcher's download count alone isn't enough: a PDF saved by a fetch
    that then failed, or dropped in by hand, is never downloaded again, so it
    would never be imported.
    """
    log_path = root / "output" / "ingest_log.csv"
    seen: set[str] = set()
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8-sig") as fh:
            seen = {source_key(row.get("source_file", "")) for row in csv.DictReader(fh)}
    return sum(
        1
        for card_id in load_cards(root / "config" / "cards.csv")
        for pdf in (root / "statements" / card_id).glob("*.pdf")
        if f"{card_id}/{pdf.name}" not in seen
    )


def _import_step(root: Path, import_, memory: Memory, run_items: list[Item]) -> bool:
    """True when the CSVs Power BI reads were saved. Leaves import_pending set
    whenever something is still to be written, so the next run imports again
    even if nothing new arrives."""
    memory.import_pending = True
    try:
        summary = import_(root)
    except OutputLocked as exc:
        log.warning("%s is open in another program; the import wasn't saved", exc.path.name)
        run_items.append(alerts.locked(exc.path.name))
        return False
    if summary.get("workbook_skipped"):
        run_items.append(alerts.workbook_locked())
    else:
        memory.import_pending = False
    log.info("Imported %s statement file(s)", summary.get("files_processed"))
    return True


def _tell(fresh: list[Item], refreshed: str | None, send_email, show_toast) -> bool:
    """Send the email and the notification. True if the email went out."""
    message = notify.compose(fresh, refreshed)
    delivered = True
    try:
        send_email(message)
    except Exception as exc:  # noqa: BLE001 - the notification still goes out
        delivered = False
        log.error("The email couldn't be sent: %s: %s", type(exc).__name__, exc)
    try:
        show_toast(notify.TITLE, [message.summary, DETAILS_IN_EMAIL if delivered else EMAIL_FAILED])
    except Exception as exc:  # noqa: BLE001
        log.warning("The Windows notification failed: %s: %s", type(exc).__name__, exc)
    return delivered
