import math
import re
import xml.etree.ElementTree as ET
from datetime import timedelta

import pytest

from restaurant_contracts.application import Action, ActorContext
from restaurant_contracts.seating import RoomView, SeatingView

from tests.support.fake_client import FakeRestaurant
from frontend.floor_plan import (
    BAR_COUNTER, LONG_TABLE, PLAN_H, PLAN_W, ROUND_TABLES,
    ROOM_BOTTOM, ROOM_RIGHT, WAITER_AT_PASS, WALK_SECONDS, floor_plan_svg,
)
from frontend.markup import plan_markup, proposal_markup
from frontend.visit import VisitSession

SVG = "{http://www.w3.org/2000/svg}"
# Same data as tests/fixtures/phase4; the image's test stage only has this app.
ROOM_DATA = {'schema_version': 1,
 'seating_enabled': True,
 'generated_at': '2026-09-29T20:01:00Z',
 'places': [{'place_id': 'table-01',
             'kind': 'table',
             'label': 'Mesa 1',
             'capacity': 2,
             'display_order': 10,
             'state': 'occupied',
             'party_size': 2,
             'mine': False,
             'seats': []},
            {'place_id': 'table-02',
             'kind': 'table',
             'label': 'Mesa 2',
             'capacity': 2,
             'display_order': 20,
             'state': 'held',
             'party_size': 1,
             'mine': False,
             'seats': []},
            {'place_id': 'table-03',
             'kind': 'table',
             'label': 'Mesa 3',
             'capacity': 4,
             'display_order': 30,
             'state': 'occupied',
             'party_size': 3,
             'mine': True,
             'seats': []},
            {'place_id': 'table-04',
             'kind': 'table',
             'label': 'Mesa 4',
             'capacity': 4,
             'display_order': 40,
             'state': 'free',
             'party_size': None,
             'mine': False,
             'seats': []},
            {'place_id': 'table-05',
             'kind': 'table',
             'label': 'Mesa 5',
             'capacity': 6,
             'display_order': 50,
             'state': 'free',
             'party_size': None,
             'mine': False,
             'seats': []},
            {'place_id': 'bar',
             'kind': 'bar',
             'label': 'Barra',
             'capacity': 8,
             'display_order': 100,
             'state': 'free',
             'party_size': None,
             'mine': False,
             'seats': [{'position': 1, 'state': 'occupied', 'mine': False},
                       {'position': 2, 'state': 'occupied', 'mine': False},
                       {'position': 3, 'state': 'held', 'mine': False},
                       {'position': 4, 'state': 'free', 'mine': False},
                       {'position': 5, 'state': 'free', 'mine': False},
                       {'position': 6, 'state': 'free', 'mine': False},
                       {'position': 7, 'state': 'free', 'mine': False},
                       {'position': 8, 'state': 'free', 'mine': False}]}]}
SEATINGS = [{'status': 'proposed',
  'proposal': {'proposal_id': 'prop_demo',
               'version': 1,
               'place': {'place_id': 'table-03',
                         'kind': 'table',
                         'label': 'Mesa 3',
                         'capacity': 4,
                         'seats': []},
               'party_size': 3,
               'expires_at': '2026-09-29T20:05:00Z'}},
 {'status': 'proposed',
  'proposal': {'proposal_id': 'prop_bar',
               'version': 1,
               'place': {'place_id': 'bar',
                         'kind': 'bar',
                         'label': 'Barra',
                         'capacity': 8,
                         'seats': [1, 2]},
               'party_size': 2,
               'expires_at': '2026-09-29T20:05:00Z'}},
 {'status': 'seated',
  'place': {'place_id': 'table-03',
            'kind': 'table',
            'label': 'Mesa 3',
            'capacity': 4,
            'seats': []},
  'party_size': 3,
  'seated_at': '2026-09-29T20:01:00Z'}]
