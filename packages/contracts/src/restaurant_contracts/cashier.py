"""Bill and payment exchanged by the waiter and the cashier (caja).

The model never chooses what is billed or how much. The BFF hands the waiter
the kitchen dishes and the bar drinks already served, the waiter forwards them
unchanged, and the cashier prices every line from the carta, and only from the
carta, with exact decimal arithmetic in code. The presented bill waits for the customer's choice
of card or cash (A2A ``input-required``); the simulated payment is always
approved in v1, and paying the same bill again returns the same receipt
instead of charging twice.

The bill's ``stage`` is the hook for the staff review of SPECS: with review
enabled the cashier first waits in ``awaiting_review`` (another
``input-required`` step, without payment options) and only offers card or
cash after a ``ReviewDecision``. Review is off by default.

``CashierPort`` is the boundary: the waiter uses it without depending on the
A2A transport of the external cashier.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from .bar import BarReport, BarRound
from .kitchen import (
    CartaId,
    KitchenIdentifier,
    KitchenPlan,
    KitchenReport,
    KitchenText,
    LineNumber,
    Quantity,
    RenderedText,
)

MAX_BILL_LINES = 50
# Exact euros with cents; the cashier's code computes every amount.
Money = Annotated[
    Decimal, Field(ge=0, le=Decimal("99999.99"), max_digits=7, decimal_places=2)
]
Currency = Literal["EUR"]
BILL_NOTE = "Solo lo ya servido: platos de cocina y bebidas de la barra."
Version = Annotated[int, Field(ge=1)]


class BillStage(StrEnum):
    """Where a presented bill waits: the optional staff review or the payment."""

    AWAITING_REVIEW = "awaiting_review"
    AWAITING_PAYMENT = "awaiting_payment"


class PaymentMethod(StrEnum):
    CARD = "tarjeta"
    CASH = "efectivo"


PAYMENT_OPTIONS: tuple[PaymentMethod, ...] = tuple(PaymentMethod)
PAYMENT_LABELS: dict[PaymentMethod, str] = {
    PaymentMethod.CARD: "Tarjeta",
    PaymentMethod.CASH: "Efectivo",
}


class CashierModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServedLine(CashierModel):
    """A kitchen dish or a bar drink already served: the only thing billed."""

    # The kitchen order or the bar round the line belongs to.
    order_id: KitchenIdentifier
    line: LineNumber
    carta_id: CartaId
    name: KitchenText
    quantity: Quantity


def _unique_lines(lines: Iterable[ServedLine | BillLine]) -> None:
    keys = [(item.order_id, item.line) for item in lines]
    if len(keys) != len(set(keys)):
        raise ValueError("Each served line is billed once")


class BillRequest(CashierModel):
    """What the waiter sends the cashier: the served lines, nothing about the customer."""

    kind: Literal["bill_request"] = "bill_request"
    schema_version: Literal[1] = 1
    bill_id: KitchenIdentifier
    lines: list[ServedLine] = Field(min_length=1, max_length=MAX_BILL_LINES)

    @model_validator(mode="after")
    def each_line_once(self) -> BillRequest:
        _unique_lines(self.lines)
        return self


class BillLine(CashierModel):
    """A served line with its unit price from the carta and its exact total."""

    order_id: KitchenIdentifier
    line: LineNumber
    carta_id: CartaId
    name: KitchenText
    quantity: Quantity
    unit_price: Money
    line_total: Money

    @model_validator(mode="after")
    def total_is_exact(self) -> BillLine:
        if self.line_total != self.unit_price * self.quantity:
            raise ValueError("A line total is its unit price times its quantity")
        return self


class BillSource(CashierModel):
    """The restaurant document the prices come from."""

    document: KitchenText
    version: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)
        ]
        | None
    ) = None
    detail: KitchenText | None = None


class Bill(CashierModel):
    """The priced bill, presented and waiting for its next step."""

    status: Literal["presented"] = "presented"
    bill_id: KitchenIdentifier
    version: Version = 1
    stage: BillStage = BillStage.AWAITING_PAYMENT
    currency: Currency = "EUR"
    lines: list[BillLine] = Field(min_length=1, max_length=MAX_BILL_LINES)
    total: Money
    payment_options: list[PaymentMethod] = Field(
        default_factory=lambda: list(PAYMENT_OPTIONS), max_length=len(PAYMENT_OPTIONS)
    )
    sources: list[BillSource] = Field(min_length=1, max_length=10)
    note: KitchenText = BILL_NOTE

    @model_validator(mode="after")
    def bill_adds_up(self) -> Bill:
        _unique_lines(self.lines)
        if self.total != sum((item.line_total for item in self.lines), Decimal(0)):
            raise ValueError("The total is the sum of the line totals")
        if len(set(self.payment_options)) != len(self.payment_options):
            raise ValueError("Each payment option is offered once")
        payable = self.stage is BillStage.AWAITING_PAYMENT
        if payable != bool(self.payment_options):
            raise ValueError("Only a bill awaiting payment offers payment options")
        return self


class PaymentChoice(CashierModel):
    """The customer's button answer to a presented bill."""

    kind: Literal["payment_choice"] = "payment_choice"
    bill_id: KitchenIdentifier
    version: Version
    method: PaymentMethod
    # The BFF event of the first attempt: retries repeat it, never a second charge.
    idempotency_key: KitchenIdentifier


