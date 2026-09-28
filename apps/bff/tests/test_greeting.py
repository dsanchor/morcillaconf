# Same cases as apps/frontend/tests/test_greeting.py: keep both in sync.
import pytest

from bff.greeting import form_of_address, greeting


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Ana", "maja"),
        ("Lucía", "maja"),
        ("María José", "maja"),
        ("Luis", "majo"),
        ("José María", "majo"),
        ("David", "majo"),
        ("Majo", "majo"),
    ],
)
def test_form_of_address_follows_the_first_name(name, expected) -> None:
    assert form_of_address(name) == expected


@pytest.mark.parametrize("name", ["Carmen", "Inés", "Pilar", "Raquel", "Itziar", "Beatriz", "LOURDES"])
def test_feminine_names_without_final_a_are_maja(name) -> None:
    assert form_of_address(name) == "maja"


@pytest.mark.parametrize("name", ["Borja", "Luca", "Bautista", "Josema", "Joshua"])
def test_masculine_names_ending_in_a_are_majo(name) -> None:
    assert form_of_address(name) == "majo"


def test_empty_name_defaults_to_majo() -> None:
    assert form_of_address("") == "majo"
    assert form_of_address("   ") == "majo"


def test_greeting_uses_the_normalized_name() -> None:
    assert greeting("Ana") == "Hombre, Ana, ¿qué tal, maja?"
    assert greeting("  Luis   Mateo ") == "Hombre, Luis Mateo, ¿qué tal, majo?"
