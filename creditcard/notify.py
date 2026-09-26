"""The message the daily check sends, and the two ways it goes out: an email
from the user's Gmail to itself, and a Windows notification on this PC."""
import base64
import os
import smtplib
import subprocess
from dataclasses import dataclass
from email.message import EmailMessage
from xml.sax.saxutils import escape

from creditcard.alerts import MERCHANT, PROBLEM, STATEMENT, Item
from creditcard.powershell import BASE_ARGS, NO_WINDOW

TITLE = "Credit cards"
REFRESHED = "Power BI was open and has been refreshed."
OPEN_SHORTCUT = ("Open the dashboard with the Credit Card Dashboard shortcut; "
                 "it refreshes when it opens.")
LOG_POINTER = "Details: output/logs/daily_run.log"


@dataclass(frozen=True)
class Message:
    subject: str
    body: str
    summary: str  # the counts, e.g. "1 new statement, 2 need attention"


def compose(items: list[Item], refresh: str | None) -> Message:
    """One message for everything new. Merchants are counted, never named."""
    statements = [i.text for i in items if i.kind == STATEMENT]
    attention = [i.text for i in items if i.kind == PROBLEM]
    merchants = sum(1 for i in items if i.kind == MERCHANT)
    if merchants:
        attention.append(f"{_count(merchants, 'new merchant')} {_verb(merchants)} a category: "
                         "see output/review_queue.csv.\n"
                         "Add each to config/merchant_map.csv with a category, or ask Claude.")

    counts = []
    if statements:
        counts.append(_count(len(statements), "new statement"))
    if attention:
        counts.append(f"{len(attention)} {_verb(len(attention))} attention")
    summary = ", ".join(counts)

    sections = []
    if statements:
        power_bi = REFRESHED if refresh == "refreshed" else OPEN_SHORTCUT
        sections.append("New statements\n" + _bullets(statements) + "\n\n" + power_bi)
    if attention:
        sections.append("Needs attention\n" + _bullets(attention))
    sections.append(LOG_POINTER)
    return Message(subject=f"{TITLE}: {summary}", body="\n\n".join(sections) + "\n",
                   summary=summary)


def send_email(message: Message, env=None, smtp_factory=smtplib.SMTP_SSL) -> None:
    """Send from and to EMAIL_USERNAME, with the app password the fetcher uses."""
    env = os.environ if env is None else env
    user = env["EMAIL_USERNAME"]
    email = EmailMessage()
    email["From"] = user
    email["To"] = user
    email["Subject"] = message.subject
    email.set_content(message.body)
    host = env.get("EMAIL_SMTP_SERVER") or "smtp.gmail.com"
    port = int(env.get("EMAIL_SMTP_PORT") or 465)
    with smtp_factory(host, port, timeout=60) as smtp:
        smtp.login(user, env["EMAIL_PASSWORD"])
        smtp.send_message(email)


# Windows PowerShell's own app id: a notification must name a registered app,
# and this one exists on every PC, so nothing has to be installed.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
_TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('__XML__')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APP_ID__').Show($toast)
"""  # noqa: E501 - PowerShell's WinRT type literals can't be wrapped


def show_toast(title: str, lines, runner=subprocess.run) -> None:
    """Show a Windows notification. Raises if PowerShell reports a failure."""
    texts = "".join(f"<text>{escape(text)}</text>" for text in (title, *lines))
    xml = f'<toast><visual><binding template="ToastGeneric">{texts}</binding></visual></toast>'
    # Inside a PowerShell single-quoted string, a quote is written twice.
    script = _TOAST_SCRIPT.replace("__XML__", xml.replace("'", "''")).replace("__APP_ID__", _APP_ID)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    runner([*BASE_ARGS, "-EncodedCommand", encoded], check=True, capture_output=True,
           timeout=60, creationflags=NO_WINDOW)


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _verb(n: int) -> str:
    return "needs" if n == 1 else "need"


def _bullets(lines: list[str]) -> str:
    """One bullet per item; an item's later lines are indented under it."""
    return "\n".join("- " + line.replace("\n", "\n  ") for line in lines)
