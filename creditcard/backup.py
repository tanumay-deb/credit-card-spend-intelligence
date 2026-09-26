"""Copies of what can't be rebuilt: the statement PDFs and the config.

output/ is rebuilt from statements/ and config/ by ingest.py, and its CSVs list
every transaction in plain text, so it stays out of backups. The PDFs keep
their banks' passwords wherever they are copied, and secrets.env is never
copied.

Destinations come from BACKUP_DIRS in config/secrets.env, separated by ";". A
file is copied when a destination lacks it or holds a different size or
modified time. Nothing in a destination is ever deleted, so a PDF deleted here
by mistake survives there.

    python -m creditcard.backup     back up now and say what was copied
"""
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

NEVER_COPIED = {"secrets.env"}
# FAT and exFAT drives store modified times to 2 seconds.
_TIME_SLACK = 2.0


@dataclass(frozen=True)
class BackupResult:
    copied: int
    failed: list[Path]


def destinations(env=None) -> list[Path]:
    env = os.environ if env is None else env
    return [Path(part.strip()) for part in env.get("BACKUP_DIRS", "").split(";") if part.strip()]


def back_up(root: Path, dests: list[Path]) -> BackupResult:
    """Copy into every destination. One that fails -- its drive missing, or a
    copy raising -- is logged and skipped, and the rest still run."""
    sources = _sources(Path(root))
    copied, failed = 0, []
    for dest in dests:
        dest = Path(dest)
        if not dest.is_absolute() or not Path(dest.anchor).exists():
            log.warning("Backup to %s skipped: the drive isn't there", dest)
            failed.append(dest)
            continue
        try:
            for source, relative in sources:
                target = dest / relative
                if _same(source, target):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
        except OSError as exc:
            log.warning("Backup to %s failed: %s: %s", dest, type(exc).__name__, exc)
            failed.append(dest)
    return BackupResult(copied, failed)


def _sources(root: Path) -> list[tuple[Path, Path]]:
    pdfs = sorted((root / "statements").rglob("*.pdf"))
    config = root / "config"
    settings = sorted(
        path for path in (config.iterdir() if config.exists() else [])
        if path.is_file() and path.name not in NEVER_COPIED and not path.name.endswith(".tmp")
    )
    return [(path, path.relative_to(root)) for path in [*pdfs, *settings]]


def _same(source: Path, target: Path) -> bool:
    try:
        theirs = target.stat()
    except FileNotFoundError:
        return False
    ours = source.stat()
    return (ours.st_size == theirs.st_size
            and abs(ours.st_mtime - theirs.st_mtime) < _TIME_SLACK)


def main(root: Path | None = None) -> int:
    from creditcard.config import load_secrets

    root = Path(root) if root else Path(__file__).resolve().parent.parent
    load_secrets(root / "config" / "secrets.env")
    dests = destinations()
    if not dests:
        print("No backup destinations: set BACKUP_DIRS in config/secrets.env.")
        return 1
    result = back_up(root, dests)
    print(f"Copied {result.copied} file(s) to {len(dests) - len(result.failed)} "
          f"of {len(dests)} destination(s).")
    for dest in result.failed:
        print(f"  failed: {dest}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
