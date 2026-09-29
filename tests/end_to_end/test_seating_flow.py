"""Seating end to end without Foundry: MCP + BFF (scripted waiter) + the view's HTTP client.

Run with ./scripts/test-e2e-seating.sh, which starts both services on a fresh
database with the demo layout (Mesa 1 y 2 de 2, Mesa 3 y 4 de 4, Mesa 5 de
6, barra de 8). The tests run in order and share that room.
"""

from __future__ import annotations

import os

import pytest

from frontend.http_client import HttpBffClient
from frontend.visit import VisitSession

BFF_URL = os.environ.get("E2E_BFF_URL")
pytestmark = pytest.mark.skipif(not BFF_URL, reason="E2E_BFF_URL is not set")


def enter(name: str) -> VisitSession:
    visit = VisitSession(HttpBffClient.open(BFF_URL, name, timeout=15))
    visit.arrive()
    assert visit.snapshot is not None
    return visit


def place(visit: VisitSession, label: str):
    visit.refresh_room()
    return next(item for item in visit.room.places if item.label == label)


def test_tables_are_exclusive_and_decided_with_the_buttons() -> None:
    ana = enter("Ana")
    ana.send_message("Hola, venimos tres")
    proposal = ana.snapshot.seating.proposal
    assert (proposal.place.label, proposal.party_size, proposal.place.capacity) == ("Mesa 3", 3, 4)
    ana.send_message("Sí, vale")
    assert ana.snapshot.seating.status == "proposed"
    ana.decide_table("confirmed")
    assert ana.snapshot.seating.status == "seated"
    assert ana.snapshot.messages[-1].text == "¡Estupendo! Os acompaño a la Mesa 3."

    luis = enter("Luis")
    luis.send_message("Somos tres")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 4"
    held = place(ana, "Mesa 4")
    assert (held.state, held.mine) == ("held", False)
    assert "Luis" not in ana.room.model_dump_json() and "luis" not in ana.room.model_dump_json()
    own = place(ana, "Mesa 3")
    assert (own.state, own.mine, own.party_size) == ("occupied", True, 3)

    luis.decide_table("rejected")
    assert luis.snapshot.seating.status == "none"
    assert place(ana, "Mesa 4").state == "free"

    ana.arrive()
    assert ana.cards[-1].title.startswith("Ya estáis sentados en la Mesa 3")

    luis.send_message("Mejor somos dos")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 1"
    first_visit = luis.snapshot.conversation_id
    luis.arrive()
    assert luis.snapshot.conversation_id != first_visit
    assert place(ana, "Mesa 1").state == "free"


def test_full_tables_send_groups_to_the_bar_in_order() -> None:
    for name, size in (("Grupo uno", "dos"), ("Grupo dos", "dos"), ("Grupo tres", "cuatro"), ("Grupo cuatro", "seis")):
        guest = enter(name)
        guest.send_message(f"Somos {size}")
        guest.decide_table("confirmed")
        assert guest.snapshot.seating.status == "seated", name

    eva = enter("Eva")
    eva.send_message("Somos dos")
    proposal = eva.snapshot.seating.proposal
    assert (proposal.place.kind, proposal.place.seats) == ("bar", [1, 2])
    eva.decide_table("confirmed")
    eva.decide_table("confirmed")
    assert sum("Os acompaño" in message.text for message in eva.snapshot.messages) == 1

    pepe = enter("Pepe")
    pepe.send_message("Nos sentamos en la barra, somos 3")
    assert pepe.snapshot.seating.proposal.place.seats == [3, 4, 5]
    bar = place(pepe, "Barra")
    assert [seat.state for seat in bar.seats] == ["occupied", "occupied", "held", "held", "held", "free", "free", "free"]
    assert [seat.mine for seat in bar.seats][2:5] == [True, True, True]


def test_a_group_too_large_gets_an_honest_answer() -> None:
    big = enter("Grupo grande")
    big.send_message("Somos nueve")
    assert big.snapshot.seating.status == "none"
    assert "no hay sitio para 9" in big.snapshot.messages[-1].text
