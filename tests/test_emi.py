from datetime import date
from decimal import Decimal

from creditcard.emi import latest_emi_snapshots
from creditcard.models import EmiPlan, StatementSummary


def _stmt(card: str, when: date) -> StatementSummary:
    zero = Decimal("0")
    return StatementSummary(
        statement_id=f"{card}_{when.isoformat()}", card_id=card, statement_date=when,
        due_date=when, period_start=when, period_end=when, previous_balance=zero,
        payments=zero, purchases=zero, total_due=zero, min_due=zero,
        finance_charges=zero, late_fee=zero, credit_limit=zero,
        available_limit=zero, source_file="f.pdf",
    )


def _plan(card: str, ref: str, when: date, remaining: int, outstanding: str,
          end: date | None = None) -> EmiPlan:
    return EmiPlan(
        card_id=card, statement_id=f"{card}_{when.isoformat()}", loan_ref=ref,
        loan_type="Smart EMI", start_date=date(2025, 9, 1), end_date=end,
        principal=Decimal("9000"), tenure_months=9, remaining_months=remaining,
        interest_rate=Decimal("15"), instalment_amount=None,
        interest_payable=Decimal("100"), outstanding=Decimal(outstanding),
        source_file="f.pdf",
    )


JUL, AUG, SEP = date(2026, 7, 2), date(2026, 8, 2), date(2026, 9, 2)
STATEMENTS = [_stmt("hdfc", JUL), _stmt("hdfc", AUG), _stmt("hdfc", SEP)]


def test_one_loan_across_many_statements_collapses_to_one_row():
    """The reported bug: a loan appeared once per monthly statement."""
    plans = [_plan("hdfc", "L1", d, r, o)
             for d, r, o in [(JUL, 5, "5000"), (AUG, 4, "4000"), (SEP, 3, "3000")]]
    assert len(latest_emi_snapshots(plans, STATEMENTS)) == 1


def test_keeps_the_most_recent_snapshot():
    plans = [_plan("hdfc", "L1", SEP, 3, "3000"), _plan("hdfc", "L1", JUL, 5, "5000")]
    (plan,) = latest_emi_snapshots(plans, STATEMENTS)
    assert plan.remaining_months == 3
    assert plan.outstanding == Decimal("3000")


def test_loan_on_the_latest_statement_is_active():
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 3, "3000")], STATEMENTS)
    assert plan.status == "active"


def test_loan_missing_from_the_latest_statement_is_closed():
    """Issuers stop listing a plan once it is paid off."""
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", JUL, 1, "500")], STATEMENTS)
    assert plan.status == "closed"


def test_closed_loan_has_nothing_left_to_pay():
    """The reported bug: a finished loan still showed the one month and the
    balance from the last statement that listed it."""
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", JUL, 1, "500")], STATEMENTS)
    assert plan.remaining_months == 0
    assert plan.outstanding == Decimal("0")
    assert plan.interest_payable == Decimal("0")


def test_loan_past_its_end_date_is_closed_whatever_its_count_said():
    statements = STATEMENTS + [_stmt("icici", JUL), _stmt("icici", SEP)]
    (plan,) = latest_emi_snapshots(
        [_plan("icici", "M1", JUL, 3, "900", end=date(2026, 8, 15))], statements)
    assert plan.status == "closed"
    assert plan.outstanding == Decimal("0")


def test_loan_that_vanishes_with_instalments_still_due_is_closed_early():
    """Prepaid, or its row was not read. Neither is a normal close, so the
    last known balance stays visible instead of being zeroed away."""
    from dataclasses import replace
    # Started recently enough that its last instalment is still months away.
    running = replace(_plan("hdfc", "L1", JUL, 4, "4000"), start_date=date(2026, 3, 1))
    (plan,) = latest_emi_snapshots([running], STATEMENTS)
    assert plan.status == "closed early"
    assert plan.remaining_months == 4
    assert plan.outstanding == Decimal("4000")


def test_different_loans_on_one_card_are_kept_apart():
    plans = [_plan("hdfc", "L1", SEP, 3, "3000"), _plan("hdfc", "L2", SEP, 6, "9000")]
    assert len(latest_emi_snapshots(plans, STATEMENTS)) == 2


def test_same_reference_on_different_cards_is_two_loans():
    statements = STATEMENTS + [_stmt("sbi", SEP)]
    plans = [_plan("hdfc", "L1", SEP, 3, "3000"), _plan("sbi", "L1", SEP, 3, "3000")]
    assert len(latest_emi_snapshots(plans, statements)) == 2


