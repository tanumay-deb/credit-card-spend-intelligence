import base64

from creditcard.alerts import MERCHANT, PROBLEM, STATEMENT, Item
from creditcard.notify import OPEN_SHORTCUT, REFRESHED, Message, compose, send_email, show_toast


def _s(text):
    return Item(f"statement:{text}", STATEMENT, text)


def _p(text):
    return Item(f"problem:{text}", PROBLEM, text)


def _m(name):
    return Item(f"merchant:{name}", MERCHANT, "A merchant needs a category.")


def test_one_statement_after_a_refresh():
    msg = compose([_s("SBI Elite: September statement imported, due 4 Oct 2026.")], "refreshed")
    assert msg.subject == "Credit cards: 1 new statement"
    assert msg.summary == "1 new statement"
    assert "New statements\n- SBI Elite: September statement imported, due 4 Oct 2026." in msg.body
    assert REFRESHED in msg.body
    assert "Needs attention" not in msg.body
    assert msg.body.rstrip().endswith("Details: output/logs/daily_run.log")


def test_without_a_refresh_it_points_to_the_shortcut():
    msg = compose([_s("a")], "not_open")
    assert OPEN_SHORTCUT in msg.body
    assert REFRESHED not in msg.body


def test_merchants_are_counted_on_one_line_and_never_named():
    items = [_s("a"), _s("b"), _p("Millennia: no October statement yet."),
             _m("SECRET ONE"), _m("SECRET TWO"), _m("SECRET THREE")]
    msg = compose(items, None)
    assert msg.subject == "Credit cards: 2 new statements, 2 need attention"
    assert "- 3 new merchants need a category: see output/review_queue.csv." in msg.body
    assert "SECRET" not in msg.body + msg.subject


def test_problems_alone_carry_no_power_bi_line():
    msg = compose([_p("x")], "refreshed")
    assert msg.subject == "Credit cards: 1 needs attention"
    assert REFRESHED not in msg.body and OPEN_SHORTCUT not in msg.body


def test_one_new_merchant_reads_naturally():
    assert "- 1 new merchant needs a category" in compose([_m("X")], None).body


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.login_as = (user, password)

    def send_message(self, message):
        FakeSMTP.sent.append((self.host, self.port, self.login_as, message))


def test_email_goes_from_and_to_the_gmail_account():
    FakeSMTP.sent = []
    env = {"EMAIL_USERNAME": "me@gmail.com", "EMAIL_PASSWORD": "app-pw"}
    send_email(Message("Credit cards: 1 new statement", "body text\n", "1 new statement"),
               env=env, smtp_factory=FakeSMTP)
    host, port, login_as, message = FakeSMTP.sent[0]
    assert (host, port, login_as) == ("smtp.gmail.com", 465, ("me@gmail.com", "app-pw"))
    assert message["From"] == message["To"] == "me@gmail.com"
    assert message["Subject"] == "Credit cards: 1 new statement"
    assert message.get_content() == "body text\n"


def test_smtp_server_can_be_overridden():
    FakeSMTP.sent = []
    env = {"EMAIL_USERNAME": "me@x.in", "EMAIL_PASSWORD": "p",
           "EMAIL_SMTP_SERVER": "smtp.x.in", "EMAIL_SMTP_PORT": "2465"}
    send_email(Message("s", "b", "s"), env=env, smtp_factory=FakeSMTP)
    assert FakeSMTP.sent[0][:2] == ("smtp.x.in", 2465)


def test_toast_runs_hidden_windows_powershell_with_escaped_text():
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))

    show_toast("Credit cards", ["1 needs attention", "Tata & Co's <card>"], runner=runner)
    args, kwargs = calls[0]
    assert args[0].lower().endswith(r"windowspowershell\v1.0\powershell.exe")
    script = base64.b64decode(args[args.index("-EncodedCommand") + 1]).decode("utf-16-le")
    # XML-escaped, then quotes doubled for the PowerShell single-quoted string.
    assert "<text>Tata &amp; Co''s &lt;card&gt;</text>" in script
    assert "<text>Credit cards</text>" in script
    assert kwargs["check"] is True
    assert "creationflags" in kwargs


def test_a_multi_line_item_is_indented_under_its_bullet():
    msg = compose([_s("SBI Elite: September statement imported.\nTotal due ₹1,000.")], None)
    assert "- SBI Elite: September statement imported.\n  Total due ₹1,000." in msg.body
