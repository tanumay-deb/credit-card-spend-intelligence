"""Collapse per-statement EMI snapshots into one row per loan.

Every monthly statement re-lists every instalment plan still running, so a
year of statements reports the same nine-month loan nine times. The dashboard
wants the loan, not its monthly echo.

Each loan keeps its most recent snapshot -- that is the one with the current
months-remaining and outstanding balance. Issuers stop listing a plan once it is
paid off, so a loan's status comes from its card's latest statement:

* active -- on the latest statement with something still to pay.
* closed -- finished. Either it is on the latest statement with nothing left
  (HDFC lists a loan once more in the month its last instalment is billed), or
  it dropped off after its final instalment or its end date. A loan that
  dropped off was last seen while still running, so its months left,
  outstanding and interest still payable are zeroed instead of echoing that old
  snapshot.
* closed early -- it vanished with instalments still to go: a prepayment, or a
  statement whose EMI table was not read. The figures are not zeroed, so a
  balance that may still be owed stays in view.

Each issuer also leaves something off, and it is filled from what it does
print. HDFC gives the rate but no instalment or end date; ICICI gives the
instalment and end date but no rate. The end date is the month of the last
instalment -- the start plus the tenure less one month, which is exactly what
ICICI prints on every plan. The instalment is the standard reducing-balance EMI
at HDFC's rate, which agrees with HDFC's own outstanding-plus-interest figures.
ICICI's rate is the one its instalments imply.
"""
import calendar
from dataclasses import replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from creditcard.models import EmiPlan, StatementSummary

_CENTS = Decimal("0.01")


def latest_emi_snapshots(
    plans: list[EmiPlan], statements: list[StatementSummary]
) -> list[EmiPlan]:
    """One EmiPlan per (card, loan), newest snapshot, completed, with status set."""
    statement_date = {s.statement_id: s.statement_date for s in statements}

    latest_for_card: dict[str, date] = {}
    for s in statements:
        if s.statement_date > latest_for_card.get(s.card_id, date.min):
            latest_for_card[s.card_id] = s.statement_date

    def snapshot_date(plan: EmiPlan) -> date:
        return statement_date.get(plan.statement_id, date.min)

    # Oldest first, so each later snapshot of the same loan overwrites the
    # earlier one and the dict ends holding the newest.
    newest: dict[tuple[str, str], EmiPlan] = {}
    for plan in sorted(plans, key=snapshot_date):
        newest[(plan.card_id, plan.loan_ref)] = plan

    collapsed = [
        _with_status(_completed(plan), snapshot_date(plan), latest_for_card.get(plan.card_id))
        for plan in newest.values()
    ]
    return sorted(collapsed, key=lambda p: (p.card_id, p.start_date, p.loan_ref))


def _with_status(plan: EmiPlan, seen_on: date, latest: date | None) -> EmiPlan:
    zero = Decimal("0")
    if seen_on == latest:
        nothing_left = plan.remaining_months == 0 and plan.outstanding == zero
        return replace(plan, status="closed" if nothing_left else "active")

    ran_its_course = plan.remaining_months <= 1 or (
        plan.end_date is not None and latest is not None and plan.end_date <= latest
    )
    if not ran_its_course:
        return replace(plan, status="closed early")

    return replace(
        plan,
        status="closed",
        remaining_months=0,
        outstanding=zero,
        interest_payable=None if plan.interest_payable is None else zero,
    )


def _completed(plan: EmiPlan) -> EmiPlan:
    """Fill the fields an issuer leaves off from the ones it prints."""
    if plan.tenure_months <= 0:
        return plan
    fills: dict[str, Any] = {}
    if plan.end_date is None:
        fills["end_date"] = add_months(plan.start_date, plan.tenure_months - 1)
    if plan.instalment_amount is None and plan.interest_rate is not None:
        fills["instalment_amount"] = _instalment(
            plan.principal, plan.interest_rate, plan.tenure_months)
    if plan.interest_rate is None and plan.instalment_amount is not None:
        fills["interest_rate"] = _implied_rate(
            plan.principal, plan.instalment_amount, plan.tenure_months)
    if plan.interest_payable is None:
        fills.update(_split_total(
            plan.outstanding,
            fills.get("instalment_amount", plan.instalment_amount),
            fills.get("interest_rate", plan.interest_rate),
            plan.remaining_months,
        ))
    return replace(plan, **fills)


def _split_total(total: Decimal, instalment: Decimal | None, annual_rate: Decimal | None,
                 months: int) -> dict[str, Decimal]:
    """Split an outstanding that includes the interest still to come.

    An issuer that prints no interest payable (ICICI) prints, as outstanding,
    the total of the remaining instalments. HDFC prints the principal still owed
    and the interest payable separately, and the dashboard adds them up that way.
    So this splits the total the same way: the principal still owed is the
    remaining instalments' present value at the plan's rate, and the rest is
    interest.
    """
    if months <= 0:
        return {"interest_payable": Decimal("0")}
    if instalment is None or annual_rate is None:
        return {}
    monthly_rate = float(annual_rate) / 1200
    value = float(instalment) * months if monthly_rate == 0 else (
        float(instalment) * (1 - (1 + monthly_rate) ** -months) / monthly_rate)
    principal_left = min(Decimal(repr(value)).quantize(_CENTS, ROUND_HALF_UP), total)
    return {"outstanding": principal_left, "interest_payable": total - principal_left}


def add_months(day: date, months: int) -> date:
    year, month = divmod(day.month - 1 + months, 12)
    year, month = day.year + year, month + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _payment(principal: float, monthly_rate: float, months: int) -> float:
    """Reducing-balance instalment that repays `principal` in `months`."""
    if monthly_rate == 0:
        return principal / months
    return principal * monthly_rate / (1 - (1 + monthly_rate) ** -months)


def _instalment(principal: Decimal, annual_rate: Decimal, months: int) -> Decimal:
    value = _payment(float(principal), float(annual_rate) / 1200, months)
    return Decimal(repr(value)).quantize(_CENTS, ROUND_HALF_UP)


def _implied_rate(principal: Decimal, instalment: Decimal, months: int) -> Decimal:
    """Annual rate at which `months` payments of `instalment` repay the loan.

    The payment rises steadily with the rate, so halving the interval converges
    on it. Instalments that only return the principal imply no interest.
    """
    p, e = float(principal), float(instalment)
    if e * months <= p:
        return Decimal("0")
    low, high = 0.0, 1.0  # monthly rates; 100% a month is far past any card EMI
    for _ in range(100):
        mid = (low + high) / 2
        if _payment(p, mid, months) < e:
            low = mid
        else:
            high = mid
    return Decimal(repr(low * 1200)).quantize(_CENTS, ROUND_HALF_UP)
