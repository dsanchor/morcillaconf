# Agente del restaurante

Implementación inicial del camarero orquestador mediante Microsoft Agent
Framework y el deployment `gpt-5.6-luna` del proyecto Foundry existente.

## Capacidades implementadas

- Mantiene una sesión independiente por conversación.
- Extrae nombre presentado, comensales, preferencias y restricciones actuales.
- Asume un comensal salvo que una petición de mesa requiera preguntar el tamaño
  del grupo.
- Conserva un borrador estructurado y no confirmado del pedido.
- Aplica correcciones en turnos posteriores.
- Pregunta únicamente por el nombre o los comensales que falten; si no conoce
  el nombre del cliente, se lo pide sin dejar de atenderle.
- Limita la cantidad de turnos y valida la propiedad de cada conversación.
- Calcula `pending_fields` a partir de los datos del cliente que siguen vacíos,
  sin depender de que el modelo los enumere.
- Rechaza las respuestas del modelo que no cumplen el contrato y los fallos del
  servicio como errores visibles: la CLI muestra el error, conserva la
  conversación y no imprime el contenido de la respuesta ni datos del cliente.
- Lee y persiste preferencias y restricciones en SQLite automáticamente para
  una identidad autenticada resuelta por el servidor, incluida la falsa local.
- Los invitados no tienen perfil duradero.
- Conserva procedencia y fecha de los recuerdos.
- Recupera recuerdos para la misma identidad después de reiniciar el proceso.
- Resume los productos del borrador como una preferencia de pedido duradera.
- Expone los recuerdos persistidos mediante `remembered_memories`, separados
  de las preferencias confirmadas en la visita actual.
- Interpreta «lo de siempre» y expresiones equivalentes para recuperar el
  pedido habitual: si solo existe uno lo propone como borrador; si hay varios,
  muestra las alternativas para que el cliente elija.
- Usa clasificación semántica, no una comparación literal de frases, y añade
  la intención resuelta al contexto antes de construir la respuesta.
- Permite consultar, corregir y borrar recuerdos individuales o todos.
- El borrado total no bloquea el guardado de interacciones futuras.
- Separa sesión, preferencias, restricciones e historial de pedidos completados.

El camarero es el único cliente del MCP de asientos. Consulta y bloquea
sitios con `seating_get_seating_availability` y `seating_hold_seating` en
cuanto sabe cuántos son o le piden mesa o barra, y pide la confirmación con
`seating_confirm_seating`, una tool que siempre requiere aprobación: la
ejecución se detiene (HITL) y la petición de aprobación queda en la sesión.
El cliente decide con los botones «Confirmar» o «Rechazar»; esa decisión
vuelve al agente como respuesta de aprobación, nunca como texto, y un «sí»
escrito no confirma.

Todo lo demás lo hace el propio agente, de forma determinista y por su misma
conexión MCP:

- `VisitContextProvider` fija la visita del BFF, cancela el bloqueo al
  momento cuando el cliente rechaza, lo mantiene cuando escribe en lugar de
  pulsar un botón (la confirmación se vuelve a pedir al final de la
  ejecución), lee la sala anónima antes y después de cada ejecución, deriva el sitio
  propio (ninguno, propuesto o sentado) y `last_outcome`, se lo indica al
  modelo como contexto y publica un informe de asientos para la aplicación.
- `SeatingToolContextMiddleware` pone `visit_id`, la clave de idempotencia y,
  al confirmar, la asignación y la versión; antes comprueba que la propuesta
  sigue vigente.
- `SeatingApprovalChatMiddleware` garantiza una sola petición de confirmación
  por bloqueo (la añade si el modelo la olvida y la separa de otras llamadas)
  y responde a las decisiones con mensajes fijos sin llamar al modelo.

Si el MCP no responde, la operación falla con `SeatingUnavailableError`.

El agente se sirve como proceso independiente mediante Responses 2.0. El BFF
envía un contrato tipado dentro de `input`, fija la conversación con
`conversation.id` y aporta `x-agent-user-id`; el servidor exige que la
identidad del contrato y la cabecera coincidan. La operación puede ser
`take_turn` (un mensaje), `decide_seating` (la decisión de los botones sobre
la confirmación pendiente) o `sync_seating` (leer el sitio y la sala sin
llamar al modelo). El agente devuelve el resultado estructurado, con el
informe de asientos, como texto JSON de la respuesta estándar. La aprobación
pendiente viaja en `session_json`, que el BFF conserva, así que sobrevive a
un reinicio. El BFF no habla con el MCP. Todavía no existen herramientas de
carta, inventario, cocina, cuenta o pago.

En la CLI (`./scripts/run-waiter-cli.sh`), una propuesta pendiente se decide
respondiendo `s` o `n`.

