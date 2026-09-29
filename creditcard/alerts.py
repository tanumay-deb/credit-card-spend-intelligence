"""What the daily check tells you, and what it has told you already.

An Item is one thing worth telling: a new statement, a merchant the rules
don't know, or a problem. Each has a stable key. Memory keeps the keys already
told, so a problem is told once rather than every day until it is fixed, and a
fixed problem that comes back is told again.

Item texts carry card names, months, dates, counts and sender domains only --
never amounts, merchant names, card numbers or statement file names, which
issuers build from card digits. They go out by email and to the notification
centre, both less private than the files on this PC.
"""
import csv
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from email.utils import parseaddr
from pathlib import Path, PureWindowsPath

from creditcard.config import load_cards
from creditcard.emi import add_months
from creditcard.reconcile import find_missing_cycles

log = logging.getLogger(__name__)

ENCODING = "utf-8-sig"
# A statement email lands a day or two after the statement date; a week past
# the expected date is late by any issuer's standard.
LATE_AFTER = timedelta(days=7)

STATEMENT, MERCHANT, PROBLEM = "statement", "merchant", "problem"


@dataclass(frozen=True)
class Item:
    key: str
    kind: str  # STATEMENT | MERCHANT | PROBLEM
    text: str


@dataclass(frozen=True)
class _Statement:
    statement_id: str
    card_id: str
    statement_date: date
    due_date: date | None
    source: str  # "<card_id>/<file name>"
    total_due: Decimal | None = None
    min_due: Decimal | None = None
    period_start: date | None = None


def items_from_files(root: Path, today: date) -> list[Item]:
    """Every item the output files and statements/_unmatched/ describe today."""
    root = Path(root)
    out = root / "output"
    cards = load_cards(root / "config" / "cards.csv")
    names = {card_id: card.card_name for card_id, card in cards.items()}
    password_envs = {card_id: card.password_env for card_id, card in cards.items()}
    # Billed only in months they are used, or closed: a quiet month is neither
    # late nor a gap.
    not_monthly = {card_id for card_id, card in cards.items()
                   if not card.monthly or card.closed}

    def name(card_id: str) -> str:
        return names.get(card_id, card_id)

    statements = [
        _Statement(
            statement_id=row["statement_id"],
            card_id=row["card_id"],
            statement_date=date.fromisoformat(row["statement_date"]),
            due_date=date.fromisoformat(row["due_date"]) if row.get("due_date") else None,
            source=source_key(row.get("source_file", "")),
            total_due=_money(row.get("total_due")),
            min_due=_money(row.get("min_due")),
            period_start=_real_date(row.get("period_start")),
        )
        for row in _rows(out / "statements.csv")
    ]
    return [
        *_statement_items(statements, name, _spend_by_statement(out), today),
        *_ingest_items(_rows(out / "ingest_log.csv"), statements, root, name,
                       password_envs),
        *_unmatched_items(root / "statements" / "_unmatched",
                          _rows(out / "email_fetch_log.csv")),
        *_late_items([s for s in statements if s.card_id not in not_monthly], today, name),
        *_gap_items([s for s in statements if s.card_id not in not_monthly], name),
        *(Item(f"merchant:{row['merchant_clean']}", MERCHANT, "A merchant needs a category.")
          for row in _rows(out / "review_queue.csv")),
    ]


@dataclass
class _Spend:
    net: Decimal = Decimal("0")
    charges: int = 0
    by_subcategory: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))


def _spend_by_statement(out: Path) -> dict[str, _Spend]:
    """What each statement added to spend, net of refunds, as the dashboard counts it."""
    spend: dict[str, _Spend] = defaultdict(_Spend)
    for row in _rows(out / "transactions.csv"):
        amount = _money(row.get("spend_amount"))
        if amount is None:
            continue
        entry = spend[row["statement_id"]]
        entry.net += amount
        entry.charges += amount > 0
        entry.by_subcategory[row.get("subcategory") or "Uncategorised"] += amount
    return spend


