"""Points the dashboard at this project's folder.

The dashboard's queries read the CSVs through one Power BI parameter,
ProjectFolder, so the project runs from any folder on any PC. This rewrites
that parameter's value in the model files; the "Credit Card Dashboard"
shortcut and tools/install.ps1 run it before Power BI opens.

    python -m creditcard.powerbi
"""
import os
import re
import subprocess
import sys
from pathlib import Path

from creditcard.powershell import NO_WINDOW

EXPRESSIONS = Path("dashboard.SemanticModel") / "definition" / "expressions.tmdl"
_LINE = re.compile(r'^expression ProjectFolder = "([^"]*)" meta ', re.MULTILINE)


class PowerBIRunning(RuntimeError):
    """Power BI would overwrite the change when it next saves the model."""


def _power_bi_running() -> bool:
    result = subprocess.run(["tasklist", "/fi", "imagename eq PBIDesktop.exe", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, creationflags=NO_WINDOW)
    return "PBIDesktop.exe" in result.stdout


def set_project_folder(root: Path, running=_power_bi_running, folder: Path | None = None) -> bool:
    """Point ProjectFolder at `folder`, or at root itself (the usual case; the
    demo passes demo/). True if the file changed. Raises PowerBIRunning rather
    than write while Power BI is open."""
    root = Path(root).resolve()
    target = Path(folder).resolve() if folder else root
    path = root / EXPRESSIONS
    text = path.read_bytes().decode("utf-8")
    match = _LINE.search(text)
    if match is None:
        raise ValueError(f"No ProjectFolder parameter in {path}")
    if os.path.normcase(match.group(1)) == os.path.normcase(str(target)):
        return False
    if running():
        raise PowerBIRunning("Close Power BI Desktop first; it would undo the change on save.")
    updated = text[:match.start(1)] + str(target) + text[match.end(1):]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(updated.encode("utf-8"))
    os.replace(tmp, path)
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m creditcard.powerbi")
    parser.add_argument("--folder", help="read from this folder instead (the demo uses demo/)")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    target = Path(args.folder).resolve() if args.folder else root
    try:
        changed = set_project_folder(root, folder=target)
    except PowerBIRunning as exc:
        print(f"Power BI's folder setting was not changed: {exc}")
        return 3
    print(f"The dashboard {'now reads' if changed else 'already reads'} from {target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
