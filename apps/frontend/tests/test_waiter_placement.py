import math
import re
import xml.etree.ElementTree as ET

import pytest
from restaurant_contracts.seating import RoomView

from frontend.floor_plan import (
    PLAN_H,
    PLAN_W,
    ROOM_BOTTOM,
    ROOM_RIGHT,
    WALK_SECONDS,
    _Bounds,
    _free_waiter_position,
    floor_plan_svg,
)
from tests.test_seating_view import ROOMS, SEATED, SVG, _classes, _parse

CASES = (
    [(index, capacity, 1) for index in range(4) for capacity in range(1, 7)]
    + [(4, capacity, 1) for capacity in range(1, 7)]
    + [(5, 8, position) for position in range(1, 9)]
)


def _crowded_room(index: int, capacity: int, position: int, others: str) -> RoomView:
    data = ROOMS[1].model_dump(mode="json")
    for place in data["places"]:
        place["mine"] = False
        if place["kind"] == "table":
            place.update(state=others, party_size=place["capacity"])
        else:
            for seat in place["seats"]:
                seat.update(state=others, mine=False)
    own = data["places"][index]
    own["mine"] = True
    if own["kind"] == "table":
        own.update(state="occupied", capacity=capacity, party_size=capacity)
    else:
        own["seats"][position - 1].update(state="occupied", mine=True)
    return RoomView.model_validate(data)


def _transform_point(x: float, y: float, transforms: list[str]) -> tuple[float, float]:
    for transform in reversed(transforms):
        for command, values in reversed(re.findall(r"(\w+)\(([^)]+)\)", transform)):
            numbers = list(map(float, re.findall(r"-?\d+(?:\.\d+)?", values)))
            if command == "translate":
                x, y = x + numbers[0], y + numbers[1]
            elif command == "rotate":
                angle = math.radians(numbers[0])
                cx, cy = numbers[1:] if len(numbers) == 3 else (0, 0)
                x, y = (
                    cx + (x - cx) * math.cos(angle) - (y - cy) * math.sin(angle),
                    cy + (x - cx) * math.sin(angle) + (y - cy) * math.cos(angle),
                )
            else:
                raise AssertionError(f"Unsupported SVG transform: {command}")
    return x, y


def _solid_bounds(root: ET.Element, transforms: list[str] | None = None):
    """Bounds from the rendered SVG, independently of the placement's obstacle model."""

    if root.tag == f"{SVG}defs" or "camarero" in root.get("class", "").split():
        return
    transforms = [*(transforms or []), root.get("transform", "")]
    if not root.get("fill", "").startswith("url("):
        tag = root.tag.removeprefix(SVG)
        stroke = float(root.get("stroke-width", "1")) / 2 if root.get("stroke") else 0
        if tag == "rect":
            x, y = float(root.get("x", "0")), float(root.get("y", "0"))
            left, top = x - stroke, y - stroke
            right, bottom = x + float(root.get("width")) + stroke, y + float(root.get("height")) + stroke
        elif tag in ("circle", "ellipse"):
            x, y = float(root.get("cx", "0")), float(root.get("cy", "0"))
            rx = float(root.get("r") if tag == "circle" else root.get("rx")) + stroke
            ry = float(root.get("r") if tag == "circle" else root.get("ry")) + stroke
            left, top, right, bottom = x - rx, y - ry, x + rx, y + ry
        else:
            left = top = right = bottom = None
        if left is not None:
            points = [_transform_point(x, y, transforms) for x in (left, right) for y in (top, bottom)]
            yield min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)
    for child in root:
        yield from _solid_bounds(child, transforms)


@pytest.mark.parametrize("index,capacity,position", CASES)
@pytest.mark.parametrize("others", ["occupied", "held"])
@pytest.mark.parametrize("name", ["Ana", "WWWWWWWWWWWWWWWW"])
def test_waiter_and_tray_do_not_overlap_any_rendered_furniture_or_person(
    index, capacity, position, others, name
) -> None:
    room = _crowded_room(index, capacity, position, others)
    root = _parse(floor_plan_svg(
        name, "atendiendo", room=room, seating=SEATED, walk_elapsed=WALK_SECONDS,
        served_dishes=2,
    ))
    [waiter] = _classes(root, "camarero")
    wx, wy = map(float, re.findall(r"-?\d+(?:\.\d+)?", waiter.get("transform")))
    left, top, right, bottom = wx - 22, wy - 21, wx + 37, wy + 14
    assert 29 <= left < right <= ROOM_RIGHT - 5
    assert 29 <= top < bottom <= ROOM_BOTTOM - 5
    for obstacle in _solid_bounds(root):
        x0, y0, x1, y1 = obstacle
        assert right <= x0 or x1 <= left or bottom <= y0 or y1 <= top, (
            f"Waiter {(left, top, right, bottom)} overlaps {obstacle}"
        )
    [customer] = _classes(root, "cliente-propio")
    cx, cy = map(float, re.findall(r"-?\d+(?:\.\d+)?", customer.get("transform")))
    assert math.hypot(wx - cx, wy - cy) <= 110


def test_unavailable_free_space_raises_instead_of_overlapping_an_icon() -> None:
    with pytest.raises(ValueError, match="No free position"):
        _free_waiter_position((100, 100, False), [_Bounds(0, 0, PLAN_W, PLAN_H)])
