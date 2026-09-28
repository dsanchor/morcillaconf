"""CSS shipped inside the package: shared scene styles plus the Streamlit port."""

from __future__ import annotations

from importlib.resources import files

STYLE_FILES = ("scene.css", "app.css")

OUTSIDE_CSS = """
header[data-testid="stHeader"] { display: none !important; }
"""

KNOCKED_CSS = """
.stApp .st-key-umbral [data-testid="stTextInputRootElement"] { border-color: var(--luz) !important; }
"""

OPENING_CSS = """
.stApp .st-key-ventana { animation: abrir-ventana 1s var(--ease-salida) 2.05s both; }
@media (min-width: 769px) {
  section[data-testid="stSidebar"] { animation: aparecer-lateral .9s var(--ease-salida) 2.35s both; }
}
@media (prefers-reduced-motion: reduce) {
  .stApp .st-key-ventana, section[data-testid="stSidebar"] { animation: none !important; }
}
"""


def base_stylesheet() -> str:
    """Tokens, scene animations and the look of Streamlit's surfaces."""

    folder = files("frontend").joinpath("styles")
    return "\n".join(folder.joinpath(name).read_text(encoding="utf-8") for name in STYLE_FILES)


def stage_stylesheet(stage: str, *, knocked: bool = False) -> str:
    """Rules that exist only in one stage, so entrance animations never replay."""

    if stage == "outside":
        return OUTSIDE_CSS + (KNOCKED_CSS if knocked else "")
    if stage == "opening":
        return OPENING_CSS
    return ""
