"""Seating end to end without Foundry; the waiter is the only client of the MCP.

./scripts/test-e2e-seating.sh starts the seating MCP, the standalone waiter
(dsanchor's create_server with a scripted model) and the remote-only BFF with
no SEATING_MCP_URL, on fresh databases with the demo
layout (Mesa 1 y 2 de 2, Mesa 3 y 4 de 4, Mesa 5 de 6, barra de 8). The view's
HTTP client drives them. The tests run in order and share that room.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

from frontend.http_client import HttpBffClient
from frontend.visit import VisitSession

BFF_URL = os.environ.get("E2E_BFF_URL")
pytestmark = pytest.mark.skipif(not BFF_URL, reason="E2E_BFF_URL is not set")


def enter(name: str) -> VisitSession:
    client = HttpBffClient.open(BFF_URL, name, timeout=30)
    assert client.simulated is False  # the BFF reports the remote waiter
    visit = VisitSession(client)
    visit.arrive()
    assert visit.snapshot is not None
    return visit


def place(visit: VisitSession, label: str):
    visit.refresh_room()
    return next(item for item in visit.room.places if item.label == label)


def test_the_bff_runs_the_remote_waiter_without_mcp() -> None:
    with urllib.request.urlopen(f"{BFF_URL}/healthz", timeout=5) as response:
        health = json.loads(response.read())
    assert health["waiter"] == "remote"
    assert "SEATING_MCP_URL" not in os.environ


def test_a_typed_yes_never_confirms_and_the_buttons_do() -> None:
    ana = enter("Ana")
    ana.send_message("Hola, venimos tres")
    proposal = ana.snapshot.seating.proposal
    assert (proposal.place.label, proposal.party_size, proposal.place.capacity) == ("Mesa 3", 3, 4)

    ana.send_message("Sí, confírmala")
    again = ana.snapshot.seating
    # The hold stays and the same card comes back.
    assert again.status == "proposed" and again.proposal.proposal_id == proposal.proposal_id
    assert ana.snapshot.messages[-1].text.startswith("Para confirmar hay que pulsar «Confirmar»")

    ana.decide_table("confirmed")
    assert ana.snapshot.seating.status == "seated"
    assert ana.snapshot.messages[-1].text == (
        "¡Estupendo! Os acompaño a la Mesa 3. ¿Qué queréis tomar?"
    )
    ana.decide_table("confirmed")
    assert sum("Os acompaño" in message.text for message in ana.snapshot.messages) == 1

    ana.arrive()
    assert ana.cards[-1].title.startswith("Ya estáis sentados en la Mesa 3")


def test_a_parallel_session_sees_the_table_and_a_rejection_frees_it() -> None:
    luis = enter("Luis")
    luis.send_message("Somos tres")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 4"
    ana = VisitSession(HttpBffClient.open(BFF_URL, "Ana", timeout=30))
    ana.arrive(ana._client.active_visit_id)
    held = place(ana, "Mesa 4")
    assert (held.state, held.mine) == ("held", False)
    own = place(ana, "Mesa 3")
    assert (own.state, own.mine, own.party_size) == ("occupied", True, 3)
    dumped = ana.room.model_dump_json()
    assert "Luis" not in dumped and "luis" not in dumped

    luis.decide_table("rejected")
    assert luis.snapshot.seating.status == "none"
    assert luis.snapshot.messages[-1].text.startswith("Sin problema, dejo libre la Mesa 4")
    assert place(ana, "Mesa 4").state == "free"


def test_a_message_while_pending_keeps_the_hold_and_new_releases_it() -> None:
    luis = VisitSession(HttpBffClient.open(BFF_URL, "Luis", timeout=30))
    luis.arrive(luis._client.active_visit_id)
    luis.send_message("Mejor somos dos")
    assert luis.snapshot.seating.proposal.place.label == "Mesa 1"

    held = luis.snapshot.seating.proposal.proposal_id
    luis.send_message("¿Tenéis vino de la Ribera?")
    assert luis.snapshot.seating.status == "proposed"
    assert luis.snapshot.seating.proposal.proposal_id == held
    assert place(luis, "Mesa 1").state == "held"
    luis.refresh_room()
    assert [item.label for item in luis.room.places if item.mine] == ["Mesa 1"]

    first_visit = luis.snapshot.conversation_id
    luis.arrive()
    assert luis.snapshot.conversation_id != first_visit
    assert place(luis, "Mesa 1").state == "free"


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
    assert "no hay sitio para nueve" in big.snapshot.messages[-1].text