class ReviewDecision(CashierModel):
    """Prepared hook: a person at the till reviews the bill before payment."""

    kind: Literal["review_decision"] = "review_decision"
    bill_id: KitchenIdentifier
    version: Version
    decision: Literal["approved", "rejected"]
    reviewer: KitchenIdentifier
    note: KitchenText | None = None


CashierInput = Annotated[
    BillRequest | PaymentChoice | ReviewDecision, Field(discriminator="kind")
]
CASHIER_INPUT_ADAPTER: TypeAdapter[BillRequest | PaymentChoice | ReviewDecision] = (
    TypeAdapter(CashierInput)
)


class Receipt(CashierModel):
    """The payment the cashier recorded; paying again returns this same receipt."""

    status: Literal["paid"] = "paid"
    bill_id: KitchenIdentifier
    version: Version
    method: PaymentMethod
    amount: Money
    currency: Currency = "EUR"
    reference: KitchenIdentifier
    paid_at: AwareDatetime
    idempotency_key: KitchenIdentifier


class CashierFailureCode(StrEnum):
    CASHIER_NOT_CONFIGURED = "cashier_not_configured"
    NOT_CONFIGURED = "knowledge_not_configured"
    KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
    CARTA_NOT_CONSULTED = "carta_not_consulted"
    PRICE_MISSING = "price_missing"
    PRICE_INCONSISTENT = "price_inconsistent"
    TIMEOUT = "timeout"
    CASHIER_UNAVAILABLE = "cashier_unavailable"
    INVALID_BILL = "invalid_bill"
    BILL_NOT_FOUND = "bill_not_found"
    NOT_PAYABLE = "not_payable"
    REVIEW_REJECTED = "review_rejected"


FAILURE_MESSAGES: dict[CashierFailureCode, str] = {
    CashierFailureCode.CASHIER_NOT_CONFIGURED: "Caja no está configurada para cobrar.",
    CashierFailureCode.NOT_CONFIGURED: (
        "Caja no puede consultar la carta: la base de conocimiento no está configurada."
    ),
    CashierFailureCode.KNOWLEDGE_UNAVAILABLE: "Caja no puede consultar la carta ahora mismo.",
    CashierFailureCode.CARTA_NOT_CONSULTED: "Caja no ha podido consultar los precios en la carta.",
    CashierFailureCode.PRICE_MISSING: (
        "Caja no encuentra en la carta el precio de algún plato o bebida servidos."
    ),
    CashierFailureCode.PRICE_INCONSISTENT: (
        "La carta da precios distintos para un mismo plato o bebida; caja no cobra sin aclararlo."
    ),
    CashierFailureCode.TIMEOUT: "Caja no ha respondido a tiempo.",
    CashierFailureCode.CASHIER_UNAVAILABLE: "Caja no puede responder ahora mismo.",
    CashierFailureCode.INVALID_BILL: "Caja ha devuelto una cuenta que no supera la validación.",
    CashierFailureCode.BILL_NOT_FOUND: "Caja ya no tiene esa cuenta abierta.",
    CashierFailureCode.NOT_PAYABLE: "Esa cuenta no se puede cobrar así ahora mismo.",
    CashierFailureCode.REVIEW_REJECTED: "Caja ha rechazado el ticket en su revisión.",
}


class CashierFailure(CashierModel):
    """Caja could not price or charge the bill; no amount is invented in its place."""

    status: Literal["failed"] = "failed"
    bill_id: KitchenIdentifier
    code: CashierFailureCode
    message: KitchenText


def cashier_failure(bill_id: str, code: CashierFailureCode) -> CashierFailure:
    return CashierFailure(bill_id=bill_id, code=code, message=FAILURE_MESSAGES[code])


CashierResult = Annotated[Bill | Receipt | CashierFailure, Field(discriminator="status")]
CASHIER_RESULT_ADAPTER: TypeAdapter[Bill | Receipt | CashierFailure] = TypeAdapter(
    CashierResult
)


class CashierTask(CashierModel):
    """The cashier's A2A task that holds a bill; the payment resumes this same task."""

    task_id: KitchenIdentifier
    context_id: KitchenIdentifier


class CashierAnswer(CashierModel):
    """What the cashier answered and the A2A task it answered in."""

    result: CashierResult
    task: CashierTask | None = None


