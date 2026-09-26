"""Blocks commits that would put private data into git.

    python tools/check_secrets.py --staged   what is about to be committed (pre-commit hook)
    python tools/check_secrets.py --all      every tracked file (CI)

It fails on any of these:
- a path under statements/ or output/, or secrets.env, a PDF, a .pbix or
  Power BI's cache.abf;
- a password the project uses, from Windows Credential Manager or secrets.env
  (--staged only: CI has neither);
- a 16-digit number that passes the Luhn check card numbers use, unless it is
  one of the known fakes the tests use.

Output names the file and the reason, never the text that matched.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_PATH = re.compile(
    r"(^|/)(statements|output)/|(^|/)secrets\.env$|\.(pdf|pbix)$|(^|/)cache\.abf$",
    re.IGNORECASE,
)
# Not part of a longer number or a decimal: 0.3333333333333333 is no card.
CARD_NUMBER = re.compile(r"(?<![\d.])\d{16}(?![\d.])")
FAKE_CARD_NUMBERS = frozenset({"4315123412341234"})  # tests/test_alerts.py
# Shorter passwords would match ordinary text; none of this project's are.
MIN_PASSWORD_LENGTH = 6


def luhn_valid(number: str) -> bool:
    total = 0
    for position, char in enumerate(reversed(number)):
        digit = int(char)
        if position % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def problems(files: dict[str, bytes], passwords: list[str]) -> list[str]:
    """One line per problem: the file and the reason."""
    passwords = [p for p in passwords if len(p) >= MIN_PASSWORD_LENGTH]
    found = []
    for path, data in sorted(files.items()):
        if PRIVATE_PATH.search(path):
            found.append(f"{path}: private file - statements, outputs, secrets.env, "
                         "PDFs and Power BI data stay out of git")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary, such as an image
        if any(password in text for password in passwords):
            found.append(f"{path}: contains a password stored in Credential Manager")
        if any(luhn_valid(n) and n not in FAKE_CARD_NUMBERS for n in CARD_NUMBER.findall(text)):
            found.append(f"{path}: contains a 16-digit number that looks like a card number")
    return found


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True).stdout


def staged_files() -> dict[str, bytes]:
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode().split("\0")
    return {name: _git("show", f":{name}") for name in names if name}


def tracked_files() -> dict[str, bytes]:
    names = _git("ls-files", "-z").decode().split("\0")
    return {name: (ROOT / name).read_bytes() for name in names if name and (ROOT / name).is_file()}


def project_passwords() -> list[str]:
    """Every password the project uses, from Credential Manager and secrets.env."""
    sys.path.insert(0, str(ROOT))
    try:
        from creditcard.vault import Keyring, names

        wanted = names(ROOT / "config")
        values = [Keyring().get(name) for name in wanted]
    except Exception:  # noqa: BLE001 - no vault here: check paths and numbers only
        return []
    secrets = ROOT / "config" / "secrets.env"
    if secrets.exists():
        for line in secrets.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in wanted and value.strip():
                values.append(value.strip())
    return [value for value in values if value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Block private data from being committed.")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--staged", action="store_true", help="check what is about to be committed")
    which.add_argument("--all", action="store_true", help="check every tracked file")
    args = parser.parse_args(argv)

    if args.staged:
        found = problems(staged_files(), project_passwords())
    else:
        found = problems(tracked_files(), passwords=[])
    for line in found:
        print(line, file=sys.stderr)
    if found and args.staged:
        print("Commit blocked. Unstage with: git restore --staged <file>", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
