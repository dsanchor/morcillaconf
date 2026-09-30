import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from restaurant_contracts.application import ChatMessage, VisibleMemory
from restaurant_contracts.memory import MemoryKind

from frontend.markup import (
    DOOR_HINT,
    Reveal,
    command_row_markup,
    conversation_markup,
    door_hint_markup,
    facade_markup,
    identity_markup,
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
MEMORY = VisibleMemory(
    memory_id="m2", kind=MemoryKind.RESTRICTION, value="frutos secos",
    source="conv_1", recorded_at=AT,
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
        door_hint_markup(), identity_markup("Ana"), command_row_markup("/memory delete <id>"),
        simulated_markup(), plan_markup("Ana", "llegando", entering=True),
        conversation_markup(ConversationView(messages=(GREETING, QUESTION), waiting=True)),
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


def test_memory_card_follows_its_message_with_ids_and_kinds() -> None:
    card = Card(after_message_id="msg_1", title="Lo que recuerdo de ti:", memories=(MEMORY,))
    markup = conversation_markup(ConversationView(messages=(GREETING, QUESTION), cards=(card,)))
    root = _parse(markup)
    hilo = root.find("div[@class='hilo']")
    assert [child.get("class") for child in hilo] == ["msg camarero", "tarjeta", "msg cliente"]
    item = hilo.find("div[@class='tarjeta']/ul/li")
    assert item.find("code").text == "m2"
    assert _text(item) == "m2 Alergia o restricción: frutos secos"


def test_clear_card_explains_that_memory_keeps_learning() -> None:
    card = Card(after_message_id=None, title="He olvidado todo lo que sabía de ti.", note="Lo que me cuentes a partir de ahora lo volveré a recordar.")
    root = _parse(conversation_markup(ConversationView(cards=(card,))))
    assert root.find(".//p[@class='nota']") is not None


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
    row = _parse(command_row_markup("/memory correct <id> <texto>"))
    assert [arg.text for arg in row.iter("span")] == ["<id>", "<texto>"]
    assert _text(row) == "/memory correct <id> <texto>"


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