def test_latest_is_judged_per_card():
    """A card whose newest statement is older than another card's is not
    thereby closed -- each card is compared with its own latest statement."""
    statements = STATEMENTS + [_stmt("icici", JUL)]
    plans = [_plan("icici", "M1", JUL, 2, "800")]
    (plan,) = latest_emi_snapshots(plans, statements)
    assert plan.status == "active"


def test_loan_whose_final_instalment_is_on_the_latest_statement_is_closed():
    """The reported bug: HDFC lists a loan once more in the month its last
    instalment is billed, with nothing left to pay, and it read as active."""
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 0, "0")], STATEMENTS)
    assert plan.status == "closed"


def test_loan_in_its_final_month_with_a_balance_left_is_active():
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 0, "850")], STATEMENTS)
    assert plan.status == "active"


def test_missing_end_date_is_the_month_of_the_last_instalment():
    """HDFC prints no end date. Every end date ICICI prints is the start date
    plus the tenure less one month, so the same rule fills HDFC's."""
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 3, "3000")], STATEMENTS)
    assert plan.end_date == date(2026, 5, 1)


def test_printed_end_date_is_kept():
    (plan,) = latest_emi_snapshots(
        [_plan("hdfc", "L1", SEP, 3, "3000", end=date(2026, 6, 15))], STATEMENTS)
    assert plan.end_date == date(2026, 6, 15)


def test_missing_instalment_repays_the_loan_at_the_printed_rate():
    """HDFC prints the rate but no instalment. Paying the filled-in amount
    monthly at that rate must clear the principal in exactly the tenure."""
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 3, "3000")], STATEMENTS)
    balance, monthly_rate = Decimal("9000"), Decimal("15") / 1200
    for _ in range(9):
        balance = balance * (1 + monthly_rate) - plan.instalment_amount
    assert abs(balance) < Decimal("0.10")


def test_missing_rate_is_the_one_the_instalment_implies():
    """ICICI prints the instalment but no rate."""
    from dataclasses import replace
    icici = replace(_plan("icici", "M1", SEP, 3, "3000"),
                    interest_rate=None, instalment_amount=Decimal("1063.53"))
    (plan,) = latest_emi_snapshots([icici], STATEMENTS + [_stmt("icici", SEP)])
    assert abs(plan.interest_rate - Decimal("15")) <= Decimal("0.01")


def test_instalments_that_only_repay_the_principal_imply_no_interest():
    from dataclasses import replace
    icici = replace(_plan("icici", "M1", SEP, 3, "3000"),
                    interest_rate=None, instalment_amount=Decimal("1000"))
    (plan,) = latest_emi_snapshots([icici], STATEMENTS + [_stmt("icici", SEP)])
    assert plan.interest_rate == Decimal("0")


def _unsplit(remaining: int, rate: str, instalment: str, outstanding: str) -> EmiPlan:
    """How ICICI prints a plan: the total still to pay as outstanding, no interest."""
    from dataclasses import replace

    return replace(_plan("hdfc", "L9", SEP, remaining, outstanding), interest_payable=None,
                   interest_rate=Decimal(rate), instalment_amount=Decimal(instalment))


def test_unprinted_interest_is_split_out_of_the_total_still_to_pay():
    """The dashboard showed (Blank) interest, and an outstanding that already
    held it. Principal left is the instalments' present value at the rate."""
    (plan,) = latest_emi_snapshots([_unsplit(3, "15.99", "1000", "3000")], STATEMENTS)
    monthly = 15.99 / 1200
    principal = Decimal(repr(1000 * (1 - (1 + monthly) ** -3) / monthly)).quantize(Decimal("0.01"))
    assert plan.outstanding == principal
    assert plan.interest_payable == Decimal("3000") - principal
    assert plan.interest_payable > 0


def test_a_no_cost_emi_has_no_interest_to_split_out():
    (plan,) = latest_emi_snapshots([_unsplit(3, "0", "1000", "3000")], STATEMENTS)
    assert (plan.outstanding, plan.interest_payable) == (Decimal("3000"), Decimal("0"))


def test_printed_interest_and_outstanding_are_kept():
    (plan,) = latest_emi_snapshots([_plan("hdfc", "L1", SEP, 3, "3000")], STATEMENTS)
    assert (plan.outstanding, plan.interest_payable) == (Decimal("3000"), Decimal("100"))