ROOMS = [
    RoomView(schema_version=1, seating_enabled=False, generated_at="2026-09-29T20:00:00Z"),
    RoomView.model_validate(ROOM_DATA),
]
PROPOSED = SeatingView.model_validate(SEATINGS[0])
SEATED = SeatingView.model_validate(SEATINGS[2])


class Clock:
    def __init__(self) -> None:
        from datetime import UTC, datetime

        self.now = datetime(2026, 9, 29, 20, 0, tzinfo=UTC)

    def __call__(self):
        return self.now


def _parse(markup: str) -> ET.Element:
    return ET.fromstring(markup.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1))


def _visit(restaurant: FakeRestaurant, name: str = "Ana") -> VisitSession:
    visit = VisitSession(restaurant.client(ActorContext(actor_id=name, authenticated=True)))
    visit.arrive()
    return visit


def _classes(root: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in root.iter() if name in (item.get("class") or "").split()]


def test_the_waiter_brings_the_tray_from_the_pass_and_then_the_dishes_rest_on_the_table() -> None:
    walking = _parse(
        floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, served_dishes=2, serve_elapsed=0.5)
    )
    [waiter] = _classes(walking, "sirviendo")
    assert f"--x0:{WAITER_AT_PASS[0]}px;--y0:{WAITER_AT_PASS[1]}px" in waiter.get("style")
    assert not _classes(walking, "plato-servido")

    served = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, served_dishes=2))
    assert not _classes(served, "sirviendo")
    assert len(_classes(served, "plato-servido")) == 2
    assert "Tu comida está servida en la mesa." in served.get("aria-label")
    assert len(_classes(_parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED)), "plato-servido")) == 0


# Plan


def _numbers(root: ET.Element) -> list[tuple[str, str, str]]:
    return [
        (text.text, text.get("x"), text.get("y"))
        for text in root.iter(f"{SVG}text")
        if text.get("class") == "numero-mesa"
    ]


def test_tables_show_their_layout_number_and_stools_do_not() -> None:
    root = _parse(floor_plan_svg("Ana", "atendiendo", room=ROOMS[1]))
    assert _numbers(root) == [
        ("1", "760", "244"),
        ("2", "1040", "244"),
        ("3", "760", "104"),
        ("4", "1040", "104"),
        ("5", "503.0", "174.0"),
    ]
    assert all(text.get("aria-hidden") == "true" for text in _classes(root, "numero-mesa"))


def test_the_number_comes_from_the_label_not_the_slot() -> None:
    data = ROOMS[1].model_dump(mode="json")
    data["places"][0]["label"] = "Mesa 12"
    data["places"][1]["label"] = "Rincón"
    root = _parse(floor_plan_svg("Ana", "atendiendo", room=RoomView.model_validate(data)))
    assert [number for number, _, _ in _numbers(root)] == ["12", "3", "4", "5"]


def test_the_decorative_room_numbers_its_five_slots() -> None:
    root = _parse(floor_plan_svg("Ana", "atendiendo"))
    assert [number for number, _, _ in _numbers(root)] == ["1", "2", "3", "4", "5"]
    assert root.get("aria-label").startswith("Plano del comedor: mesas 1 a 5 y barra, vacías")


def test_without_seating_the_plan_is_the_decorative_room() -> None:
    assert floor_plan_svg("Ana", "atendiendo", room=ROOMS[0]) == floor_plan_svg("Ana", "atendiendo")


def test_chairs_follow_each_capacity() -> None:
    root = _parse(floor_plan_svg("Ana", "atendiendo", room=ROOMS[1]))
    chairs = [rect for rect in root.iter(f"{SVG}rect") if rect.get("rx") == "5"]
    assert len(chairs) == 2 + 2 + 4 + 4 + 6
    stools = [circle for circle in root.iter(f"{SVG}circle") if circle.get("r") == "11"]
    assert len(stools) == 8