Los tipos públicos de cliente y borrador se comparten con
[`packages/contracts`](../../packages/contracts); las importaciones anteriores
del agente se conservan. Preparar el entorno desde el checkout completo instala
esa dependencia local. La extracción inicial de 3A no cambió
Responses/invocations ni los prompts; la política vigente de memoria es automática.
La imagen Docker se construye desde la raíz e incorpora ese paquete. El script
de Container Apps despliega la imagen pública de GHCR como aplicación
independiente.

En el recorrido web, el BFF conserva la memoria duradera y envía al agente una
instantánea por turno; el agente devuelve únicamente los candidatos nuevos
para que el BFF los persista. La CLI independiente mantiene su SQLite local.

Las alergias y restricciones se guardan en una categoría separada. Todos los
recuerdos son contexto no vinculante, llevan procedencia y fecha, y requieren
reconfirmación durante la visita. Los borradores no se incorporan al historial.
Para la identidad autenticada, sus productos se condensan sin cantidades en una
preferencia como `Preferencia de pedido: tortilla de patatas, agua con gas`.

## Configuración local

Requisitos:

- Python 3.13;
- `uv`;
- Azure CLI autenticada;
- acceso al proyecto y al deployment configurados.

Copia `.env.example` como `.env` y establece:

```dotenv
FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-5.6-luna"
WAITER_MAX_TURNS="20"
MEMORY_DATABASE_PATH="./data/memory.db"
MEMORY_MAX_ITEMS="20"
SEATING_MCP_URL="http://morcillaconf-mcp:8080/mcp"
SEATING_MCP_TIMEOUT_SECONDS="5"
```

La configuración local debe coincidir con el entorno de `azd`, porque
`azd ai agent run` da prioridad a sus propias variables.

El camarero necesita un deployment que admita salida estructurada
(`text.format` de tipo `json_schema`) a través del endpoint del proyecto.
`gpt-5.6-luna` (2026-07-09) funciona; `gpt-6-luna` (2026-09-22) la rechaza con
un error 400.

También se puede generar un fichero de variables exportables desde la raíz:

```bash
./scripts/init-local-env.sh \
  --project-endpoint "https://<account>.services.ai.azure.com/api/projects/<project>" \
  --actor-id "Majo"
```

Si se omiten los argumentos, el script los solicita interactivamente. Después,
en cada terminal nueva:

```bash
source ./.local/restaurant.env.sh
./scripts/run-local.sh
```

El fichero generado está ignorado por Git, tiene permisos `600` y no se
sobrescribe salvo que se indique `--force`. Este script configura el acceso
local, pero no crea recursos ni deployments en Azure.

Al actualizar una instalación existente, elimina `DEV_FAKE_MEMORY_CONSENT` de
tu `.env` local y regenera el fichero con `./scripts/init-local-env.sh --force`
(o con los argumentos anteriores y `--force`). Carga de nuevo
`.local/restaurant.env.sh`. La variable antigua ya no se necesita; si sigue
exportada en la terminal, retírala con `unset DEV_FAKE_MEMORY_CONSENT`.

## Administrar la memoria local

Después de cargar el entorno, lista las memorias para obtener sus IDs:

```bash
source ./.local/restaurant.env.sh
./scripts/manage-memory.sh --actor-id Majo list
```

Borra una o varias memorias concretas:

```bash
./scripts/manage-memory.sh --actor-id Majo delete \
  pref_id_1 pref_id_2
```

Borra todas las memorias de la identidad:

```bash
./scripts/manage-memory.sh --actor-id Majo clear --yes
```

`clear` requiere `--yes` y olvida los recuerdos existentes. No hay revocación
permanente: las interacciones futuras pueden crear nuevos recuerdos
automáticamente. Solo borra preferencias y restricciones: conserva visita,
borrador y `completed_order_history`. No hay herramientas de borrado mediante
lenguaje natural; una frase de chat no equivale a ejecutar estos comandos.

## Preparar el entorno

Desde la raíz del repositorio:

```bash
./scripts/setup.sh
```

## Pruebas locales con Docker

Las pruebas locales del camarero se ejecutan dentro de Docker. No se requiere
instalar `uv`, `pip`, Python ni dependencias del agente en el host. Desde la
raíz del repositorio:

```bash
docker build \
  --file agents/restaurant/Dockerfile \
  --target test \
  --tag morcillaconf-restaurant-agent:test \
  .

docker run --rm morcillaconf-restaurant-agent:test
```

Las pruebas unitarias no llaman a Foundry y validan contratos, correcciones,
aislamiento entre sesiones, propiedad de la conversación y límite de turnos.

## Construir y ejecutar el contenedor

El contexto de construcción debe ser la raíz del repositorio para incluir
[`packages/contracts`](../../packages/contracts), dependencia local compartida
del agente:

