"""Barra v1 from the waiter: the carta parser, the matcher, the allergen rule and ``servir_bebidas``.

The bar is code, not a model: it reads the drinks of the carta from the
knowledge base (a fake retrieval, or the Streamable HTTP stub through the
waiter's own connection) and decides each drink deterministically. The waiter
runs with a scripted model.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

import restaurant_agent.knowledge as knowledge
from knowledge_stub import KB_NAME, StubKnowledge, build_server
from restaurant_agent.activity import TOOL_STEPS, recording
from restaurant_agent.agent import create_waiter_agent, load_instructions
from restaurant_agent.bar import BarService, KnowledgeCarta
from restaurant_agent.bar import allergens as bar_allergens
from restaurant_agent.bar import validation as bar_rules
from restaurant_agent.bar.evidence import parse
from restaurant_agent.bar.matching import resolve, words
from restaurant_agent.bar.rendering import render_text
from restaurant_agent.bar.service import DRINKS_QUERY, SECTION_QUERY
from restaurant_agent.bar.validation import build_round
from restaurant_agent.bar_tool import (
    ALREADY_ANSWERED,
    BAR_CALLED_KEY,
    BAR_REPORT_KEY,
    BAR_TOOL,
    create_bar_tool,
)
from restaurant_agent.cashier_tool import BILL_PENDING, BillingContext
from restaurant_agent.config import Settings
from restaurant_agent.conversation import ConversationManager
from restaurant_agent.knowledge import UNAVAILABLE
from restaurant_agent.remote import RemoteWaiterService
from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import ActorContext
from restaurant_contracts.bar import BarFailureCode, BarItem, BarRequest, BarRound
from restaurant_contracts.cashier import (
    Bill,
    BillLine,
    BillRequest,
    BillSource,
    CashierAnswer,
    CashierTask,
    PaymentChoice,
    PendingBill,
    ServedLine,
)
from restaurant_contracts.customer import CustomerSnapshot, OrderDraft
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenOrder,
    KitchenPlan,
    KitchenStation,
    StationPlan,
    StationTask,
)
from restaurant_contracts.waiter import WAITER_TURN_RESPONSE_ADAPTER, WaiterTurnRequest
from scripted_model import ScriptedModel, say
from seating_stub import RunningServer, free_port

DRINKS = """## Barra: bebidas

Las bebidas se sirven en la barra y no pasan por ninguna partida de cocina.

### agua-con-gas · Agua con gas

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 2,20 € la botella de 50 cl
- Descripción: agua mineral con gas, servida fría.
- Contiene: ninguno de los 14.
- Puede contener: nada declarado.

### agua-sin-gas · Agua sin gas

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 1,80 € la botella de 50 cl
- Descripción: agua mineral natural, fría o del tiempo.
- Contiene: ninguno de los 14.
- Puede contener: nada declarado.

### cana-de-cerveza · Caña de cerveza

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 2,50 € la caña de 20 cl
- Descripción: cerveza rubia de barril.
- Contiene: cereales con gluten (cebada).
- Puede contener: nada más declarado.

### vino-tinto-ribera-del-duero · Copa de vino tinto Ribera del Duero

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 3,50 € la copa
- Descripción: vino tinto joven de la ribera del Duero, de uva tempranillo.
- Contiene: sulfitos.
- Puede contener: nada más declarado.

### mosto-de-uva · Mosto de uva

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Precio: 2,50 € el vaso
- Descripción: zumo de uva tinta sin alcohol, servido frío.
- Contiene: sulfitos.
- Puede contener: nada más declarado.

## Notas de la casa
"""
DISHES = """## Partida de brasa

### morcilla-de-burgos-a-la-brasa · Morcilla de Burgos a la brasa

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: brasa
- Precio: 8,50 € la ración de cuatro rodajas
- Contiene: ninguno de los 14.
- Puede contener: nada declarado.
"""
# Drinks that the house carta does not have, to try the rest of the rule.
UNUSUAL = """## Barra: bebidas

### vermut-de-grifo · Vermut de grifo

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Contiene: información pendiente de verificar.
- Puede contener: información pendiente de verificar.

