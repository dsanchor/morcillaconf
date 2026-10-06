from __future__ import annotations

from datetime import UTC, datetime

from restaurant_contracts.cashier import (
    Bill,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    PaymentChoice,
    PaymentMethod,
    Receipt,
    ReviewDecision,
)

from cashier_agent.evidence import parse
from cashier_agent.ledger import Ledger
from cashier_agent.pricing import price_bill

from carta_fixtures import CARTA, SERVED, TOTAL

AT = datetime(2026, 10, 6, 21, 30, tzinfo=UTC)


def ledger() -> Ledger:
    references = iter(f"pay_{number}" for number in range(1, 10))
    return Ledger(clock=lambda: AT, reference=lambda: next(references))


def bill(stage: BillStage = BillStage.AWAITING_PAYMENT) -> Bill:
    priced = price_bill(SERVED, parse([CARTA]), stage=stage)
    assert isinstance(priced, Bill)
    return priced


def choice(method: PaymentMethod = PaymentMethod.CARD, key: str = "evt_1", version: int = 1) -> PaymentChoice:
    return PaymentChoice(bill_id="bill_1", version=version, method=method, idempotency_key=key)


def test_a_payment_is_recorded_once_and_repeating_it_returns_the_same_receipt() -> None:
    books = ledger()
    books.open(bill(), "task_1")

    first = books.pay(choice())
    assert isinstance(first, Receipt)
    assert (first.method, first.amount, first.reference, first.paid_at) == (
        PaymentMethod.CARD, TOTAL, "pay_1", AT,
    )
    assert books.pay(choice()) == first
    assert books.pay(choice(PaymentMethod.CASH, key="evt_2")) == first
    assert books.charges == 1


def test_a_payment_needs_the_presented_version_and_an_offered_method() -> None:
    books = ledger()
    books.open(bill(), "task_1")

    stale = books.pay(choice(version=2))
    assert isinstance(stale, CashierFailure) and stale.code is CashierFailureCode.NOT_PAYABLE
    assert books.charges == 0
    assert isinstance(books.pay(choice()), Receipt)


def test_an_unknown_or_cancelled_bill_is_never_charged() -> None:
    books = ledger()
    assert books.pay(choice()).code is CashierFailureCode.BILL_NOT_FOUND
    books.open(bill(), "task_1")
    books.close_task("task_1")
    assert books.pay(choice()).code is CashierFailureCode.BILL_NOT_FOUND
    assert books.charges == 0


def test_a_paid_bill_keeps_its_receipt_after_its_task_closes() -> None:
    books = ledger()
    books.open(bill(), "task_1")
    paid = books.pay(choice())
    books.close_task("task_1")
    assert books.pay(choice(key="evt_9")) == paid


def test_with_review_the_bill_is_paid_only_after_a_person_approves_it() -> None:
    books = ledger()
    books.open(bill(BillStage.AWAITING_REVIEW), "task_1")

    assert books.pay(choice()).code is CashierFailureCode.NOT_PAYABLE
    approved = books.review(
        ReviewDecision(bill_id="bill_1", version=1, decision="approved", reviewer="caja")
    )
    assert isinstance(approved, Bill)
    assert approved.stage is BillStage.AWAITING_PAYMENT
    assert approved.payment_options == [PaymentMethod.CARD, PaymentMethod.CASH]
    assert isinstance(books.pay(choice()), Receipt)


def test_a_rejected_review_closes_the_bill_without_charging() -> None:
    books = ledger()
    books.open(bill(BillStage.AWAITING_REVIEW), "task_1")

    rejected = books.review(
        ReviewDecision(bill_id="bill_1", version=1, decision="rejected", reviewer="caja")
    )
    assert rejected.code is CashierFailureCode.REVIEW_REJECTED
    assert books.pay(choice()).code is CashierFailureCode.BILL_NOT_FOUND
    assert books.charges == 0
