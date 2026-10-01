# Conocimiento del restaurante

Contenido original y sintético de la demo para la base de conocimiento de
Foundry IQ (fase 5). No es información alimentaria real: precios, recetas y
alérgenos existen solo para demostrar cómo el camarero, y más adelante el chef,
responden con fuentes.

| Documento | Fichero versionado | Se publica en | Fuente de conocimiento |
|---|---|---|---|
| Carta | [`menu/carta.md`](menu/carta.md) | contenedor `carta`, `carta.md` | `carta-de-la-casa` (blob) |
| Recetario | [`recipes/recetario.html`](recipes/recetario.html) → [`recipes/recetario.pdf`](recipes/recetario.pdf) | contenedor `recetario`, `recetario.pdf` | `recetario-de-la-casa` (índice propio `recetario-index`) |
| Ingredientes | [`ingredients/ingredientes.md`](ingredients/ingredientes.md) | contenedor `recetario`, `ingredientes.md` | `recetario-de-la-casa` (mismo índice) |

La tercera fuente, `web-bing`, es la búsqueda web de respaldo y no tiene
ficheros. Los tres documentos forman una única base de conocimiento,
`conocimiento-restaurante`; su aprovisionamiento está en el
[README principal](../../README.md#base-de-conocimiento-foundry-iq).

## Reglas del contenido

- Cada plato y cada ingrediente tiene un identificador estable en
  `kebab-case`. No cambia entre versiones: camarero, chef y despensa se
  refieren a él.
- La carta se organiza por las partidas de cocina de la demo (brasa, fritos y
  pinchos fríos); las bebidas se sirven en la barra.
- Cada entrada repite su procedencia (documento y versión) para que cualquier
  fragmento recuperado permita citar la fuente.
- Los alérgenos usan los 14 nombres de declaración obligatoria en la UE:
  cereales con gluten, crustáceos, huevos, pescado, cacahuetes, soja, leche,
  frutos de cáscara, apio, mostaza, sésamo, sulfitos, altramuces y moluscos.
  Cuando falta la ficha, se escribe «información pendiente de verificar»; el
  camarero no debe deducirlos.
- La carta es fija: no depende del día. Estar en la carta no acredita
  existencias; eso lo confirma la despensa.
- No se incluyen tiempos de espera: los estimará cocina.

## Cambiar el contenido

1. Edita el fichero y sube la versión en la cabecera y en cada entrada.
2. Si cambias el recetario, regenera el PDF con
   `./scripts/build-recetario-pdf.sh` y versiona el HTML y el PDF juntos.
3. Comprueba la coherencia con `./scripts/test.sh`
   (`tests/contract/test_knowledge_content.py`).
4. Publica con `./scripts/provision-knowledge.sh scripts/knowledge.env`, que
   vuelve a subir los ficheros y reindexa.
