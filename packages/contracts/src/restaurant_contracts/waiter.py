"""Typed wire contract for invoking the restaurant waiter remotely."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from .activity import MAX_ACTIVITY_STEPS, ActivityStep
from .application import ActorContext
from .bar import BarReport
from .cashier import CashierReport, PaymentMethod, PendingBill, ServedLine
from .customer import CustomerSnapshot, OrderDraft
from .kitchen import KitchenReport
from .seating import PlaceKind, PlaceState, SeatingPlace


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WaiterTurnRequest(WireModel):
    operation: Literal["take_turn"] = "take_turn"
    conversation_id: str = Field(min_length=1, max_length=200)
    actor: ActorContext
    presented_name: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int = Field(ge=0)
    persisted_order_preferences: list[str] = Field(default_factory=list)
    session_json: str | None = None
    correlation_id: str = Field(min_length=1, max_length=200)
    visit_id: str | None = None
    # What the bill may include: the kitchen dishes and the bar drinks already
    # served, how many cooked orders still wait at the pass and the bill
    # already presented.
    # Left out of the JSON when empty, like the kitchen's report.
    served: list[ServedLine] = Field(
        default_factory=list, max_length=50, exclude_if=lambda value: not value
    )
    orders_at_pass: int = Field(default=0, ge=0, le=50, exclude_if=lambda value: value == 0)
    pending_bill: PendingBill | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class WaiterSeatingRequest(WireModel):
    """Seating work the waiter does on its own MCP connection, without the model."""

    conversation_id: str = Field(min_length=1, max_length=200)
    actor: ActorContext
    presented_name: str = Field(min_length=1, max_length=100)
    session_json: str | None = None
    correlation_id: str = Field(min_length=1, max_length=200)
    visit_id: str = Field(min_length=1, max_length=200)


class WaiterSeatingDecisionRequest(WaiterSeatingRequest):
    """The customer's button answer to the paused confirmation (HITL)."""

    operation: Literal["decide_seating"] = "decide_seating"
    decision: Literal["confirmed", "rejected"]
    proposal_token: str = Field(min_length=1, max_length=100)


class WaiterSeatingSyncRequest(WaiterSeatingRequest):
    """Read the visit's seating and the room from the MCP."""

    operation: Literal["sync_seating"] = "sync_seating"


class WaiterSeatingReleaseRequest(WaiterSeatingRequest):
    """Release this visit's pending or occupied place."""

    operation: Literal["release_seating"] = "release_seating"


class WaiterServeRequest(WireModel):
    """The waiter takes cooked dishes from the pass to the customer, without the model."""

    operation: Literal["serve_order"] = "serve_order"
    conversation_id: str = Field(min_length=1, max_length=200)
    actor: ActorContext
    correlation_id: str = Field(min_length=1, max_length=200)
    order_id: str = Field(min_length=1, max_length=200)
    dishes: list[str] = Field(min_length=1, max_length=20)


class WaiterPayRequest(WireModel):
    """The customer's button answer to the presented bill; the waiter only relays it."""

    operation: Literal["pay_bill"] = "pay_bill"
    conversation_id: str = Field(min_length=1, max_length=200)
    actor: ActorContext
    correlation_id: str = Field(min_length=1, max_length=200)
    bill: PendingBill
    method: PaymentMethod
    idempotency_key: str = Field(min_length=1, max_length=200)


WaiterRequest = Annotated[
    WaiterTurnRequest
    | WaiterSeatingDecisionRequest
    | WaiterSeatingSyncRequest
    | WaiterSeatingReleaseRequest
    | WaiterServeRequest
    | WaiterPayRequest,
    Field(discriminator="operation"),
]
WAITER_REQUEST_ADAPTER = TypeAdapter(WaiterRequest)


class SeatingOutcome(WireModel):
    decision: Literal["confirmed", "rejected", "superseded", "expired", "cancelled"]
    place: str = Field(max_length=100)


class RoomSeatReport(WireModel):
    position: int = Field(ge=1, le=100)
    state: PlaceState
    expires_at: AwareDatetime | None = None


class RoomPlaceReport(WireModel):
    """One place of the anonymised room: no visits, assignments or names."""

    place_id: str = Field(min_length=1, max_length=100)
    kind: PlaceKind
    label: str = Field(min_length=1, max_length=100)
    capacity: int = Field(ge=1, le=100)
    display_order: int = Field(ge=0)
    state: PlaceState
    party_size: int | None = Field(default=None, ge=1)
    expires_at: AwareDatetime | None = None
    seats: list[RoomSeatReport] = Field(default_factory=list)


