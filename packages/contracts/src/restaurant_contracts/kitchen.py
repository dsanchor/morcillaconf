"""Kitchen order and plan exchanged by the waiter and the chef.

The waiter sends the chef only the order: dishes, quantities, modifications
and the allergies or intolerances declared for it, never the customer's
profile, history or memory. The chef answers with a plan that has passed
deterministic validation, or with an explicit failure; never with an
invented plan. ``KitchenPort`` is the boundary between both: the waiter uses
it without depending on the A2A transport used by the external kitchen.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

KitchenIdentifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
KitchenText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
DishName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
# The stable identifier of a carta entry, such as ``morcilla-de-burgos-a-la-brasa``.
CartaId = Annotated[
    str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
]
LineNumber = Annotated[int, Field(ge=1, le=20)]
Quantity = Annotated[int, Field(ge=1, le=20)]
RENDERED_TEXT_LIMIT = 6_000
RenderedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=RENDERED_TEXT_LIMIT),
]


class KitchenStation(StrEnum):
    """The partidas named by the carta; drinks are served at the bar."""

    BRASA = "brasa"
    FRITOS = "fritos"
    PINCHOS_FRIOS = "pinchos_frios"
    BARRA = "barra"


STATION_ORDER: tuple[KitchenStation, ...] = tuple(KitchenStation)
STATION_LABELS: dict[KitchenStation, str] = {
    KitchenStation.BRASA: "Brasa",
    KitchenStation.FRITOS: "Fritos",
    KitchenStation.PINCHOS_FRIOS: "Pinchos fríos",
    KitchenStation.BARRA: "Barra",
}


class KitchenModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KitchenOrderLine(KitchenModel):
    """One line of the order, as the customer asked for it."""

    line: LineNumber
    name: DishName
    quantity: Quantity = 1
    modifications: list[KitchenText] = Field(default_factory=list, max_length=10)


class KitchenOrder(KitchenModel):
    """What the waiter sends the chef: the order and nothing about the customer."""

    schema_version: Literal[1] = 1
    order_id: KitchenIdentifier
    lines: list[KitchenOrderLine] = Field(min_length=1, max_length=20)
    restrictions: list[KitchenText] = Field(default_factory=list, max_length=14)

    @model_validator(mode="after")
    def lines_are_numbered_in_order(self) -> KitchenOrder:
        if [item.line for item in self.lines] != list(range(1, len(self.lines) + 1)):
            raise ValueError("Order lines are numbered 1, 2, 3... in order")
        return self

    def get_line(self, number: int) -> KitchenOrderLine:
        return self.lines[number - 1]


class AcceptedItem(KitchenModel):
    """An order line the kitchen can prepare, as the carta and the recipe book say."""

    line: LineNumber
    carta_id: CartaId
    name: KitchenText
    quantity: Quantity
    station: KitchenStation
    adaptations: list[KitchenText] = Field(default_factory=list, max_length=10)
    allergens: list[KitchenText] = Field(default_factory=list, max_length=14)
    traces: list[KitchenText] = Field(default_factory=list, max_length=14)
    allergens_verified: bool = True


class RejectedItem(KitchenModel):
    """An order line the kitchen does not take, always with its reason."""

    line: LineNumber
    requested: KitchenText
    quantity: Quantity
    reason: KitchenText
    carta_id: CartaId | None = None


class StationTask(KitchenModel):
    """What one partida will prepare for an accepted line."""

    line: LineNumber
    carta_id: CartaId
    name: KitchenText
    quantity: Quantity
    steps: list[KitchenText] = Field(default_factory=list, max_length=6)
    omit: list[KitchenText] = Field(default_factory=list, max_length=10)
    precautions: list[KitchenText] = Field(default_factory=list, max_length=10)


class StationPlan(KitchenModel):
    station: KitchenStation
    tasks: list[StationTask] = Field(min_length=1, max_length=20)


class KitchenSource(KitchenModel):
    """A restaurant document the plan is based on."""

    document: KitchenText
    version: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)
    ] | None = None
    detail: KitchenText | None = None


class KitchenPlan(KitchenModel):
    """The chef's validated answer: every line accepted or rejected, never both."""

    status: Literal["planned"] = "planned"
    order_id: KitchenIdentifier
    version: Annotated[int, Field(ge=1)] = 1
    accepted: list[AcceptedItem] = Field(default_factory=list, max_length=20)
    rejected: list[RejectedItem] = Field(default_factory=list, max_length=20)
    warnings: list[KitchenText] = Field(default_factory=list, max_length=20)
    stations: list[StationPlan] = Field(default_factory=list, max_length=len(STATION_ORDER))
    sources: list[KitchenSource] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def plan_is_consistent(self) -> KitchenPlan:
        accepted = [item.line for item in self.accepted]
        rejected = [item.line for item in self.rejected]
        if not accepted and not rejected:
            raise ValueError("A kitchen plan decides at least one order line")
        if len(set(accepted)) != len(accepted) or len(set(rejected)) != len(rejected):
            raise ValueError("Each order line is decided once")
        if set(accepted) & set(rejected):
            raise ValueError("No order line can be both accepted and rejected")
        stations = [plan.station for plan in self.stations]
        if stations != sorted(set(stations), key=STATION_ORDER.index):
            raise ValueError("Each station appears once, in kitchen order")
        by_line = {item.line: item for item in self.accepted}
        tasks = [(plan.station, task) for plan in self.stations for task in plan.tasks]
        if sorted(task.line for _, task in tasks) != sorted(accepted):
            raise ValueError("Every accepted line has exactly one station task")
        for station, task in tasks:
            item = by_line[task.line]
            if (item.carta_id, item.quantity, item.station) != (
                task.carta_id,
                task.quantity,
                station,
            ):
                raise ValueError("A station task matches its accepted line")
        return self

    @property
    def verdict(self) -> Literal["accepted", "partial", "rejected"]:
        if not self.rejected:
            return "accepted"
        if not self.accepted:
            return "rejected"
        return "partial"


