import pytest

from creditcard.__main__ import main
from tests.test_daily import _root
from tests.test_pipeline_run import _seed_config


def test_ingest_writes_the_outputs(tmp_path):
    _seed_config(tmp_path)
    assert main(["ingest", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "output" / "transactions.csv").exists()


def test_daily_preview_prints_what_would_be_told(tmp_path, capsys):
    root = _root(tmp_path, statements=["2026-09-18"], told=[])
    assert main(["daily", "--root", str(root), "--preview"]) == 0
    assert "Credit cards: 1 new statement" in capsys.readouterr().out


def test_vault_gets_everything_after_its_name(tmp_path, capsys):
    _seed_config(tmp_path)
    assert main(["vault", "--root", str(tmp_path), "check"]) == 1
    assert "EMAIL_PASSWORD: missing" in capsys.readouterr().out


def test_an_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as info:
        main(["frobnicate"])
    assert info.value.code == 2