class PendingBill(CashierModel):
    """A presented bill, as the waiter needs it to resume or cancel its task."""

    bill_id: KitchenIdentifier
    version: Version
    task: CashierTask


class CashierReport(CashierModel):
    """One exchange of the waiter with the cashier: result, Spanish text and A2A task."""

    bill_id: KitchenIdentifier
    request: BillRequest | None = None
    result: CashierResult
    text: RenderedText
    task: CashierTask | None = None

    @model_validator(mode="after")
    def report_is_consistent(self) -> CashierReport:
        if self.result.bill_id != self.bill_id:
            raise ValueError("The cashier result answers this bill")
        if self.request is not None:
            if self.request.bill_id != self.bill_id:
                raise ValueError("The bill request is this bill's")
            if isinstance(self.result, Bill) and _line_keys(self.result.lines) != _line_keys(
                self.request.lines
            ):
                raise ValueError("The bill prices exactly the served lines")
        if isinstance(self.result, Bill) and self.task is None:
            raise ValueError("A presented bill keeps its A2A task to resume the payment")
        return self

    @property
    def pending(self) -> PendingBill | None:
        if not isinstance(self.result, Bill) or self.task is None:
            return None
        return PendingBill(bill_id=self.bill_id, version=self.result.version, task=self.task)


def _line_keys(lines: Iterable[ServedLine | BillLine]) -> list[tuple[str, int, str, int]]:
    return sorted((item.order_id, item.line, item.carta_id, item.quantity) for item in lines)


class BillView(CashierModel):
    """The bill waiting in the conversation, for the view's payment buttons."""

    bill_id: KitchenIdentifier
    version: Version
    stage: BillStage
    total: Money
    currency: Currency = "EUR"
    payment_options: list[PaymentMethod] = Field(
        default_factory=list, max_length=len(PAYMENT_OPTIONS)
    )

    @classmethod
    def of(cls, bill: Bill) -> BillView:
        return cls(
            bill_id=bill.bill_id,
            version=bill.version,
            stage=bill.stage,
            total=bill.total,
            payment_options=list(bill.payment_options),
        )

    @property
    def payable(self) -> bool:
        return self.stage is BillStage.AWAITING_PAYMENT and bool(self.payment_options)


def served_lines(
    reports: Iterable[KitchenReport | BarReport], served_orders: Collection[str]
) -> list[ServedLine]:
    """The accepted dishes and the served drinks of the orders and rounds already served, in order."""

    lines: list[ServedLine] = []
    for report in reports:
        result = report.result
        if isinstance(result, KitchenPlan) and result.order_id in served_orders:
            order_id, items = result.order_id, result.accepted
        elif isinstance(result, BarRound) and result.round_id in served_orders:
            order_id, items = result.round_id, result.served
        else:
            continue
        lines.extend(
            ServedLine(
                order_id=order_id,
                line=item.line,
                carta_id=item.carta_id,
                name=item.name,
                quantity=item.quantity,
            )
            for item in items
        )
    return lines


def _new_lines(report: KitchenReport | BarReport) -> list[tuple[str, int]]:
    """The dishes or drinks a report puts on their way to the table: (order or round, line)."""

    result = report.result
    if isinstance(result, KitchenPlan):
        return [(result.order_id, item.line) for item in result.accepted]
    if isinstance(result, BarRound):
        return [(result.round_id, item.line) for item in result.served]
    return []


def supersedes_bill(report: KitchenReport | BarReport) -> bool:
    """New dishes or drinks: a bill presented before them is no longer exact."""

    return bool(_new_lines(report))


def bill_misses(request: BillRequest | None, report: KitchenReport | BarReport) -> bool:
    """Whether a bill leaves out dishes or drinks the report put on their way to the table.

    A bill presented in the same turn as a round of drinks may already include
    them; one presented before them, or one that misses them, is stale.
    """

    billed = {(line.order_id, line.line) for line in request.lines} if request is not None else set()
    return any(key not in billed for key in _new_lines(report))


def euros(amount: Decimal) -> str:
    """Spanish rendering of an amount: ``1.234,50 €``."""

    whole, cents = f"{amount.quantize(Decimal('0.01')):,.2f}".split(".")
    return f"{whole.replace(',', '.')},{cents} €"


class CashierPort(Protocol):
    """Where the waiter asks for the bill and relays the payment: the A2A cashier."""

    async def present(self, request: BillRequest) -> CashierAnswer:
        """Never raises: every problem becomes an explicit ``CashierFailure``."""
        ...

    async def pay(self, pending: PendingBill, choice: PaymentChoice) -> CashierAnswer:
        """Never raises; a bill already paid answers its stored receipt."""
        ...

    async def cancel(self, pending: PendingBill) -> bool:
        """Best effort: whether the cashier's task for the bill is now cancelled."""
        ...
