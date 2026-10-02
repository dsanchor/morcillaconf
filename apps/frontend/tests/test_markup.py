import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.application import ChatMessage
from restaurant_contracts.kitchen import (
    AcceptedItem,
    KitchenFailure,
    KitchenFailureCode,
    KitchenOrder,
    KitchenOrderLine,
    KitchenPlan,
    KitchenReport,
    KitchenSource,
    KitchenStation,
    RejectedItem,
    StationPlan,
    StationTask,
)

from frontend.markup import (
    DOOR_HINT,
    KITCHEN_FAILED,
    KITCHEN_LABEL,
    Reveal,
    activity_markup,
    command_row_markup,
    conversation_markup,
    door_hint_markup,
    facade_markup,
    identity_markup,
    kitchen_row,
    plan_markup,
    simulated_markup,
    text_html,
)
from frontend.slash_commands import COMMAND_LIST
from frontend.stylesheets import base_stylesheet, stage_stylesheet
from frontend.visit import Card, ConversationView

AT = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)
GREETING = ChatMessage(
    message_id="msg_1",
    role="assistant",
    text="Hombre, Ana, ¿qué tal, maja? ¿Has venido sola o acompañada?",
    occurred_at=AT, command_event_id="cmd_arrive",
)
QUESTION = ChatMessage(
    message_id="msg_2", role="user", text='<b>hola</b> & "adiós"\n$5 :smile:',
    occurred_at=AT, command_event_id="cmd_message",
)
ORDER = KitchenOrder(
    order_id="ko_1",
    lines=[
        KitchenOrderLine(line=1, name="morcilla a la brasa"),
        KitchenOrderLine(line=2, name="croquetas", quantity=2, modifications=["sin cebolla"]),
    ],
    restrictions=["celiaquía"],
)
PLAN = KitchenPlan(
    order_id="ko_1",
    accepted=[
        AcceptedItem(
            line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
            quantity=1, station=KitchenStation.BRASA, adaptations=["sin pimiento"],
        )
    ],
    rejected=[
        RejectedItem(line=2, requested="croquetas, sin cebolla", quantity=2, reason="Llevan <gluten> & cebolla.")
    ],
    warnings=["La casa no ofrece platos certificados sin gluten."],
    stations=[
        StationPlan(
            station=KitchenStation.BRASA,
            tasks=[
                StationTask(
                    line=1, carta_id="morcilla-de-burgos-a-la-brasa", name="Morcilla de Burgos a la brasa",
                    quantity=1, steps=["Marcar a la brasa"], omit=["pimiento asado"], precautions=["Pinzas limpias"],
                )
            ],
        )
    ],
    sources=[KitchenSource(document="carta de la casa", version="1")],
)
KITCHEN = ChatMessage(
    message_id="msg_k", role="kitchen", text="Plan de cocina", occurred_at=AT, command_event_id="cmd_message",
    kitchen=KitchenReport(order=ORDER, result=PLAN, text="Plan de cocina"),
)
REPLY = ChatMessage(
    message_id="msg_r", role="assistant", text="Cocina acepta la morcilla.", occurred_at=AT,
    command_event_id="cmd_message",
)