class SeatingReport(WireModel):
    """The visit's own seating and the anonymised room, as the waiter read them."""

    status: Literal["none", "proposed", "seated"] = "none"
    awaiting_decision: bool = False
    token: str | None = Field(default=None, max_length=100)
    place: SeatingPlace | None = None
    party_size: int | None = Field(default=None, ge=1, le=20)
    version: int | None = Field(default=None, ge=1)
    expires_at: AwareDatetime | None = None
    seated_at: AwareDatetime | None = None
    last_outcome: SeatingOutcome | None = None
    room: list[RoomPlaceReport] = Field(default_factory=list)


class WaiterTurnSuccess(WireModel):
    status: Literal["completed"] = "completed"
    reply: str = Field(min_length=1, max_length=2_000)
    customer: CustomerSnapshot
    order_draft: OrderDraft
    turn_count: int = Field(ge=1)
    persisted_order_preferences: list[str] = Field(default_factory=list)
    memory_intent: Literal["none", "reuse_latest_order"] = Field(
        default="none", exclude_if=lambda value: value == "none"
    )
    remembered_memory_count: int = Field(
        default=0, ge=0, le=100, exclude_if=lambda value: value == 0
    )
    session_json: str | None = None
    seating: SeatingReport | None = None
    # The kitchen's answer when the waiter sent it the order in this turn.
    # Left out of the JSON when absent, so a BFF of the previous version still
    # reads every other turn while both are being redeployed.
    kitchen: KitchenReport | None = Field(default=None, exclude_if=lambda value: value is None)
    # The drinks the waiter served from the bar in this turn, also left out
    # of the JSON when absent.
    bar: BarReport | None = Field(default=None, exclude_if=lambda value: value is None)
    # The cashier's answer when the customer asked for the bill in this turn.
    cashier: CashierReport | None = Field(default=None, exclude_if=lambda value: value is None)
    activity: list[ActivityStep] = Field(
        default_factory=list, max_length=MAX_ACTIVITY_STEPS, exclude_if=lambda value: not value
    )


class WaiterSeatingSuccess(WireModel):
    """Result of a seating decision or sync; ``reply`` is the waiter's fixed answer."""

    status: Literal["completed"] = "completed"
    operation: Literal["decide_seating", "sync_seating", "release_seating"]
    reply: str = Field(default="", max_length=2_000)
    outcome: Literal[
        "confirmed", "rejected", "expired", "stale", "unavailable", "cancelled"
    ] | None = None
    session_json: str | None = None
    seating: SeatingReport | None = None


class WaiterServeSuccess(WireModel):
    status: Literal["completed"] = "completed"
    operation: Literal["serve_order"] = "serve_order"
    order_id: str = Field(min_length=1, max_length=200)
    reply: str = Field(min_length=1, max_length=2_000)


class WaiterPaySuccess(WireModel):
    """The cashier's receipt or failure and the waiter's fixed answer to it."""

    status: Literal["completed"] = "completed"
    operation: Literal["pay_bill"] = "pay_bill"
    reply: str = Field(min_length=1, max_length=2_000)
    cashier: CashierReport
    activity: list[ActivityStep] = Field(
        default_factory=list, max_length=MAX_ACTIVITY_STEPS, exclude_if=lambda value: not value
    )


class WaiterTurnFailure(WireModel):
    status: Literal["failed"] = "failed"
    code: Literal[
        "turn_limit",
        "seating_unavailable",
        "no_pending_decision",
        "unavailable",
        "invalid_response",
        "internal_error",
    ]
    message: str = Field(min_length=1, max_length=500)


WaiterTurnResponse = WaiterTurnSuccess | WaiterTurnFailure
WAITER_TURN_RESPONSE_ADAPTER = TypeAdapter(WaiterTurnResponse)
WaiterSeatingResponse = WaiterSeatingSuccess | WaiterTurnFailure
WAITER_SEATING_RESPONSE_ADAPTER = TypeAdapter(WaiterSeatingResponse)
WaiterServeResponse = WaiterServeSuccess | WaiterTurnFailure
WAITER_SERVE_RESPONSE_ADAPTER = TypeAdapter(WaiterServeResponse)
WaiterPayResponse = WaiterPaySuccess | WaiterTurnFailure
WAITER_PAY_RESPONSE_ADAPTER = TypeAdapter(WaiterPayResponse)
