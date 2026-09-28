import pytest

from bff.identity import InvalidNameError, actor_id_for, presented_name


def test_presented_name_keeps_the_name_as_typed() -> None:
    assert presented_name("  María   José ") == "María José"


@pytest.mark.parametrize(
    "variant",
    ["María José", "maria jose", "  MARÍA   JOSÉ ", "Maria\tJosé", "MaRíA JoSe"],
)
def test_case_accents_and_spaces_are_ignored(variant) -> None:
    assert actor_id_for(variant) == "maria jose"


def test_the_tilde_of_n_is_kept() -> None:
    assert actor_id_for("Peña") == actor_id_for("PEÑA") == "peña"
    assert actor_id_for("Peña") != actor_id_for("Pena")
    assert actor_id_for("Iñaki") == "iñaki"


def test_other_diacritics_are_ignored() -> None:
    assert actor_id_for("Çüa") == "cua"


def test_different_names_are_different_customers() -> None:
    assert actor_id_for("Ana") != actor_id_for("Anna")


@pytest.mark.parametrize("raw", ["", "   ", "a" * 41, "Ana\u0000", "Ana\u200b"])
def test_invalid_names_are_rejected(raw) -> None:
    with pytest.raises(InvalidNameError):
        actor_id_for(raw)
