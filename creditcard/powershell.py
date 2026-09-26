"""Running Windows PowerShell 5.1 with no console window.

Windows PowerShell, not PowerShell 7: the notification uses WinRT type syntax
that only 5.1 has, and 5.1 ships with every copy of Windows 10 and 11.
"""
import os
import subprocess
from pathlib import Path

POWERSHELL = str(
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
)
BASE_ARGS = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
# The daily run has no console of its own (pythonw), so without this every
# child PowerShell would flash a console window on screen.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
