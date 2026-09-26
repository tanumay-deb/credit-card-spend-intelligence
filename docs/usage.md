# Usage guide

Everything needed to run, operate and maintain the project. The overview is in the [README](../README.md).

Parses password-protected credit card statement PDFs into CSVs that Power BI reads.

Five cards across three issuers: HDFC (two cards), ICICI and SBI (two cards).

## Why a Python step at all

Power BI's PDF connector cannot open password-protected PDFs, and every statement
here is encrypted. So a script decrypts, parses and normalises before Power BI
sees anything.

## The `cards` command

Everything runs through one command, `cards.cmd` in the project folder. It works from
any folder:

```bash
cards fetch        # download statement PDFs from Gmail
cards ingest       # import every statement into output/
cards daily        # the daily check (--preview, --test-notify)
cards backup       # back up statements/ and config/ now
cards vault check  # passwords in Credential Manager (migrate | set NAME | check)
cards dashboard    # open the dashboard and refresh it
```

`cards <command> --help` lists each command's options. In PowerShell, run it from
the project folder as `.\cards`. From any other folder, use the full path,
`C:\Projects\CreditCard\cards`. The old `ingest.py`,
`fetch_statements.py` and `daily_run.py` still work, and run the same code.

## Setup

1. Create the virtualenv and install dependencies:

   ```bash
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.lock
   ```

   Commands here use Windows paths and run in cmd.exe or PowerShell. In Git
   Bash, use forward slashes instead: `./.venv/Scripts/python.exe`.

   Always run through `.venv` — the pinned versions here are older than what may
   be installed globally, and installing them globally would downgrade other projects.

2. Fill in `config/cards.csv` with your real values: last four digits, credit limit,
   statement day. The `parser` and `password_env` columns are already correct.

3. Create `config/secrets.env` from the template. Fill in your Gmail address and
   `BACKUP_DIRS`, and leave the password lines blank:

   ```bash
   copy config\secrets.env.example config\secrets.env
   ```

   The passwords go into Windows Credential Manager. Each one is encrypted with your
   Windows login, and you can see them under Control Panel → Credential Manager →
   Windows Credentials, as `CreditCard`. Store each password at a hidden prompt:

   ```bash
   cards vault set HDFC_MILLENNIA_PDF_PASSWORD
   cards vault check
   ```

   The names are each card's `password_env` from `cards.csv`, plus `EMAIL_PASSWORD`.
   `check` lists every name as `stored`, `missing` or `plain text in secrets.env`, and
   never shows a value. If passwords are still written in `secrets.env`,
   `cards vault migrate` moves them into Credential Manager and blanks
   their lines. `secrets.env` is git-ignored — never commit it.

4. Put statement PDFs in `statements/<card_id>/`, for example
   `statements/hdfc_regalia/2026-08.pdf`.

   Folders are keyed by `card_id`, not by bank. Two of these cards are SBI, and an
   issuer-keyed folder would make each claim the other's statements.

## Runs on its own

Once it's set up, a Windows scheduled task runs a check every day at 12 PM:

1. It downloads new statement PDFs from Gmail. It looks back 60 days and skips anything
   it already has.
2. If something new arrived, it imports everything. Output files open in Excel are
   handled; see below.
3. If the dashboard is open in Power BI Desktop, it presses the dashboard's Refresh
   button.
4. It emails you, from your Gmail to itself, and shows a Windows notification. It does
   this for each new statement and for anything that needs you:
   - a statement that won't open or doesn't add up;
   - an unrecognised PDF in `statements/_unmatched/`;
   - a statement more than 7 days late;
   - new merchants for `review_queue.csv`;
   - Gmail failing.

   It tells you about each thing once. On a quiet day it sends nothing.

   Each new statement gets three lines in the email:
   - the statement period;
   - the total due, the minimum due, and the due date with a countdown;
   - what the cycle's spending came to, with its top 3 categories.

   Each problem comes with a line on how to fix it. Messages never contain merchant
   names or card numbers.

To set it up:

```bash
cards daily --preview
cards daily --test-notify
powershell -ExecutionPolicy Bypass -File tools\install.ps1
```

1. `--preview` prints what the first check would tell you, and sends nothing.
2. `--test-notify` sends one test email and one test notification.
3. `install.ps1` creates two things:
   - **The "CreditCard daily check" task.** It runs every day at 12:00, only while you're
     logged in. If the PC was off or asleep at that time, it catches up.
   - **A "Credit Card Dashboard" desktop shortcut.** It opens the dashboard and refreshes
     it once the dashboard has loaded.

   `-At 09:30` picks another time, and `-Uninstall` removes both.

**Log and memory:**
- Every check adds to `output/logs/daily_run.log`.
- `output/notified.json` records what you've already been told. If you delete it, the
  next check starts afresh without re-announcing old statements.

**Backups:** every check also copies `statements/` and `config/` to each folder in
`BACKUP_DIRS`:
- `secrets.env` and `output/` are never copied. The CSVs in `output/` list every
  transaction in plain text, and they can be rebuilt from the PDFs.
- Only new or changed files are copied, and nothing in a backup is ever deleted.
- A folder on a missing drive is skipped, and you're told once.
- To back up right away, run `cards backup`.

