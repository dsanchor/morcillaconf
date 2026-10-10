"""Privacy-safe business events and metrics for the restaurant journey."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, UpDownCounter

AttributeValue = bool | int | float | str
Attributes = Mapping[str, AttributeValue]


class BusinessTelemetry:
    """Emit business facts without customer identity or free-form content."""

    def __init__(self) -> None:
        meter = metrics.get_meter("morcillaconf.business")
        self._tracer = trace.get_tracer("morcillaconf.business")
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._up_down_counters: dict[str, UpDownCounter] = {}
        self._meter = meter

    def event(
        self,
        name: str,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        attributes: Attributes | None = None,
    ) -> None:
        event_attributes: dict[str, AttributeValue] = {
            "business.event.id": f"bevt_{uuid.uuid4().hex}",
            "business.event.name": name,
            "business.event.schema_version": 1,
            "restaurant.visit.id": visit_id,
            "restaurant.conversation.id": conversation_id,
        }
        if attributes:
            event_attributes.update(attributes)
        with self._tracer.start_as_current_span(
            "restaurant.business.event",
            attributes={
                "business.event.name": name,
                "business.event.schema_version": 1,
            },
        ) as span:
            span.add_event(name, event_attributes, timestamp=int(occurred_at.timestamp() * 1e9))

    def count(
        self,
        name: str,
        value: int | float = 1,
        *,
        unit: str = "{event}",
        attributes: Attributes | None = None,
    ) -> None:
        instrument = self._counters.get(name)
        if instrument is None:
            instrument = self._meter.create_counter(name, unit=unit)
            self._counters[name] = instrument
        instrument.add(value, attributes)

    def record(
        self,
        name: str,
        value: int | float | Decimal,
        *,
        unit: str,
        attributes: Attributes | None = None,
    ) -> None:
        instrument = self._histograms.get(name)
        if instrument is None:
            instrument = self._meter.create_histogram(name, unit=unit)
            self._histograms[name] = instrument
        instrument.record(float(value), attributes)

    def adjust(
        self,
        name: str,
        value: int,
        *,
        unit: str,
        attributes: Attributes | None = None,
    ) -> None:
        instrument = self._up_down_counters.get(name)
        if instrument is None:
            instrument = self._meter.create_up_down_counter(name, unit=unit)
            self._up_down_counters[name] = instrument
        instrument.add(value, attributes)

    def visit_started(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        returning: bool,
    ) -> None:
        lifecycle = "returning" if returning else "first_visit"
        attributes = {"customer.kind": "authenticated", "customer.lifecycle": lifecycle}
        self.event(
            "restaurant.visit.started",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes=attributes,
        )
        self.count("restaurant.visits.started", unit="{visit}", attributes=attributes)
        self.count(
            f"restaurant.customers.{lifecycle}", unit="{visit}", attributes=attributes
        )

    def seating_proposed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        place_kind: str,
        party_size: int,
    ) -> None:
        attributes = {"place.kind": place_kind}
        self.event(
            "restaurant.seating.proposed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={**attributes, "restaurant.party.size": party_size},
        )
        self.count("restaurant.seating.proposals", unit="{proposal}", attributes=attributes)
        self.adjust(
            "restaurant.seating.pending_holds", 1, unit="{hold}", attributes=attributes
        )

    def seating_decided(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        place_kind: str,
        party_size: int,
        outcome: str,
        time_to_seat_seconds: float | None = None,
    ) -> None:
        attributes = {"place.kind": place_kind}
        event_name = f"restaurant.seating.{outcome}"
        self.event(
            event_name,
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={**attributes, "restaurant.party.size": party_size},
        )
        self.adjust(
            "restaurant.seating.pending_holds", -1, unit="{hold}", attributes=attributes
        )
        metric = {
            "confirmed": "confirmations",
            "rejected": "rejections",
            "expired": "expirations",
        }.get(outcome)
        if metric:
            self.count(
                f"restaurant.seating.{metric}",
                unit="{decision}",
                attributes=attributes,
            )
        if outcome == "confirmed":
            self.count("restaurant.guests.arrived", party_size, unit="{guest}")
            self.count(
                "restaurant.guests.seated",
                party_size,
                unit="{guest}",
                attributes=attributes,
            )
            self.record("restaurant.party.size", party_size, unit="{guest}")
            self.adjust(
                "restaurant.seating.active_visits",
                1,
                unit="{visit}",
                attributes=attributes,
            )
            self.adjust(
                "restaurant.seating.occupied_guests",
                party_size,
                unit="{guest}",
                attributes=attributes,
            )
            self.adjust(
                "restaurant.seating.occupied_places",
                1,
                unit="{place}",
                attributes=attributes,
            )
            if time_to_seat_seconds is not None:
                self.record(
                    "restaurant.visit.time_to_seat",
                    time_to_seat_seconds,
                    unit="s",
                    attributes=attributes,
                )

    def seating_released(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        place_kind: str,
        party_size: int,
    ) -> None:
        attributes = {"place.kind": place_kind}
        self.event(
            "restaurant.seating.released",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={**attributes, "restaurant.party.size": party_size},
        )
        self.count("restaurant.seating.releases", unit="{release}", attributes=attributes)
        self.adjust(
            "restaurant.seating.active_visits", -1, unit="{visit}", attributes=attributes
        )
        self.adjust(
            "restaurant.seating.occupied_guests",
            -party_size,
            unit="{guest}",
            attributes=attributes,
        )
        self.adjust(
            "restaurant.seating.occupied_places", -1, unit="{place}", attributes=attributes
        )

    def turn_completed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        turn_count: int,
        draft_lines_before: int,
        draft_lines_after: int,
        memory_items_before: int,
        memory_items_after: int,
        memory_intent: str,
        remembered_memory_count: int,
    ) -> None:
        self.event(
            "restaurant.conversation.turn_completed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "restaurant.conversation.turn_count": turn_count,
                "restaurant.order.draft_lines": draft_lines_after,
            },
        )
        self.record(
            "restaurant.conversation.turns_per_visit", turn_count, unit="{turn}"
        )
        if draft_lines_before and draft_lines_after != draft_lines_before:
            self.count("restaurant.order.changes", unit="{change}")
        if draft_lines_after and draft_lines_before == draft_lines_after:
            self.count("restaurant.order.repeats", unit="{turn}")
        memory_delta = memory_items_after - memory_items_before
        if memory_delta > 0:
            self.count("restaurant.memory.updated", memory_delta, unit="{memory}")
            self.event(
                "restaurant.memory.updated",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={"restaurant.memory.delta": memory_delta},
            )
        if remembered_memory_count:
            self.count(
                "restaurant.memory.available",
                remembered_memory_count,
                unit="{memory}",
            )
        if memory_intent == "reuse_latest_order":
            self.count("restaurant.memory.reuse_requested", unit="{request}")
            applied = draft_lines_after > 0
            metric = "reuse_applied" if applied else "reuse_abandoned"
            self.count(f"restaurant.memory.{metric}", unit="{request}")
            self.event(
                f"restaurant.memory.{metric}",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={
                    "restaurant.memory.remembered_count": remembered_memory_count
                },
            )

    def turn_failed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        code: str,
    ) -> None:
        attributes = {"failure.component": "waiter", "failure.code": code}
        self.event(
            "restaurant.conversation.turn_failed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes=attributes,
        )
        self.count("restaurant.business.failures", unit="{failure}", attributes=attributes)

    def kitchen_completed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        verdict: str,
        requested_units: int,
        accepted: Iterable[tuple[str, str, int, int]],
        rejected: Iterable[tuple[str | None, int]],
        restriction_count: int,
        duration_seconds: float,
        party_size: int | None,
        first_order_seconds: float | None,
        turn_count: int,
    ) -> None:
        accepted_items = list(accepted)
        rejected_items = list(rejected)
        attributes = {"verdict": verdict}
        self.event(
            "restaurant.order.submitted",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "order.component": "kitchen",
                "restaurant.order.requested_units": requested_units,
                "restaurant.order.has_restrictions": restriction_count > 0,
            },
        )
        self.event(
            "restaurant.kitchen.completed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                **attributes,
                "restaurant.order.requested_units": requested_units,
                "restaurant.order.accepted_units": sum(item[2] for item in accepted_items),
                "restaurant.order.rejected_units": sum(item[1] for item in rejected_items),
                "restaurant.order.has_restrictions": restriction_count > 0,
                "restaurant.kitchen.duration_seconds": duration_seconds,
            },
        )
        self.count("restaurant.kitchen.orders", unit="{order}", attributes=attributes)
        self.count(
            "restaurant.items.requested",
            requested_units,
            unit="{item}",
            attributes={"item.type": "dish"},
        )
        self.record(
            "restaurant.kitchen.order_duration", duration_seconds, unit="s", attributes=attributes
        )
        self.record(
            "restaurant.kitchen.lines_per_order",
            len(accepted_items) + len(rejected_items),
            unit="{line}",
        )
        self.record(
            "restaurant.kitchen.units_per_order", requested_units, unit="{item}"
        )
        self.record("restaurant.order.lines", len(accepted_items) + len(rejected_items), unit="{line}")
        self.record("restaurant.order.units", requested_units, unit="{item}")
        self.record(
            "restaurant.order.distinct_products",
            len({item[0] for item in accepted_items}),
            unit="{product}",
        )
        if party_size:
            self.record(
                "restaurant.order.units_per_guest",
                requested_units / party_size,
                unit="{item}",
            )
        if first_order_seconds is not None:
            self.record(
                "restaurant.visit.time_to_first_order",
                first_order_seconds,
                unit="s",
            )
            self.record(
                "restaurant.conversation.turns_to_order",
                turn_count,
                unit="{turn}",
            )
        if restriction_count:
            self.count(
                "restaurant.kitchen.orders_with_restrictions", unit="{order}"
            )
        for carta_id, station, quantity, adaptation_count in accepted_items:
            item_attributes = {
                "item.type": "dish",
                "carta_id": carta_id,
                "station": station,
            }
            self.event(
                "restaurant.item.accepted",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={**item_attributes, "restaurant.item.quantity": quantity},
            )
            self.count(
                "restaurant.items.accepted",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.kitchen.lines.accepted",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            if adaptation_count:
                self.count(
                    "restaurant.kitchen.adapted_items",
                    quantity,
                    unit="{item}",
                    attributes={"station": station},
                )
        for carta_id, quantity in rejected_items:
            item_attributes: dict[str, AttributeValue] = {
                "item.type": "dish",
                "reason.code": "rejected",
            }
            if carta_id:
                item_attributes["carta_id"] = carta_id
            self.event(
                "restaurant.item.rejected",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={**item_attributes, "restaurant.item.quantity": quantity},
            )
            self.count(
                "restaurant.items.rejected",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.kitchen.lines.rejected",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )

    def component_failed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        component: str,
        code: str,
    ) -> None:
        attributes = {"failure.component": component, "failure.code": code}
        self.event(
            f"restaurant.{component}.failed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes=attributes,
        )
        self.count(
            f"restaurant.{component}.failures", unit="{failure}", attributes=attributes
        )
        self.count("restaurant.business.failures", unit="{failure}", attributes=attributes)

    def bar_completed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        verdict: str,
        requested_units: int,
        served: Iterable[tuple[str, int]],
        rejected: Iterable[tuple[str | None, int, bool]],
        party_size: int | None,
        duration_seconds: float,
        first_order_seconds: float | None,
        turn_count: int,
        first_service: bool,
        first_service_seconds: float | None,
    ) -> None:
        served_items = list(served)
        rejected_items = list(rejected)
        attributes = {"verdict": verdict}
        self.event(
            "restaurant.order.submitted",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "order.component": "bar",
                "restaurant.order.requested_units": requested_units,
            },
        )
        self.event(
            "restaurant.bar.completed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                **attributes,
                "restaurant.bar.requested_units": requested_units,
                "restaurant.bar.served_units": sum(item[1] for item in served_items),
                "restaurant.bar.rejected_units": sum(item[1] for item in rejected_items),
                "restaurant.bar.duration_seconds": duration_seconds,
            },
        )
        self.count("restaurant.bar.rounds", unit="{round}", attributes=attributes)
        self.count(
            "restaurant.items.requested",
            requested_units,
            unit="{item}",
            attributes={"item.type": "drink"},
        )
        self.record("restaurant.bar.round_duration", duration_seconds, unit="s")
        self.record("restaurant.bar.drinks_per_round", requested_units, unit="{drink}")
        if party_size:
            self.record(
                "restaurant.bar.drinks_per_guest",
                requested_units / party_size,
                unit="{drink}",
            )
        if first_order_seconds is not None:
            self.record(
                "restaurant.visit.time_to_first_order",
                first_order_seconds,
                unit="s",
            )
            self.record(
                "restaurant.conversation.turns_to_order",
                turn_count,
                unit="{turn}",
            )
        if first_service and party_size:
            self.count("restaurant.guests.served", party_size, unit="{guest}")
            if first_service_seconds is not None:
                self.record(
                    "restaurant.visit.time_to_first_service",
                    first_service_seconds,
                    unit="s",
                )
        for carta_id, quantity in served_items:
            item_attributes = {"item.type": "drink", "carta_id": carta_id}
            self.event(
                "restaurant.item.served",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={**item_attributes, "restaurant.item.quantity": quantity},
            )
            self.count(
                "restaurant.items.accepted",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.items.served",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.bar.drinks.served",
                quantity,
                unit="{drink}",
                attributes=item_attributes,
            )
        for carta_id, quantity, ambiguous in rejected_items:
            item_attributes: dict[str, AttributeValue] = {
                "item.type": "drink",
                "reason.code": "ambiguous" if ambiguous else "rejected",
            }
            if carta_id:
                item_attributes["carta_id"] = carta_id
            self.event(
                "restaurant.item.rejected",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={**item_attributes, "restaurant.item.quantity": quantity},
            )
            self.count(
                "restaurant.items.rejected",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.bar.drinks.rejected",
                quantity,
                unit="{drink}",
                attributes=item_attributes,
            )
            if ambiguous:
                self.count(
                    "restaurant.bar.ambiguous_requests", unit="{request}"
                )

    def dishes_served(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        items: Iterable[tuple[str, str, int]],
        ready_to_served_seconds: float,
        party_size: int | None,
        first_service: bool,
        first_service_seconds: float | None,
    ) -> None:
        served_items = list(items)
        units = sum(item[2] for item in served_items)
        self.event(
            "restaurant.item.served",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={"item.type": "dish", "restaurant.item.units": units},
        )
        if first_service and party_size:
            self.count("restaurant.guests.served", party_size, unit="{guest}")
            if first_service_seconds is not None:
                self.record(
                    "restaurant.visit.time_to_first_service",
                    first_service_seconds,
                    unit="s",
                )
        self.record(
            "restaurant.kitchen.ready_to_served_duration",
            ready_to_served_seconds,
            unit="s",
        )
        for carta_id, station, quantity in served_items:
            item_attributes = {
                "item.type": "dish",
                "carta_id": carta_id,
                "station": station,
            }
            self.event(
                "restaurant.item.served",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={**item_attributes, "restaurant.item.quantity": quantity},
            )
            self.count(
                "restaurant.items.served",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )

    def bill_presented(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        total: Decimal,
        line_count: int,
        units: int,
        party_size: int | None,
        superseded: bool,
    ) -> None:
        self.event(
            "restaurant.bill.presented",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "restaurant.bill.total": float(total),
                "restaurant.bill.lines": line_count,
                "restaurant.bill.units": units,
                "restaurant.bill.superseded": superseded,
            },
        )
        self.count("restaurant.bills.presented", unit="{bill}")
        self.record("restaurant.bill.total", total, unit="EUR")
        self.record("restaurant.bill.lines", line_count, unit="{line}")
        self.record("restaurant.bill.units", units, unit="{item}")
        if party_size:
            self.record(
                "restaurant.bill.per_guest", total / party_size, unit="EUR"
            )
        if superseded:
            self.bill_superseded(
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                reason="new_items",
            )

    def bill_superseded(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        reason: str,
    ) -> None:
        attributes = {"reason": reason}
        self.event(
            "restaurant.bill.superseded",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes=attributes,
        )
        self.count("restaurant.bills.superseded", unit="{bill}", attributes=attributes)
        self.count("restaurant.bill.reopened", unit="{bill}", attributes=attributes)

    def payment_completed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        method: str,
        amount: Decimal,
        party_size: int | None,
        lines: Iterable[tuple[str, str, int, Decimal]],
        payment_seconds: float,
        visit_seconds: float,
        turn_count: int,
    ) -> None:
        payment_attributes = {"payment.method": method, "currency": "EUR"}
        self.event(
            "restaurant.payment.completed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                **payment_attributes,
                "restaurant.payment.amount": float(amount),
                "restaurant.party.size": party_size or 0,
            },
        )
        self.event(
            "restaurant.visit.closed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "restaurant.visit.paid": True,
                "restaurant.payment.amount": float(amount),
                "restaurant.party.size": party_size or 0,
            },
        )
        self.count(
            "restaurant.payments.completed",
            unit="{payment}",
            attributes=payment_attributes,
        )
        self.count(
            "restaurant.revenue",
            float(amount),
            unit="EUR",
            attributes=payment_attributes,
        )
        self.record(
            "restaurant.payment.duration",
            payment_seconds,
            unit="s",
            attributes=payment_attributes,
        )
        self.record("restaurant.visit.duration", visit_seconds, unit="s")
        self.record(
            "restaurant.conversation.turns_to_payment", turn_count, unit="{turn}"
        )
        self.count("restaurant.visits.completed", unit="{visit}")
        if party_size:
            self.count("restaurant.guests.paid", party_size, unit="{guest}")
        for item_type, carta_id, quantity, line_total in lines:
            item_attributes = {
                "item.type": item_type,
                "carta_id": carta_id,
                "currency": "EUR",
            }
            self.event(
                "restaurant.item.paid",
                visit_id=visit_id,
                conversation_id=conversation_id,
                occurred_at=occurred_at,
                attributes={
                    **item_attributes,
                    "restaurant.item.quantity": quantity,
                    "restaurant.item.line_total": float(line_total),
                },
            )
            self.count(
                "restaurant.items.paid",
                quantity,
                unit="{item}",
                attributes=item_attributes,
            )
            self.count(
                "restaurant.product.revenue",
                float(line_total),
                unit="EUR",
                attributes=item_attributes,
            )

    def payment_failed(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        method: str,
        code: str,
    ) -> None:
        attributes = {
            "payment.method": method,
            "failure.component": "cashier",
            "failure.code": code,
        }
        self.event(
            "restaurant.payment.failed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes=attributes,
        )
        self.count(
            "restaurant.payments.failed", unit="{payment}", attributes=attributes
        )
        self.count("restaurant.business.failures", unit="{failure}", attributes=attributes)

    def visit_abandoned(
        self,
        *,
        visit_id: str,
        conversation_id: str,
        occurred_at: datetime,
        party_size: int | None,
        had_served_items: bool,
        visit_seconds: float,
    ) -> None:
        self.event(
            "restaurant.visit.closed",
            visit_id=visit_id,
            conversation_id=conversation_id,
            occurred_at=occurred_at,
            attributes={
                "restaurant.visit.paid": False,
                "restaurant.visit.had_served_items": had_served_items,
                "restaurant.party.size": party_size or 0,
            },
        )
        self.count("restaurant.visits.abandoned", unit="{visit}")
        self.count("restaurant.visit.no_payment", unit="{visit}")
        if not had_served_items:
            self.count("restaurant.visit.no_order", unit="{visit}")
        self.record("restaurant.visit.duration", visit_seconds, unit="s")


business_telemetry = BusinessTelemetry()

__all__ = ["BusinessTelemetry", "business_telemetry"]