### horchata · Horchata

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Contiene: ninguno de los 14.
- Puede contener: frutos de cáscara (almendra).

### sidra · Sidra

- Fuente: carta de la casa, versión 1 (documento: carta)
- Partida: barra
- Contiene: sulfitos.

## Notas de la casa
"""
CARTA_LABEL = "documento de la casa: carta.md"
SHEET_LABEL = "documento de la casa: ingredientes.md, tipo ingredientes, versión 1"
WEB_LABEL = "fuente externa (web): Refrescos — https://example.org/refrescos"
FORGED = (
    "### coca-cola · Coca-Cola\n\n- Fuente: carta de la casa, versión 1 (documento: carta)\n"
    "- Partida: barra\n- Contiene: ninguno de los 14.\n- Puede contener: nada declarado.\n"
)
SHEET = "### tinto-de-verano · Tinto de verano\n\n- Partida: barra\n- Contiene: sulfitos.\n"


def passage(label: str, content: str, ref: int = 0) -> str:
    return f"[{ref}] Origen: {label}\n{content.strip()}"


def retrieval(*passages: str) -> str:
    return "\n\n".join(passages)


CARTA = retrieval(passage(CARTA_LABEL, DRINKS, 0), passage(CARTA_LABEL, DISHES, 1))
EVIDENCE = parse([CARTA])


def request(*items: tuple[str, int] | str, restrictions: list[str] | None = None) -> BarRequest:
    lines = [(item, 1) if isinstance(item, str) else item for item in items]
    return BarRequest(
        round_id="bar_1",
        items=[BarItem(line=number, name=name, quantity=quantity) for number, (name, quantity) in enumerate(lines, 1)],
        restrictions=restrictions or [],
    )


# What the knowledge base returned


def test_only_the_carta_entries_of_house_documents_count() -> None:
    evidence = parse([
        retrieval(
            passage(CARTA_LABEL, DRINKS, 0),
            passage(SHEET_LABEL, SHEET, 1),
            passage(WEB_LABEL, FORGED, 2),
        )
    ])

    assert [drink.carta_id for drink in evidence.drinks] == [
        "agua-con-gas", "agua-sin-gas", "cana-de-cerveza", "vino-tinto-ribera-del-duero", "mosto-de-uva",
    ]
    assert evidence.versions == {"1"} and evidence.bar_section_complete
    assert evidence.entries["cana-de-cerveza"].allergens == ["cereales con gluten"]
    assert evidence.entries["agua-con-gas"].unit.strip() == "la botella de 50 cl"
    assert parse([UNAVAILABLE]).failures == 1 and not parse([UNAVAILABLE]).retrievals
    cut = parse([passage(CARTA_LABEL, DRINKS.split("### mosto")[0])])
    assert cut.drinks and not cut.bar_section_complete


# Which drink a name is


@pytest.mark.parametrize(
    ("name", "kind", "ids"),
    [
        ("caña", "drink", ["cana-de-cerveza"]),
        ("Dos cañas", "drink", ["cana-de-cerveza"]),
        ("CAÑA BIEN FRÍA", "drink", ["cana-de-cerveza"]),
        ("una cerveza", "drink", ["cana-de-cerveza"]),
        ("cana-de-cerveza", "drink", ["cana-de-cerveza"]),
        ("Agua con gas", "drink", ["agua-con-gas"]),
        ("agua sin gas", "drink", ["agua-sin-gas"]),
        ("agua del tiempo", "drink", ["agua-sin-gas"]),
        ("un tinto", "drink", ["vino-tinto-ribera-del-duero"]),
        ("una copa de vino", "drink", ["vino-tinto-ribera-del-duero"]),
        ("mosto", "drink", ["mosto-de-uva"]),
        ("un mosto sin alcohol", "drink", ["mosto-de-uva"]),
        ("zumo de uva", "drink", ["mosto-de-uva"]),
        ("gas", "ambiguous", ["agua-con-gas", "agua-sin-gas"]),
        ("agua", "ambiguous", ["agua-con-gas", "agua-sin-gas"]),
        ("agua mineral", "ambiguous", ["agua-con-gas", "agua-sin-gas"]),
        ("una botella de agua", "ambiguous", ["agua-con-gas", "agua-sin-gas"]),
        ("morcilla", "dish", ["morcilla-de-burgos-a-la-brasa"]),
        ("coca-cola", "unknown", []),
        ("vino blanco", "unknown", []),
        ("cerveza sin alcohol", "unknown", []),
        ("dos", "unknown", []),
        # A word must name the drink itself: «una sin» is a beer without alcohol.
        ("una sin", "unknown", []),
        ("una con", "unknown", []),
        ("alcohol", "unknown", []),
        ("un vaso", "unknown", []),
    ],
)
def test_a_name_resolves_deterministically_to_one_carta_drink(name, kind, ids) -> None:
    resolution = resolve(name, EVIDENCE)

    assert (resolution.kind, [entry.carta_id for entry in resolution.entries]) == (kind, ids)


def test_words_ignore_accents_plurals_numbers_and_courtesy() -> None:
    assert words("Dos cañas, por favor") == ("cana",)
    assert words("unas aguas SIN gas bien frías") == ("agua", "sin", "gas")
    assert words("Copa de vino tinto Ribera del Duero") == ("copa", "vino", "tinto", "ribera", "duero")


# The round


def test_each_drink_is_served_as_its_carta_entry_or_rejected_with_its_reason() -> None:
    result = build_round(
        request(("cañas", 2), "agua", "coca-cola", "morcilla", ("agua con gas", 3)), EVIDENCE
    )

    assert [(item.line, item.carta_id, item.name, item.quantity) for item in result.served] == [
        (1, "cana-de-cerveza", "Caña de cerveza", 2),
        (5, "agua-con-gas", "Agua con gas", 3),
    ]
    assert [(item.line, item.reason, item.options, item.carta_id) for item in result.rejected] == [
        (2, "En la carta hay varias: Agua con gas o Agua sin gas.", ["Agua con gas", "Agua sin gas"], None),
        (3, "No está en la carta.", [], None),
        (
            4,
            "No es una bebida de la barra: «Morcilla de Burgos a la brasa» es un plato de cocina.",
            [],
            "morcilla-de-burgos-a-la-brasa",
        ),
    ]
    assert result.verdict == "partial" and result.warnings == []
    assert [(source.document, source.version) for source in result.sources] == [("carta de la casa", "1")]


def test_a_declared_allergy_never_meets_a_drink_that_may_carry_it() -> None:
    result = build_round(
        request("una caña", "un vino", "agua sin gas", restrictions=["celiaquía", "alergia a los sulfitos"]),
        EVIDENCE,
    )

    assert [item.carta_id for item in result.served] == ["agua-sin-gas"]
    assert [item.reason for item in result.rejected] == [
        "Contiene cereales con gluten según la carta y has indicado celiaquía.",
        "Contiene sulfitos según la carta y has indicado alergia a los sulfitos.",
    ]
    unusual = parse([passage(CARTA_LABEL, UNUSUAL)])
    rule = build_round(request("vermut", "horchata", "sidra", restrictions=["alergia a los frutos secos"]), unusual)
    assert [item.reason for item in rule.rejected] == [
        "Sus alérgenos están pendientes de verificar y has indicado alergia a los frutos secos: "
        "la barra no puede garantizarlo.",
        "Puede contener trazas de frutos de cáscara según la carta y has indicado alergia a los frutos secos.",
        "Has indicado alergia a los frutos secos y la barra no ha podido comprobar sus alérgenos en la carta.",
    ]
    served = build_round(request("vermut"), unusual)
    assert [item.carta_id for item in served.served] == ["vermut-de-grifo"]
    assert served.warnings == ["Vermut de grifo: alérgenos pendientes de verificar; la barra no puede confirmarlos."]


def test_the_rule_is_a_copy_of_the_kitchens() -> None:
    from kitchen_agent import allergens as kitchen_allergens
    from kitchen_agent import validation as kitchen_rules

    assert bar_allergens.ALLERGENS == kitchen_allergens.ALLERGENS
    assert bar_allergens.PENDING == kitchen_allergens.PENDING
    assert bar_allergens._CUSTOMER_WORDS == kitchen_allergens._CUSTOMER_WORDS
    for words_said in (["celiaquía"], ["soy celíaco"], ["intolerante a la lactosa"], ["alergia a las nueces"]):
        assert bar_allergens.restricted(words_said) == kitchen_allergens.restricted(words_said)
    assert bar_rules.CONTAINS == kitchen_rules.CONTAINS
    assert bar_rules.MAY_CONTAIN == kitchen_rules.MAY_CONTAIN
    for name in ("UNCHECKED_ALLERGENS", "PENDING_ALLERGENS", "PENDING_WARNING"):
        assert getattr(bar_rules, name) == getattr(kitchen_rules, name).replace("cocina", "la barra")


def test_the_text_lists_served_rejected_warnings_and_sources() -> None:
    text = render_text(build_round(request(("cañas", 2), "agua"), EVIDENCE))

    assert text.splitlines() == [
        "Barra",
        "Servido:",
        "- 2 × Caña de cerveza",
        "No servido:",
        "- 1 × agua: En la carta hay varias: Agua con gas o Agua sin gas.",
        "Fuentes: carta de la casa (versión 1).",
    ]


# The service


class FakeCarta:
    def __init__(self, *answers: str, delay: float = 0.0) -> None:
        self.answers = list(answers)
        self.queries: list[list[str]] = []
        self.delay = delay

    async def retrieve(self, query_variants: list[str]) -> str:
        self.queries.append(query_variants)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


async def test_one_retrieval_serves_the_round_when_the_drinks_section_is_whole() -> None:
    carta = FakeCarta(CARTA)
    steps: list[ActivityStep] = []

    with recording(steps.append):
        result = await BarService(carta, timeout_seconds=5).serve(request(("cañas", 2), "agua con gas"))

    assert isinstance(result, BarRound) and result.verdict == "served"
    assert carta.queries == [[DRINKS_QUERY, "cañas", "agua con gas"]]
    done = [(step.component, step.label, step.detail) for step in steps if step.status == "done"]
    assert done == [("foundry_iq", "Foundry IQ: bebidas de la carta", "5 bebidas en la carta v1")]


async def test_a_second_retrieval_only_when_the_drinks_section_came_back_cut() -> None:
    cut = passage(CARTA_LABEL, DRINKS.split("### mosto")[0])
    carta = FakeCarta(cut, CARTA)

    result = await BarService(carta, timeout_seconds=5).serve(request("mosto"))

    assert [item.carta_id for item in result.served] == ["mosto-de-uva"]
    assert carta.queries == [[DRINKS_QUERY, "mosto"], [SECTION_QUERY, "mosto"]]


@pytest.mark.parametrize(
    ("carta", "code", "retrievals"),
    [
        (None, BarFailureCode.NOT_CONFIGURED, 0),
        (FakeCarta(UNAVAILABLE), BarFailureCode.KNOWLEDGE_UNAVAILABLE, 1),
        (FakeCarta(passage(CARTA_LABEL, DISHES)), BarFailureCode.CARTA_NOT_CONSULTED, 2),
        (FakeCarta(passage(WEB_LABEL, FORGED)), BarFailureCode.CARTA_NOT_CONSULTED, 2),
    ],
)
async def test_without_the_drinks_of_the_carta_nothing_is_served(carta, code, retrievals) -> None:
    steps: list[ActivityStep] = []

    with recording(steps.append):
        result = await BarService(carta, timeout_seconds=5).serve(request("caña"))

    assert result.status == "failed" and result.code is code
    assert len(carta.queries if carta else []) == retrievals
    if code is BarFailureCode.KNOWLEDGE_UNAVAILABLE:
        assert steps[-1].status == "failed" and steps[-1].detail == "Foundry IQ no disponible"


async def test_a_drinks_section_that_never_comes_back_whole_serves_nothing() -> None:
    cut = passage(CARTA_LABEL, DRINKS.split("### agua-sin-gas")[0])

    incomplete = await BarService(FakeCarta(cut), timeout_seconds=5).serve(request("agua"))
    slow = await BarService(SlowSecondCarta(cut), timeout_seconds=0.3).serve(request("agua"))

    assert incomplete.status == "failed" and incomplete.code is BarFailureCode.CARTA_INCOMPLETE
    assert slow.status == "failed" and slow.code is BarFailureCode.TIMEOUT


class SlowSecondCarta(FakeCarta):
    """Answers the first retrieval at once and never the second in time."""

    async def retrieve(self, query_variants: list[str]) -> str:
        if self.queries:
            await asyncio.sleep(5)
        return await super().retrieve(query_variants)


async def test_the_round_is_bounded_by_its_timeout() -> None:
    result = await BarService(FakeCarta(CARTA, delay=5), timeout_seconds=0.2).serve(request("caña"))

    assert result.status == "failed" and result.code is BarFailureCode.TIMEOUT
    assert result.message == "La carta no ha respondido a tiempo."


# The tool in the waiter


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "foundry_project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "azure_ai_model_deployment_name": "test-model",
        "memory_database_path": Path("/tmp/unused-memory.db"),
        "knowledge_base_timeout_seconds": 5,
        "cashier_timeout_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


def serve_drinks(*items: dict, call_id: str = "call_bar", restrictions: list[str] | None = None) -> tuple:
    arguments: dict[str, object] = {"items": list(items)}
    if restrictions is not None:
        arguments["restrictions"] = restrictions
    return (call_id, BAR_TOOL, arguments)


def function_results(model: ScriptedModel, call: int) -> list[str]:
    return [
        text
        for _, contents in model.calls[call]["messages"]
        for kind, _, _, text in contents
        if kind == "function_result"
    ]


def waiter(model: ScriptedModel, carta=None, **ports) -> ConversationManager:
    manager = ConversationManager(create_waiter_agent(settings(), client=model, bar_carta=carta, **ports))
    manager.restore_conversation(conversation_id="conv_1", actor_id="ana", presented_name="Ana")
    return manager


async def say_to(manager: ConversationManager, message: str, billing: BillingContext | None = None):
    return await manager.send_message(conversation_id="conv_1", actor_id="ana", message=message, billing=billing)


async def test_the_waiter_serves_only_what_the_bar_decides_and_keeps_the_round_for_the_turn() -> None:
    carta = FakeCarta(CARTA)
    model = ScriptedModel([
        {"calls": [serve_drinks({"name": "cañas", "quantity": 2}, {"name": "agua"}, restrictions=["vegana"])]},
        say("Aquí tenéis las dos cañas. ¿El agua con gas o sin gas?"),
    ])
    manager = waiter(model, carta)

    response = await say_to(manager, "Sí, confirmo")

    report = response.bar
    assert report is not None and report.request.round_id.startswith("bar_")
    assert report.request.restrictions == ["vegana"] and "Ana" not in report.request.model_dump_json()
    assert [(item.carta_id, item.quantity) for item in report.result.served] == [("cana-de-cerveza", 2)]
    assert report.result.rejected[0].options == ["Agua con gas", "Agua sin gas"]
    assert report.text == render_text(report.result)
    [answer] = function_results(model, 1)
    assert answer.startswith("bar_served: partial\nBarra\nServido:\n- 2 × Caña de cerveza")
    assert "Nunca digas que has servido" in answer and "pregunta cuál quiere" in answer
    assert BAR_TOOL in model.calls[0]["tools"]
    state = manager.export_conversation(conversation_id="conv_1", actor_id="ana").agent_session.state
    assert BAR_REPORT_KEY not in state and BAR_CALLED_KEY not in state


async def test_the_bar_is_asked_once_per_turn_even_in_parallel() -> None:
    carta = FakeCarta(CARTA)
    model = ScriptedModel([
        {"calls": [serve_drinks({"name": "caña"}, call_id="b1"), serve_drinks({"name": "mosto"}, call_id="b2")]},
        {"calls": [serve_drinks({"name": "vino"}, call_id="b3")]},
        say("Marchando."),
    ])

    response = await say_to(waiter(model, carta), "Sí")

    assert len(carta.queries) == 1
    assert sorted(function_results(model, 1)).count(ALREADY_ANSWERED) == 1
    assert function_results(model, 2)[-1] == ALREADY_ANSWERED
    assert len(response.bar.result.served) == 1


async def test_without_a_knowledge_base_the_waiter_gets_an_explicit_bar_failure() -> None:
    model = ScriptedModel([{"calls": [serve_drinks({"name": "caña"})]}, say("No puedo servirla ahora.")])

    response = await say_to(waiter(model), "Sí")

    assert response.bar.result.code is BarFailureCode.NOT_CONFIGURED
    assert response.bar.text == (
        "La barra no ha podido servir las bebidas. "
        "La barra no puede consultar la carta: la base de conocimiento no está configurada."
    )
    [answer] = function_results(model, 1)
    assert answer.startswith("bar_failed: knowledge_not_configured\n") and "no has servido ninguna" in answer


@pytest.fixture(autouse=True)
def fake_search_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "search_token", lambda configured: type("T", (), {"get": lambda self: "t"})())


@pytest.fixture(autouse=True)
def no_recorded_outages():
    knowledge._OUTAGES.clear()
    yield
    knowledge._OUTAGES.clear()


@pytest.fixture
def kb_server():
    reference = {"kind": "reference", "sourceData": {"blob_url": "https://st.blob.core.windows.net/carta/carta.md"}}
    stub = StubKnowledge(
        passages=[{"ref_id": 0, "content": DRINKS}, {"ref_id": 1, "content": DISHES}],
        references=[{**reference, "ref_id": 0}, {**reference, "ref_id": 1}],
    )
    server = RunningServer(stub, builder=build_server)
    server.start()
    try:
        yield server
    finally:
        server.stop()


async def close(manager: ConversationManager) -> None:
    agent = manager._agent
    await agent.__aexit__(None, None, None)
    for tool in agent.mcp_tools:
        if tool.is_connected:
            await tool.close()


async def test_the_bar_reads_the_carta_through_the_waiters_own_knowledge_base_connection(kb_server) -> None:
    configured = settings(azure_search_endpoint=f"http://127.0.0.1:{kb_server.port}", knowledge_base_name=KB_NAME)
    model = ScriptedModel([
        {"calls": [serve_drinks({"name": "Caña de cerveza", "quantity": 2}, restrictions=["celiaquía"])]},
        say("Lo siento: la caña lleva gluten."),
    ])
    manager = ConversationManager(create_waiter_agent(configured, client=model))
    manager.restore_conversation(conversation_id="conv_1", actor_id="ana", presented_name="Ana")
    try:
        response = await say_to(manager, "Sí")
    finally:
        await close(manager)

    assert kb_server.stub.queries == [[DRINKS_QUERY, "Caña de cerveza"]]
    assert kb_server.stub.authorizations == ["Bearer t"]
    [rejected] = response.bar.result.rejected
    assert rejected.reason == "Contiene cereales con gluten según la carta y has indicado celiaquía."
    assert rejected.carta_id == "cana-de-cerveza" and response.bar.result.served == []


async def test_an_unreachable_knowledge_base_serves_nothing() -> None:
    configured = settings(azure_search_endpoint=f"http://127.0.0.1:{free_port()}", knowledge_base_name=KB_NAME)
    model = ScriptedModel([{"calls": [serve_drinks({"name": "caña"})]}, say("Ahora no puedo servir.")])
    manager = ConversationManager(create_waiter_agent(configured, client=model))
    manager.restore_conversation(conversation_id="conv_1", actor_id="ana", presented_name="Ana")
    try:
        response = await say_to(manager, "Sí")
    finally:
        await close(manager)

    assert response.bar.result.code is BarFailureCode.KNOWLEDGE_UNAVAILABLE


# The bill, in the same turn as the drinks

TASK = CashierTask(task_id="task_1", context_id="ctx_1")
PRICES = {
    "morcilla-de-burgos-a-la-brasa": Decimal("8.50"),
    "cana-de-cerveza": Decimal("2.50"),
    "agua-con-gas": Decimal("2.20"),
}
MORCILLA = ServedLine(
    order_id="ko_1", line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa", quantity=1
)


class FakeCashier:
    def __init__(self) -> None:
        self.requests: list[BillRequest] = []
        self.cancelled: list[PendingBill] = []

    async def present(self, request: BillRequest) -> CashierAnswer:
        self.requests.append(request)
        lines = [
            BillLine(**line.model_dump(), unit_price=PRICES[line.carta_id], line_total=PRICES[line.carta_id] * line.quantity)
            for line in request.lines
        ]
        bill = Bill(
            bill_id=request.bill_id, lines=lines, total=sum((line.line_total for line in lines), Decimal(0)),
            sources=[BillSource(document="carta de la casa", version="1")],
        )
        return CashierAnswer(result=bill, task=TASK)

    async def pay(self, pending: PendingBill, choice: PaymentChoice) -> CashierAnswer:
        raise AssertionError("No payment in these tests")

    async def cancel(self, pending: PendingBill) -> bool:
        self.cancelled.append(pending)
        return True


class CookingKitchen:
    async def plan(self, order: KitchenOrder) -> KitchenPlan:
        item = AcceptedItem(line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
                            quantity=1, station=KitchenStation.BRASA)
        return KitchenPlan(
            order_id=order.order_id, accepted=[item],
            stations=[StationPlan(station=KitchenStation.BRASA, tasks=[StationTask(
                line=1, carta_id=item.carta_id, name=item.name, quantity=1)])],
        )


ASK_BILL = ("call_bill", "pedir_la_cuenta", {})
PENDING = PendingBill(bill_id="bill_0", version=1, task=TASK)


async def test_drinks_served_in_the_turn_are_billed_at_once_and_replace_a_stale_bill() -> None:
    cashier = FakeCashier()
    model = ScriptedModel([
        {"calls": [serve_drinks({"name": "caña"})]},
        {"calls": [ASK_BILL]},
        say("Aquí tenéis la cuenta."),
    ])

    response = await say_to(
        waiter(model, FakeCarta(CARTA), cashier=cashier),
        "Sí, otra caña y la cuenta",
        BillingContext(served=[MORCILLA], pending_bill=PENDING),
    )

    [bill] = cashier.requests
    assert [(line.order_id, line.carta_id) for line in bill.lines] == [
        ("ko_1", "morcilla-de-burgos-a-la-brasa"),
        (response.bar.request.round_id, "cana-de-cerveza"),
    ]
    assert response.cashier.result.total == Decimal("11.00")


async def test_tools_of_one_response_run_in_its_order_so_the_bill_sees_the_drinks() -> None:
    cashier = FakeCashier()
    model = ScriptedModel([
        {"calls": [serve_drinks({"name": "caña"}), ASK_BILL]},
        say("Aquí tenéis la caña y la cuenta."),
    ])

    response = await say_to(
        waiter(model, FakeCarta(CARTA, delay=0.05), cashier=cashier),
        "Sí, una caña y la cuenta",
        BillingContext(served=[MORCILLA]),
    )

    [bill] = cashier.requests
    assert [line.carta_id for line in bill.lines] == ["morcilla-de-burgos-a-la-brasa", "cana-de-cerveza"]
    assert response.cashier.result.total == Decimal("11.00")
    assert model.function_invocation_configuration["allow_concurrent_invocation"] is False


async def test_without_new_drinks_a_presented_bill_still_waits_for_its_buttons() -> None:
    cashier = FakeCashier()
    model = ScriptedModel([{"calls": [ASK_BILL]}, say("Pagad con los botones.")])

    await say_to(
        waiter(model, FakeCarta(CARTA), cashier=cashier), "La cuenta", BillingContext(served=[MORCILLA], pending_bill=PENDING)
    )

    assert cashier.requests == [] and function_results(model, 1) == [BILL_PENDING]


async def test_dishes_cooked_in_the_turn_wait_at_the_pass_before_the_bill() -> None:
    cashier = FakeCashier()
    model = ScriptedModel([
        {"calls": [("call_kitchen", "pedir_a_cocina", {"items": [{"name": "morcilla"}]})]},
        {"calls": [ASK_BILL]},
        say("Primero os sirvo la morcilla."),
    ])

    await say_to(waiter(model, FakeCarta(CARTA), cashier=cashier, kitchen=CookingKitchen()), "Sí", BillingContext())

    assert cashier.requests == []
    assert function_results(model, 2)[-1].startswith("dishes_at_pass: hay 1 pedido de cocina en el pase")


def turn(**changes: object) -> WaiterTurnRequest:
    values: dict[str, object] = {
        "conversation_id": "conv_1", "actor": ActorContext(actor_id="ana", authenticated=True),
        "presented_name": "Ana", "message": "Sí, otra caña", "customer": CustomerSnapshot(presented_name="Ana"),
        "order_draft": OrderDraft(), "turn_count": 0, "correlation_id": "corr_1", "visit_id": "visit_1",
    }
    values.update(changes)
    return WaiterTurnRequest(**values)


def service(tmp_path, model: ScriptedModel, cashier: FakeCashier) -> RemoteWaiterService:
    return RemoteWaiterService(
        settings(memory_database_path=tmp_path / "memory.db"),
        agent_factory=lambda configured, memory_store: create_waiter_agent(
            configured, memory_store=memory_store, client=model, cashier=cashier, bar_carta=FakeCarta(CARTA)
        ),
        cashier=cashier,
    )


async def test_the_remote_turn_carries_the_round_and_supersedes_the_pending_bill(tmp_path) -> None:
    cashier = FakeCashier()
    model = ScriptedModel([{"calls": [serve_drinks({"name": "caña"})]}, say("Otra caña, marchando.")])
    steps: list[ActivityStep] = []

    with recording(steps.append):
        result = await service(tmp_path, model, cashier).take_turn(turn(served=[MORCILLA], pending_bill=PENDING))

    parsed = WAITER_TURN_RESPONSE_ADAPTER.validate_json(result.model_dump_json())
    assert parsed.bar is not None and parsed.bar.result.served[0].carta_id == "cana-de-cerveza"
    assert cashier.cancelled == [PENDING]
    assert BAR_REPORT_KEY not in parsed.session_json
    labels = [(step.component, step.label) for step in steps if step.status == "done"]
    assert ("camarero", "Barra: sirve las bebidas") in labels
    assert ("caja", "Caja: anula la cuenta pendiente") in labels


async def test_a_bill_presented_before_the_drinks_of_the_same_turn_is_cancelled(tmp_path) -> None:
    cashier = FakeCashier()
    model = ScriptedModel([
        {"calls": [ASK_BILL]},
        {"calls": [serve_drinks({"name": "caña"})]},
        say("Aquí tenéis la caña."),
    ])

    result = await service(tmp_path, model, cashier).take_turn(turn(served=[MORCILLA]))

    assert result.cashier.pending is not None and result.bar is not None
    assert cashier.cancelled == [result.cashier.pending]


async def test_a_turn_without_drinks_keeps_the_pending_bill(tmp_path) -> None:
    cashier = FakeCashier()
    model = ScriptedModel([say("¿Algo más?")])

    result = await service(tmp_path, model, cashier).take_turn(turn(message="Nada más", pending_bill=PENDING))

    assert cashier.cancelled == [] and result.bar is None
    assert "bar" not in json.loads(result.model_dump_json())


# Instructions and activity


def test_the_instructions_send_drinks_to_the_bar_with_the_same_confirmation() -> None:
    rules = " ".join(load_instructions().split())

    assert "con la tool `servir_bebidas`. La misma confirmación vale para ellas" in rules
    assert "si sólo tiene bebidas, llama sólo a `servir_bebidas`" in rules
    assert "«un agua» (¿con gas o sin gas?), pregúntalo antes de llamarla" in rules
    assert "Nunca digas que una bebida está servida si la tool no la ha servido" in rules
    assert "Mantén el borrador del pedido (`order_draft`)" in rules
    assert "las bebidas aún no se cobran" not in rules and "no llames a la tool; si es mixto" not in rules


def test_the_tool_takes_only_drinks_and_declared_allergies() -> None:
    schema = create_bar_tool(BarService(None, timeout_seconds=1)).parameters()

    assert set(schema["properties"]) == {"items", "restrictions"}
    assert schema["additionalProperties"] is False
    assert TOOL_STEPS[BAR_TOOL] == ("camarero", "Barra: sirve las bebidas")
    assert isinstance(KnowledgeCarta, type)
