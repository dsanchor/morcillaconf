"""Drinks the waiter serves from the bar, checked against the carta by code.

Drinks never go to the kitchen: the waiter serves them himself from the bar
with ``servir_bebidas``. The model only decides when to call it and passes
what the customer asked for; which carta entry each drink is, whether a
declared allergy or intolerance rules it out and what is served are decided by
code from the carta the knowledge base returned. Each call is one round with
its own id. There is no pass: the BFF records a served round at once, so its
drinks are billed like the kitchen dishes already served.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .kitchen import (
    CartaId,
    DishName,
    KitchenIdentifier,
    KitchenSource,
    KitchenText,
    LineNumber,
    Quantity,
    RenderedText,
)


class BarModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BarItem(BarModel):
    """One drink as the customer asked for it."""

    line: LineNumber
    name: DishName
    quantity: Quantity = 1


class BarRequest(BarModel):
    """What the waiter asks the bar: the drinks and the declared allergies, nothing else."""

    schema_version: Literal[1] = 1
    round_id: KitchenIdentifier
    items: list[BarItem] = Field(min_length=1, max_length=20)
    restrictions: list[KitchenText] = Field(default_factory=list, max_length=14)

    @model_validator(mode="after")
    def lines_are_numbered_in_order(self) -> BarRequest:
        if [item.line for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Bar lines are numbered 1, 2, 3... in order")
        return self

    def get_line(self, number: int) -> BarItem:
        return self.items[number - 1]


class ServedDrink(BarModel):
    """A drink served from the bar, named as its carta entry."""

    line: LineNumber
    carta_id: CartaId
    name: KitchenText
    quantity: Quantity


class RejectedDrink(BarModel):
    """A drink the bar does not serve, always with its reason."""

    line: LineNumber
    requested: KitchenText
    quantity: Quantity
    reason: KitchenText
    carta_id: CartaId | None = None
    # For an ambiguous name, the carta drinks it may be, so the waiter can ask.
    options: list[KitchenText] = Field(default_factory=list, max_length=10)


class BarRound(BarModel):
    """The bar's answer: what it served and what it did not, with the carta behind it."""

    status: Literal["served"] = "served"
    round_id: KitchenIdentifier
    served: list[ServedDrink] = Field(default_factory=list, max_length=20)
    rejected: list[RejectedDrink] = Field(default_factory=list, max_length=20)
    warnings: list[KitchenText] = Field(default_factory=list, max_length=20)
    sources: list[KitchenSource] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def round_is_consistent(self) -> BarRound:
        served = [item.line for item in self.served]
        rejected = [item.line for item in self.rejected]
        if not served and not rejected:
            raise ValueError("A bar round decides at least one line")
        if len(set(served)) != len(served) or len(set(rejected)) != len(rejected):
            raise ValueError("Each bar line is decided once")
        if set(served) & set(rejected):
            raise ValueError("No bar line can be both served and rejected")
        return self

    @property
    def verdict(self) -> Literal["served", "partial", "rejected"]:
        if not self.rejected:
            return "served"
        if not self.served:
            return "rejected"
        return "partial"


class BarFailureCode(StrEnum):
    NOT_CONFIGURED = "knowledge_not_configured"
    KNOWLEDGE_UNAVAILABLE = "knowledge_unavailable"
    CARTA_NOT_CONSULTED = "carta_not_consulted"
    TIMEOUT = "timeout"
    BAR_UNAVAILABLE = "bar_unavailable"


BAR_FAILURE_MESSAGES: dict[BarFailureCode, str] = {
    BarFailureCode.NOT_CONFIGURED: (
        "La barra no puede consultar la carta: la base de conocimiento no está configurada."
    ),
    BarFailureCode.KNOWLEDGE_UNAVAILABLE: "La barra no puede consultar la carta ahora mismo.",
    BarFailureCode.CARTA_NOT_CONSULTED: "La barra no encuentra las bebidas en la carta consultada.",
    BarFailureCode.TIMEOUT: "La carta no ha respondido a tiempo.",
    BarFailureCode.BAR_UNAVAILABLE: "La barra no puede servir ahora mismo.",
}


class BarFailure(BarModel):
    """The bar could not check the drinks against the carta: it serves nothing."""

    status: Literal["failed"] = "failed"
    round_id: KitchenIdentifier
    code: BarFailureCode
    message: KitchenText


def bar_failure(round_id: str, code: BarFailureCode) -> BarFailure:
    return BarFailure(round_id=round_id, code=code, message=BAR_FAILURE_MESSAGES[code])


BarResult = Annotated[BarRound | BarFailure, Field(discriminator="status")]
BAR_RESULT_ADAPTER: TypeAdapter[BarRound | BarFailure] = TypeAdapter(BarResult)


class BarReport(BarModel):
    """One call of the waiter to the bar: the request, the result and its Spanish text."""

    request: BarRequest
    result: BarResult
    text: RenderedText

    @model_validator(mode="after")
    def result_answers_the_request(self) -> BarReport:
        if self.result.round_id != self.request.round_id:
            raise ValueError("The bar result answers this round")
        if isinstance(self.result, BarRound):
            decided = [item.line for item in (*self.result.served, *self.result.rejected)]
            if sorted(decided) != [item.line for item in self.request.items]:
                raise ValueError("The round decides every requested drink")
            for item in (*self.result.served, *self.result.rejected):
                if item.quantity != self.request.get_line(item.line).quantity:
                    raise ValueError("The round keeps the requested quantities")
        return self