**When a file is open in Excel:** if any output CSV is open, the import changes nothing.
The check tells you which file to close and tries again the next day. If only
`credit_card_data.xlsx` is open, the CSVs are still updated and only the workbook waits.

## Setting up a new PC

1. **Install the software.** Install Python 3.11 and Power BI Desktop. Clone this
   repository into any folder, then create `.venv` and install `requirements.lock`
   (Setup, step 1).
2. **Restore your statements.** Copy `statements\` back from a backup. The backup
   folders are in `BACKUP_DIRS`, for example `OneDrive\Backups\CreditCard\statements`.
3. **Create `config\secrets.env`.** Copy it from `config\secrets.env.example`, then fill
   in `EMAIL_USERNAME` and `BACKUP_DIRS`.
4. **Store the passwords.** Run `cards vault check` to
   see which passwords are needed. Store each one with
   `cards vault set NAME`.
5. **Rebuild the data.** Run `cards ingest`. This rebuilds
   `output\` from the PDFs.
6. **Install the daily check.** Run
   `powershell -ExecutionPolicy Bypass -File tools\install.ps1`. It also points the
   dashboard's `ProjectFolder` parameter at the folder you cloned into, so nothing
   refers to `C:\Projects\CreditCard` any more.
7. **Open the dashboard.** Open it with the Credit Card Dashboard shortcut, which
   refreshes it.

## Running it by hand

```bash
cards ingest
```

Then:

1. Check `output/ingest_log.csv` for `failed` or `warning` rows. A `warning` means
   `sum_check_delta` is non-zero — the parsed rows do not add up to the statement's
   own printed total, so the parser missed something.
2. Tag anything worth naming from `output/review_queue.csv` by adding lines to
   `config/merchant_map.csv`, whose columns are `merchant_clean,subcategory`. The
   queue is sorted by value, so the biggest unknowns come first. A mapping here
   beats any keyword rule, and re-running retags matching history, not just new rows.
3. Refresh in Power BI.

The command exits non-zero if any statement failed to parse or failed to reconcile,
so it is safe to use in a scheduled task.

## When you pay a bill

Append a line to `config/payments.csv`:

```csv
hdfc_regalia,2026-09-05,32000,paid in full
```

This is the only manual data entry in the system, and it exists for a reason: a
statement PDF only shows a payment in the *following* month's statement. Without
this line, a bill you paid last week looks unpaid until next month's PDF arrives.

The next statement checks your work — its own printed payments figure is compared
against what you logged, and a mismatch is reported.

## Files you own vs files the script owns

| Path | Owner | Notes |
|---|---|---|
| `config/*.csv`, `config/secrets.env` | you | The script only ever reads these |
| `statements/<card_id>/*.pdf` | you | Git-ignored |
| `output/*.csv` | the script | Regenerated every run; safe to delete |
| `output/notified.json`, `output/logs/` | the daily check | What you've been told, and a log of every check |

There is no partial state. `output/` is derived entirely from `statements/` plus
`config/`, so deleting it and re-running reproduces it exactly. That is also the
recovery path after fixing a parser.

## Power BI

Save the report as **`.pbip`** (File → Save As → Power BI project), not `.pbix`.
A `.pbix` is a binary that embeds a cached copy of your transactions — committing
one would put every purchase you have made into git history. `.pbip` stores the
report and model definition as text, with no data. `*.pbix` is git-ignored.
## Downloading statements from email

You can automatically download statement PDFs from your email into `statements/<card_id>/`:

1. Add your IMAP settings to `config/secrets.env`, and store the password in Credential
   Manager. For Gmail, generate an App Password in your Google Account security
   settings.
   ```
   EMAIL_IMAP_SERVER=imap.gmail.com
   EMAIL_IMAP_PORT=993
   EMAIL_USERNAME=your_email@gmail.com
   EMAIL_PASSWORD=
   EMAIL_FOLDER=INBOX
   ```
   ```bash
   cards vault set EMAIL_PASSWORD
   ```

2. Download statements:
   ```bash
   # Check matching emails without saving files
   cards fetch --dry-run

   # Download statements from the past 60 days
   cards fetch

   # Download and immediately run the ingest pipeline
   cards fetch --ingest
   # OR
   cards ingest --fetch
   ```

Rules in `config/email_rules.csv` automatically match incoming emails and attachment filenames to their respective `card_id`. Any unrecognized statement attachment is saved into `statements/_unmatched/` so nothing is missed.

## Tests

```bash
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
```

Every push runs the same checks on GitHub Actions, on Windows, installing from
`requirements.lock`. It also runs `tools/check_secrets.py --all`, which fails if a
statement, an output or a card-shaped number is in the repository. The pre-commit hook
in `.githooks/` runs the same check on every commit, and also looks for your stored
passwords. `tools/install.ps1` turns the hook on.

Issuer parser tests run against redacted text fixtures in `tests/fixtures/`, and email fetch tests mock IMAP responses, so the test suite requires no network access or live passwords.

## Status

The pipeline, email statement downloader, and issuer parsers for HDFC, ICICI, and SBI are implemented and tested.
