"""Domain models. Money is always Decimal; dates are always date."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Card:
    card_id: str
    issuer: str
    card_name: str
    last4: str
    parser: str
    password_env: str
    credit_limit: Decimal
    statement_day: int
    # Cards that share one pooled credit limit carry the same group, so the
    # dashboard counts that limit once instead of once per card. Defaults to
    # the card's own id, i.e. every card independent.
    limit_group: str = ""
    # False for a card whose issuer sends a statement only in months the
    # card is used, so a month without one is neither missing nor late.
    monthly: bool = True
    # A card you no longer hold: its statements stay in the history, but it
    # sends no more, and the dashboard doesn't count it as a card you have.
    closed: bool = False

    @property
    def limit_pool(self) -> str:
        return self.limit_group or self.card_id


@dataclass(frozen=True)
class Rule:
    priority: int
    pattern: str
    subcategory: str


@dataclass(frozen=True)
class Transaction:
    txn_id: str
    card_id: str
    statement_id: str
    txn_date: date
    posting_date: date | None
    merchant_raw: str
    merchant_clean: str
    amount: Decimal
    direction: str        # "debit" | "credit"
    txn_type: str         # purchase|payment|refund|fee|interest|cashback|emi|payment_reversal
    category_group: str
    subcategory: str
    category_source: str  # "map" | "rule" | "type" | "upi" | "refund" | "unmatched"
    is_forex: bool
    forex_ccy: str | None
    forex_amount: Decimal | None
    source_file: str
    # What the row adds to spend as billed: + for a charge, - for a refund, None
    # for payments, cashback and returned autopays. Set by creditcard.spend.
    spend_amount: Decimal | None = None


@dataclass(frozen=True)
class StatementSummary:
    statement_id: str
    card_id: str
    statement_date: date
    due_date: date
    period_start: date
    period_end: date
    previous_balance: Decimal
    payments: Decimal
    purchases: Decimal
    total_due: Decimal
    min_due: Decimal
    finance_charges: Decimal
    late_fee: Decimal
    credit_limit: Decimal
    available_limit: Decimal
    source_file: str


@dataclass(frozen=True)
class EmiPlan:
    """One instalment plan as the statement reports it.

    Issuers print different subsets: HDFC gives an interest rate and the
    interest still payable but no end date; ICICI gives a finish date and the
    instalment amount but no rate. The optional fields are genuinely absent,
    not unparsed, so they stay None rather than being guessed at.
    """
    card_id: str
    statement_id: str
    loan_ref: str
    loan_type: str
    start_date: date
    end_date: date | None
    principal: Decimal
    tenure_months: int
    remaining_months: int
    interest_rate: Decimal | None
    instalment_amount: Decimal | None
    interest_payable: Decimal | None
    outstanding: Decimal
    source_file: str
    # "active" while the plan is still listed on its card's latest statement;
    # "closed" once a newer statement stops listing it. Set by the pipeline,
    # the only place that sees every statement at once.
    status: str = "active"


@dataclass(frozen=True)
class ParseResult:
    """What a parser returns for one statement PDF."""
    statement: StatementSummary
    transactions: list[Transaction]
    emi_plans: tuple[EmiPlan, ...] = ()


@dataclass(frozen=True)
class IngestLogRow:
    source_file: str
    file_hash: str
    parser: str
    run_timestamp: str
    rows_found: int
    statement_purchases: Decimal | None
    sum_check_delta: Decimal | None
    status: str   # "ok" | "warning" | "failed"
    error: str


@dataclass(frozen=True)
class Payment:
    card_id: str
    payment_date: date
    amount: Decimal
    note: str
