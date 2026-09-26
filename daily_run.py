# daily_run.py
"""Same as `cards daily`. Task Scheduler runs this file (tools/install.ps1)."""
import sys

from creditcard.__main__ import main as cards


def main(argv: list[str] | None = None) -> int:
    return cards(["daily", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
