import re
import xml.etree.ElementTree as ET

import pytest

from frontend.facade import FACADE_STATES, facade_html
from frontend.floor_plan import (
    DOOR_GAP,
    PASS_OPENING,
    WAITER_STATES,
    customer_icon_svg,
    floor_plan_svg,
    waiter_icon_svg,
)

SVG = "{http://www.w3.org/2000/svg}"


def _parse(markup: str) -> ET.Element:
    return ET.fromstring(markup.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))


@pytest.mark.parametrize("state", FACADE_STATES)
def test_facade_is_well_formed_for_every_state(state) -> None:
    root = _parse(facade_html(state))
    assert root.tag == "div"
    assert root.get("class") == f"escena {state}"
    svg = root.find(f"{SVG}svg")
    assert svg is not None and svg.get("aria-hidden") == "true"
    assert len(root.findall(f".//{SVG}g[@class='hoja hoja-izq']")) == 1
    assert len(root.findall(f".//{SVG}g[@class='hoja hoja-der']")) == 1


def test_facade_rejects_unknown_states() -> None:
    with pytest.raises(ValueError):
        facade_html("rota")


def test_facade_has_no_blank_lines_for_markdown_blocks() -> None:
    for state in FACADE_STATES:
        assert "\n\n" not in facade_html(state)


@pytest.mark.parametrize("waiter", WAITER_STATES)
def test_plan_is_well_formed_for_every_waiter_state(waiter) -> None:
    root = _parse(floor_plan_svg("Ana", waiter))
    assert root.get("class") == f"planta {waiter}"
    assert root.get("role") == "img"
    assert "Ana en la entrada" in root.get("aria-label")
    assert root.find(f".//{SVG}g[@class='camarero']") is not None
    assert root.find(f".//{SVG}g[@class='cliente']/{SVG}text").text == "Ana"


def test_waiter_attends_the_customer_by_the_door() -> None:
    at_bar = _parse(floor_plan_svg("Ana", "barra")).find(f".//{SVG}g[@class='camarero']")
    serving = _parse(floor_plan_svg("Ana", "atendiendo")).find(f".//{SVG}g[@class='camarero']")
    assert at_bar.get("transform") == "translate(560,124)"
    assert serving.get("transform") == "translate(192,272)"


def _segments(path: str) -> set[frozenset[tuple[float, float]]]:
    """Straight segments of an absolute M/H/V/L/Z path, ignoring direction."""

    tokens = re.findall(r"[A-Za-z]|-?\d+(?:\.\d+)?", path)
    segments: set[frozenset[tuple[float, float]]] = set()
    x = y = start_x = start_y = 0.0
    command = ""
    while tokens:
        if tokens[0].isalpha():
            command = tokens.pop(0)
        assert command in "MHVLZ", f"unsupported path command {command}"
        if command == "Z":
            end = (start_x, start_y)
        elif command == "H":
            end = (float(tokens.pop(0)), y)
        elif command == "V":
            end = (x, float(tokens.pop(0)))
        else:
            end = (float(tokens.pop(0)), float(tokens.pop(0)))
        if command == "M":
            start_x, start_y = end
        elif end != (x, y):
            segments.add(frozenset({(x, y), end}))
        x, y = end
    return segments


def test_walls_enclose_the_room_except_the_door_and_the_pass() -> None:
    plan = _parse(floor_plan_svg("Ana", "barra"))
    walls = next(path for path in plan.iter(f"{SVG}path") if path.get("stroke-width") == "10")
    path = walls.get("d")
    assert "Z" not in path.upper()
    segments = _segments(path)
    left, right = DOOR_GAP
    top, bottom = PASS_OPENING
    assert frozenset({(24, 24), (24, 316)}) in segments
    assert frozenset({(24, 24), (976, 24)}) in segments
    assert frozenset({(976, 24), (976, top)}) in segments
    assert frozenset({(976, bottom), (976, 316)}) in segments
    assert frozenset({(976, 316), (right, 316)}) in segments
    assert frozenset({(24, 316), (left, 316)}) in segments
    for segment in segments:
        (x1, y1), (x2, y2) = sorted(segment)
        if y1 == y2 == 316:
            assert x2 <= left or x1 >= right, "the door gap must stay open"


def test_plan_escapes_the_customer_name() -> None:
    name = '<script>alert("x")</script> & Co'
    markup = floor_plan_svg(name, "atendiendo")
    assert "<script>" not in markup
    root = _parse(markup)
    assert root.find(f".//{SVG}g[@class='cliente']/{SVG}text").text == f"{name[:15]}…"
    assert name in root.get("aria-label")


def test_plan_without_customer_shows_an_empty_room() -> None:
    root = _parse(floor_plan_svg(None))
    assert root.find(f".//{SVG}g[@class='cliente']") is None
    assert root.get("aria-label").startswith("Plano del comedor")


def test_plan_rejects_unknown_waiter_states() -> None:
    with pytest.raises(ValueError):
        floor_plan_svg("Ana", "cocina")


@pytest.mark.parametrize("icon", [waiter_icon_svg, customer_icon_svg])
def test_icons_are_well_formed(icon) -> None:
    assert _parse(icon()).get("class") == "icono"
