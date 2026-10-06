"""The cashier's bills and payments: stages, the simulated payment and its receipt.

A bill moves ``awaiting_review`` (only with staff review on) →
``awaiting_payment`` → paid. The payment is simulated and always approved in
v1. It is recorded once per bill: paying again, with the same idempotency key
or another, returns the stored receipt and never charges twice. Like the A2A
task store, the ledger lives in the cashier's memory: a restart forgets the
open bills, and the customer asks for the bill again.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from restaurant_contracts.cashier import (
    PAYMENT_OPTIONS,
    Bill,
    BillStage,
    CashierFailure,
    CashierFailureCode,
    PaymentChoice,
    Receipt,
    ReviewDecision,
    cashier_failure,
)


@dataclass
class Account:
    bill: Bill
    task_id: str
    receipt: Receipt | None = None
    closed: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _reference() -> str:
    return f"pay_{uuid.uuid4().hex[:12]}"


class Ledger:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _now,
        reference: Callable[[], str] = _reference,
    ) -> None:
        self._clock = clock
        self._reference = reference
        self._accounts: dict[str, Account] = {}
        self._by_task: dict[str, str] = {}
        self.charges = 0

    def open(self, bill: Bill, task_id: str) -> None:
        self._accounts[bill.bill_id] = Account(bill=bill, task_id=task_id)
        self._by_task[task_id] = bill.bill_id

    def account(self, bill_id: str) -> Account | None:
        return self._accounts.get(bill_id)

    def review(self, decision: ReviewDecision) -> Bill | CashierFailure:
        """The person at the till approves the bill (it can be paid) or rejects it."""

        account = self._accounts.get(decision.bill_id)
        if account is None or account.closed:
            return cashier_failure(decision.bill_id, CashierFailureCode.BILL_NOT_FOUND)
        if (
            account.bill.stage is not BillStage.AWAITING_REVIEW
            or decision.version != account.bill.version
        ):
            return cashier_failure(decision.bill_id, CashierFailureCode.NOT_PAYABLE)
        if decision.decision == "rejected":
            account.closed = True
            return cashier_failure(decision.bill_id, CashierFailureCode.REVIEW_REJECTED)
        account.bill = Bill.model_validate(
            {
                **account.bill.model_dump(),
                "stage": BillStage.AWAITING_PAYMENT,
                "payment_options": list(PAYMENT_OPTIONS),
            }
        )
        return account.bill

    def pay(self, choice: PaymentChoice) -> Receipt | CashierFailure:
        account = self._accounts.get(choice.bill_id)
        if account is None:
            return cashier_failure(choice.bill_id, CashierFailureCode.BILL_NOT_FOUND)
        if account.receipt is not None:
            return account.receipt
        if account.closed:
            return cashier_failure(choice.bill_id, CashierFailureCode.BILL_NOT_FOUND)
        bill = account.bill
        if (
            choice.version != bill.version
            or bill.stage is not BillStage.AWAITING_PAYMENT
            or choice.method not in bill.payment_options
        ):
            return cashier_failure(choice.bill_id, CashierFailureCode.NOT_PAYABLE)
        # Simulated payment provider: always approved in v1.
        self.charges += 1
        account.receipt = Receipt(
            bill_id=bill.bill_id,
            version=bill.version,
            method=choice.method,
            amount=bill.total,
            reference=self._reference(),
            paid_at=self._clock(),
            idempotency_key=choice.idempotency_key,
        )
        return account.receipt

    def close_task(self, task_id: str) -> None:
        """The bill's task was cancelled: it can no longer be paid."""

        bill_id = self._by_task.get(task_id)
        account = self._accounts.get(bill_id) if bill_id else None
        if account is not None and account.receipt is None:
            account.closed = True
