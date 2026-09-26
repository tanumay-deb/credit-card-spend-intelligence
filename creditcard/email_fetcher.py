"""Automated email statement download service.

Connects to an IMAP mailbox, discovers credit card statement emails,
extracts PDF attachments, maps them to card_ids, and saves them into
statements/<card_id>/ with deduplication and audit logging.
"""
from __future__ import annotations

import csv
import email
import email.header
import email.utils
import fnmatch
import hashlib
import imaplib
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from creditcard.models import Card

log = logging.getLogger(__name__)

ENCODING = "utf-8-sig"


def parse_folders(raw: str) -> tuple[str, ...]:
    """Parse comma-separated folders, stripping whitespace and outer quotes."""
    if not raw or not raw.strip():
        return ("INBOX",)
    import io

    reader = csv.reader(io.StringIO(raw.strip()), skipinitialspace=True)
    for row in reader:
        cleaned = [f.strip().strip('"').strip("'") for f in row if f.strip()]
        if cleaned:
            return tuple(cleaned)
    return ("INBOX",)


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    folders: tuple[str, ...] = ("INBOX",)

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folders: tuple[str, ...] | list[str] | None = None,
        folder: str | None = None,
    ):
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "password", password)
        if folders is not None:
            object.__setattr__(self, "folders", tuple(folders))
        elif folder is not None:
            object.__setattr__(self, "folders", parse_folders(folder))
        else:
            object.__setattr__(self, "folders", ("INBOX",))

    @property
    def folder(self) -> str:
        return self.folders[0] if self.folders else "INBOX"


@dataclass(frozen=True)
class EmailRule:
    card_id: str
    sender_contains: str = ""
    subject_contains: str = ""
    body_contains: str = ""
    filename_pattern: str = "*.pdf"


@dataclass(frozen=True)
class DownloadResult:
    email_date: str
    sender: str
    subject: str
    card_id: str
    filename: str
    saved_path: str
    status: str  # "downloaded" | "skipped_exists" | "skipped_hash" | "unmatched" | "error"
    file_hash: str
    error: str = ""


def load_email_config(env_path: Path | None = None) -> EmailConfig:
    """Load IMAP settings from secrets.env and environment variables.

    Environment variables take precedence over secrets.env.
    """
    if env_path and Path(env_path).exists():
        with open(env_path, encoding=ENCODING) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    if env_path:
        # The app password is kept in Windows Credential Manager.
        from creditcard.vault import fill_env

        fill_env(Path(env_path).parent)

    host = os.environ.get("EMAIL_IMAP_SERVER", "imap.gmail.com").strip()
    port_str = os.environ.get("EMAIL_IMAP_PORT", "993").strip()
    username = os.environ.get("EMAIL_USERNAME", "").strip()
    password = os.environ.get("EMAIL_PASSWORD", "").strip()
    folder_raw = os.environ.get("EMAIL_FOLDERS") or os.environ.get("EMAIL_FOLDER", "INBOX")
    folders = parse_folders(folder_raw)

    if not username:
        raise ValueError("EMAIL_USERNAME is not set in config/secrets.env or environment.")
    if not password:
        raise ValueError("EMAIL_PASSWORD is not set in config/secrets.env or environment.")

    try:
        port = int(port_str)
    except ValueError:
        port = 993

    return EmailConfig(host=host, port=port, username=username, password=password, folders=folders)


def list_imap_folders(client: imaplib.IMAP4_SSL) -> list[str]:
    """Return a sorted list of folder names available on the IMAP server."""
    status, folder_data = client.list()
    if status != "OK" or not folder_data:
        return []
    folders = []
    pattern = re.compile(r'\((?P<flags>[^)]*)\)\s+"(?P<delim>[^"]*)"\s+(?P<name>.*)')
    for f in folder_data:
        if isinstance(f, bytes):
            text = f.decode("utf-8", errors="replace").strip()
            m = pattern.search(text)
            if m:
                name = m.group("name").strip().strip('"')
                folders.append(name)
            else:
                folders.append(text.split()[-1].strip('"'))
    return sorted(folders)


