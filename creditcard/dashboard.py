"""Refreshing the open Power BI dashboard, through tools/refresh_dashboard.ps1.

The script presses Power BI Desktop's own Refresh button and confirms the data
reloaded; see its header for why it doesn't send a refresh command instead.
"""
import logging
import subprocess
from pathlib import Path

from creditcard.powershell import BASE_ARGS, NO_WINDOW

log = logging.getLogger(__name__)

REFRESHED, NOT_OPEN, FAILED = "refreshed", "not_open", "failed"
SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "refresh_dashboard.ps1"


def refresh(runner=subprocess.run, timeout: int = 360) -> str:
    """REFRESHED, NOT_OPEN when the dashboard isn't open, or FAILED."""
    try:
        result = runner([*BASE_ARGS, "-File", str(SCRIPT)], capture_output=True, text=True,
                        timeout=timeout, creationflags=NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Power BI refresh did not complete: %s", type(exc).__name__)
        return FAILED
    output = (result.stdout or "").strip()
    if result.returncode == 0:
        log.info("Power BI: %s", output)
        return REFRESHED
    if result.returncode == 3:
        log.info("Power BI: the dashboard is not open")
        return NOT_OPEN
    log.warning("Power BI refresh failed (exit %s): %s", result.returncode, output[-500:])
    return FAILED
