"""Build index.html for the static design preview from the package modules (stdlib only).

Run ``python3 build_preview.py`` here and serve this folder, for example with
``python3 -m http.server``. The preview simulates the waiter in JavaScript;
the Streamlit view uses FakeBffClient instead.
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent / "src" / "frontend"
sys.path.insert(0, str(PACKAGE.parent))

from frontend.facade import facade_html  # noqa: E402
from frontend.floor_plan import floor_plan_svg, waiter_icon_svg  # noqa: E402

CONTRACT = """<!--
THESIS: La puerta de un mesón burgalés de noche es la interfaz: entrar es
identificarse. Rechaza el chatbot con cabecera, burbujas genéricas y panel de mando.
OWN-WORLD: Noche castellana. Piedra de Hontoria bajo un farol, portón de roble
claveteado con forja bajo arco de dovelas, luz ámbar como único color de estado,
vino de Ribera para el cliente, planta cenital en piedra sobre barro. Alegreya.
STORY: Escribes tu nombre, la puerta se abre, el camarero te saluda por tu nombre
y se acerca en la planta. Entiendes que la sala solo cambia cuando el sistema
confirma algo.
FIRST VIEWPORT: Fachada a sangre; portón cerrado centrado, farol a la derecha,
agujas de la catedral al fondo; nombre y «Entrar» sobre el umbral.
FORM: fijada por el brief, sin tirada. Puesta en escena: llamar, abrir, entrar.
-->"""

COMMANDS = (
    ("/new", "Nueva visita"),
    ("/exit", "Salir"),
)

SEND_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h14M13 6l6 6-6 6" '
    'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)
MENU_ICON = (
    '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">'
    '<path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round"/></svg>'
)


def _commands() -> str:
    items = []
    for command, label in COMMANDS:
        base, *args = command.split(" <")
        shown = base + "".join(
            f' <span class="arg">&lt;{arg.rstrip(">")}&gt;</span>' for arg in args
        )
        value = command.replace("<", "&lt;").replace(">", "&gt;")
        items.append(
            f'<li><button class="comando" type="button" data-cmd="{value}" '
            f'title="{label}" aria-label="{value}: {label}">'
            f"<code>{shown}</code></button></li>"
        )
    return "".join(items)


def build() -> str:
    scene_css = (PACKAGE / "styles" / "scene.css").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrada al restaurante</title>
{CONTRACT}
<style>
{scene_css}
</style>
<link rel="stylesheet" href="preview.css">
</head>
<body>
<template id="icono-camarero">{waiter_icon_svg()}</template>
{facade_html("cerrada")}
<form class="umbral" novalidate>
  <p class="aviso" id="aviso-nombre" role="alert" hidden>Dinos tu nombre y te abrimos.</p>
  <label class="oculto-accesible" for="nombre">Tu nombre</label>
  <input id="nombre" name="nombre" autocomplete="given-name" placeholder="Tu nombre"
    maxlength="40" aria-describedby="aviso-nombre">
  <button type="submit">Entrar</button>
</form>
<div class="interior" hidden>
  <button class="abrir-menu" type="button" aria-label="Comandos">{MENU_ICON}<code>/comandos</code></button>
  <aside class="comandos" aria-label="Comandos">
    <div class="quien"><span class="nombre"></span></div>
    <ul class="lista">{_commands()}</ul>
  </aside>
  <main class="sala">
    <section class="ventana" aria-label="Conversación con el camarero">
      <div class="mensajes" role="log" aria-live="polite"></div>
      <form class="redactor">
        <label class="oculto-accesible" for="mensaje">Mensaje para el camarero</label>
        <input id="mensaje" autocomplete="off" placeholder="Escribe al camarero">
        <button type="submit" aria-label="Enviar">{SEND_ICON}</button>
      </form>
    </section>
    <p class="simulado">Camarero simulado</p>
    <div class="planta-marco">{floor_plan_svg("Ana", "barra")}</div>
  </main>
</div>
<script src="preview.js"></script>
</body>
</html>
"""


if __name__ == "__main__":
    (HERE / "index.html").write_text(build(), encoding="utf-8")
    print("index.html generado")
