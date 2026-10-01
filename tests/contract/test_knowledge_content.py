"""Consistency of the static knowledge the Foundry IQ knowledge base indexes."""

import re
from html.parser import HTMLParser
from pathlib import Path

KNOWLEDGE = Path(__file__).resolve().parents[2] / "data/knowledge"
CARTA = KNOWLEDGE / "menu/carta.md"
RECETARIO_HTML = KNOWLEDGE / "recipes/recetario.html"
RECETARIO_PDF = KNOWLEDGE / "recipes/recetario.pdf"
INGREDIENTES = KNOWLEDGE / "ingredients/ingredientes.md"

EU_ALLERGENS = {
    "cereales con gluten", "crustáceos", "huevos", "pescado", "cacahuetes",
    "soja", "leche", "frutos de cáscara", "apio", "mostaza", "sésamo",
    "sulfitos", "altramuces", "moluscos",
}
NONE = ("ninguno de los 14.", "nada declarado.", "nada más declarado.")
PENDING = "información pendiente de verificar"
PARTIDAS = {"brasa", "fritos", "pinchos fríos", "barra"}
ENTRY = re.compile(r"^### (?P<id>\S+) · (?P<name>.+)$")
STABLE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PRICE = re.compile(r"^\d{1,3},\d{2} € ")


def entries(path: Path) -> dict[str, dict[str, str]]:
    """``### id · name`` blocks with their ``- Field: value`` bullets."""

    found: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    field = None
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = ENTRY.match(line)
        if heading:
            assert heading["id"] not in found, f"duplicate id {heading['id']}"
            current = found[heading["id"]] = {"name": heading["name"]}
            field = None
        elif line.startswith("## "):
            current = None
        elif current is not None and line.startswith("- ") and ": " in line:
            field, value = line[2:].split(": ", 1)
            current[field] = value
        elif current is not None and field and line.startswith("  "):
            current[field] += " " + line.strip()
    return found


def allergens(value: str) -> set[str]:
    """Allergen names of a ``Contiene``/``Puede contener`` value."""

    value = value.strip()
    if value.startswith(NONE) or value.startswith(PENDING):
        return set()
    listed = re.sub(r"\([^)]*\)", "", value).rstrip(".")
    return {name.strip() for name in listed.split(",") if name.strip()}


class RecipeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.recipes: dict[str, dict[str, str]] = {}
        self._current: str | None = None
        self._in_allergens = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "section" and attributes.get("class") == "receta":
            self._current = attributes["id"]
            self.recipes[self._current] = {"carta_ids": attributes["data-carta-ids"], "contiene": ""}
        elif tag == "p" and attributes.get("class") == "alergenos":
            self._in_allergens = True

    def handle_endtag(self, tag):
        if tag == "p":
            self._in_allergens = False

    def handle_data(self, data):
        if self._in_allergens and self._current:
            self.recipes[self._current]["contiene"] += data


def recipes() -> dict[str, dict[str, str]]:
    parser = RecipeParser()
    parser.feed(RECETARIO_HTML.read_text(encoding="utf-8"))
    return parser.recipes


def test_carta_dishes_are_complete_and_self_citing() -> None:
    dishes = entries(CARTA)
    assert len(dishes) >= 12
    for dish_id, dish in dishes.items():
        assert STABLE_ID.match(dish_id), dish_id
        assert dish["Fuente"] == "carta de la casa, versión 1 (documento: carta)", dish_id
        assert dish["Partida"] in PARTIDAS, dish_id
        assert PRICE.match(dish["Precio"]), dish_id
        for field in ("Descripción", "Contiene", "Puede contener", "Advertencias", "Típico de Burgos"):
            assert dish.get(field), (dish_id, field)
        declared = allergens(dish["Contiene"]) | allergens(dish["Puede contener"])
        assert declared <= EU_ALLERGENS, (dish_id, declared - EU_ALLERGENS)
    assert {dish["Partida"] for dish in dishes.values()} == PARTIDAS


def test_carta_includes_the_dishes_used_in_specs_and_memory_examples() -> None:
    dishes = entries(CARTA)
    for dish_id in (
        "morcilla-de-burgos-a-la-brasa", "agua-con-gas", "agua-sin-gas",
        "pincho-de-tortilla-de-patatas", "vino-tinto-ribera-del-duero", "cana-de-cerveza",
    ):
        assert dish_id in dishes


def test_missing_allergen_information_is_explicit() -> None:
    pending = [dish_id for dish_id, dish in entries(CARTA).items() if dish["Contiene"].startswith(PENDING)]
    assert pending == ["chorizo-a-la-brasa"]


def test_every_house_dish_has_a_recipe_with_the_same_allergens() -> None:
    dishes = entries(CARTA)
    by_dish = {
        dish_id: recipe
        for recipe in recipes().values()
        for dish_id in recipe["carta_ids"].split()
    }
    house = {
        dish_id for dish_id, dish in dishes.items()
        if dish["Partida"] != "barra" and not dish["Contiene"].startswith(PENDING)
    }
    assert set(by_dish) == house
    for dish_id in house:
        recipe_allergens = allergens(by_dish[dish_id]["contiene"].replace("Contiene:", "", 1))
        assert recipe_allergens == allergens(dishes[dish_id]["Contiene"]), dish_id


def test_ingredient_sheet_uses_stable_ids_and_eu_allergens() -> None:
    dish_ids = set(entries(CARTA))
    ingredients = entries(INGREDIENTES)
    assert len(ingredients) >= 20
    for ingredient_id, ingredient in ingredients.items():
        assert ingredient_id.startswith("ing-") and STABLE_ID.match(ingredient_id), ingredient_id
        assert ingredient["Fuente"] == "ingredientes de la casa, versión 1 (documento: ingredientes)"
        assert allergens(ingredient["Contiene"]) <= EU_ALLERGENS, ingredient_id
        used_in = set(re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)+", ingredient["Se usa en"]))
        assert used_in <= dish_ids | set(ingredients), (ingredient_id, used_in - dish_ids - set(ingredients))


def test_the_pdf_is_versioned_next_to_its_source() -> None:
    pdf = RECETARIO_PDF.read_bytes()
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) < 1_000_000
    # A cover page plus one page per recipe, as printed by build-recetario-pdf.sh.
    pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert pages == 1 + len(recipes())