def load_email_rules(path: Path) -> list[EmailRule]:
    """Load card matching rules from config/email_rules.csv."""
    if not Path(path).exists():
        return []

    rules: list[EmailRule] = []
    with open(path, newline="", encoding=ENCODING) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            card_id = (row.get("card_id") or "").strip()
            if not card_id:
                continue
            rules.append(
                EmailRule(
                    card_id=card_id,
                    sender_contains=(row.get("sender_contains") or "").strip().lower(),
                    subject_contains=(row.get("subject_contains") or "").strip().lower(),
                    body_contains=(row.get("body_contains") or "").strip().lower(),
                    filename_pattern=(row.get("filename_pattern") or "*.pdf").strip(),
                )
            )
    return rules


class EmailAuthError(ConnectionError):
    """The mail server rejected the username or app password.

    Kept apart from other connection failures because it won't fix itself: the
    daily check reports it at once, but waits for a second failed run before
    reporting a network error, which usually clears on its own.
    """


def connect_imap(config: EmailConfig) -> imaplib.IMAP4_SSL:
    """Establish a secure IMAP connection.

    Password is never logged in error messages.
    """
    try:
        client = imaplib.IMAP4_SSL(config.host, config.port)
    except Exception as exc:
        raise ConnectionError(
            f"Failed to connect to IMAP server {config.host}:{config.port}: {exc}"
        ) from None
    try:
        client.login(config.username, config.password)
        return client
    except imaplib.IMAP4.error as exc:
        _close_quietly(client)
        raise EmailAuthError(
            f"IMAP server {config.host} rejected the login for {config.username}: {exc}"
        ) from None
    except Exception as exc:
        _close_quietly(client)
        raise ConnectionError(
            f"Failed to log in to IMAP server {config.host}:{config.port} as {config.username}: {exc}"
        ) from None


def _close_quietly(client) -> None:
    try:
        client.logout()
    except Exception:
        pass


def decode_header_str(header_val: str | None) -> str:
    """Decode RFC 2047 encoded email headers."""
    if not header_val:
        return ""
    decoded_fragments = []
    for part, enc in email.header.decode_header(header_val):
        if isinstance(part, bytes):
            decoded_fragments.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded_fragments.append(str(part))
    return "".join(decoded_fragments).strip()


def parse_email_message(
    raw_bytes: bytes,
) -> tuple[str, str, str, str, list[tuple[str, bytes]]]:
    """Parse raw email into (date_iso, sender, subject, body_text, attachments).

    Attachments is a list of (filename, file_bytes).
    """
    msg = email.message_from_bytes(raw_bytes)

    subject = decode_header_str(msg.get("Subject", ""))
    sender = decode_header_str(msg.get("From", ""))

    date_str = msg.get("Date", "")
    date_iso = ""
    if date_str:
        try:
            parsed_dt = email.utils.parsedate_to_datetime(date_str)
            date_iso = parsed_dt.strftime("%Y-%m-%d")
        except Exception:
            date_iso = ""

    body_parts = []
    attachments: list[tuple[str, bytes]] = []

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = part.get("Content-Disposition", "")
        filename = decode_header_str(part.get_filename())

        if filename or "attachment" in disposition.lower():
            payload = part.get_payload(decode=True)
            if payload and isinstance(payload, bytes):
                fn = filename or "statement.pdf"
                attachments.append((fn, payload))
        elif content_type in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload and isinstance(payload, bytes):
                body_parts.append(payload.decode("utf-8", errors="replace"))

    body_text = "\n".join(body_parts)
    return date_iso, sender, subject, body_text, attachments