def test_round_chairs_face_the_table() -> None:
    root = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1]))
    chair_groups = [
        group for group in root.iter(f"{SVG}g")
        if (group.get("transform") or "").startswith("rotate(")
    ]
    assert len(chair_groups) == 2 + 2 + 4 + 4
    assert chair_groups[0].get("transform") == "rotate(270.0,706,244)"
    assert chair_groups[1].get("transform") == "rotate(90.0,814,244)"


def test_six_seat_table_is_vertical_with_three_chairs_on_each_side() -> None:
    root = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1]))
    table = next(
        rect for rect in root.iter(f"{SVG}rect")
        if rect.get("x") == "480" and rect.get("y") == "74"
    )
    assert (table.get("width"), table.get("height")) == ("46", "200")
    chairs = [
        rect for rect in root.iter(f"{SVG}rect")
        if rect.get("rx") == "5" and rect.get("x") in ("446.0", "540.0")
    ]
    assert len(chairs) == 6
    assert {rect.get("y") for rect in chairs} == {"110.0", "160.0", "210.0"}


def test_bar_has_eight_stools_in_one_row_opposite_the_entrance() -> None:
    root = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1]))
    stools = [circle for circle in root.iter(f"{SVG}circle") if circle.get("r") == "11"]
    assert len(stools) == 8
    assert {stool.get("cy") for stool in stools} == {"102"}
    assert len({stool.get("cx") for stool in stools}) == 8
    assert max(float(stool.get("cx")) for stool in stools) < 420


def test_expanded_room_keeps_bar_and_tables_separate() -> None:
    assert (PLAN_W, PLAN_H) == (1280, 340)
    bar_x, _, bar_width, _ = BAR_COUNTER
    left, top, width, height = LONG_TABLE
    assert left - 24 - 21 >= bar_x + bar_width + 40
    assert all(cx - 75 >= left + width + 24 + 21 + 40 for cx, _ in ROUND_TABLES)
    assert top >= 24 and top + height <= PLAN_H - 24
    assert ROUND_TABLES[0][1] > ROUND_TABLES[2][1]
    assert ROUND_TABLES[1][1] > ROUND_TABLES[3][1]


def test_vertical_table_keeps_guests_plates_and_service_on_the_correct_sides() -> None:
    data = ROOMS[1].model_dump(mode="json")
    data["places"][2].update(state="free", mine=False, party_size=None)
    data["places"][4].update(state="occupied", mine=True, party_size=6)
    room = RoomView.model_validate(data)
    seating = SEATED.model_copy(update={
        "place": SEATED.place.model_copy(update={"place_id": "table-05", "label": "Mesa 5", "capacity": 6}),
        "party_size": 6,
    })
    root = _parse(floor_plan_svg("Ana", "barra", room=room, seating=seating, served_dishes=6))
    guests = _classes(root, "comensal")
    assert len(guests) == 6
    assert {guest.get("transform") for guest in guests} == {
        f"translate({x},{y})" for x in (456, 550) for y in (124.0, 174.0, 224.0)
    }
    plates = _classes(root, "plato-servido")
    assert len(plates) == 6
    assert {plate.get("transform") for plate in plates} == {
        f"translate({x},{y})" for x in (492, 514) for y in (124.0, 174.0, 224.0)
    }
    serving = _parse(floor_plan_svg(
        "Ana", "barra", room=room, seating=seating, served_dishes=6, serve_elapsed=0.5,
    ))
    [waiter] = _classes(serving, "sirviendo")
    [resting] = _classes(root, "camarero")
    assert waiter.get("transform") == resting.get("transform")


def test_other_groups_are_anonymous_figures_and_holds_are_reserved() -> None:
    markup = floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED)
    root = _parse(markup)
    assert len(_classes(root, "grupo")) == 2 + 2  # Mesa 1 and two bar stools
    assert len(_classes(root, "asiento-reservado")) == 2 + 1  # Mesa 2 and one stool
    assert _classes(root, "reservada")
    texts = [
        text.text
        for text in root.iter(f"{SVG}text")
        if text.get("class") not in ("numero-mesa", "pase", "rotulo-caja")
    ]
    assert texts == ["Ana"]
    label = root.get("aria-label")
    assert "Mesa 1: ocupada por un grupo de 2" in label and "Mesa 2: reservada" in label
    assert "Barra: 3 de 8 puestos ocupados" in label


