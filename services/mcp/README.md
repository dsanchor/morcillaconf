# MCP de asientos

Servidor MCP HTTP determinista para consultar y asignar mesas y puestos
contiguos de barra. No contiene prompts ni toma decisiones de conversación.

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
export SEATING_LAYOUT_JSON='{"resources":[{"resource_id":"table-01","kind":"table","label":"Mesa 1","capacity":2,"display_order":10,"enabled":true},{"resource_id":"bar","kind":"bar","label":"Barra","capacity":4,"display_order":100,"enabled":true,"seat_prefix":"bar-seat"}]}'
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
