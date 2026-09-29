import xml.etree.ElementTree as ET
from datetime import timedelta


from restaurant_contracts.application import Action, ActorContext
from restaurant_contracts.seating import RoomView, SeatingView

from frontend.fake_client import FakeRestaurant
from frontend.floor_plan import WALK_SECONDS, floor_plan_svg
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
        ("1", "300", "104"),
        ("2", "300", "244"),
        ("3", "462", "174"),
        ("4", "858", "246"),
        ("5", "682.0", "229.0"),
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


def test_other_groups_are_anonymous_figures_and_holds_are_reserved() -> None:
    markup = floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED)
    root = _parse(markup)
    assert len(_classes(root, "grupo")) == 2 + 2  # Mesa 1 and two bar stools
    assert len(_classes(root, "asiento-reservado")) == 2 + 1  # Mesa 2 and one stool
    assert _classes(root, "reservada")
    texts = [text.text for text in root.iter(f"{SVG}text") if text.get("class") != "numero-mesa"]
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
    assert "--x0:124px;--y0:264px;--xm:380px;--ym:174px" in walking[0].get("style")
    assert walking[1].get("style").endswith("animation-delay:0.12s")
    later = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=1.0))
    assert _classes(later, "caminando")[0].get("style").endswith("animation-delay:-1.00s")
    done = _parse(floor_plan_svg("Ana", "barra", room=ROOMS[1], seating=SEATED, walk_elapsed=WALK_SECONDS + 1))
    assert not _classes(done, "caminando")


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
    assert visit.snapshot.messages[-1].text == "¡Estupendo! Os acompaño a la Mesa 3."
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


def test_saying_yes_never_confirms() -> None:
    visit = _visit(FakeRestaurant())
    visit.send_message("Somos dos")
    visit.send_message("Sí")
    assert visit.snapshot.seating.status == "proposed"
    assert "Confirmar" in visit.snapshot.messages[-1].text


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
