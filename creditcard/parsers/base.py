"""PDF access, shared parsing helpers, and the parser contract.

Passwords are never logged or re-raised.
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

import pdfplumber
from pypdf import PdfReader

from creditcard.models import Card, ParseResult

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "18 Apr, 2026", "March 28, 2026", "05 Aug 26" -- every issuer writes it
# differently, so both orderings are matched.
DATE_LABEL_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})[a-zA-Z]*,?\s*(\d{2,4})")
DATE_MONTH_FIRST_RE = re.compile(r"([A-Za-z]{3})[a-zA-Z]*\s+(\d{1,2}),?\s*(\d{2,4})")


def month_num(abbrev: str) -> int:
    return MONTHS[abbrev[:3].lower()]


def safe_date(year: int, month: int, day: int) -> date:
    """Build a date without assuming the components are already in range.

    Every genuine statement's dates are valid, so the plain construction is
    what runs in production. The fallback exists so one malformed field --
    the only source of this text is pdfplumber's extraction, and redacted
    fixtures deliberately scramble digits -- can never take down parsing of a
    whole statement. It rolls day/month forward the way spreadsheet date
    arithmetic does, which always yields some valid date.
    """
    try:
        return date(year, month, day)
    except ValueError:
        total_months = year * 12 + (month - 1)
        norm_year, norm_month0 = divmod(total_months, 12)
        return date(norm_year, norm_month0 + 1, 1) + timedelta(days=day - 1)


def to_decimal(raw: str) -> Decimal:
    """Parse a statement amount. Commas are thousands separators, never decimals."""
    return Decimal(raw.replace(",", "").replace("`", "").strip())


class PdfPasswordError(Exception):
    """Raised when a PDF cannot be decrypted with the supplied password."""


def decrypt_pdf(path: Path, password: str) -> PdfReader:
    """Open a PDF, decrypting it if needed.

    The error message names the file but never echoes the password, so a failed
    run can be pasted into a bug report safely.
    """
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        if reader.decrypt(password) == 0:
            raise PdfPasswordError(
                f"Could not decrypt {Path(path).name} - check the password env var"
            )
    return reader


def extract_text(path: Path, password: str) -> str:
    """Decrypt and extract text, page by page.

    decrypt_pdf runs first purely for its error message -- pdfminer's own
    failure on a bad password is opaque. pdfplumber then reads the original
    file directly, passing the password through. Re-serialising pages via
    PdfWriter to hand pdfplumber a decrypted copy would risk altering or
    dropping content, and a statement is the one thing that must come through
    verbatim.
    """
    decrypt_pdf(path, password)
    with pdfplumber.open(path, password=password) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


class Parser(Protocol):
    """Contract every issuer parser implements."""

    name: str

    def parse(self, text: str, card: Card, source_file: str) -> ParseResult:
        ...