def test_the_customers_group_sits_at_its_table() -> None:
    root = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED))
    group = _classes(root, "comensal")
    assert len(group) == 3
    assert len(_classes(root, "cliente-propio")) == 1 and len(_classes(root, "acompanante")) == 2
    assert not _classes(root, "caminando")
    assert root.find(f".//{SVG}g[@class='cliente']") is None
    assert "Ana y su grupo, sentados" in root.get("aria-label")


def test_the_walk_plays_once_and_continues_across_renders() -> None:
    start = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=0.0))
    walking = _classes(start, "caminando")
    assert len(walking) == 3
    assert "--x0:124px;--y0:264px;--xm:420px;--ym:42px" in walking[0].get("style")
    assert "--recorrido:path('M 124 264 Q 124 130 400 130 Q " in walking[0].get("style")
    assert walking[1].get("style").endswith("animation-delay:0.12s")
    later = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=1.0))
    assert _classes(later, "caminando")[0].get("style").endswith("animation-delay:-1.00s")
    done = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=WALK_SECONDS + 1))
    assert not _classes(done, "caminando")


@pytest.mark.parametrize("place_index", range(6))
def test_the_waiter_stays_beside_every_table_and_bar_after_walking_and_serving(place_index) -> None:
    data = ROOMS[1].model_dump(mode="json")
    data["places"][2].update(state="free", mine=False, party_size=None)
    place = data["places"][place_index]
    if place["kind"] == "bar":
        place["mine"] = True
        for seat in place["seats"]:
            seat.update(state="free", mine=False)
        place["seats"][3].update(state="occupied", mine=True)
    else:
        place.update(state="occupied", mine=True, party_size=1)
    room = RoomView.model_validate(data)
    positions = []
    for options in ({}, {"walk_elapsed": WALK_SECONDS}, {"served_dishes": 2}):
        root = _parse(floor_plan_svg("Ana", "barra", room=room, seating=SEATED, **options))
        [waiter] = _classes(root, "camarero")
        [customer] = _classes(root, "cliente-propio")
        wx, wy = map(float, re.findall(r"-?\d+(?:\.\d+)?", waiter.get("transform")))
        cx, cy = map(float, re.findall(r"-?\d+(?:\.\d+)?", customer.get("transform")))
        assert 30 <= math.hypot(wx - cx, wy - cy) <= 100
        assert root.get("class") == "planta atendiendo"
        assert "el camarero, atendiéndote" in root.get("aria-label")
        positions.append(waiter.get("transform"))
    assert len(set(positions)) == 1

    serving = _parse(floor_plan_svg(
        "Ana", "barra", room=room, seating=SEATED, served_dishes=2, serve_elapsed=0.5,
    ))
    [waiter] = _classes(serving, "sirviendo")
    assert waiter.get("transform") == positions[0]
    _assert_waiter_path_inside_room(waiter)

    walking = _parse(floor_plan_svg(
        "Ana", "barra", room=room, seating=SEATED, walk_elapsed=0.5,
    ))
    [waiter] = _classes(walking, "acompanando")
    offset = tuple(
        float(re.search(rf"--{axis}0:([^p]+)px", waiter[0].get("style")).group(1))
        for axis in ("dx", "dy")
    )
    _assert_waiter_path_inside_room(waiter, offset)


