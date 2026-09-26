import os

from creditcard import vault

CARDS = (
    "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
    "c1,HDFC,Millennia,0000,hdfc,C1_PW,0,1\n"
)


class FakeVault:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


def _config(base, secrets=None):
    cfg = base / "config"
    cfg.mkdir(parents=True)
    (cfg / "cards.csv").write_text(CARDS, encoding="utf-8")
    if secrets is not None:
        (cfg / "secrets.env").write_bytes(secrets.encode("utf-8"))
    return cfg


def _blank(monkeypatch, *names):
    """setenv, not delenv, so monkeypatch restores whatever fill_env writes."""
    for name in names:
        monkeypatch.setenv(name, "")


def test_names_are_each_card_password_and_the_email_password(tmp_path):
    assert vault.names(_config(tmp_path)) == ["C1_PW", "EMAIL_PASSWORD"]


def test_fill_env_takes_blank_passwords_from_the_vault(tmp_path, monkeypatch):
    _blank(monkeypatch, "C1_PW", "EMAIL_PASSWORD")
    missing = vault.fill_env(_config(tmp_path), FakeVault(C1_PW="pdf-pw"))
    assert os.environ["C1_PW"] == "pdf-pw"
    assert missing == ["EMAIL_PASSWORD"]


def test_a_password_already_set_wins_over_the_vault(tmp_path, monkeypatch):
    _blank(monkeypatch, "EMAIL_PASSWORD")
    monkeypatch.setenv("C1_PW", "from-file")
    vault.fill_env(_config(tmp_path), FakeVault(C1_PW="from-vault"))
    assert os.environ["C1_PW"] == "from-file"


def test_migrate_moves_passwords_and_keeps_every_other_line(tmp_path, capsys):
    cfg = _config(tmp_path, secrets=(
        "# my settings\r\nC1_PW=pdf-pw\r\nEMAIL_USERNAME=me@gmail.com\r\n"
        "EMAIL_PASSWORD = app-pw\r\n# C1_PW=commented-out\r\n"
    ))
    fake = FakeVault()
    moved = vault.migrate(cfg, fake)
    assert moved == ["C1_PW", "EMAIL_PASSWORD"]
    assert fake.values == {"C1_PW": "pdf-pw", "EMAIL_PASSWORD": "app-pw"}
    assert (cfg / "secrets.env").read_bytes() == (
        b"# my settings\r\nC1_PW=\r\nEMAIL_USERNAME=me@gmail.com\r\n"
        b"EMAIL_PASSWORD=\r\n# C1_PW=commented-out\r\n"
    )
    out = capsys.readouterr().out
    assert "pdf-pw" not in out and "app-pw" not in out


def test_migrate_keeps_a_line_the_vault_did_not_keep(tmp_path):
    class Forgetful(FakeVault):
        def set(self, name, value):
            pass

    cfg = _config(tmp_path, secrets="C1_PW=pdf-pw\n")
    assert vault.migrate(cfg, Forgetful()) == []
    assert (cfg / "secrets.env").read_text(encoding="utf-8") == "C1_PW=pdf-pw\n"


def test_status_reports_plain_text_stored_and_missing(tmp_path):
    cfg = _config(tmp_path / "a", secrets="C1_PW=still-here\n")
    assert vault.status(cfg, FakeVault(EMAIL_PASSWORD="x")) == {
        "C1_PW": vault.PLAIN_TEXT, "EMAIL_PASSWORD": vault.STORED,
    }
    assert vault.status(_config(tmp_path / "b"), FakeVault())["C1_PW"] == vault.MISSING


def test_check_prints_names_never_values_and_fails_when_one_is_missing(tmp_path, capsys):
    _config(tmp_path, secrets="C1_PW=secret-value\n")
    assert vault.main(["--root", str(tmp_path), "check"], vault=FakeVault()) == 1
    out = capsys.readouterr().out
    assert "C1_PW: plain text in secrets.env" in out
    assert "EMAIL_PASSWORD: missing" in out
    assert "secret-value" not in out


def test_set_stores_what_is_typed_at_a_hidden_prompt(tmp_path, monkeypatch, capsys):
    _config(tmp_path)
    monkeypatch.setattr(vault.getpass, "getpass", lambda prompt: "typed-secret")
    fake = FakeVault()
    assert vault.main(["--root", str(tmp_path), "set", "C1_PW"], vault=fake) == 0
    assert fake.values == {"C1_PW": "typed-secret"}
    assert "typed-secret" not in capsys.readouterr().out


def test_load_secrets_fills_blank_passwords_from_credential_manager(tmp_path, monkeypatch):
    from creditcard.config import load_secrets

    cfg = _config(tmp_path, secrets="C1_PW=\nEMAIL_PASSWORD=\n")
    _blank(monkeypatch, "C1_PW", "EMAIL_PASSWORD")
    monkeypatch.setattr(vault, "Keyring", lambda: FakeVault(C1_PW="pdf-pw"))
    load_secrets(cfg / "secrets.env")
    assert os.environ["C1_PW"] == "pdf-pw"
