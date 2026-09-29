"""Loads user-owned config. This module never writes to config/.

Every file is opened as utf-8-sig, not utf-8. These are files the user edits by
hand, and Excel on Windows writes a UTF-8 BOM. Under plain utf-8 the BOM lands
on the first header name, so load_cards dies with a bare KeyError: 'card_id'
and load_secrets fails silently, setting a key with an invisible leading
character while the real password stays unset. utf-8-sig strips a BOM when
present and is a no-op otherwise.
"""
import csv
import os
from datetime import date as _date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from creditcard.models import Card, Payment, Rule

ENCODING = "utf-8-sig"

EXTRA = "__extra__"


def _reject_extra_fields(row: dict, path: Path, line_no: int) -> None:
    """A hand-typed thousands separator turns one field into several.

    csv.DictReader routes the surplus into a restkey rather than failing, so
    "32,000" silently becomes 32. That is a 1000x error on a money column, and
    it is the single most likely mistake when hand-editing these files.
    """
    if row.get(EXTRA):
        raise ValueError(
            f"{path} line {line_no}: too many columns. A number written with a "
            f"comma, such as 32,000, splits into two fields - write 32000, or "
            f'quote the value as "32,000".'
        )


def load_cards(path: Path) -> dict[str, Card]:
    if not Path(path).exists():
        raise FileNotFoundError(f"Required config missing: {path}")
    cards: dict[str, Card] = {}
    with open(path, newline="", encoding=ENCODING) as fh:
        for line_no, row in enumerate(csv.DictReader(fh, restkey=EXTRA), start=2):
            _reject_extra_fields(row, path, line_no)
            try:
                cards[row["card_id"]] = Card(
                    card_id=row["card_id"],
                    issuer=row["issuer"],
                    card_name=row["card_name"],
                    last4=row["last4"],
                    parser=row["parser"],
                    password_env=row["password_env"],
                    credit_limit=Decimal(row["credit_limit"] or "0"),
                    statement_day=int(row["statement_day"] or 1),
                    limit_group=(row.get("limit_group") or "").strip(),
                    monthly=(row.get("monthly") or "yes").strip().lower()
                    not in {"no", "n", "false", "0"},
                    closed=(row.get("closed") or "").strip().lower()
                    in {"yes", "y", "true", "1"},
                )
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise ValueError(
                    f"{path} line {line_no}: cannot read card "
                    f"{row.get('card_id', '?')!r} - {exc}"
                ) from exc
    return cards


def load_categories(path: Path) -> dict[str, str]:
    """subcategory -> category_group. Subcategory names must be unique."""
    if not Path(path).exists():
        raise FileNotFoundError(f"Required config missing: {path}")
    groups: dict[str, str] = {}
    with open(path, newline="", encoding=ENCODING) as fh:
        for line_no, row in enumerate(csv.DictReader(fh, restkey=EXTRA), start=2):
            _reject_extra_fields(row, path, line_no)
            subcategory = row["subcategory"]
            if subcategory in groups:
                raise ValueError(
                    f"{path} line {line_no}: subcategory {subcategory!r} appears "
                    f"under both {groups[subcategory]!r} and "
                    f"{row['category_group']!r}; names must be unique across groups"
                )
            groups[subcategory] = row["category_group"]
    return groups


def load_rules(path: Path) -> list[Rule]:
    if not Path(path).exists():
        return []
    rules: list[Rule] = []
    with open(path, newline="", encoding=ENCODING) as fh:
        for line_no, row in enumerate(csv.DictReader(fh, restkey=EXTRA), start=2):
            _reject_extra_fields(row, path, line_no)
            try:
                rules.append(
                    Rule(
                        int(row["priority"]),
                        row["pattern"].upper(),
                        row["subcategory"],
                    )
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"{path} line {line_no}: cannot read rule - {exc}"
                ) from exc
    return sorted(rules, key=lambda r: r.priority)


def load_merchant_map(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    merchant_map: dict[str, str] = {}
    with open(path, newline="", encoding=ENCODING) as fh:
        for line_no, row in enumerate(csv.DictReader(fh, restkey=EXTRA), start=2):
            _reject_extra_fields(row, path, line_no)
            merchant_map[row["merchant_clean"].upper()] = row["subcategory"]
    return merchant_map


def load_secrets(path: Path) -> None:
    """Read KEY=VALUE lines into os.environ. Values are never logged.

    The file wins over an existing environment variable. config/secrets.env is
    the user's source of truth, exactly as cards.csv is; a stale exported value
    silently shadowing a corrected file is miserable to debug when the only
    symptom is "the PDF will not decrypt".

    Passwords left blank here come from Windows Credential Manager (see
    creditcard/vault.py), which is where they are kept.
    """
    if Path(path).exists():
        with open(path, encoding=ENCODING) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
    from creditcard.vault import fill_env  # vault reads cards.csv through this module

    fill_env(Path(path).parent)


def load_payments(path: Path) -> list[Payment]:
    """Payments you logged manually. Optional - absent file means none logged."""
    if not Path(path).exists():
        return []
    payments: list[Payment] = []
    with open(path, newline="", encoding=ENCODING) as fh:
        for line_no, row in enumerate(csv.DictReader(fh, restkey=EXTRA), start=2):
            if not row.get("card_id"):
                continue
            _reject_extra_fields(row, path, line_no)
            try:
                payments.append(
                    Payment(
                        card_id=row["card_id"],
                        payment_date=_date.fromisoformat(row["payment_date"]),
                        amount=Decimal(row["amount"]),
                        note=(row.get("note") or "").strip(),
                    )
                )
            except (KeyError, ValueError, InvalidOperation) as exc:
                raise ValueError(
                    f"{path} line {line_no}: cannot read payment - {exc}"
                ) from exc
    return payments
