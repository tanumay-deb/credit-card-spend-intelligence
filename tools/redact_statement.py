"""tools/redact_statement.py -- redact an extracted statement text file.

Usage:
    python tools/redact_statement.py <input.txt> <output.txt>

Turns the raw text pdfplumber extracted from a real statement into something
safe to commit as a test fixture, without disturbing the layout the parsers
key off. Every replacement is exactly as long as what it replaces, so line
count, line length and column position never move.

What happens, in order:

1. Email addresses (regex-matched) become "redacted@example.com", padded or
   truncated to the exact width of the address they replace.
2. A small set of label- and position-anchored rules locate the cardholder
   name, any co-applicant name, and free-text address lines, and replace
   just those spans with same-length letter filler ("X"). The anchors are
   fixed boilerplate strings and structural positions that every statement
   of a given issuer prints regardless of who the customer is (e.g.
   "Credit Card No.", "GSTIN of SBI Card", "TRANSACTIONS FOR ") -- never
   the specific person's data -- so this module carries no PII of its own
   and the same rules apply to anyone's statement in that issuer's format.
3. Every digit, anywhere in the file -- transaction amounts, card and
   account numbers, reference numbers, dates, digits embedded in a merchant
   string -- is shifted by a per-position amount drawn from a **key that is
   generated fresh on every run and never written down** (see "Why the key
   is not in this file"). Shifts are in 1..9, so no digit maps to itself.
   Punctuation -- comma, period, slash, colon, space, the "X" a bank
   already used to mask part of a card number -- is untouched, so
   "1,499.00"-shaped amounts stay valid after substitution.
4. The amounts a statement's own arithmetic ties together are then
   **regenerated as a consistent set**, replacing what step 3 produced for
   them (see "Why the totals are regenerated").

Merchant/transaction description text is left alone apart from its digits --
it is needed to exercise categorisation later and is not personally
identifying the way a card or account number is.

Why the key is not in this file
-------------------------------
This tool and the fixtures it produces are committed to the same repository.
Any substitution driven by a constant checked in here is therefore not a
redaction at all: reading the constant out of the source and subtracting it
from the fixture returns the original digits exactly -- every amount, every
card number, every date. An earlier version of this module shifted each
digit by a *deterministic* multiplicative hash of its position, advertised
as "reproducible", and was invertible for exactly that reason.

So the shift sequence now comes from `random.SystemRandom` and is discarded
when the process exits. Redaction is one-way and nothing needs to undo it.
The consequence -- two runs on the same input produce different output -- is
the property to check for, not a defect: it is what tells you no key was
retained anywhere.

Why the totals are regenerated
------------------------------
Per-digit substitution cannot preserve a sum. Addition carries between digit
positions; a substitution that maps each position independently does not.
Two different numbers in the document -- thirty-two transaction amounts and
the PURCHASES figure that is their total -- come out shifted independently
and no longer add up, no matter how the shift is chosen. This is structural,
not a flaw in a particular hash: any scheme that rewrites digits in place,
one at a time, at fixed width, loses cross-number arithmetic.

That matters because reconciliation -- comparing what a parser summed from
the rows against the total the statement printed -- is the check that
catches a parser silently dropping or duplicating a row. A fixture whose
rows cannot sum to its own total cannot exercise it, and every issuer's
golden-file test then has to either skip the check or pin a meaningless
delta.

The fix is to stop treating those amounts as independent digit runs. For
each issuer, `_RECONCILERS` names the transaction rows and the summary
figure that totals them. A fresh total is drawn at random within the exact
digit width the original printed, and the row amounts are then drawn at
random to sum to it, each within its own original width. Only digit
characters are written, so commas, decimal points and column positions are
untouched, and the resulting fixture reconciles exactly.

These regenerated amounts are not derived from the real ones -- the real
values are read only to check that the tool has located the right spans, and
are otherwise unused -- so unlike a substitution there is no map to invert
and a digit that happens to match the original reveals nothing.

Before regenerating, the tool checks that the rows in the *input* really do
sum to the total it is about to replace. If they do not, its locators are
wrong for this layout and it raises rather than emit a fixture whose
arithmetic silently means nothing.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


class ReconciliationError(Exception):
    """The reconciling amounts could not be located, or did not add up.

    Raised instead of falling back to a plain digit substitution, because a
    fixture that looks fine but cannot reconcile is the failure this module
    exists to prevent, and it is invisible until someone writes the parser.
    """


# ---------------------------------------------------------------------------
# 1. Digit substitution
# ---------------------------------------------------------------------------
# Each digit is shifted by a per-position amount in 1..9 -- never 0, so a
# digit can never map to itself -- drawn from a key held only in memory. Two
# consequences worth stating:
#
#   * The same digit group repeated across the statement (the same year in
#     fifty transaction dates) lands on different replacements each time, so
#     nothing can be inferred from repetition.
#   * The key is gone when the process exits, so the fixture cannot be
#     inverted by anyone -- including someone holding this repository.
def _substitute_digits(text: str, rng: random.Random) -> str:
    out = []
    for ch in text:
        if ch.isdigit():
            out.append(str((int(ch) + rng.randrange(1, 10)) % 10))
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# 2. Email redaction
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_EMAIL_PLACEHOLDER = "redacted@example.com"


def _redact_email(match: re.Match[str]) -> str:
    original = match.group(0)
    target_len = len(original)
    if target_len == len(_EMAIL_PLACEHOLDER):
        return _EMAIL_PLACEHOLDER
    if target_len > len(_EMAIL_PLACEHOLDER):
        pad = "x" * (target_len - len(_EMAIL_PLACEHOLDER))
        return "redacted" + pad + "@example.com"
    if target_len <= 0:
        return ""
    return _EMAIL_PLACEHOLDER[:target_len]


# ---------------------------------------------------------------------------
# 3. Name / address filler
# ---------------------------------------------------------------------------
def _letter_fill(text: str) -> str:
    """Same-length filler: every letter becomes 'X'; digits, whitespace and
    punctuation pass through untouched (digits get substituted in the global
    digit pass; whitespace/punctuation carry the column structure)."""
    if _EMAIL_PLACEHOLDER in text:
        # An already-redacted email placeholder can share a line with this
        # span in principle; refilling it would throw away the fixed value
        # for no privacy benefit, so leave it alone.
        return text
    return "".join("X" if ch.isalpha() else ch for ch in text)


# ---------------------------------------------------------------------------
# Issuer-shaped, label-anchored name/address rules
# ---------------------------------------------------------------------------
_HDFC_SUFFIX_ANCHORS = ("Credit Card No.", "Alternate Account Number", "Billing Period")
_HDFC_SAFE_STARTS = ("Statement Date", "Email")
_ICICI_SAFE_MARKERS = (
    "SSTTAATTEEMMEENNTT DDAATTEE",
    "PPAAYYMMEENNTT DDUUEE DDAATTEE",
    "STATEMENT SUMMARY",
)
_SBI_TXN_FOR = "TRANSACTIONS FOR "
_DATE_DDMMYYYY = re.compile(r"^\d{2}/\d{2}/\d{4}")


def _apply_name_address_rules(lines: list[str]) -> list[str]:
    """Return a new list of line-contents with name/address spans filled.

    Each rule is anchored on a fixed label or a structural position that is
    printed on every statement of that issuer, not on any specific person's
    text, so nothing here needs to change from one customer's statement to
    the next.
    """
    lines = list(lines)
    handled = [False] * len(lines)

    # --- HDFC: personal text sits BEFORE one of these fixed field labels
    # on the same (PDF-column-flattened) line ---
    for i, line in enumerate(lines):
        for anchor in _HDFC_SUFFIX_ANCHORS:
            pos = line.find(anchor)
            if pos != -1:
                lines[i] = _letter_fill(line[:pos]) + line[pos:]
                handled[i] = True
                break

    # --- HDFC: recognised metadata-label lines carry no personal text of
    # their own (the label's VALUE, e.g. a date, is not name/address) ---
    for i, line in enumerate(lines):
        if not handled[i] and line.startswith(_HDFC_SAFE_STARTS):
            handled[i] = True

    # --- HDFC: unlabelled address-continuation lines between the
    # cardholder-name line and the start of the transactions summary table
    # ("PAYMENTS/CREDITS ..."). Anything in that span not already handled
    # above is address text with no label to anchor on directly.
    cc_no_idx = next((i for i, line in enumerate(lines) if "Credit Card No." in line), None)
    if cc_no_idx is not None:
        pay_credits_idx = next(
            (i for i, line in enumerate(lines) if "PAYMENTS/CREDITS" in line), None
        )
        zone_end = pay_credits_idx if pay_credits_idx is not None else len(lines)
        for i in range(cc_no_idx, zone_end):
            if not handled[i]:
                lines[i] = _letter_fill(lines[i])
                handled[i] = True

    # --- HDFC: the cardholder name is repeated as a standalone line right
    # after the transaction table's column header, before the first dated
    # row (only on the first occurrence; later pages repeat the header
    # without repeating the name) ---
    for i, line in enumerate(lines):
        if line.startswith("DATE & TIME"):
            j = i + 1
            if j < len(lines) and not handled[j] and not _DATE_DDMMYYYY.match(lines[j]):
                lines[j] = _letter_fill(lines[j])
                handled[j] = True

    # --- ICICI: the very first line is "<title> <NAME>" (e.g. "MR ...") ---
    if lines and re.match(r"^(MR|MS|MRS)\s", lines[0]):
        lines[0] = _letter_fill(lines[0])
        handled[0] = True
        # Unlabelled address lines follow immediately, until a recognised
        # statement-date/summary marker.
        zone_end = len(lines)
        for i in range(1, len(lines)):
            if any(marker in lines[i] for marker in _ICICI_SAFE_MARKERS):
                zone_end = i
                break
        for i in range(1, zone_end):
            if not handled[i]:
                lines[i] = _letter_fill(lines[i])
                handled[i] = True

    # --- SBI: the cardholder name is the line right after the fixed GSTIN
    # preamble line that opens every SBI Card statement ---
    for i, line in enumerate(lines):
        if line.startswith("GSTIN of SBI Card"):
            j = i + 1
            if j < len(lines) and not handled[j]:
                lines[j] = _letter_fill(lines[j])
                handled[j] = True
            break

    # --- SBI: "TRANSACTIONS FOR <NAME>" section markers (can repeat) ---
    for i, line in enumerate(lines):
        pos = line.find(_SBI_TXN_FOR)
        if pos != -1:
            start = pos + len(_SBI_TXN_FOR)
            lines[i] = line[:start] + _letter_fill(line[start:])
            handled[i] = True

    return lines


# ---------------------------------------------------------------------------
# 4. Arithmetic consistency
# ---------------------------------------------------------------------------
# Per issuer: how to find the transaction rows, and how to find the summary
# figure that totals them. Every pattern is anchored on the issuer's fixed
# column headers and row shape, never on any particular value.
#
# The relations encoded here were each confirmed against the real statements
# they describe -- the rows sum to the printed figure to the paisa on every
# statement held for that issuer -- and `_collect_groups` re-checks the
# relation on each input before rewriting anything.

_HDFC_TXN_RE = re.compile(
    r"^[ \t]*\d{2}/\d{2}/\d{4}\|[ \t]*\d{2}:\d{2}[ \t]+"
    r".*?[ \t]*(?P<credit>\+)?[ \t]*C[ \t]*(?P<amt>[\d,]+\.\d{2})[ \t]*l[ \t\r]*$",
    re.M,
)
# The summary table pdfplumber flattens into label lines and value lines; the
# amounts appear in a fixed order regardless of how the lines wrap.
_HDFC_SUMMARY_AMOUNT_RE = re.compile(r"C[ \t]*(?P<amt>\d[\d,]*(?:\.\d{1,2})?)")

# ICICI rows are not always flush to the line start: on page 1 the flattened
# two-column layout can bleed a fragment of the neighbouring rewards column
# ("5% ") onto the front of a transaction line, so the row is matched from
# its date rather than from the margin.
_ICICI_TXN_RE = re.compile(
    r"\d{2}/\d{2}/\d{4}[ \t]+\d{6,}[ \t]+.*?[ \t]+-?\d+[ \t]+"
    r"(?P<amt>[\d,]+\.\d{2})(?P<credit>[ \t]+CR)?[ \t\r]*$",
    re.M,
)
_ICICI_SUMMARY_LABEL = (
    "Previous Balance Purchases / Charges Cash Advances Payments / Credits"
)
_BACKTICK_AMOUNT_RE = re.compile(r"`[ \t]*(?P<amt>[\d,]+\.\d{2})")

_SBI_TXN_RE = re.compile(
    r"^[ \t]*\d{1,2}[ \t]+[A-Z][a-z]{2}[ \t]+\d{2}[ \t]+.*?[ \t]+"
    r"(?P<amt>[\d,]+\.\d{2})[ \t]+(?P<dc>[CD])[ \t\r]*$",
    re.M,
)
_SBI_SUMMARY_LABEL = "( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )"
_PLAIN_AMOUNT_RE = re.compile(r"(?P<amt>[\d,]+\.\d{2})")

_ISSUER_MARKERS = {
    "hdfc": ("HDFC Bank Credit Card Statement",),
    "icici": ("ICICI Bank Credit Card GST Number", "SSTTAATTEEMMEENNTT DDAATTEE"),
    "sbi": ("GSTIN of SBI Card",),
}

Span = tuple[int, int]


@dataclass(frozen=True)
class _Group:
    """One "these rows add up to that figure" relation within a statement."""

    label: str
    total: Span
    rows: tuple[Span, ...]


def detect_issuer(text: str) -> str | None:
    for issuer, markers in _ISSUER_MARKERS.items():
        if any(marker in text for marker in markers):
            return issuer
    return None


def _amount_spans(text: str, pattern: re.Pattern[str], start: int, end: int) -> list[Span]:
    return [m.span("amt") for m in pattern.finditer(text, start, end)]


def _value(text: str, span: Span) -> Decimal:
    return Decimal(text[span[0]:span[1]].replace(",", ""))


def _find(text: str, label: str, issuer: str) -> int:
    idx = text.find(label)
    if idx == -1:
        raise ReconciliationError(
            f"{issuer}: could not find {label!r}; the layout this tool knows "
            "has changed, and a fixture generated from it would not reconcile"
        )
    return idx


def _line_after(text: str, idx: int, issuer: str, label: str) -> Span:
    """Span of the line following the one containing `idx`."""
    first_break = text.find("\n", idx)
    if first_break == -1:
        raise ReconciliationError(f"{issuer}: nothing follows {label!r}")
    second_break = text.find("\n", first_break + 1)
    if second_break == -1:
        second_break = len(text)
    return first_break + 1, second_break


def _hdfc_groups(text: str) -> list[_Group]:
    start = _find(text, "PAYMENTS/CREDITS", "hdfc")
    ends = [
        i
        for i in (text.find("Purchase Indicator", start), text.find("DATE & TIME", start))
        if i != -1
    ]
    block_end = min(ends) if ends else len(text)
    amounts = _amount_spans(text, _HDFC_SUMMARY_AMOUNT_RE, start, block_end)
    if len(amounts) < 5:
        raise ReconciliationError(
            f"hdfc: expected at least 5 amounts in the summary block, "
            f"found {len(amounts)}"
        )
    # Printed order: total due, previous dues, payments, purchases, finance.
    rows = list(_HDFC_TXN_RE.finditer(text))
    debits = [m.span("amt") for m in rows if not m.group("credit")]
    credits = [m.span("amt") for m in rows if m.group("credit")]
    return [
        _Group("purchases", amounts[3], tuple(debits)),
        _Group("payments", amounts[2], tuple(credits)),
    ]


def _icici_groups(text: str) -> list[_Group]:
    idx = _find(text, _ICICI_SUMMARY_LABEL, "icici")
    lo, hi = _line_after(text, idx, "icici", _ICICI_SUMMARY_LABEL)
    amounts = _amount_spans(text, _BACKTICK_AMOUNT_RE, lo, hi)
    if len(amounts) != 4:
        raise ReconciliationError(
            f"icici: expected 4 amounts under {_ICICI_SUMMARY_LABEL!r}, "
            f"found {len(amounts)}"
        )
    rows = list(_ICICI_TXN_RE.finditer(text))
    debits = [m.span("amt") for m in rows if not m.group("credit")]
    credits = [m.span("amt") for m in rows if m.group("credit")]
    return [
        _Group("purchases", amounts[1], tuple(debits)),
        _Group("payments", amounts[3], tuple(credits)),
    ]


def _sbi_groups(text: str) -> list[_Group]:
    idx = _find(text, _SBI_SUMMARY_LABEL, "sbi")
    lo, hi = _line_after(text, idx, "sbi", _SBI_SUMMARY_LABEL)
    amounts = _amount_spans(text, _PLAIN_AMOUNT_RE, lo, hi)
    if len(amounts) != 5:
        raise ReconciliationError(
            f"sbi: expected 5 amounts under the ACCOUNT SUMMARY column headers, "
            f"found {len(amounts)}"
        )
    rows = list(_SBI_TXN_RE.finditer(text))
    debits = [m.span("amt") for m in rows if m.group("dc") == "D"]
    credits = [m.span("amt") for m in rows if m.group("dc") == "C"]
    return [
        _Group("purchases", amounts[2], tuple(debits)),
        _Group("payments", amounts[1], tuple(credits)),
    ]


_RECONCILERS = {
    "hdfc": _hdfc_groups,
    "icici": _icici_groups,
    "sbi": _sbi_groups,
}


def _collect_groups(text: str, issuer: str) -> list[_Group]:
    """Locate every reconciling group, and verify it holds on this input.

    The check is the tool's own proof that it found the right spans: if the
    rows it matched do not already sum to the figure it is about to replace,
    it has misread the layout, and rewriting on that basis would bake a
    relationship into the fixture that was never true of the statement.
    """
    groups = _RECONCILERS[issuer](text)
    for group in groups:
        rows_total = sum((_value(text, s) for s in group.rows), Decimal("0"))
        if rows_total != _value(text, group.total):
            # Deliberately reports no amounts: this tool's error output must
            # stay safe to paste into a bug report about a real statement.
            raise ReconciliationError(
                f"{issuer} {group.label}: the {len(group.rows)} matched row(s) "
                "do not sum to the printed figure in the input, so the rows or "
                "the figure have been located wrongly"
            )
    return groups


def _digit_positions(text: str, span: Span) -> list[int]:
    return [i for i in range(span[0], span[1]) if text[i].isdigit()]


def _random_total(width: int, cap: int, rng: random.Random) -> int:
    """A replacement total that still renders in exactly `width` digits.

    Bounded above by what the rows can actually add up to, so the split that
    follows is always solvable. The original total satisfies both bounds --
    it renders at this width and is the sum of the rows -- so the range is
    never empty.
    """
    high = min(10 ** width - 1, cap)
    low = 10 ** (width - 1) if 10 ** (width - 1) <= high else 0
    return rng.randint(low, high)


def _random_split(total: int, caps: list[int], rng: random.Random) -> list[int]:
    """Random values summing to `total`, with values[i] <= caps[i].

    Each row is drawn around its proportional share of what is left, so the
    replacement amounts keep the rough spread of a real statement instead of
    the first row absorbing the total and the rest landing on zero.
    """
    n = len(caps)
    order = list(range(n))
    rng.shuffle(order)
    ordered_caps = [caps[i] for i in order]

    # suffix[i] = how much rows i..n-1 can still absorb between them.
    suffix = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + ordered_caps[i]
    if not 0 <= total <= suffix[0]:
        raise ReconciliationError(
            f"cannot split a total across {n} row(s) of these widths"
        )

    drawn = [0] * n
    remaining = total
    for i in range(n):
        low = max(0, remaining - suffix[i + 1])
        high = min(ordered_caps[i], remaining)
        if low >= high:
            value = low
        else:
            share = remaining * ordered_caps[i] / suffix[i]
            mode = min(max(share, low), high)
            value = min(max(int(round(rng.triangular(low, high, mode))), low), high)
        drawn[i] = value
        remaining -= value

    result = [0] * n
    for position, original_index in enumerate(order):
        result[original_index] = drawn[position]
    return result


def _write_digits(chars: list[str], positions: list[int], value: int) -> None:
    rendered = str(value).zfill(len(positions))
    if len(rendered) != len(positions):
        raise ReconciliationError(
            f"{value} does not fit in {len(positions)} digits"
        )
    for index, digit in zip(positions, rendered, strict=True):
        chars[index] = digit


def _apply_reconciliation(text: str, groups: list[_Group], rng: random.Random) -> str:
    """Rewrite each group's amounts so the rows sum to the figure again.

    Only digit characters are written; commas, decimal points and every
    surrounding character stay exactly where they were, so this pass cannot
    move a column even by one position.
    """
    chars = list(text)
    for group in groups:
        total_positions = _digit_positions(text, group.total)
        row_positions = [_digit_positions(text, span) for span in group.rows]

        if not row_positions:
            # No rows to total: the only honest figure is zero, and the input
            # check above has already established the printed one is zero too.
            _write_digits(chars, total_positions, 0)
            continue

        caps = [10 ** len(p) - 1 for p in row_positions]
        total = _random_total(len(total_positions), sum(caps), rng)
        for positions, value in zip(row_positions, _random_split(total, caps, rng), strict=True):
            _write_digits(chars, positions, value)
        _write_digits(chars, total_positions, total)
    return "".join(chars)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def _split_ending(line: str) -> tuple[str, str]:
    """Split a line (as returned by str.splitlines(keepends=True)) into its
    content and its original line-ending, so the ending can be reattached
    unchanged (these statements use CRLF; forcing \\n would change nothing
    that matters for parsing but there is no reason to normalise it)."""
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def redact_text(text: str, rng: random.Random | None = None) -> str:
    """Redact `text`, returning a copy of exactly the same length.

    `rng` exists so tests can pin a sequence. Leave it unset for real work:
    the default draws from the OS and keeps nothing, which is what makes the
    result impossible to invert.
    """
    rng = rng if rng is not None else random.SystemRandom()

    raw_lines = text.splitlines(keepends=True)
    contents = []
    endings = []
    for raw in raw_lines:
        content, ending = _split_ending(raw)
        contents.append(content)
        endings.append(ending)

    contents = [_EMAIL_RE.sub(_redact_email, c) for c in contents]
    contents = _apply_name_address_rules(contents)

    rebuilt = "".join(c + e for c, e in zip(contents, endings, strict=True))

    # Located on the pre-substitution text so the input's own arithmetic can
    # be verified; substitution preserves length exactly and never turns a
    # digit into anything else, so the spans stay valid afterwards.
    issuer = detect_issuer(rebuilt)
    groups = _collect_groups(rebuilt, issuer) if issuer else []

    substituted = _substitute_digits(rebuilt, rng)
    return _apply_reconciliation(substituted, groups, rng) if groups else substituted


def redact_file(src: Path, dst: Path) -> None:
    with open(src, encoding="utf-8", newline="") as fh:
        original = fh.read()

    redacted = redact_text(original)

    if len(redacted) != len(original):
        # Should be impossible by construction (every rule above replaces
        # text with same-length text), but a silent length drift here would
        # break every downstream column-width assumption without a trace.
        raise AssertionError(
            f"redacted output length {len(redacted)} != input length {len(original)}"
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        fh.write(redacted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Redact a statement text file while preserving line "
        "structure and column widths exactly."
    )
    parser.add_argument("input", type=Path, help="path to the extracted statement text")
    parser.add_argument("output", type=Path, help="path to write the redacted copy to")
    args = parser.parse_args(argv)

    redact_file(args.input, args.output)
    print(f"Wrote redacted copy: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