def _parse(markup: str) -> ET.Element:
    return ET.fromstring(re.sub(r"<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', markup))


def _text(element: ET.Element) -> str:
    return "".join(element.itertext())


def test_text_is_escaped_for_raw_html_markdown_blocks() -> None:
    escaped = text_html('<script>x</script> & $1 :tada:\nfin')
    assert "<script>" not in escaped
    assert "$" not in escaped and ":" not in escaped
    assert escaped.endswith("<br/>fin")


def test_every_fragment_is_a_single_html_block() -> None:
    fragments = [
        facade_markup("cerrada"), facade_markup("llama", 1), facade_markup("abriendo"),
        door_hint_markup(), identity_markup("Ana"), command_row_markup("/new"),
        simulated_markup(), plan_markup("Ana", "llegando", entering=True),
        conversation_markup(ConversationView(messages=(GREETING, QUESTION), waiting=True)),
        kitchen_row(KITCHEN),
    ]
    for fragment in fragments:
        assert fragment.startswith("<div")
        assert "\n" not in fragment
        _parse(fragment)


def test_conversation_renders_escaped_bubbles_from_the_snapshot() -> None:
    root = _parse(conversation_markup(ConversationView(messages=(GREETING, QUESTION))))
    assert root.get("role") == "log"
    rows = root.findall(".//div[@class='msg camarero']") + root.findall(".//div[@class='msg cliente']")
    assert len(rows) == 2
    customer = root.find(".//div[@class='msg cliente']/div[@class='burbuja']")
    assert _text(customer) == '<b>hola</b> & "adiós"$5 :smile:'
    assert root.find(".//div[@class='msg camarero']/*[@class='icono']") is not None


def test_card_follows_its_message() -> None:
    card = Card(after_message_id="msg_1", title="Aviso")
    markup = conversation_markup(ConversationView(messages=(GREETING, QUESTION), cards=(card,)))
    root = _parse(markup)
    hilo = root.find("div[@class='hilo']")
    assert [child.get("class") for child in hilo] == ["msg camarero", "tarjeta", "msg cliente"]
    assert _text(hilo.find("div[@class='tarjeta']/p")) == "Aviso"


def test_sending_provisional_and_typing_states_are_visible() -> None:
    view = ConversationView(messages=(GREETING,), outgoing="Hola", waiting=True)
    root = _parse(conversation_markup(view))
    assert root.find(".//div[@class='msg cliente enviando']") is not None
    typing = root.find(".//div[@class='burbuja escribiendo']")
    assert typing.get("aria-label") == "El camarero está escribiendo"
    view = ConversationView(messages=(GREETING,), provisional=(("msg_3", "Tomo"),))
    root = _parse(conversation_markup(view))
    assert _text(root.find(".//div[@class='msg camarero provisional']")) == "Tomo"
    assert root.find(".//div[@class='burbuja escribiendo']") is None


def test_greeting_reveal_stacks_typing_and_text() -> None:
    markup = conversation_markup(ConversationView(messages=(GREETING,)), reveal=Reveal("msg_1", "door"))
    root = _parse(markup)
    stack = root.find(".//div[@class='pila revelar-door']")
    assert [row.get("class") for row in stack] == ["msg camarero aguarda", "msg camarero saludo"]
    assert stack[0].get("aria-hidden") == "true"
    assert _text(stack[1]) == GREETING.text


def test_sidebar_fragments_escape_and_dim_arguments() -> None:
    assert _text(_parse(identity_markup("<Ana>"))) == "<Ana>"
    row = _parse(command_row_markup("/acción <id> <texto>"))
    assert [arg.text for arg in row.iter("span")] == ["<id>", "<texto>"]
    assert _text(row) == "/acción <id> <texto>"


def test_door_hint_and_simulated_label() -> None:
    hint = _parse(door_hint_markup())
    assert hint.find("p").get("role") == "alert" and _text(hint) == DOOR_HINT
    assert _text(_parse(simulated_markup())) == "Camarero simulado"


def test_facade_markup_alternates_to_rattle_again() -> None:
    assert facade_markup("llama", 1) != facade_markup("llama", 2)
    assert 'class="escena llama"' in facade_markup("llama", 1)


def test_stylesheets_are_safe_inside_a_sanitized_style_tag() -> None:
    css = base_stylesheet()
    assert "@import" in css.splitlines()[1]
    for stage in ("outside", "opening", "inside"):
        css += stage_stylesheet(stage, knocked=True)
    assert "<" not in css
    assert ".st-key-ventana" in css and ".st-key-umbral" in css


def test_sidebar_is_sized_for_the_longest_command_on_one_line() -> None:
    css = base_stylesheet()
    longest = max(len(command) for command in COMMAND_LIST)
    width = int(re.search(r"--lateral: (\d+)px;", css).group(1))
    assert f"({longest} * .61)" in css
    assert longest * 0.61 * 16 + 2 * 12 + 2 * 18 <= width
    assert "white-space: nowrap" in css


def test_the_cooked_dishes_are_their_own_bubble_between_the_order_and_the_reply() -> None:
    root = _parse(conversation_markup(ConversationView(messages=(GREETING, QUESTION, KITCHEN, REPLY))))
    hilo = root.find("div[@class='hilo']")
    assert [child.get("class") for child in hilo] == [
        "msg camarero", "msg cliente", "msg cocina", "msg camarero",
    ]
    chef = hilo[2]
    assert chef.find("*[@class='icono']") is not None
    bubble = chef.find("div[@class='burbuja plan-cocina']")
    assert (bubble.get("role"), bubble.get("aria-label")) == ("group", KITCHEN_LABEL)
    lines = [_text(element) for element in bubble if element.tag == "p"]
    assert lines == [
        "Los platos posibles ya están cocinados.", "Listo para servir", "Rechazado", "Avisos", "Partidas",
        "Fuentes: carta de la casa (versión 1)",
    ]
    items = [_text(item) for item in bubble.iter("li")]
    assert items == [
        "1 × Morcilla de Burgos a la brasaBrasa · sin pimiento · alérgenos: ninguno de los 14",
        "2 × croquetas, sin cebollaLlevan <gluten> & cebolla.",
        "La casa no ofrece platos certificados sin gluten.",
        "Brasa1 × Morcilla de Burgos a la brasa. Marcar a la brasa. Omitir: pimiento asado. Precauciones: Pinzas limpias.",
    ]


def test_the_activity_panel_shows_each_component_live_and_escaped() -> None:
    order = QUESTION.model_copy(
        update={
            "activity": [
                ActivityStep(step_id="m", component="memoria", label="Lee la memoria del cliente", detail="<alergia> & leche", duration_ms=4),
                ActivityStep(step_id="c", component="cocina", label="A2A: envía la comanda a cocina", status="running"),
                ActivityStep(step_id="p", component="especialista", label="Parrilla revisa sus tareas", detail="OK", duration_ms=2500),
            ]
        }
    )
    root = _parse(activity_markup([GREETING, order]))
    steps = root.findall(".//li")
    assert [step.get("class") for step in steps] == [
        "capo-paso memoria done", "capo-paso cocina running", "capo-paso especialista done",
    ]
    assert _text(steps[0]) == "Memoria4 msLee la memoria del cliente<alergia> & leche"
    assert "en curso" in _text(steps[1]) and "2,5 s" in _text(steps[2])
    assert root.get("aria-label") == "Bajo el capó"
    assert "Escribe al camarero" in _text(_parse(activity_markup([GREETING])))


def test_the_kitchen_scene_matches_the_floor_plan_and_shows_the_agents() -> None:
    active = _parse(plan_markup("Ana", "atendiendo", kitchen_active=True))
    scenes = [element for element in active.iter() if element.tag.endswith("svg")]
    assert [scene.get("viewBox") for scene in scenes] == ["0 0 1000 340", "0 0 500 340"]
    kitchen = next(scene for scene in scenes if "cocina-plano activa" in scene.get("class"))
    assert kitchen.get("aria-label") == "Cocina: en preparación"
    assert all(name in _text(kitchen) for name in ("Chef", "Parrilla", "Fritos", "General"))

    cooked = _parse(plan_markup("Ana", "atendiendo", kitchen_plan=PLAN))
    kitchen = next(
        element
        for element in cooked.iter()
        if element.tag.endswith("svg") and "cocina-plano lista" in element.get("class")
    )
    kitchen_text = _text(kitchen)
    assert "EN EL PASE" in kitchen_text
    assert "Morcilla de Burg… · 15 s" in kitchen_text
    served = _parse(plan_markup("Ana", "atendiendo", kitchen_plan=PLAN, kitchen_served=True))
    served_kitchen = next(
        element
        for element in served.iter()
        if element.tag.endswith("svg") and "cocina-plano servida" in (element.get("class") or "")
    )
    assert "SERVIDO" in _text(served_kitchen)
    assert "Morcilla" not in _text(served_kitchen)
    css = base_stylesheet()
    assert "grid-template-columns: minmax(0, 2fr) minmax(0, 1fr)" in css


def test_a_kitchen_failure_is_said_in_the_chefs_bubble() -> None:
    failure = KitchenFailure(order_id="ko_1", code=KitchenFailureCode.TIMEOUT, message="Cocina no ha respondido a tiempo.")
    message = KITCHEN.model_copy(
        update={"kitchen": KitchenReport(order=ORDER, result=failure, text="Cocina no ha podido preparar el plan.")}
    )
    bubble = _parse(kitchen_row(message)).find("div[@class='burbuja plan-cocina']")
    assert [_text(element) for element in bubble] == [KITCHEN_FAILED, "Cocina no ha respondido a tiempo."]


def test_the_chef_speaks_white_on_azulejo_blue() -> None:
    css = base_stylesheet()
    azulejo = re.search(r"--azulejo: (#[0-9a-f]{6});", css).group(1)
    bubble = re.search(r"\.msg\.cocina \.burbuja \{([^}]*)\}", css).group(1)
    assert "background: var(--azulejo)" in bubble and "color: #ffffff" in bubble

    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    # Readable when projected: well above WCAG AAA for body text.
    assert (1.05) / (luminance(azulejo) + 0.05) >= 7