def match_card_for_email(
    sender: str,
    subject: str,
    body: str,
    filename: str,
    rules: list[EmailRule],
    cards: dict[str, Card],
) -> str | None:
    """Identify which card_id a statement email belongs to.

    Checks:
    1. Explicit email_rules.csv matches.
    2. Non-default last4 digits from cards.csv in subject/body/filename.
    3. Card name keywords in subject.
    """
    s_sender = sender.lower()
    s_subject = subject.lower()
    s_body = body.lower()
    s_filename = filename.lower()

    # 1. Check rules
    for rule in rules:
        if rule.sender_contains and rule.sender_contains not in s_sender:
            continue
        if rule.subject_contains and rule.subject_contains not in s_subject:
            continue
        if rule.body_contains and rule.body_contains not in s_body:
            continue
        if rule.filename_pattern and not fnmatch.fnmatch(s_filename, rule.filename_pattern.lower()):
            continue
        return rule.card_id

    # 2. Check last 4 digits if populated (not placeholder '0000')
    for card_id, card in cards.items():
        if card.last4 and card.last4 != "0000":
            if card.last4 in s_subject or card.last4 in s_body or card.last4 in s_filename:
                return card_id

    # 3. Keyword matching on card names
    for card_id, card in cards.items():
        # e.g. "Millennia", "Swiggy", "Amazon Pay", "Cashback", "PRIME"
        cname = card.card_name.lower()
        if len(cname) > 3 and (cname in s_subject or cname in s_filename):
            return card_id

    return None


