"""Guards on tools/redact_statement.py.

Two properties matter, and they pull in opposite directions:

*Privacy* -- the redacted copy must not let anyone reconstruct the real
statement, including someone holding this repository and therefore the
redactor's own source.

*Fidelity* -- the redacted copy must still behave like the statement it
stands in for. Column positions and line structure are preserved by
construction, but a statement also carries **internal arithmetic**: its
printed "purchases" figure is the sum of its own debit rows. A fixture that
loses that stops being able to exercise reconciliation, which is the check
that catches a parser silently dropping rows.

The samples below are invented, not redacted captures of anyone's
statement, so they can assert on exact values in both directions.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.statement_shapes import SHAPES  # noqa: E402
from tools.redact_statement import ReconciliationError, redact_text  # noqa: E402

# ---------------------------------------------------------------------------
# Invented samples, one per issuer layout. Each is internally consistent:
# its debit rows sum to its printed purchases figure and its credit rows to
# its printed payments figure, exactly as a real statement's do.
# ---------------------------------------------------------------------------
HDFC_SAMPLE = """\
Regalia HDFC Bank Credit Card Statement
HSN Code: 997114 HDFC Bank Credit Cards GSTIN: 27AAACH2702H1ZL
JOHN SMITH Credit Card No. 528512XXXXXX1234
12 EXAMPLE ROAD, BLOCK C Alternate Account Number 123456789012345678
SOMEWHERE 700001
Statement Date 02 Apr, 2026
Email john.smith@example.org
Billing Period 02 Mar, 2026 to 01 Apr, 2026
PAYMENTS/CREDITS PURCHASES/DEBIT
PREVIOUS STATEMENT DUES FINANCE CHARGES TOTAL AMOUNT DUE
RECEIVED (Current Billing Cycle)
_ C12,345.67
C10,000.00 C5,000.00 + C7,345.67 + C0.00 =
TOTAL CREDIT LIMIT
(Including Cash) AVAILABLE CREDIT LIMIT AVAILABLE CASH LIMIT MINIMUM DUE DUE DATE
C1,234.00 18 Apr, 2026
C5,00,000 C4,00,000 C50,000
DATE & TIME TRANSACTION DESCRIPTION AMOUNT PI
JOHN SMITH
05/03/2026| 10:11 COFFEE SHOP BANGALORE C 345.67 l
07/03/2026| 12:00 GROCERY STORE PUNE C 7,000.00 l
20/03/2026| 09:30 BPPY CC PAYMENT DP1234abcd + C 5,000.00 l
"""

ICICI_SAMPLE = """\
MR JOHN SMITH
12 EXAMPLE ROAD
SOMEWHERE 700001
STATEMENT SUMMARY
Total Amount due
`15,000.00
Previous Balance Purchases / Charges Cash Advances Payments / Credits
`10,000.00 `8,000.00 `0.00 `3,000.00
Date SerNo. Transaction Details Reward Points Intl.# amount Amount (in`)
01/03/2026 12345678901 AMAZON PAY IN E COMMERC BANGALORE IN 10 5,000.00
02/03/2026 12345678902 GOOGLE PLAY CONTENT PU MUMBAI IN 5 3,000.00
03/03/2026 12345678903 BBPS Payment received 0 3,000.00 CR
ICICI Bank Credit Card GST Number: 27AAACI1195H1ZK
"""

SBI_SAMPLE = """\
GSTIN of SBI Card : 29AAECS1234K1ZV Stmt/Debit Note/Credit Note/Tax Invoice (ORIGINAL FOR RECIPIENT)
JOHN SMITH Credit Card Number
XXXX XXXX XXXX XX12
ACCOUNT SUMMARY
Additions
Payments,
Previous Balance Reversals & other Purchases & Other Fee, Taxes & Total Outstanding
( ` ) Credits ( ` ) Debits ( ` ) Interest Charges( ` ) ( ` )
2,000.00 1,500.00 900.00 0.00 1,400.00
Date Transaction Details Amount ( ` )
05 Aug 25 PAYMENT RECEIVED 123456789 1,500.00 C
TRANSACTIONS FOR JOHN SMITH
07 Aug 25 NETFLIX MUMBAI MAH 900.00 D
"""

SAMPLES = [HDFC_SAMPLE, ICICI_SAMPLE, SBI_SAMPLE]


CASES = [
    pytest.param(HDFC_SAMPLE, "hdfc", id="hdfc"),
    pytest.param(ICICI_SAMPLE, "icici", id="icici"),
    pytest.param(SBI_SAMPLE, "sbi", id="sbi"),
]


# ---------------------------------------------------------------------------
# Fidelity: the redacted copy keeps the statement's own arithmetic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sample,issuer", CASES)
def test_debit_rows_sum_to_the_redacted_purchases_figure(sample, issuer):
    """The property the whole exercise exists for.

    A parser that drops a row must be catchable by comparing what it parsed
    against the statement's own printed total, so that total has to stay
    true of the redacted rows -- not of the real ones it no longer describes.
    """
    read_rows, read_totals = SHAPES[issuer]
    redacted = redact_text(sample)
    assert read_rows(redacted).debit_total == read_totals(redacted).purchases


@pytest.mark.parametrize("sample,issuer", CASES)
def test_credit_rows_sum_to_the_redacted_payments_figure(sample, issuer):
    read_rows, read_totals = SHAPES[issuer]
    redacted = redact_text(sample)
    assert read_rows(redacted).credit_total == read_totals(redacted).payments


@pytest.mark.parametrize("sample,issuer", CASES)
def test_reconciled_amounts_are_not_the_real_ones(sample, issuer):
    """Reconciling must not be achieved by leaving the real figures in place."""
    _, read_totals = SHAPES[issuer]
    redacted = redact_text(sample)
    assert read_totals(redacted).purchases != read_totals(sample).purchases


@pytest.mark.parametrize("sample,issuer", CASES)
def test_every_row_is_still_found_after_redaction(sample, issuer):
    """Substitution must not create or destroy transaction rows."""
    read_rows, _ = SHAPES[issuer]
    redacted = redact_text(sample)
    before, after = read_rows(sample), read_rows(redacted)
    assert (len(before.debits), len(before.credits)) == (len(after.debits), len(after.credits))


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sample", SAMPLES, ids=["hdfc", "icici", "sbi"])
def test_two_runs_of_the_same_input_differ(sample):
    """No key is baked into the source.

    A substitution driven by a constant in this repository is reversible by
    anyone holding the repository: the fixture and the tool that produced it
    are committed side by side. Different output each run is the observable
    consequence of the key being drawn fresh and never recorded.
    """
    assert redact_text(sample) != redact_text(sample)


@pytest.mark.parametrize("sample,marker", [
    (HDFC_SAMPLE, "Credit Card No."),
    (ICICI_SAMPLE, "ICICI Bank Credit Card GST Number"),
    (SBI_SAMPLE, "GSTIN of SBI Card"),
])
def test_no_digit_survives_in_place_outside_the_reconciled_amounts(sample, marker):
    """The substitution pass has no fixed points.

    Checked on an identity-bearing line that carries no reconciling amount --
    a card, account or registration number. Those digits are pure
    substitution, so every one of them must differ from the original.
    """
    redacted = redact_text(sample)
    start = sample.index(marker)
    line_start = sample.rfind("\n", 0, start) + 1
    line_end = sample.index("\n", start)
    pairs = [(a, b) for a, b in zip(sample[line_start:line_end],
                                    redacted[line_start:line_end], strict=True) if a.isdigit()]
    assert pairs, f"no digits on the {marker!r} line to check"
    assert all(a != b for a, b in pairs)


@pytest.mark.parametrize("sample", SAMPLES, ids=["hdfc", "icici", "sbi"])
def test_structure_is_preserved_exactly(sample):
    redacted = redact_text(sample)
    assert len(redacted) == len(sample)
    assert [len(line) for line in redacted.splitlines()] == [len(line) for line in sample.splitlines()]
    for a, b in zip(sample, redacted, strict=True):
        assert a.isdigit() == b.isdigit()


def test_personal_name_does_not_survive():
    for sample in (HDFC_SAMPLE, ICICI_SAMPLE, SBI_SAMPLE):
        assert "SMITH" not in redact_text(sample).upper()


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------
def test_a_recognised_issuer_missing_its_summary_block_is_an_error():
    """Silently skipping the arithmetic pass would ship a fixture that cannot
    reconcile -- exactly the failure this tool is being changed to prevent --
    so a layout that no longer matches has to stop the run."""
    broken = HDFC_SAMPLE.replace("PAYMENTS/CREDITS PURCHASES/DEBIT", "SUMMARY")
    with pytest.raises(ReconciliationError):
        redact_text(broken)


def test_text_from_no_recognised_issuer_is_still_redacted():
    """The digit/name passes are issuer-independent and must still run."""
    plain = "Some other bank\nAccount 123456789\nBalance 1,234.56\n"
    out = redact_text(plain)
    assert len(out) == len(plain)
    assert out != plain
