import string
from pathlib import Path

from creditcard.backup import back_up, destinations


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "statements" / "c1").mkdir(parents=True)
    (root / "statements" / "_unmatched").mkdir()
    (root / "config").mkdir()
    (root / "output").mkdir()
    (root / "statements" / "c1" / "a.pdf").write_bytes(b"%PDF a")
    (root / "statements" / "_unmatched" / "u.pdf").write_bytes(b"%PDF u")
    (root / "config" / "cards.csv").write_text("card_id")
    (root / "config" / "secrets.env").write_text("EMAIL_PASSWORD=pw")
    (root / "output" / "transactions.csv").write_text("every transaction")
    return root


def _missing_drive() -> Path:
    letter = next(c for c in reversed(string.ascii_uppercase) if not Path(f"{c}:\\").exists())
    return Path(f"{letter}:\\Backups\\CreditCard")


def test_copies_the_pdfs_and_config_but_not_secrets_or_outputs(tmp_path):
    root, dest = _project(tmp_path), tmp_path / "backup"
    result = back_up(root, [dest])
    assert (result.copied, result.failed) == (3, [])
    assert (dest / "statements" / "c1" / "a.pdf").read_bytes() == b"%PDF a"
    assert (dest / "statements" / "_unmatched" / "u.pdf").exists()
    assert (dest / "config" / "cards.csv").exists()
    assert not (dest / "config" / "secrets.env").exists()
    assert not (dest / "output").exists()


def test_a_second_run_copies_nothing_and_a_changed_file_is_copied_again(tmp_path):
    root, dest = _project(tmp_path), tmp_path / "backup"
    back_up(root, [dest])
    assert back_up(root, [dest]).copied == 0
    (root / "statements" / "c1" / "a.pdf").write_bytes(b"%PDF changed")
    assert back_up(root, [dest]).copied == 1
    assert (dest / "statements" / "c1" / "a.pdf").read_bytes() == b"%PDF changed"


def test_a_file_deleted_here_stays_in_the_backup(tmp_path):
    root, dest = _project(tmp_path), tmp_path / "backup"
    back_up(root, [dest])
    (root / "statements" / "c1" / "a.pdf").unlink()
    back_up(root, [dest])
    assert (dest / "statements" / "c1" / "a.pdf").exists()


def test_a_missing_drive_or_a_failed_copy_fails_only_that_destination(tmp_path):
    root, good = _project(tmp_path), tmp_path / "backup"
    blocked = tmp_path / "a_file_not_a_folder"
    blocked.write_text("x")
    result = back_up(root, [_missing_drive(), blocked, good])
    assert result.failed == [_missing_drive(), blocked]
    assert (good / "statements" / "c1" / "a.pdf").exists()


def test_destinations_come_from_backup_dirs():
    env = {"BACKUP_DIRS": r"C:\Users\me\OneDrive\Backups\CreditCard; E:\Backups\CreditCard ;"}
    assert destinations(env) == [Path(r"C:\Users\me\OneDrive\Backups\CreditCard"),
                                 Path(r"E:\Backups\CreditCard")]
    assert destinations({}) == []