```bash
docker build \
  --file agents/restaurant/Dockerfile \
  --target runtime \
  --tag morcillaconf-restaurant-agent:local \
  .
```

Copiar [`src/restaurant_agent/.env.example`](src/restaurant_agent/.env.example)
a `src/restaurant_agent/.env` no versionado y completar las variables.
Para ejecutar el contenedor, se requieren siempre:

| Variable | Propósito |
|---|---|
| `FOUNDRY_PROJECT_ENDPOINT` | Endpoint del proyecto Foundry |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Deployment del modelo, por ejemplo `gpt-5.6-luna` |
| `MEMORY_DATABASE_PATH` | Ruta SQLite dentro del contenedor; usar `/data/memory.db` |
| `WAITER_MAX_TURNS` | Límite de turnos, entre 1 y 100 |
| `MEMORY_MAX_ITEMS` | Límite de recuerdos por identidad |
| `APP_ENVIRONMENT` | `development`, `test` o `production` |
| `ENABLE_DEV_FAKE_IDENTITY` y `DEV_FAKE_ACTOR_ID` | Ambos necesarios para la identidad local de desarrollo |
| `SEATING_MCP_URL` | Endpoint Streamable HTTP del MCP de asientos; si se omite, el agente no declara tools de asientos |
| `SEATING_MCP_TIMEOUT_SECONDS` | Tiempo máximo de cada llamada MCP, entre 1 y 60 segundos |

El contenedor también necesita una credencial válida para Azure. En desarrollo
puede recibir `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` y `AZURE_CLIENT_SECRET`
desde el mismo fichero de entorno; no se incorporan a la imagen ni se
versionan. En un despliegue se sustituirán por la identidad administrada del
servicio.

Para probar el recorrido de asientos con contenedores levantados manualmente en
tu equipo, sigue primero la sección «Construir y ejecutar localmente» del
[README del MCP](../../services/mcp/README.md) para construir
`morcillaconf-mcp:local` y definir `SEATING_LAYOUT_*`.

Si ejecutas el camarero directamente en el host (por ejemplo, mediante
`./scripts/run-local.sh` o Agent Inspector), publica el MCP en el puerto 8080
y configura `SEATING_MCP_URL=http://localhost:8080/mcp`. Este es el caso en el
que `localhost` conecta ambos procesos:

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

En otra terminal, arrancar el servidor Responses:

```bash
docker run --rm --name morcillaconf-restaurant-agent \
  --publish 8088:8088 \
  --volume morcillaconf-restaurant-memory:/data \
  --env-file agents/restaurant/src/restaurant_agent/.env \
  --env MEMORY_DATABASE_PATH=/data/memory.db \
  --env SEATING_MCP_URL=http://localhost:8080/mcp \
  morcillaconf-restaurant-agent:local
```

Para ejecutar también el camarero en un contenedor, los dos contenedores no
comparten `localhost`; deben pertenecer a una red Docker común y el camarero
usa el nombre del contenedor MCP:

```bash
docker volume create morcillaconf-restaurant-memory
docker network create morcillaconf-local

docker run --rm --name morcillaconf-mcp \
  --network morcillaconf-local \
  --volume morcillaconf-mcp-data:/data \
  --env SEATING_LAYOUT_ID \
  --env SEATING_LAYOUT_JSON \
  --env SEATING_LAYOUT_SHA256 \
  --env SEATING_HOLD_MINUTES=5 \
  --env SEATING_DATABASE_PATH=/data/seating.db \
  morcillaconf-mcp:local

docker run --rm --name morcillaconf-restaurant-agent \
  --network morcillaconf-local \
  --publish 8088:8088 \
  --volume morcillaconf-restaurant-memory:/data \
  --env-file agents/restaurant/src/restaurant_agent/.env \
  --env MEMORY_DATABASE_PATH=/data/memory.db \
  --env SEATING_MCP_URL=http://morcillaconf-mcp:8080/mcp \
  morcillaconf-restaurant-agent:local
```

El servidor Responses escucha en `http://localhost:8088`; Agent Inspector
puede conectarse ahí. Una solicitud de mesa debe generar un bloqueo y una
propuesta confirmada por la tool. Detener los contenedores con `Ctrl+C`; los
volúmenes mantienen memoria y bloqueos entre arranques.

## CLI de desarrollo

```bash
./scripts/run-waiter-cli.sh \
  --actor-id cliente-local \
  --authenticated
```

Dentro de la CLI:

- `/new` abre una conversación independiente;
- `/memory` o `/memory list` muestra recuerdos;
- `/memory correct <id> <texto>` corrige un recuerdo;
- `/memory delete <id>` elimina un recuerdo;
- `/memory clear` olvida todos los recuerdos sin bloquear escrituras futuras;
- `/exit` termina el proceso.