def _assert_waiter_path_inside_room(
    waiter: ET.Element, offset: tuple[float, float] = (0, 0)
) -> None:
    path = re.search(r"--recorrido:path\('([^']+)'\)", waiter.get("style")).group(1)
    coordinates = list(map(float, re.findall(r"-?\d+(?:\.\d+)?", path)))
    # Bounding every control point also bounds the whole Bezier curve.
    for x, y in zip(coordinates[::2], coordinates[1::2]):
        assert 24 + 5 + 22 <= x + offset[0] <= ROOM_RIGHT - 5 - 37
        assert 24 + 5 + 21 <= y + offset[1] <= ROOM_BOTTOM - 5 - 14


@pytest.mark.parametrize("elapsed", [0.0, 1.0, 2.5])
def test_the_waiter_follows_the_same_walk_and_progress_as_the_customer(elapsed) -> None:
    root = _parse(floor_plan_svg(
        "Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=elapsed,
    ))
    [waiter] = _classes(root, "acompanando")
    [customer] = _classes(root, "cliente-propio")
    assert waiter.get("style") == customer.get("style")
    assert waiter.get("transform") == customer.get("transform")

    done = _parse(floor_plan_svg(
        "Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=WALK_SECONDS,
    ))
    assert not _classes(done, "acompanando")
    [resting] = _classes(done, "camarero")
    cx, cy = map(float, re.findall(r"-?\d+(?:\.\d+)?", customer.get("transform")))
    dx, dy = map(float, re.findall(r"-?\d+(?:\.\d+)?", waiter[0].get("transform")))
    wx, wy = map(float, re.findall(r"-?\d+(?:\.\d+)?", resting.get("transform")))
    assert (cx + dx, cy + dy) == (wx, wy)


def _proposal_room() -> RoomView:
    data = ROOMS[1].model_dump(mode="json")
    data["places"][2].update(state="held", mine=True, party_size=3)
    return RoomView.model_validate(data)


def test_the_customers_proposal_is_highlighted_at_the_door() -> None:
    root = _parse(floor_plan_svg("Ana", "atendiendo", room=_proposal_room(), seating=PROPOSED))
    assert len(_classes(root, "asiento-propio")) == 3
    assert _classes(root, "propia")
    assert root.find(f".//{SVG}g[@class='cliente']") is not None
    assert "Mesa 3: tu propuesta para 3" in root.get("aria-label")


def test_the_plan_markup_is_one_line_with_the_room() -> None:
    markup = plan_markup("Ana", "atendiendo", room=_proposal_room(), seating=PROPOSED)
    assert "\n" not in markup and markup.startswith('<div class="planta-marco">')


# Proposal card


def test_the_proposal_card_names_place_and_seats_without_ids() -> None:
    table = proposal_markup(PROPOSED.proposal)
    assert "Mesa 3" in table and "3 de 4 asientos" in table and "prop_" not in table
    bar = SeatingView.model_validate(SEATINGS[1]).proposal
    assert "Puestos 1 a 2" in proposal_markup(bar)


# Visit and fake room


def test_a_stated_party_gets_a_proposal_and_confirming_seats_it() -> None:
    restaurant = FakeRestaurant()
    visit = _visit(restaurant)
    visit.send_message("Venimos tres")
    assert visit.snapshot.customer.party_size == 3
    assert visit.snapshot.seating.proposal.place.label == "Mesa 3"
    assert visit.allows(Action.DECIDE_TABLE)
    visit.decide_table("confirmed")
    assert visit.snapshot.seating.status == "seated"
    assert visit.snapshot.messages[-1].text == (
        "¡Estupendo! Os acompaño a la Mesa 3. ¿Qué queréis tomar?"
    )
    assert visit.walk_elapsed() is not None and visit.walk_elapsed() < 1
    visit.decide_table("confirmed")
    assert sum("Os acompaño" in message.text for message in visit.snapshot.messages) == 1


def test_rejecting_frees_the_table_for_the_next_group() -> None:
    restaurant = FakeRestaurant()
    ana = _visit(restaurant)
    ana.send_message("Somos cuatro")
    ana.decide_table("rejected")
    assert ana.snapshot.seating.status == "none"
    luis = _visit(restaurant, "Luis")
    luis.send_message("Somos cuatro")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 3"