def _statement_items(statements, name, spend: dict[str, _Spend], today: date) -> list[Item]:
    """Three lines per statement: what arrived, what is owed, where the money went.

    Amounts appear because the user asked for them (2026-09-26). Merchant names
    never do; the categories say where the money went.
    """
    items = []
    for s in statements:
        period = (f" ({_day(s.period_start)} to {_day(s.statement_date)})"
                  if s.period_start else "")
        lines = [f"{name(s.card_id)}: {s.statement_date:%B} statement imported{period}."]

        owed = []
        if s.total_due is not None:
            owed.append(f"total due {rupees(s.total_due)}")
        if s.min_due is not None:
            owed.append(f"minimum {rupees(s.min_due)}")
        if s.due_date:
            owed.append(f"due {_day(s.due_date)} {s.due_date.year}{_countdown(s.due_date, today)}")
        if owed:
            sentence = ", ".join(owed) + "."
            lines.append(sentence[0].upper() + sentence[1:])

        cycle = spend.get(s.statement_id, _Spend())
        if cycle.charges or cycle.net:
            top = sorted(((sub, amount) for sub, amount in cycle.by_subcategory.items()
                          if amount > 0), key=lambda pair: pair[1], reverse=True)[:3]
            shares = ", ".join(f"{sub} {rupees(amount)} ({_percent(amount, cycle.net)})"
                               for sub, amount in top)
            plural = "" if cycle.charges == 1 else "s"
            lines.append(f"Spent {rupees(cycle.net)} in {cycle.charges} transaction{plural}"
                         + (f": {shares}." if shares else "."))
        else:
            lines.append("No spending this cycle.")
        items.append(Item(f"statement:{s.statement_id}", STATEMENT, "\n".join(lines)))
    return items


def _countdown(due: date, today: date) -> str:
    days = (due - today).days
    if days < 0:
        return ""
    return " (today)" if days == 0 else f" (in {days} day{'' if days == 1 else 's'})"


def _percent(part: Decimal, whole: Decimal) -> str:
    if whole <= 0:
        return "-"
    return f"{(part / whole * 100).quantize(Decimal('1'), ROUND_HALF_UP)}%"


def rupees(value: Decimal) -> str:
    """Whole rupees in Indian grouping, as the dashboard shows them: ₹12,34,568."""
    whole = int(value.quantize(Decimal("1"), ROUND_HALF_UP))
    digits = str(abs(whole))
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{'-' if whole < 0 else ''}₹{','.join([*groups, tail])}"


def _ingest_items(rows, statements, root: Path, name, password_envs) -> list[Item]:
    by_source = {s.source: s for s in statements}
    items = []
    for row in rows:
        source = source_key(row.get("source_file", ""))
        card_id = source.partition("/")[0]
        saved = _saved_on(root / "statements" / source)
        if row.get("status") == "failed":
            why = "wrong password?" if "decrypt" in row.get("error", "").lower() \
                else "couldn't be read"
            env = password_envs.get(card_id, "NAME")
            items.append(Item(f"failed:{source}", PROBLEM,
                              f"{name(card_id)}: a statement{saved} won't open ({why}).\n"
                              "If the bank changed its password, store the new one: "
                              f"cards vault set {env}"))
        elif row.get("status") == "warning":
            stmt = by_source.get(source)
            what = f"the {stmt.statement_date:%B} statement" if stmt else f"a statement{saved}"
            items.append(Item(f"unreconciled:{source}", PROBLEM,
                              f"{name(card_id)}: {what} doesn't add up to its printed total.\n"
                              "A row was missed or read twice; output/ingest_log.csv shows "
                              "by how much."))
    return items


def _unmatched_items(folder: Path, fetch_rows) -> list[Item]:
    fetched = {PureWindowsPath(r.get("saved_path", "")).name: r
               for r in fetch_rows if r.get("status") == "unmatched"}
    items = []
    for pdf in (sorted(folder.glob("*.pdf")) if folder.exists() else []):
        row = fetched.get(pdf.name)
        domain = parseaddr(row.get("sender", ""))[1].rpartition("@")[2].lower() if row else ""
        if row is not None and domain:
            when = _email_day(row.get("email_date", "")) or _day(_mtime(pdf))
            text = f"An unrecognised statement from {domain} ({when}) is in statements/_unmatched/."
        else:
            text = (f"An unrecognised statement PDF{_saved_on(pdf)} is in "
                    "statements/_unmatched/.")
        items.append(Item(f"unmatched:{pdf.name}", PROBLEM,
                          text + "\nAdd its card to config/cards.csv, or delete the PDF if "
                          "it isn't yours."))
    return items


def _late_items(statements, today: date, name) -> list[Item]:
    latest: dict[str, date] = {}
    for s in statements:
        latest[s.card_id] = max(s.statement_date, latest.get(s.card_id, s.statement_date))
    items = []
    for card_id, last in sorted(latest.items()):
        expected = add_months(last, 1)
        if today > expected + LATE_AFTER:
            items.append(Item(
                f"late:{card_id}:{expected:%Y-%m}", PROBLEM,
                f"{name(card_id)}: no {expected:%B} statement yet "
                f"(expected around {_day(expected)}).\n"
                "Look for it in Gmail. If this card is billed only in months you use it, "
                "set monthly=no for it in config/cards.csv.",
            ))
    return items


def _gap_items(statements, name) -> list[Item]:
    items = []
    for card_id, month in find_missing_cycles(statements):
        first = date.fromisoformat(f"{month}-01")
        items.append(Item(f"gap:{card_id}:{month}", PROBLEM,
                          f"{name(card_id)}: no statement for {first:%B %Y}, "
                          "between ones you have.\n"
                          f"Download it from the bank's website into statements/{card_id}/."))
    return items