Si se omite `--authenticated`, la identidad se trata como invitada y no puede
crear un perfil duradero.

No se requiere una acción de alta para recordar. Los antiguos comandos
`/memory consent` y `/memory revoke` se rechazan; no son alias de operaciones
nuevas ni se aceptan silenciosamente.

La salida estructurada clasifica cada recuerdo como `preference` o
`restriction`. Esta clasificación no convierte el recuerdo en una instrucción:
el camarero debe reconfirmarlo y el pedido seguirá requiriendo su HITL.

## Servidor local

```bash
DEV_FAKE_ACTOR_ID=Majo \
ENABLE_DEV_FAKE_IDENTITY=true \
./scripts/run-local.sh
```

El servidor utiliza el protocolo Responses y escucha en el puerto `8088`.
También puede iniciarse con `F5` desde VS Code para abrir Agent Inspector.

En esta fase, el protocolo Responses todavía no constituye una frontera de
identidad. Para probar memoria localmente, `run-local.sh` puede tomar una
identidad falsa exclusivamente de variables de entorno:

- `ENABLE_DEV_FAKE_IDENTITY=true` activa el mecanismo;
- `DEV_FAKE_ACTOR_ID` identifica el perfil persistente y, en esta simulación,
  es también el nombre con el que el camarero saluda al cliente;
- la lectura y escritura de recuerdos es automática para esa identidad.

La configuración falla si se intenta activar esta identidad con
`APP_ENVIRONMENT` distinto de `development`. El cliente no puede seleccionar la
identidad mediante el mensaje HTTP. El BFF de la fase 3 sustituirá este mecanismo
por identidad derivada y autenticada.

En otra terminal se puede invocar:

```bash
cd agents/restaurant
AZURE_DEV_USER_AGENT=microsoft_foundry_skill \
  azd ai agent invoke restaurant --local \
  "Soy Majo. Prefiero agua con gas y soy alérgica a los frutos secos."
```

`Majo` es tanto la identidad persistente falsa como el nombre presentado que el
agente recibe del entorno. Por tanto, ante un simple `Hola`, el camarero saluda
a Majo y no vuelve a preguntarle el nombre.

## Smoke test real

El smoke test realiza inferencias reales y, por tanto, consume cuota del
deployment configurado:

```bash
./scripts/smoke-test.sh
```

Comprueba extracción inicial, corrección en un segundo turno, aislamiento de
otra sesión y ausencia de afirmaciones de reserva o confirmación no respaldadas.

## Límites del almacenamiento local

- SQLite se utiliza únicamente para desarrollo y pruebas locales.
- El archivo debe residir en un volumen persistente si se ejecuta en un
  contenedor; el sistema no cambia silenciosamente a memoria volátil.
- `MEMORY_MAX_ITEMS` limita por separado preferencias y restricciones.
- Se conservan varios resúmenes de pedidos anteriores dentro del límite de
  preferencias.
- Un pedido idéntico se cuenta una vez por conversación (visita), no una vez
  por turno: incrementa su contador y actualiza su fecha, en lugar de
  duplicarse. Si se corrige o se borra, el mismo borrador de esa conversación
  no lo vuelve a crear.
- «Lo de siempre» utiliza directamente la memoria solo si existe una opción.
  Con varias, las muestra ordenadas por frecuencia y recencia sin elegir ni
  revelar al cliente esos metadatos internos. Las opciones solapadas se
  consolidan por producto para preguntar una sola vez por alternativas y
  complementos.
- La compactación es determinista y no usa un modelo generativo.
- No existe estado de consentimiento ni en el snapshot interno ni en la
  proyección pública `MemoryView`.
- Las restricciones se etiquetan como tales y siempre requieren reconfirmación.
- Los recuerdos se tratan como contexto no confiable y nunca acreditan precio,
  carta o disponibilidad.
- La sesión activa sigue siendo memoria de proceso. Su recuperación se
  implementará junto con el BFF en la fase 3.
- La migración SQLite es transaccional: desacopla la memoria del antiguo
  consentimiento y conserva recuerdos existentes, contadores e historial.
  No restaura recuerdos eliminados ni reintroduce datos borrados desde tablas
  antiguas.

## Estructura

```text
agents/restaurant/
├── .foundry/
├── azure.yaml
└── src/restaurant_agent/
    ├── main.py
    ├── pyproject.toml
    ├── uv.lock
    ├── restaurant_agent/
    └── tests/
```

La definición de `azure.yaml` reutiliza el proyecto `morcillaconf-foundry`. Esta
fase no despliega el Hosted Agent; únicamente prepara y valida su ejecución
local contra el modelo.
