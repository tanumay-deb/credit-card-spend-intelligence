import csv
import smtplib
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from creditcard import daily
from creditcard.alerts import Memory
from creditcard.email_fetcher import EmailAuthError
from creditcard.notify import REFRESHED
from creditcard.writers import OutputLocked

TODAY = date(2026, 9, 20)
CARDS = (
    "card_id,issuer,card_name,last4,parser,password_env,credit_limit,statement_day\n"
    "c1,HDFC,Millennia,0000,hdfc,C1_PW,0,1\n"
)


def _write_statements(root: Path, *dates: str):
    path = root / "output" / "statements.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["statement_id", "card_id", "statement_date", "due_date", "source_file"])
        for d in dates:
            writer.writerow([f"c1_{d}", "c1", d, "", f"statements\\c1\\{d}.pdf"])


def _root(tmp_path, statements=(), told=None) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "cards.csv").write_text(CARDS, encoding="utf-8")
    _write_statements(tmp_path, *statements)
    if told is not None:
        Memory(told=set(told)).save(tmp_path / "output" / "notified.json")
    return tmp_path


def _memory(root):
    return Memory.load(root / "output" / "notified.json")


class Fakes:
    def __init__(self, downloaded=0, fetch_error=None, import_error=None,
                 workbook_skipped=False, refresh_result="refreshed", email_error=None,
                 new_statement=None, backup_failed=()):
        self.downloaded, self.fetch_error = downloaded, fetch_error
        self.import_error, self.workbook_skipped = import_error, workbook_skipped
        self.refresh_result, self.email_error = refresh_result, email_error
        self.new_statement = new_statement
        self.backup_failed, self.backups = list(backup_failed), 0
        self.imports = self.refreshes = 0
        self.emails, self.toasts = [], []

    def fetch(self, root):
        if self.fetch_error:
            raise self.fetch_error
        return [SimpleNamespace(status="downloaded")] * self.downloaded

    def import_(self, root):
        self.imports += 1
        if self.import_error:
            raise self.import_error
        if self.new_statement:
            _write_statements(root, self.new_statement)
        return {"workbook_skipped": self.workbook_skipped, "files_processed": 1}

    def refresh(self):
        self.refreshes += 1
        return self.refresh_result

    def backup(self, root):
        self.backups += 1
        return self.backup_failed

    def send_email(self, message):
        if self.email_error:
            raise self.email_error
        self.emails.append(message)

    def show_toast(self, title, lines):
        self.toasts.append(list(lines))

    def run(self, root):
        return daily.run(root, TODAY, fetch=self.fetch, import_=self.import_,
                         refresh=self.refresh, backup=self.backup,
                         send_email=self.send_email, show_toast=self.show_toast)


def test_a_new_statement_is_imported_refreshed_and_told(tmp_path):
    root = _root(tmp_path, told=[])
    fakes = Fakes(downloaded=1, new_statement="2026-09-18")
    report = fakes.run(root)
    assert (fakes.imports, fakes.refreshes, report.imported) == (1, 1, True)
    [email] = fakes.emails
    assert email.subject == "Credit cards: 1 new statement"
    assert "Millennia: September statement imported." in email.body
    assert REFRESHED in email.body
    assert fakes.toasts == [["1 new statement", daily.DETAILS_IN_EMAIL]]
    assert "statement:c1_2026-09-18" in _memory(root).told


def test_a_quiet_day_imports_and_tells_nothing(tmp_path):
    root = _root(tmp_path, statements=["2026-09-18"], told=["statement:c1_2026-09-18"])
    fakes = Fakes()
    fakes.run(root)
    assert (fakes.imports, fakes.refreshes, fakes.emails, fakes.toasts) == (0, 0, [], [])


def test_a_rejected_login_is_told_on_the_first_failure(tmp_path):
    root = _root(tmp_path, told=[])
    fakes = Fakes(fetch_error=EmailAuthError("rejected"))
    fakes.run(root)
    assert "Gmail rejected the app password" in fakes.emails[0].body


def test_a_network_failure_is_told_only_on_the_second_run_in_a_row(tmp_path):
    root = _root(tmp_path, told=[])
    failing = Fakes(fetch_error=OSError("unreachable"))
    failing.run(root)
    assert failing.emails == []
    failing.run(root)
    assert "Couldn't reach Gmail on the last 2 checks (OSError)." in failing.emails[0].body
    Fakes().run(root)
    assert _memory(root).fetch_failures == 0