def test_parallel_groups_see_each_other_anonymously() -> None:
    restaurant = FakeRestaurant()
    ana = _visit(restaurant)
    ana.send_message("Somos dos")
    ana.decide_table("confirmed")
    luis = _visit(restaurant, "Luis")
    luis.send_message("Somos dos")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 2"
    assert ana.refresh_room() is False
    places = {place.label: place for place in ana.room.places}
    assert (places["Mesa 1"].state, places["Mesa 1"].mine) == ("occupied", True)
    assert (places["Mesa 2"].state, places["Mesa 2"].mine) == ("held", False)
    assert "Luis" not in ana.room.model_dump_json()


def test_full_tables_offer_the_bar_in_order() -> None:
    restaurant = FakeRestaurant()
    for number, size in enumerate((2, 2, 4, 4, 6)):
        guest = _visit(restaurant, f"Grupo {number}")
        guest.send_message(f"Somos {size}")
        guest.decide_table("confirmed")
    ana = _visit(restaurant)
    ana.send_message("Somos dos")
    assert ana.snapshot.seating.proposal.place.seats == [1, 2]
    assert "barra, puestos 1 a 2" in ana.snapshot.messages[-1].text
    luis = _visit(restaurant, "Luis")
    luis.send_message("Nos sentamos en la barra, somos 3")
    assert luis.snapshot.seating.proposal.place.seats == [3, 4, 5]


def test_a_late_confirmation_reports_the_expiry_and_clears_the_card() -> None:
    clock = Clock()
    restaurant = FakeRestaurant(clock=clock)
    visit = _visit(restaurant)
    visit.send_message("Venimos tres")
    clock.now += timedelta(minutes=6)
    assert visit.refresh_room() is False
    visit.decide_table("confirmed")
    assert visit.cards[-1].title.startswith("La reserva de la Mesa 3 ha caducado")
    assert visit.snapshot.seating.status == "none"


def test_new_visit_rules_follow_the_seating() -> None:
    restaurant = FakeRestaurant()
    visit = _visit(restaurant)
    visit.send_message("Somos dos")
    first = visit.snapshot.conversation_id
    visit.arrive()
    assert visit.snapshot.conversation_id != first
    visit.send_message("Somos dos")
    visit.decide_table("confirmed")
    seated = visit.snapshot.conversation_id
    visit.arrive()
    assert visit.snapshot.conversation_id == seated
    assert visit.cards[-1].title.startswith("Ya estáis sentados en la Mesa 1")


def test_saying_yes_never_confirms_and_keeps_the_proposal() -> None:
    visit = _visit(FakeRestaurant())
    visit.send_message("Somos dos")
    first = visit.snapshot.seating.proposal.proposal_id
    visit.send_message("Sí")
    assert visit.snapshot.seating.status == "proposed"
    assert visit.snapshot.seating.proposal.proposal_id == first
    assert visit.snapshot.messages[-1].text.startswith("Para confirmar hay que pulsar «Confirmar»")


def test_writing_while_pending_keeps_the_same_stools() -> None:
    visit = _visit(FakeRestaurant())
    visit.send_message("Nos sentamos en la barra, somos 2")
    first = visit.snapshot.seating.proposal.proposal_id
    visit.send_message("¿Tenéis vino?")
    proposal = visit.snapshot.seating.proposal
    assert (proposal.proposal_id, proposal.place.seats) == (first, [1, 2])


def test_the_room_poll_picks_up_a_seating_changed_elsewhere() -> None:
    restaurant = FakeRestaurant()
    visit = _visit(restaurant)
    visit.send_message("Somos dos")
    other = VisitSession(restaurant.client(ActorContext(actor_id="Ana", authenticated=True)))
    other.arrive(visit.snapshot.visit_id)
    other.decide_table("confirmed")
    assert visit.refresh_room() is True
    assert visit.snapshot.seating.status == "seated"
    assert visit.walk_elapsed() is None
