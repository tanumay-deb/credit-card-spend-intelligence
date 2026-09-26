import subprocess
from types import SimpleNamespace

import pytest

from creditcard import dashboard


def _runner(returncode=0, raises=None):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if raises:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout="message", stderr="")

    run.calls = calls
    return run


@pytest.mark.parametrize("code, result", [
    (0, dashboard.REFRESHED), (3, dashboard.NOT_OPEN), (1, dashboard.FAILED), (7, dashboard.FAILED),
])
def test_exit_code_maps_to_a_result(code, result):
    assert dashboard.refresh(runner=_runner(code)) == result


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("powershell", 360), OSError("gone")])
def test_a_hung_or_missing_powershell_counts_as_failed(error):
    assert dashboard.refresh(runner=_runner(raises=error)) == dashboard.FAILED


def test_runs_the_refresh_script_hidden():
    run = _runner(0)
    dashboard.refresh(runner=run, timeout=42)
    args, kwargs = run.calls[0]
    script = args[args.index("-File") + 1]
    assert script.endswith("refresh_dashboard.ps1")
    assert dashboard.SCRIPT.exists()
    assert kwargs["timeout"] == 42
    assert "creationflags" in kwargs