def test_a_locked_output_is_retried_the_next_day_with_nothing_new_downloaded(tmp_path):
    root = _root(tmp_path, told=[])
    locked = Fakes(downloaded=1,
                   import_error=OutputLocked(root / "output" / "transactions.csv"))
    locked.run(root)
    assert "output/transactions.csv is open in another program" in locked.emails[0].body
    assert locked.refreshes == 0
    assert _memory(root).import_pending

    retry = Fakes(downloaded=0)
    retry.run(root)
    assert (retry.imports, retry.refreshes) == (1, 1)
    assert not _memory(root).import_pending


def test_a_skipped_workbook_still_refreshes_and_retries_tomorrow(tmp_path):
    root = _root(tmp_path, told=[])
    fakes = Fakes(downloaded=1, workbook_skipped=True)
    fakes.run(root)
    assert fakes.refreshes == 1
    assert "credit_card_data.xlsx was open" in fakes.emails[0].body
    assert _memory(root).import_pending


def test_a_failed_email_is_not_remembered_and_the_notification_says_so(tmp_path):
    root = _root(tmp_path, statements=["2026-09-18"], told=[])
    broken = Fakes(email_error=smtplib.SMTPException("535"))
    report = broken.run(root)
    assert not report.delivered
    assert broken.toasts == [["1 new statement", daily.EMAIL_FAILED]]
    assert "statement:c1_2026-09-18" not in _memory(root).told

    working = Fakes()
    working.run(root)
    assert working.emails[0].subject == "Credit cards: 1 new statement"


def test_a_crash_is_reported_and_keeps_what_was_told(tmp_path):
    root = _root(tmp_path, statements=["2026-09-18"], told=["statement:c1_2026-09-18"])
    fakes = Fakes(downloaded=1, import_error=KeyError("x"))
    report = fakes.run(root)
    assert report.crashed == "KeyError"
    assert "The daily check stopped with KeyError." in fakes.emails[0].body
    memory = _memory(root)
    assert "statement:c1_2026-09-18" in memory.told
    assert memory.import_pending


def test_the_first_run_stays_quiet_about_the_backlog(tmp_path):
    root = _root(tmp_path, statements=["2026-08-18", "2026-09-18"])
    first = Fakes()
    first.run(root)
    assert first.emails == []

    _write_statements(root, "2026-08-18", "2026-09-18", "2026-10-18")
    later = Fakes()
    daily.run(root, date(2026, 10, 20), fetch=later.fetch, import_=later.import_,
              refresh=later.refresh, backup=later.backup, send_email=later.send_email,
              show_toast=later.show_toast)
    assert later.emails[0].subject == "Credit cards: 1 new statement"


def test_preview_composes_without_sending_or_remembering(tmp_path):
    root = _root(tmp_path, statements=["2026-09-18"], told=[])
    message = daily.preview(root, TODAY)
    assert message.subject == "Credit cards: 1 new statement"
    assert _memory(root).told == set()


def test_daily_run_preview_prints_the_message(tmp_path, capsys):
    import daily_run

    root = _root(tmp_path, statements=["2026-09-18"], told=[])
    assert daily_run.main(["--root", str(root), "--preview"]) == 0
    assert "Credit cards: 1 new statement" in capsys.readouterr().out
    assert not (root / "output" / "logs").exists()


def _pdf(root, name):
    path = root / "statements" / "c1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF")


def _ingest_log(root, *names):
    path = root / "output" / "ingest_log.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source_file", "status"])
        for name in names:
            writer.writerow([f"D:\anywhere\\statements\\c1\\{name}", "ok"])


def test_a_pdf_the_last_import_never_saw_is_imported(tmp_path):
    """A PDF saved by a fetch that then failed (or dropped in by hand) is
    already known to the fetcher, so it is never 'downloaded' again. Without
    this it would never be imported by the daily check."""
    root = _root(tmp_path, told=[])
    _pdf(root, "a.pdf")
    _pdf(root, "b.pdf")
    _ingest_log(root, "a.pdf")
    fakes = Fakes(downloaded=0)
    fakes.run(root)
    assert fakes.imports == 1


def test_pdfs_already_imported_do_not_trigger_an_import(tmp_path):
    root = _root(tmp_path, told=[])
    _pdf(root, "a.pdf")
    _ingest_log(root, "a.pdf")
    fakes = Fakes(downloaded=0)
    fakes.run(root)
    assert fakes.imports == 0


def test_the_backup_runs_every_day_and_a_failure_is_told_once(tmp_path):
    root = _root(tmp_path, statements=["2026-09-18"], told=["statement:c1_2026-09-18"])
    failing = Fakes(backup_failed=[r"E:\Backups\CreditCard"])
    failing.run(root)
    assert failing.backups == 1
    assert r"Backup to E:\Backups\CreditCard failed." in failing.emails[0].body
    again = Fakes(backup_failed=[r"E:\Backups\CreditCard"])
    again.run(root)
    assert (again.backups, again.emails) == (1, [])
