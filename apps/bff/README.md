# BFF: FastAPI entre la vista y el camarero

Carril 3C de la [fase 3](../../PLAN_IMPLEMENTACION.md#fase-3-una-vista-de-cliente-bff-y-continuidad).
El BFF recibe la llegada del cliente, crea o recupera su visita y conversación,
envía los mensajes al camarero y publica el estado confirmado por HTTP y SSE,
según los [contratos públicos](../../packages/contracts/README.md) (versión 1,
sin cambios).

El camarero se ejecuta como un servicio independiente y el BFF lo invoca por
su endpoint estándar Responses 2.0. Solo el contenedor del agente conoce el
proyecto Foundry, el deployment del modelo y sus credenciales. El BFF conserva
sesiones web, visitas, snapshots, SSE y confirmaciones HITL.

## Preparar, probar y arrancar (Codespace)

Desde la raíz del repositorio:

```bash
./scripts/setup-bff.sh   # uv sync; genera apps/bff/uv.lock solo si no existe
./scripts/test-bff.sh    # pytest con el camarero simulado, nunca llama a Foundry
./scripts/run-bff.sh     # uvicorn en 0.0.0.0:8000, un único worker
```

`apps/bff/uv.lock` está versionado y los scripts usan `--frozen`.

Camarero remoto con el agente ejecutándose en otro proceso:

```bash
export BFF_WAITER=remote
export WAITER_AGENT_URL="http://127.0.0.1:8088"
./scripts/run-bff.sh
curl http://127.0.0.1:8000/healthz   # {"status":"ok","waiter":"remote"}
```

Arranca antes el agente con sus variables de Foundry mediante
`./scripts/run-local.sh`. Sin agente ni Foundry,
`BFF_WAITER=scripted ./scripts/run-bff.sh` conserva el camarero simulado para
desarrollo. La vista se conecta con
`FRONTEND_BFF_CLIENT=http FRONTEND_BFF_URL=http://127.0.0.1:8000 ./scripts/run-frontend.sh`
([README del frontend](../frontend/README.md)).

No hace falta cargar `.local/restaurant.env.sh` ni un entorno de `azd`: todo se
configura con variables de entorno o con un `.env` opcional en `apps/bff`
(ver [`.env.example`](.env.example)).

## Docker

El contexto de construcción es la raíz porque la imagen copia los contratos
compartidos. La etapa `runtime` no copia ni instala el paquete del agente.

```bash
docker build --file apps/bff/Dockerfile --target test --tag morcillaconf-bff:test .
docker run --rm morcillaconf-bff:test

docker build --file apps/bff/Dockerfile --target runtime --tag morcillaconf-bff:local .
docker run --rm --publish 8000:8000 --env-file apps/bff/.env \
  --volume morcillaconf-bff-data:/data morcillaconf-bff:local
```

La imagen se ejecuta como usuario no root, guarda SQLite en `/data` y expone un
`HEALTHCHECK` sobre `/healthz`. El workflow
[`bff-image.yml`](../../.github/workflows/bff-image.yml) ejecuta la etapa
`test` y publica `ghcr.io/<owner>/morcillaconf-bff:<sha>`.

## Configuración

| Variable | Uso | Por defecto |
|---|---|---|
| `BFF_WAITER` | `remote` (Responses 2.0) o `scripted` (desarrollo) | `remote` |
| `WAITER_AGENT_URL` | URL base del agente; obligatorio con `remote` | – |
| `WAITER_AGENT_TIMEOUT_SECONDS` | Tiempo máximo de una invocación remota | `60` |
| `BFF_DATABASE_PATH` | SQLite del BFF: sesiones, visitas, eventos y resultados | `data/bff.db` |
| `MEMORY_DATABASE_PATH` | SQLite de la memoria del camarero | `data/memory.db` |
| `BFF_SQLITE_JOURNAL_MODE` | `WAL` local o `DELETE` sobre Azure Files | `WAL` |
| `MEMORY_MAX_ITEMS` | Límite de recuerdos por tipo | `20` |
| `WAITER_MAX_TURNS` | Mensajes por visita | `20` |
| `APP_ENVIRONMENT` | `development`, `test` o `production` | `development` |
| `BFF_SESSION_TTL_HOURS` | Duración de la sesión de demo | `12` |
| `BFF_EVENT_RETENTION` | Eventos que se conservan por conversación | `500` |
| `BFF_SSE_HEARTBEAT_SECONDS` | Intervalo del latido SSE | `15` |
| `BFF_SCRIPTED_DELAY_SECONDS` | Pausa del camarero simulado | `0` |
| `BFF_HOST`, `BFF_PORT` | Dirección de `run-bff.sh` (`BFF_PORT` también en la imagen) | `0.0.0.0`, `8000` |
| `SEATING_MCP_URL` | MCP de asientos (`http://127.0.0.1:8080/mcp` con `./scripts/run-mcp.sh`); sin ella no hay mesas | vacío |
| `SEATING_MCP_TIMEOUT_SECONDS` | Tiempo máximo de cada llamada al MCP | `5` |
| `BFF_ROOM_CACHE_SECONDS` | Caché de la sala por visita | `1` |

El BFF envía la identidad resuelta mediante `x-agent-user-id` y un contrato
tipado en el campo `input` de Responses. El agente rechaza discrepancias entre
ambas identidades. Con `SEATING_MCP_URL`, el BFF confirma y cancela propuestas;
el agente remoto usa su propia `SEATING_MCP_URL` para consultar y bloquear.

## API (`/v1`)

| Método y ruta | Respuesta |
|---|---|
| `POST /v1/sessions` `{"name": "Ana"}` | 201: `token`, `identity`, `presented_name`, `active_visit_id`, `waiter`, `expires_at` |
| `POST /v1/commands` (comando público) | 202 si queda `pending`; 200 con `completed` o `failed` |
| `GET /v1/commands/{event_id}` | Resultado propio por `event_id` |
| `GET /v1/conversations/{id}/snapshot` | `RestaurantSnapshot` confirmado |
| `GET /v1/conversations/{id}/events?after_cursor=N` | SSE: `id` = cursor, `event` = tipo, `data` = evento; honra `Last-Event-ID` |
| `GET /v1/conversations/{id}/room` | `RoomView` anónimo de la sala, con `mine` en los sitios propios |
| `GET /healthz` | `{"status": "ok", "waiter": ..., "seating": "on"/"off"}` |

Salvo `sessions` y `healthz`, todo exige `Authorization: Bearer <token>`. Los
errores de transporte devuelven un `PublicError`: `invalid_command` 422,
`unauthenticated` 401, `forbidden` 403, `not_found` 404, `conflict` e
`idempotency_conflict` 409, `cursor_expired` 410 (con `fetch_snapshot`),
`unavailable` 503 e `internal_error` 500. Un comando que termina en `failed`
se responde con 200 y su resultado. Nunca se devuelve el contenido recibido.

## Comportamiento

- **Identidad.** El nombre de la puerta es la única identidad. El actor ignora
  mayúsculas, tildes y espacios repetidos, y conserva la ñ: «María José» y
  «maria  JOSE» son el mismo cliente; «Peña» y «Pena», no. El nombre presentado
  es el escrito. El token se guarda solo como hash; los comandos nunca llevan
  identidad. No es autenticación real ni se usa Entra ID.
- **Nombre fijado por la aplicación.** El camarero recibe el nombre de la
  puerta, no lo pregunta y no lo cambia aunque en el chat se diga otro: la
  aplicación sobrescribe `customer.presented_name` en cada turno.
- **Llegada.** Saludo determinista e instantáneo, sin modelo: «Hombre, {nombre},
  ¿qué tal, majo/maja?». El camarero no vuelve a saludar.
- **Visita activa.** `POST /v1/sessions` devuelve la última visita del cliente
  y la vista la recupera con `resume_visit_id`: recargar o salir y volver a
  entrar no crea otra visita ni reenvía mensajes. `/new` abre una visita nueva,
  que pasa a ser la activa.
- **Mensajes.** Se responde `pending` al momento, el snapshot pasa a
  `processing` y el turno se ejecuta en segundo plano, uno por conversación.
  Al terminar se publican el snapshot confirmado y `command.status_changed`.
  Los fallos del modelo o del servicio se convierten en un `failed` con un
  mensaje en español, sin contenido del modelo ni datos personales. No hay
  `conversation.text_delta`: el camarero usa salida estructurada.
- **Idempotencia.** Clave `(actor, event_id)` con huella del comando: repetirlo
  devuelve el mismo resultado; otro contenido con la misma clave es
  `idempotency_conflict`.
- **Memoria.** Automática en el `SQLiteMemoryStore` del camarero. Consultar,
  corregir, borrar y olvidar todo actúan sobre la identidad de la sesión. Los
  recuerdos se muestran con ids cortos por cliente (`m1`, `m2`…) que no se
  reutilizan.
- **Continuidad.** Estado, borrador, historial del camarero (sesión de Agent
  Framework en JSON), eventos y resultados viven en SQLite y sobreviven a un
  reinicio. Un turno interrumpido por un reinicio se marca como fallido al
  arrancar.
- **Mesas y barra.** El MCP es la fuente de verdad. Antes de cada turno el
  BFF siembra su id de visita en la sesión del camarero (también en sesiones
  de la fase 3 con un id aleatorio) y le pasa el estado de asiento de la
  visita. Si el turno bloquea un sitio, el BFF guarda la propuesta pendiente
  con un `proposal_id` público. `table.confirmation_decided` la confirma
  (ocupada, con `seated_at`) o la rechaza (libre al momento) mediante el
  gateway, comprobando pertenencia, versión y caducidad; un doble clic o un
  reintento no confirma dos veces. El camarero responde con un mensaje fijo,
  sin modelo. Una confirmación tardía recibe «La reserva de … ha caducado».
  Lo que se decide fuera de un turno (confirmar, rechazar, caducar, `/new` o
  un reinicio del MCP) llega al camarero en el turno siguiente: el estado de
  asiento lleva `last_outcome` y los mensajes fijos se añaden a su historial.
  `/new` cancela una propuesta pendiente y se rechaza mientras el grupo está
  sentado. La consulta de la sala y cada turno reconcilian con el MCP: tras
  `./scripts/run-mcp.sh --reset` los sitios propios desaparecen. Si el MCP no
  responde: «El servicio de mesas no responde ahora mismo…».
- **Observabilidad.** Spans `bff.command`, `bff.waiter.turn` y
  `bff.seating.decision` con tipo,
  correlación, conversación y resultado, sin texto ni nombres. Solo se usa la
  API de OpenTelemetry: falta configurar un exportador (Application Insights,
  fase 9).

## Permisos en Azure Container Apps

En Azure, el BFF usa su identidad administrada a través de
`DefaultAzureCredential`. Esa identidad necesita un rol como **Azure AI User**
sobre el proyecto de Foundry. Con una identidad asignada por el usuario, define
`AZURE_CLIENT_ID`. No se guardan secretos en la imagen.

Ejecuta **una réplica** con un worker: los turnos se ejecutan en el proceso y el
estado vive en SQLite. SQLite dentro del contenedor no es durable; la
persistencia compartida llega en la fase 9.

## Límites

- El camarero real con Foundry y el MCP de asientos se validan en el
  Codespace; en CI se usa el camarero simulado.
- Una ocupación solo se libera tras el pago (pendiente de fase 4); para
  demostraciones se vacía la sala con `./scripts/run-mcp.sh --reset`.
- El modelo puede mencionar otro nombre o saludar en su texto; la aplicación
  fija el dato, no la redacción.
- Las reglas del saludo están duplicadas en `bff/greeting.py` y en el frontend;
  sus pruebas usan los mismos casos.
