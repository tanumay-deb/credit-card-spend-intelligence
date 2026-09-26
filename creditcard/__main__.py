"""cards: the one command for this project. cards.cmd runs `python -m creditcard`.

    cards fetch       download statement PDFs from Gmail
    cards ingest      import every statement into output/
    cards daily       the daily check (--preview, --test-notify)
    cards backup      back up statements/ and config/ now
    cards vault       passwords in Credential Manager: migrate | set NAME | check
    cards dashboard   open the dashboard and refresh it (--demo: on made-up data)
    cards demo        write a year of made-up data to demo/, for trying it out

Every subcommand takes --root, the project folder, which defaults to this project.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from creditcard.commands import daily, fetch, ingest
from creditcard.powershell import BASE_ARGS

ROOT = Path(__file__).resolve().parent.parent


def _backup(args: argparse.Namespace) -> int:
    from creditcard import backup

    return backup.main(args.root)


def _dashboard(args: argparse.Namespace) -> int:
    script = args.root / "tools" / "open_dashboard.ps1"
    extra = []
    if args.demo:
        folder = args.root / "demo"
        if not (folder / "output" / "transactions.csv").exists():
            print("No demo data yet - run: cards demo")
            return 1
        from creditcard.powerbi import PowerBIRunning, set_project_folder

        try:
            set_project_folder(args.root, folder=folder)
        except PowerBIRunning:
            print("Close Power BI first: an open dashboard keeps showing the data it has.")
            return 1
        extra = ["-Folder", str(folder)]
    return subprocess.run([*BASE_ARGS, "-File", str(script), *extra]).returncode


def _demo(args: argparse.Namespace) -> int:
    from datetime import date

    from creditcard import demo

    today = date.fromisoformat(args.as_of) if args.as_of else None
    folder = demo.build(args.root, today=today, seed=args.seed)
    print(f"Demo data written to {folder}. See it with: cards dashboard --demo")
    print("(Close Power BI first. A plain `cards dashboard` switches back to your data.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["vault"]:
        # The vault has its own subcommands; hand it everything after "vault".
        from creditcard import vault

        return vault.main(argv[1:])

    parser = argparse.ArgumentParser(
        prog="cards", description="Credit card statements: fetch, import, check, back up.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=ROOT,
                        help="project folder (default: this project)")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, module, summary in (
        ("fetch", fetch, "download statement PDFs from Gmail"),
        ("ingest", ingest, "import every statement into output/"),
        ("daily", daily, "the daily check that Task Scheduler runs"),
    ):
        sub = commands.add_parser(name, parents=[common], help=summary, description=summary)
        module.add_arguments(sub)
        sub.set_defaults(run=module.run)
    commands.add_parser("backup", parents=[common],
                        help="back up statements/ and config/ now").set_defaults(run=_backup)
    dash = commands.add_parser("dashboard", parents=[common],
                               help="open the dashboard and refresh it")
    dash.add_argument("--demo", action="store_true", help="show the made-up demo data")
    dash.set_defaults(run=_dashboard)
    made_up = commands.add_parser("demo", parents=[common],
                                  help="write a year of made-up data to demo/")
    made_up.add_argument("--as-of", help="end the year at this date (YYYY-MM-DD); default today")
    made_up.add_argument("--seed", type=int, default=7, help="another seed, another demo")
    made_up.set_defaults(run=_demo)
    commands.add_parser("vault", help="passwords in Credential Manager: migrate | set NAME | check")

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