# --- items from the run itself -------------------------------------------------

def fetch_auth() -> Item:
    return Item("fetch_auth", PROBLEM,
                "Gmail rejected the app password, so no statements are being downloaded. "
                "Check EMAIL_PASSWORD in config/secrets.env.")


def fetch_network(failures: int, error_type: str) -> Item:
    return Item("fetch_network", PROBLEM,
                f"Couldn't reach Gmail on the last {failures} checks ({error_type}).\n"
                "Check the internet connection; the next check tries again.")


def locked(file_name: str) -> Item:
    return Item(f"locked:{file_name}", PROBLEM,
                f"output/{file_name} is open in another program, so today's import "
                "wasn't saved. Close it; the next check tries again.")


def workbook_locked() -> Item:
    return Item("workbook_locked", PROBLEM,
                "credit_card_data.xlsx was open, so it wasn't updated. The CSVs and "
                "dashboard were; the next check tries again.")


def refresh_failed() -> Item:
    return Item("refresh_failed", PROBLEM,
                "Power BI was open but didn't refresh itself. Click Refresh.")


def backup_failed(destination: str) -> Item:
    return Item(f"backup:{destination}", PROBLEM,
                f"Backup to {destination} failed. Details: output/logs/daily_run.log.\n"
                "Check that the drive is connected and OneDrive is signed in.")


def crashed(error_type: str) -> Item:
    return Item(f"crashed:{error_type}", PROBLEM,
                f"The daily check stopped with {error_type}. "
                "Details: output/logs/daily_run.log.")


# --- memory -------------------------------------------------------------------

@dataclass
class Memory:
    """What has been told, persisted in output/notified.json.

    A missing or unreadable file is a first run: statements and merchants
    already there are recorded without being told, so the first check doesn't
    announce the whole history.
    """
    told: set[str] = field(default_factory=set)
    fetch_failures: int = 0
    import_pending: bool = False
    first_run: bool = False

    @classmethod
    def load(cls, path: Path) -> "Memory":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(
                told=set(data["told"]),
                fetch_failures=int(data.get("fetch_failures", 0)),
                import_pending=bool(data.get("import_pending", False)),
            )
        except FileNotFoundError:
            return cls(first_run=True)
        except (ValueError, KeyError, TypeError):
            log.warning("%s is unreadable; treating this as a first run", path)
            return cls(first_run=True)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.stem}.tmp{path.suffix}")
        tmp.write_text(json.dumps({
            "told": sorted(self.told),
            "fetch_failures": self.fetch_failures,
            "import_pending": self.import_pending,
        }, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def fresh(self, current: list[Item]) -> list[Item]:
        """The current items not told yet. A first run tells problems only."""
        new = [i for i in current if i.key not in self.told]
        if self.first_run:
            new = [i for i in new if i.kind == PROBLEM]
        return new

    def settle(self, current: list[Item], told_now: list[Item], delivered: bool,
               partial: bool = False) -> None:
        """Record the outcome of a run.

        Keys no longer current are forgotten, so a problem that returns is told
        again -- unless the run was partial (it crashed), when the current list
        is incomplete and forgetting would repeat everything tomorrow. Items
        told now are recorded only if the email went out; otherwise the next
        run tries again.
        """
        current_keys = {i.key for i in current}
        kept = set(self.told) if partial else self.told & current_keys
        if self.first_run:
            kept |= {i.key for i in current if i.kind != PROBLEM}
        if delivered:
            kept |= {i.key for i in told_now}
        self.told = kept
        self.first_run = False


# --- helpers ------------------------------------------------------------------

def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding=ENCODING) as fh:
        return list(csv.DictReader(fh))


def source_key(source_file: str) -> str:
    """"<card folder>/<file name>" whether the path was written relative (a
    manual ingest.py run) or absolute (the daily run), so both runs name one
    file the same way."""
    path = PureWindowsPath(source_file)
    return f"{path.parent.name}/{path.name}"


def _day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _mtime(path: Path) -> date:
    return date.fromtimestamp(path.stat().st_mtime)


def _saved_on(path: Path) -> str:
    try:
        return f" saved on {_day(_mtime(path))}"
    except OSError:
        return ""


def _money(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except ArithmeticError:
        return None


def _real_date(value: str | None) -> date | None:
    """A date, unless missing or the 1970 placeholder a parser writes for none."""
    try:
        day = date.fromisoformat(value) if value else None
    except ValueError:
        return None
    return day if day and day.year > 2000 else None


def _email_day(iso: str) -> str:
    try:
        return _day(date.fromisoformat(iso))
    except ValueError:
        return ""