def calculate_hash(data: bytes) -> str:
    """Calculate 16-char SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()[:16]


def get_existing_hashes(target_dir: Path) -> set[str]:
    """Scan directory for SHA-256 hashes of all existing PDFs."""
    hashes: set[str] = set()
    if not target_dir.exists():
        return hashes
    for f in target_dir.rglob("*.pdf"):
        try:
            with open(f, "rb") as fh:
                h = hashlib.sha256(fh.read()).hexdigest()[:16]
                hashes.add(h)
        except OSError:
            pass
    return hashes


def build_search_query(days_back: int | None = 60, unread_only: bool = False) -> str:
    """Build an IMAP search criteria string."""
    criteria = []
    if unread_only:
        criteria.append("UNSEEN")

    if days_back is not None and days_back > 0:
        since_date = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%d-%b-%Y")
        criteria.append(f'SINCE "{since_date}"')

    if not criteria:
        return "ALL"
    return "(" + " ".join(criteria) + ")"


def search_emails(client: imaplib.IMAP4_SSL, query: str) -> list[str]:
    """Search messages and return list of message IDs."""
    status, data = client.search(None, query)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].decode("ascii", errors="ignore").split()


NON_CREDIT_CARD_KEYWORDS = (
    "transaction alert",
    "txn alert",
    "alert from",
    "otp",
    "demat",
    "demat account",
    "e-account statement",
    "savings account",
    "current account",
    "points worth",
    "reward points",
    "home loan",
    "personal loan",
    "mutual fund",
)

NON_CREDIT_CARD_SENDERS = (
    "alerts.sbi.bank.in",
    "customernotification@icici.bank.in",
    "cbs",
)


def is_non_credit_card_email(sender: str, subject: str) -> bool:
    """Return True if an email is clearly NOT a credit card statement.

    For example transaction alerts, Demat, savings account or reward points.
    """
    s_sub = subject.lower()
    s_sender = sender.lower()

    for s_kw in NON_CREDIT_CARD_SENDERS:
        if s_kw in s_sender:
            return True

    for kw in NON_CREDIT_CARD_KEYWORDS:
        if kw in s_sub:
            return True

    # Statement emails must contain "statement" or "stmt" in the subject
    if not any(k in s_sub for k in ("statement", "stmt")):
        return True

    return False


def find_candidate_messages(
    client: imaplib.IMAP4_SSL,
    rules: list[EmailRule],
    cards: dict[str, Card],
    days_back: int | None = 60,
    unread_only: bool = False,
    sender_filter: str | None = None,
    subject_filter: str | None = None,
    search_all: bool = False,
) -> list[str]:
    """Find message IDs matching statement criteria.

    Combines sender and subject criteria so only relevant credit card statement
    emails are fetched, skipping all non-statement mailbox traffic.
    """
    date_crit = []
    if unread_only:
        date_crit.append("UNSEEN")
    if days_back is not None and days_back > 0:
        since_date = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%d-%b-%Y")
        date_crit.append(f'SINCE "{since_date}"')
    base_prefix = " ".join(date_crit)

    # 1. Explicit user filters (e.g. via --sender or --subject)
    if sender_filter or subject_filter:
        parts = []
        if base_prefix:
            parts.append(base_prefix)
        if sender_filter:
            parts.append(f'FROM "{sender_filter}"')
        if subject_filter:
            parts.append(f'SUBJECT "{subject_filter}"')
        query = "(" + " ".join(parts) + ")"
        log.info("Running filtered search query: %s", query)
        return search_emails(client, query)

    if search_all:
        query = f"({base_prefix})" if base_prefix else "ALL"
        log.info("Running open search query: %s", query)
        return search_emails(client, query)

    # 2. Automated combined queries (FROM bank AND SUBJECT statement/card)
    matched_ids: set[str] = set()

    # Pair rule senders with rule subjects AND require "statement"
    for r in rules:
        if r.sender_contains and r.subject_contains:
            criteria = (
                f'FROM "{r.sender_contains}" SUBJECT "{r.subject_contains}" SUBJECT "statement"'
            )
            q = f"({base_prefix} {criteria})" if base_prefix else f"({criteria})"
            ids = search_emails(client, q)
            if ids:
                matched_ids.update(ids)

    # Specific credit card statement queries
    targeted_pairs = (
        ("sbicard", "Statement"),
        ("hdfcbank", "Credit Card Statement"),
        ("icicibank", "Credit Card Statement"),
    )
    for sender, kw in targeted_pairs:
        q = (
            f'({base_prefix} FROM "{sender}" SUBJECT "{kw}")'
            if base_prefix
            else f'(FROM "{sender}" SUBJECT "{kw}")'
        )
        ids = search_emails(client, q)
        if ids:
            matched_ids.update(ids)

    # General credit card statement query
    q_general = (
        f'({base_prefix} SUBJECT "credit card statement")'
        if base_prefix
        else '(SUBJECT "credit card statement")'
    )
    ids = search_emails(client, q_general)
    if ids:
        matched_ids.update(ids)

    def _int_sort(msg_id: str) -> int:
        try:
            return int(msg_id)
        except ValueError:
            return 0

    return sorted(matched_ids, key=_int_sort)


def fetch_and_process_statements(
    root_dir: Path,
    days_back: int | None = 60,
    unread_only: bool = False,
    dry_run: bool = False,
    card_filter: str | None = None,
    folders: list[str] | tuple[str, ...] | str | None = None,
    sender_filter: str | None = None,
    subject_filter: str | None = None,
    search_all: bool = False,
    client: imaplib.IMAP4_SSL | None = None,
    csv_out: Path | str | None = None,
    excel_out: Path | str | None = None,
) -> list[DownloadResult]:
    """Search mailbox across one or more folders, extract statement PDFs, map to cards, and save."""
    root = Path(root_dir)
    cfg_dir = root / "config"
    out_dir = root / "output"
    statements_dir = root / "statements"

    out_dir.mkdir(parents=True, exist_ok=True)
    statements_dir.mkdir(parents=True, exist_ok=True)

    email_cfg = load_email_config(cfg_dir / "secrets.env")
    rules = load_email_rules(cfg_dir / "email_rules.csv")

    from creditcard.config import load_cards

    cards = load_cards(cfg_dir / "cards.csv") if (cfg_dir / "cards.csv").exists() else {}

    if isinstance(folders, str):
        target_folders = parse_folders(folders)
    elif folders:
        target_folders = tuple(folders)
    else:
        target_folders = email_cfg.folders

    close_client_at_end = False
    if client is None:
        client = connect_imap(email_cfg)
        close_client_at_end = True

    results: list[DownloadResult] = []

    try:
        # Build existing file hashes to avoid re-downloading identical PDFs across folders
        known_hashes = get_existing_hashes(statements_dir)

        for folder_name in target_folders:
            clean_folder = folder_name.strip().strip('"').strip("'")
            log.info("Scanning IMAP folder: %s", clean_folder)
            try:
                needs_quotes = " " in clean_folder and not clean_folder.startswith('"')
                mailbox = f'"{clean_folder}"' if needs_quotes else clean_folder
                status, _ = client.select(mailbox, readonly=True)
                if status != "OK":
                    log.warning(
                        "Could not select IMAP folder %r (status=%s), skipping.",
                        clean_folder,
                        status,
                    )
                    continue
            except Exception as exc:
                log.warning("Failed to access folder %r: %s, skipping.", clean_folder, exc)
                continue

            msg_ids = find_candidate_messages(
                client=client,
                rules=rules,
                cards=cards,
                days_back=days_back,
                unread_only=unread_only,
                sender_filter=sender_filter,
                subject_filter=subject_filter,
                search_all=search_all,
            )
            log.info("Found %d candidate statement email(s) in %s", len(msg_ids), clean_folder)

            total_cand = len(msg_ids)
            # Iterate in reverse (latest first)
            for idx, msg_id in enumerate(reversed(msg_ids), 1):
                # Use BODY.PEEK[] so unread emails are not marked as read
                status, msg_data = client.fetch(msg_id, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                raw_bytes = msg_data[0][1]
                if not isinstance(raw_bytes, bytes):
                    continue

                date_iso, sender, subject, body, attachments = parse_email_message(raw_bytes)
                if is_non_credit_card_email(sender, subject):
                    log.debug("Skipping non-credit-card email: %s", subject)
                    continue

                log.info("Examining [%d/%d]: %s (%s)", idx, total_cand, subject[:60], date_iso)

                # Only process if there are PDF attachments
                pdf_attachments = [
                    (fn, data) for fn, data in attachments if fn.lower().endswith(".pdf")
                ]
                if not pdf_attachments:
                    continue

                for orig_fn, pdf_bytes in pdf_attachments:
                    card_id = match_card_for_email(sender, subject, body, orig_fn, rules, cards)

                    if card_filter and card_id != card_filter:
                        continue

                    if card_id is None:
                        s_sub = subject.lower()
                        statement_words = ("credit card", "card statement", "card monthly statement")
                        if not any(kw in s_sub for kw in statement_words):
                            continue
                        target_folder = statements_dir / "_unmatched"
                        assigned_card = "unmatched"
                    else:
                        target_folder = statements_dir / card_id
                        assigned_card = card_id

                    f_hash = calculate_hash(pdf_bytes)
                    clean_fn = re.sub(r"[^\w\-.]", "_", orig_fn)
                    target_file = target_folder / clean_fn

                    if f_hash in known_hashes:
                        results.append(
                            DownloadResult(
                                email_date=date_iso,
                                sender=sender,
                                subject=subject,
                                card_id=assigned_card,
                                filename=orig_fn,
                                saved_path=str(target_file),
                                status="skipped_hash",
                                file_hash=f_hash,
                            )
                        )
                        continue

                    if target_file.exists():
                        # Same name exists, verify if hash is identical
                        existing_hash = calculate_hash(target_file.read_bytes())
                        if existing_hash == f_hash:
                            results.append(
                                DownloadResult(
                                    email_date=date_iso,
                                    sender=sender,
                                    subject=subject,
                                    card_id=assigned_card,
                                    filename=orig_fn,
                                    saved_path=str(target_file),
                                    status="skipped_exists",
                                    file_hash=f_hash,
                                )
                            )
                            continue
                        else:
                            # Disambiguate name with date prefix or hash
                            prefix = f"{date_iso}_" if date_iso else ""
                            target_file = target_folder / f"{prefix}{clean_fn}"

                    if dry_run:
                        results.append(
                            DownloadResult(
                                email_date=date_iso,
                                sender=sender,
                                subject=subject,
                                card_id=assigned_card,
                                filename=orig_fn,
                                saved_path=str(target_file),
                                status="downloaded" if card_id else "unmatched",
                                file_hash=f_hash,
                            )
                        )
                    else:
                        target_folder.mkdir(parents=True, exist_ok=True)
                        with open(target_file, "wb") as out_fh:
                            out_fh.write(pdf_bytes)
                        known_hashes.add(f_hash)

                        results.append(
                            DownloadResult(
                                email_date=date_iso,
                                sender=sender,
                                subject=subject,
                                card_id=assigned_card,
                                filename=orig_fn,
                                saved_path=str(target_file),
                                status="downloaded" if card_id else "unmatched",
                                file_hash=f_hash,
                            )
                        )

    finally:
        if close_client_at_end:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass

    if dry_run:
        target_csv = Path(csv_out) if csv_out else out_dir / "fetched_statements_preview.csv"
        target_xlsx = Path(excel_out) if excel_out else out_dir / "fetched_statements_preview.xlsx"
        export_fetch_results(results, target_csv, target_xlsx)
    else:
        write_email_fetch_log(results, out_dir / "email_fetch_log.csv")
        target_csv = Path(csv_out) if csv_out else out_dir / "fetched_statements.csv"
        target_xlsx = Path(excel_out) if excel_out else out_dir / "fetched_statements.xlsx"
        export_fetch_results(results, target_csv, target_xlsx)

    return results


def export_fetch_results(
    results: list[DownloadResult],
    csv_path: Path,
    excel_path: Path | None = None,
) -> tuple[Path, Path | None]:
    """Export statement download results to CSV and optional styled Excel workbook.

    Columns:
      - email_date: Date email was received
      - card_id: Mapped card identifier
      - status: downloaded | skipped_exists | skipped_hash | unmatched | error
      - filename: Attachment filename
      - sender: From address
      - subject: Email subject line
      - saved_path: Path where file was/would be saved
      - file_hash: SHA-256 hash of PDF
      - error: Error message if any
    """
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "email_date",
        "card_id",
        "status",
        "filename",
        "sender",
        "subject",
        "saved_path",
        "file_hash",
        "error",
    ]
    rows = [
        {
            "email_date": r.email_date,
            "card_id": r.card_id,
            "status": r.status,
            "filename": r.filename,
            "sender": r.sender,
            "subject": r.subject,
            "saved_path": r.saved_path,
            "file_hash": r.file_hash,
            "error": r.error,
        }
        for r in results
    ]

    with open(csv_path, "w", newline="", encoding=ENCODING) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    excel_written: Path | None = None
    if excel_path is not None:
        excel_path = Path(excel_path)
        excel_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pandas as pd
            from openpyxl.styles import Font, PatternFill

            df = pd.DataFrame(rows, columns=fieldnames)
            with pd.ExcelWriter(excel_path, engine="openpyxl") as excel:
                df.to_excel(excel, sheet_name="Fetched_Statements", index=False)
                ws = excel.sheets["Fetched_Statements"]

                header_font = Font(bold=True, color="FFFFFF")
                header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
                for cell in ws[1]:
                    cell.font = header_font
                    cell.fill = header_fill

                for col in ws.columns:
                    col_letter = col[0].column_letter
                    max_len = max(len(str(cell.value or "")) for cell in col)
                    ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)

            excel_written = excel_path
        except Exception as exc:
            log.warning("Failed to generate Excel report %s: %s", excel_path, exc)

    return csv_path, excel_written



def write_email_fetch_log(results: list[DownloadResult], log_path: Path) -> None:
    """Append or rewrite the fetch log CSV."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "email_date",
        "sender",
        "subject",
        "card_id",
        "filename",
        "saved_path",
        "status",
        "file_hash",
        "error",
    ]

    # If log already exists, read existing rows to preserve history
    existing_rows: list[dict] = []
    if log_path.exists():
        with open(log_path, newline="", encoding=ENCODING) as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                existing_rows.append(row)

    # Append fresh results
    for r in results:
        existing_rows.append(
            {
                "email_date": r.email_date,
                "sender": r.sender,
                "subject": r.subject,
                "card_id": r.card_id,
                "filename": r.filename,
                "saved_path": r.saved_path,
                "status": r.status,
                "file_hash": r.file_hash,
                "error": r.error,
            }
        )

    with open(log_path, "w", newline="", encoding=ENCODING) as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)
