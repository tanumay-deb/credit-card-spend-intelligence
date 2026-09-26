import pytest

from creditcard import powerbi
from creditcard.powerbi import PowerBIRunning, set_project_folder

LINE = ('expression ProjectFolder = "{}" meta [IsParameterQuery=true, Type="Text", '
        'IsParameterQueryRequired=true]')


def _project(tmp_path, folder, newline="\n"):
    path = tmp_path / powerbi.EXPRESSIONS
    path.parent.mkdir(parents=True)
    text = LINE.format(folder) + newline + "\tlineageTag: x" + newline
    path.write_bytes(text.encode("utf-8"))
    return path


def _not_running():
    return False


def test_points_the_parameter_at_the_project_folder(tmp_path):
    path = _project(tmp_path, r"C:\Projects\CreditCard")
    assert set_project_folder(tmp_path, running=_not_running) is True
    assert LINE.format(tmp_path.resolve()) in path.read_text(encoding="utf-8")


def test_leaves_the_file_alone_when_it_already_matches(tmp_path):
    path = _project(tmp_path, str(tmp_path.resolve()).upper())
    before = path.read_bytes()
    assert set_project_folder(tmp_path, running=lambda: True) is False
    assert path.read_bytes() == before


def test_keeps_windows_line_endings(tmp_path):
    path = _project(tmp_path, r"D:\old", newline="\r\n")
    set_project_folder(tmp_path, running=_not_running)
    assert path.read_bytes().count(b"\r\n") == 2


def test_refuses_while_power_bi_is_running(tmp_path):
    """Power BI rewrites the model when it saves, undoing the change."""
    path = _project(tmp_path, r"D:\old")
    with pytest.raises(PowerBIRunning):
        set_project_folder(tmp_path, running=lambda: True)
    assert r"D:\old" in path.read_text(encoding="utf-8")


def test_the_real_model_has_the_parameter_and_no_hard_coded_folder():
    from pathlib import Path

    root = Path(powerbi.__file__).resolve().parent.parent
    assert powerbi._LINE.search((root / powerbi.EXPRESSIONS).read_text(encoding="utf-8"))
    for table in (root / "dashboard.SemanticModel" / "definition" / "tables").glob("*.tmdl"):
        text = table.read_text(encoding="utf-8")
        assert "C:\\Projects" not in text, table.name


def test_can_point_the_parameter_at_another_folder(tmp_path):
    path = _project(tmp_path, r"C:\Projects\CreditCard")
    target = tmp_path / "demo"
    assert set_project_folder(tmp_path, running=_not_running, folder=target) is True
    assert LINE.format(target.resolve()) in path.read_text(encoding="utf-8")
