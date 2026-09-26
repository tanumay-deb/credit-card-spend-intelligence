"""Passwords in Windows Credential Manager instead of config/secrets.env.

Each password is a Credential Manager entry under the service "CreditCard",
named like its environment variable: every card's password_env from cards.csv,
plus EMAIL_PASSWORD. fill_env copies them into this process's environment,
where the parsers, the fetcher and the email sender already look, so nothing
else needs to know where passwords are kept.

A password still written in secrets.env wins, so moving back is only a matter
of writing it there again; `check` reports it as plain text.

    python -m creditcard.vault migrate      move passwords out of secrets.env
    python -m creditcard.vault set NAME     store one, typed at a hidden prompt
    python -m creditcard.vault check        which are stored, missing or in plain text

No command prints a password.
"""
import argparse
import getpass
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE = "CreditCard"
EMAIL_PASSWORD = "EMAIL_PASSWORD"
ENCODING = "utf-8-sig"
STORED, MISSING, PLAIN_TEXT = "stored", "missing", "plain text in secrets.env"


class Keyring:
    """The real vault: Windows Credential Manager, through keyring."""

    def get(self, name: str) -> str | None:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(SERVICE, name)
        except KeyringError as exc:
            log.warning("Couldn't read %s from Credential Manager: %s", name, type(exc).__name__)
            return None

    def set(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)


def names(config_dir: Path) -> list[str]:
    """Every password the project uses: each card's, then the Gmail one."""
    from creditcard.config import load_cards

    cards = Path(config_dir) / "cards.csv"
    found = [card.password_env for card in load_cards(cards).values()] if cards.exists() else []
    return list(dict.fromkeys([*found, EMAIL_PASSWORD]))


def fill_env(config_dir: Path, vault=None) -> list[str]:
    """Set each password that is blank in the environment from the vault, for
    this process only. Returns the names found in neither."""
    vault = vault or Keyring()
    missing = []
    for name in names(config_dir):
        if os.environ.get(name):
            continue
        value = vault.get(name)
        if value:
            os.environ[name] = value
        else:
            missing.append(name)
    return missing


def migrate(config_dir: Path, vault=None) -> list[str]:
    """Move every password written in secrets.env into the vault and blank its
    line. A line is blanked only once the vault gives the value back, and every
    other line is kept byte for byte. Returns the names moved."""
    vault = vault or Keyring()
    path = Path(config_dir) / "secrets.env"
    if not path.exists():
        return []
    wanted = set(names(config_dir))
    with open(path, encoding=ENCODING, newline="") as fh:
        lines = fh.read().splitlines(keepends=True)

    moved, kept = [], []
    for line in lines:
        body = line.rstrip("\r\n")
        key, sep, value = body.partition("=")
        key, value = key.strip(), value.strip()
        if sep and key in wanted and value and not key.startswith("#"):
            vault.set(key, value)
            if vault.get(key) == value:
                kept.append(f"{key}={line[len(body):]}")
                moved.append(key)
                continue
            log.warning("Credential Manager didn't keep %s; left it in secrets.env", key)
        kept.append(line)

    if moved:
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(kept))
        os.replace(tmp, path)
    return moved


def status(config_dir: Path, vault=None) -> dict[str, str]:
    """Each password's state: STORED, MISSING or PLAIN_TEXT."""
    vault = vault or Keyring()
    written = _written_in_file(Path(config_dir) / "secrets.env")
    return {
        name: PLAIN_TEXT if name in written else STORED if vault.get(name) else MISSING
        for name in names(config_dir)
    }


def _written_in_file(path: Path) -> set[str]:
    if not path.exists():
        return set()
    written = set()
    with open(path, encoding=ENCODING) as fh:
        for line in fh:
            key, sep, value = line.strip().partition("=")
            if sep and value.strip() and not key.startswith("#"):
                written.add(key.strip())
    return written


def main(argv: list[str] | None = None, vault=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m creditcard.vault",
                                     description="Keep the project's passwords in "
                                                 "Windows Credential Manager.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="project root (default: this project)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="move passwords out of config/secrets.env")
    set_parser = commands.add_parser("set", help="store one password, typed at a hidden prompt")
    set_parser.add_argument("name", help="e.g. EMAIL_PASSWORD or a card's password_env")
    commands.add_parser("check", help="show which passwords are stored, missing or in plain text")
    args = parser.parse_args(argv)
    config_dir = Path(args.root) / "config"
    vault = vault or Keyring()

    if args.command == "migrate":
        moved = migrate(config_dir, vault)
        print(f"Moved into Windows Credential Manager: {', '.join(moved)}" if moved
              else "No passwords in config/secrets.env to move.")
        return 0

    if args.command == "set":
        if args.name not in names(config_dir):
            print(f"Note: no card in cards.csv uses {args.name}.")
        value = getpass.getpass(f"{args.name} (input hidden): ")
        if not value:
            print("Nothing entered; nothing stored.")
            return 1
        vault.set(args.name, value)
        print(f"Stored {args.name} in Windows Credential Manager.")
        return 0

    states = status(config_dir, vault)
    for name, state in states.items():
        print(f"{name}: {state}")
    return 1 if MISSING in states.values() else 0


if __name__ == "__main__":
    sys.exit(main())
