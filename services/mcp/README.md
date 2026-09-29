# MCP de asientos

Servidor MCP HTTP determinista para consultar y asignar mesas y puestos
contiguos de barra. No contiene prompts ni toma decisiones de conversación.

## Reglas

- **Una mesa, un grupo.** Una mesa bloqueada u ocupada no está disponible para
  ninguna otra visita, aunque le queden sillas libres. Se elige la mesa libre
  más pequeña en la que cabe el grupo y, a igualdad, la de menor orden.
- **Barra.** Cada grupo ocupa puestos contiguos. Se elige el tramo libre que
  deja el menor hueco y, a igualdad, la posición más baja: en una barra vacía
  los grupos se sientan 1, 2, 3… en orden y sin huecos.
- **Preferencia.** `any` busca mesa y, si no cabe en ninguna, barra; `table`
  solo mesa; `bar` solo barra. Si nada encaja, `no_seating`.
- **Bloqueo, confirmación y cancelación.** `hold_seating` bloquea durante
  `SEATING_HOLD_MINUTES`. Una visita tiene como máximo un sitio activo: un
  bloqueo nuevo con otra clave sustituye en la misma transacción al bloqueo
  pendiente de esa visita (queda `replaced`); si no cabe nada, se conserva el
  anterior. `confirm_seating` lo convierte en ocupación, `cancel_seating_hold`
  lo cancela y libera el sitio al momento y, al caducar, queda `expired`. Una
  ocupación solo se libera con `release_seating`.
- **Errores.** Empiezan por un código estable: `no_seating:`, `conflict:`,
  `expired:`, `not_found:` o `idempotency_conflict:`.

## Herramientas

| Herramienta | Consumidor | Uso |
|---|---|---|
| `get_seating_availability` | Modelo del camarero | Plazas libres y `largest_group` por recurso |
| `hold_seating` | Modelo del camarero | Bloqueo temporal; `visit_id` e `idempotency_key` los pone el middleware |
| `confirm_seating` | Aplicación (BFF) | Confirmar un bloqueo vigente con su versión |
| `cancel_seating_hold` | Aplicación (BFF) | Rechazo del cliente o `/new` con propuesta pendiente |
| `get_seating_map` | Aplicación (BFF) | Sala anónima: estado por mesa y por puesto, marcas `mine` y última asignación de la visita |
| `release_seating` | Aplicación, tras el pago | Liberar una ocupación (pendiente de fase 4) |

El modelo solo recibe las dos primeras (`allowed_tools`). El mapa nunca
devuelve identificadores de otras visitas ni sus asignaciones.

## Arranque local con `uv`

```bash
./scripts/setup-mcp.sh
./scripts/run-mcp.sh           # conserva la sala
./scripts/run-mcp.sh --reset   # vacía la base de asientos antes de arrancar
```

Usa el layout versionado
[`layouts/morcillaconf-demo-v1.json`](layouts/morcillaconf-demo-v1.json):
Mesa 1 y Mesa 2 de 2, Mesa 3 y Mesa 4 de 4, Mesa 5 de 6 y una barra de 8
puestos. El hash se calcula con el propio servicio
(`python -m restaurant_mcp.layout FICHERO --sha256`). Variables opcionales:
`SEATING_LAYOUT_FILE`, `SEATING_DATABASE_PATH` (por defecto
`services/mcp/data/seating.db`), `SEATING_HOLD_MINUTES`, `MCP_HOST` y
`MCP_PORT` (8080). Para probar caducidades: `SEATING_HOLD_MINUTES=1`.

## Configuración

El layout llega por configuración, no se incorpora a la imagen:

- `SEATING_LAYOUT_ID`: identificador legible de la distribución.
- `SEATING_LAYOUT_JSON`: JSON con mesas y barras.
- `SEATING_LAYOUT_SHA256`: SHA-256 del JSON canónico (claves ordenadas y
  separadores compactos).
- `SEATING_DATABASE_PATH`: SQLite local durante este incremento.
- `SEATING_HOLD_MINUTES`: duración del bloqueo; cinco minutos por defecto.

Al arrancar, la aplicación valida el JSON y su hash antes de abrir la base. Si
la combinación ID/hash difiere de la persistida, elimina exclusivamente los
recursos, asignaciones y resultados idempotentes de este servicio y crea el
layout nuevo en una transacción. Un JSON inválido o hash incorrecto no modifica
la base existente.

## Pruebas locales con Docker

No hace falta instalar Python, `uv`, `pip` ni las dependencias del servicio en
el equipo anfitrión. La etapa `test` del `Dockerfile` contiene las dependencias
de desarrollo y ejecuta todas las pruebas del MCP:

```bash
docker build --target test --tag morcillaconf-mcp:test .
docker run --rm morcillaconf-mcp:test
```

Ejecutar estas órdenes desde [`services/mcp/`](.). El segundo comando termina
con el resultado de `pytest`; no inicia el servidor.

## Construir y ejecutar localmente

Construir la imagen de ejecución:

```bash
docker build --target runtime --tag morcillaconf-mcp:local .
```

Definir el layout y calcular su hash **dentro de la imagen**, sin utilizar
Python del anfitrión:

```bash
export SEATING_LAYOUT_ID="morcillaconf-demo-v1"
export SEATING_LAYOUT_JSON="$(cat layouts/morcillaconf-demo-v1.json)"
export SEATING_LAYOUT_SHA256="$(
  docker run --rm \
    --env SEATING_LAYOUT_JSON \
    --entrypoint /app/.venv/bin/python \
    morcillaconf-mcp:local \
    -c 'import json, os; from restaurant_mcp.seating import SeatingLayout; print(SeatingLayout.model_validate(json.loads(os.environ["SEATING_LAYOUT_JSON"])).fingerprint())'
)"
```

Crear un volumen dedicado y ejecutar el servicio:

```bash
docker volume create morcillaconf-mcp-data

docker run --rm --name morcillaconf-mcp \
  --publish 8080:8080 \
  --volume morcillaconf-mcp-data:/data \
  --env SEATING_LAYOUT_ID \
  --env SEATING_LAYOUT_JSON \
  --env SEATING_LAYOUT_SHA256 \
  --env SEATING_HOLD_MINUTES=5 \
  --env SEATING_DATABASE_PATH=/data/seating.db \
  morcillaconf-mcp:local
```

El servidor expone MCP Streamable HTTP en `http://localhost:8080/mcp`. El
navegador nunca lo invoca directamente; el workflow del camarero es su
consumidor. `visit_id` e `idempotency_key` de `hold_seating` los inyecta el
estado de sesión del camarero, no el modelo ni el navegador. Para detenerlo,
usar `Ctrl+C`; el volumen conserva SQLite entre arranques mientras el ID y hash
de layout no cambien.

## Imagen y CI

El `Dockerfile` crea una imagen independiente. El workflow
`.github/workflows/mcp-image.yml` valida, construye y publica la imagen en
GitHub Packages de este repositorio cuando cambian el servicio o su workflow.
