from tools.check_secrets import problems, tracked_files

# Built at run time so no card-shaped literal sits in this file for the
# guard's own --all run to find.
CARD = "41" + "1" * 14            # passes the Luhn check, like a real card number
NOT_A_CARD = "41" + "1" * 13 + "2"  # fails it


def test_private_paths_are_blocked():
    files = {
        "statements/hdfc_regalia/2026-09.pdf": b"%PDF",
        "output/transactions.csv": b"txn_id",
        "config/secrets.env": b"EMAIL_PASSWORD=",
        "dashboard.pbix": b"",
        "dashboard.SemanticModel/.pbi/cache.abf": b"",
        "README.md": b"fine",
    }
    blocked = problems(files, passwords=[])
    assert len(blocked) == 5
    assert not any(line.startswith("README.md") for line in blocked)


def test_a_stored_password_is_found_but_never_printed():
    blocked = problems({"notes.md": b"login with Hunter2Pass then"}, passwords=["Hunter2Pass"])
    assert blocked == ["notes.md: contains a password stored in Credential Manager"]
    assert "Hunter2Pass" not in blocked[0]


def test_passwords_too_short_to_match_reliably_are_ignored():
    assert problems({"a.py": b"x = 1234"}, passwords=["1234"]) == []


def test_a_card_shaped_number_is_blocked():
    assert problems({"a.py": f"n = {CARD}".encode()}, passwords=[]) == [
        "a.py: contains a 16-digit number that looks like a card number"
    ]


def test_decimals_luhn_failures_and_known_fakes_pass():
    files = {
        "a.json": f'{{"x": 0.{CARD}}}'.encode(),
        "b.txt": f"Invoice No: {NOT_A_CARD}".encode(),
        "c.py": b'SECRET_FILE = "4315123412341234_statement.pdf"',
    }
    assert problems(files, passwords=[]) == []


def test_binary_files_are_skipped_for_content():
    assert problems({"icon.png": b"\x89PNG\xff\xfe" + CARD.encode()}, passwords=[]) == []


def test_every_tracked_file_passes():
    """What CI runs: the repository as committed holds nothing private."""
    assert problems(tracked_files(), passwords=[]) == []