class KitchenFailureCode(StrEnum):
    KITCHEN_NOT_CONFIGURED = "kitchen_not_configured"
    NOT_CONFIGURED = "knowledge_not_configured"
    KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
    CARTA_NOT_CONSULTED = "carta_not_consulted"
    TIMEOUT = "timeout"
    CHEF_UNAVAILABLE = "chef_unavailable"
    INVALID_PLAN = "invalid_plan"


class KitchenFailure(KitchenModel):
    """The kitchen could not plan the order; nothing is invented in its place."""

    status: Literal["failed"] = "failed"
    order_id: KitchenIdentifier
    code: KitchenFailureCode
    message: KitchenText


KitchenResult = Annotated[KitchenPlan | KitchenFailure, Field(discriminator="status")]
KITCHEN_RESULT_ADAPTER: TypeAdapter[KitchenPlan | KitchenFailure] = TypeAdapter(KitchenResult)


class KitchenReport(KitchenModel):
    """One call of the waiter to the kitchen: the order, the result and its Spanish text."""

    order: KitchenOrder
    result: KitchenResult
    text: RenderedText

    @model_validator(mode="after")
    def result_answers_the_order(self) -> KitchenReport:
        if self.result.order_id != self.order.order_id:
            raise ValueError("The kitchen result answers this order")
        if isinstance(self.result, KitchenPlan):
            decided = [item.line for item in (*self.result.accepted, *self.result.rejected)]
            if sorted(decided) != [line.line for line in self.order.lines]:
                raise ValueError("The plan decides every order line")
            for item in (*self.result.accepted, *self.result.rejected):
                if item.quantity != self.order.get_line(item.line).quantity:
                    raise ValueError("The plan keeps the ordered quantities")
        return self


class KitchenPort(Protocol):
    """Where the waiter sends an order: the external kitchen A2A agent."""

    async def plan(self, order: KitchenOrder) -> KitchenPlan | KitchenFailure:
        """Never raises: every problem becomes an explicit ``KitchenFailure``."""
        ...
