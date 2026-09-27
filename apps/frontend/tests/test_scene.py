import xml.etree.ElementTree as ET

import pytest

from frontend.facade import FACADE_STATES, facade_html
from frontend.floor_plan import (
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
