# Frontend: vista de cliente en Streamlit

Carril 3B de la [fase 3](../../PLAN_IMPLEMENTACION.md#fase-3-una-vista-de-cliente-bff-y-continuidad):
una única vista de cliente que habla solo con el contrato público del BFF
([`packages/contracts`](../../packages/contracts/README.md)). Por defecto el
camarero es **simulado** mediante `FakeBffClient` y la vista lo indica con
«Camarero simulado». Con `FRONTEND_BFF_CLIENT=http` la vista habla con el
[BFF](../bff/README.md) y, a través de él, con el camarero real.

## Probar y ejecutar localmente con Docker

El frontend se prueba y ejecuta sin instalar Python, `uv`, `pip` ni
dependencias en el host. Desde la raíz del repositorio, construir y ejecutar la
etapa de pruebas:

```bash
docker build \
  --file apps/frontend/Dockerfile \
  --target test \
  --tag morcillaconf-frontend:test \
  .

docker run --rm morcillaconf-frontend:test
```

Construir la imagen de ejecución:

```bash
docker build \
  --file apps/frontend/Dockerfile \
  --target runtime \
  --tag morcillaconf-frontend:local \
  .
```

Copiar [`.env.example`](.env.example) a un `.env` local no versionado. Las
variables disponibles son:

| Variable | Valores | Por defecto |
|---|---|---|
| `FRONTEND_BFF_CLIENT` | `fake` (simulado) o `http` (BFF real) | `fake` |
| `FRONTEND_BFF_URL` | URL base del BFF con `http` | `http://127.0.0.1:8000` |
| `FRONTEND_BFF_TIMEOUT_SECONDS` | Espera máxima de cada llamada y del stream SSE, 1-300 | `30` |
| `FRONTEND_FAKE_PAUSE_SECONDS` | Pausa simulada del camarero antes de responder, 0-10 | `0.9` |
| `FRONTEND_PORT` | Puerto del servidor Streamlit dentro del contenedor | `8501` |

Ejecutar la imagen:

```bash
docker run --rm --name morcillaconf-frontend \
  --publish 8501:8501 \
  --env-file apps/frontend/.env \
  morcillaconf-frontend:local
```

La vista queda disponible en `http://localhost:8501`. Con
`FRONTEND_BFF_CLIENT=http`, `FRONTEND_BFF_URL` debe apuntar al BFF accesible
desde el contenedor. En ningún caso hay que configurar credenciales de
Foundry, MCP o agentes en este contenedor: solo el BFF las usa.

## Preparar, probar y arrancar en Codespaces

Desde la raíz del repositorio:

```bash
./scripts/setup-frontend.sh   # uv sync --frozen en apps/frontend
./scripts/test-frontend.sh    # pytest, incluido el recorrido con AppTest
./scripts/run-frontend.sh     # streamlit en 0.0.0.0:8501, sin abrir navegador
```

`apps/frontend/uv.lock` está versionado y fija Streamlit 1.64.0; los scripts
usan `--frozen`. Si falta el lockfile, `setup-frontend.sh` lo genera y hay que
versionarlo. En el Codespace abre el puerto 8501 reenviado.

### Con el BFF real

Arranca el BFF en otra terminal (ver [su README](../bff/README.md)) y después:

```bash
FRONTEND_BFF_CLIENT=http FRONTEND_BFF_URL=http://127.0.0.1:8000 ./scripts/run-frontend.sh
```

El cliente HTTP (`http_client.py`) usa solo la biblioteca estándar (`urllib`
en hilos con `asyncio.to_thread`), así que no cambia `uv.lock`. El nombre de la
puerta se envía una sola vez para abrir la sesión de demo; después solo viaja
un token opaco. Si el BFF no responde, la puerta sigue cerrada con un aviso.
«Camarero simulado» solo aparece con el falso o con el camarero simulado del
BFF (`BFF_WAITER=scripted`).

## Imagen de contenedor y despliegue en Container Apps

El frontend se empaqueta en su propia imagen, independiente del resto de
componentes, según [CONVENCIONES](../../CONVENCIONES.md#contenedores-y-workflows-de-imagen).
La etapa `runtime` del [`Dockerfile`](Dockerfile):

- usa `python:3.13-slim` y `uv sync --frozen --no-dev` con `apps/frontend/uv.lock`,
  primero las dependencias en una capa propia y después el propio frontend;
- solo incluye `packages/contracts`, `src` y `.streamlit`; el
  [`.dockerignore`](../../.dockerignore) de la raíz excluye `.env`, entornos
  virtuales y cachés;
- se ejecuta con un usuario sin privilegios desde `apps/frontend`, de modo que
  se aplica `.streamlit/config.toml`;
- arranca Streamlit sin navegador en `0.0.0.0:$FRONTEND_PORT` y comprueba su
  salud con `/_stcore/health`.

Las variables `FRONTEND_*` se pasan en tiempo de ejecución (`--env-file` o
`-e`) y no requieren reconstruir la imagen; también el modo `http` con el
BFF.

### Imagen publicada

El workflow [`frontend-image.yml`](../../.github/workflows/frontend-image.yml)
se activa en `push` solo con cambios en `apps/frontend/**`,
`packages/contracts/**`, `.dockerignore` o el propio YAML. Ejecuta la etapa
`test`, comprueba que la etapa `runtime` importa la aplicación y publica la
imagen `runtime` en GitHub Packages:

```text
ghcr.io/dsanchor/morcillaconf-frontend:<sha-del-commit>
```

No se publica la etiqueta `latest`.

### Desplegar en Azure Container Apps

> Este despliegue **todavía no se ha ejecutado**. Crear estos recursos tiene
> coste y requiere aprobación previa.

Pasos con la CLI de Azure (`az extension add --name containerapp`):

```bash
RG=rg-morcillaconf
LOCATION=swedencentral
ENV_NAME=cae-morcillaconf
APP_NAME=ca-morcillaconf-frontend
IMAGE=ghcr.io/dsanchor/morcillaconf-frontend:<sha-del-commit>

az group create --name "$RG" --location "$LOCATION"
az containerapp env create --name "$ENV_NAME" --resource-group "$RG" \
  --location "$LOCATION"

az containerapp create --name "$APP_NAME" --resource-group "$RG" \
  --environment "$ENV_NAME" \
  --image "$IMAGE" \
  --registry-server ghcr.io \
  --registry-username <usuario-github> \
  --registry-password <PAT con read:packages> \
  --target-port 8501 --ingress external \
  --min-replicas 1 --max-replicas 1 \
  --env-vars FRONTEND_BFF_CLIENT=fake FRONTEND_PORT=8501

az containerapp show --name "$APP_NAME" --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn --output tsv
```

Para una versión nueva, `az containerapp update --name "$APP_NAME"
--resource-group "$RG" --image ghcr.io/dsanchor/morcillaconf-frontend:<sha>`.
Para usar el [BFF](../bff/README.md), basta con cambiar las variables
(`FRONTEND_BFF_CLIENT=http` y `FRONTEND_BFF_URL` con la URL del BFF) mediante
`az containerapp update --set-env-vars ...`.

Particularidades de Streamlit:

- El estado de cada sesión vive en la memoria de la réplica y la conexión usa
  WebSockets. Usa una sola réplica (`--min-replicas 1 --max-replicas 1`) o, si
  se escala, activa la afinidad de sesión (`az containerapp ingress sticky-sessions
  set --affinity sticky`).
- Con `--min-replicas 0` la aplicación escala a cero y la primera visita paga
  un arranque en frío.
- El camarero simulado guarda su estado en memoria: se pierde al reiniciar o
  redesplegar la réplica.
- El PAT solo necesita `read:packages`; Container Apps lo guarda como secreto
  de la aplicación. Si el paquete se hace público, se pueden omitir las
  credenciales del registro.

## Recorrido

1. **Fuera:** fachada nocturna con el nombre y «Entrar» sobre el umbral. Sin
   nombre, la puerta tiembla y aparece «Dinos tu nombre y te abrimos.».
2. **Entrando:** el nombre es la identidad única de la demo. El
   adaptador la vincula (`ActorContext(actor_id=nombre, authenticated=True)`),
   envía `customer.arrived` y guarda el snapshot. Una sola ejecución muestra la
   puerta abriéndose, la ventana, el saludo y el camarero acercándose; la
   siguiente ya pinta la sala sin repetir la animación.
3. **Dentro:** lateral con el nombre y los comandos (`/new`, `/memory`,
   `/memory clear` y `/exit` se pulsan; los que llevan argumentos se escriben en
   la conversación), ventana de conversación, «Camarero simulado» y el plano.
   `/exit` vuelve a la puerta cerrada.

El camarero simulado saluda con «Hombre, {nombre}, ¿qué tal, maja?» o «majo»
según el nombre, responde frases breves y convierte «prefiero …» y «soy
alérgica a …» en recuerdos visibles. Los recuerdos pertenecen a la identidad:
sobreviven a `/new` y a salir y volver a entrar con el mismo nombre mientras
dure la sesión del navegador.

Entrar con el mismo nombre recupera la visita activa (la última) en lugar de
abrir otra: `/exit` y volver a entrar continúa la conversación, y solo `/new`
abre una visita nueva. Con el falso, recargar la página empieza de cero porque
el estado vive en la sesión del navegador; con el BFF, recargar y volver a
escribir el nombre recupera la misma visita sin reenviar mensajes. Si al
recuperarla el camarero aún responde un mensaje anterior, la vista sigue el
stream hasta que termina.

### Mesas y barra (fase 4)

- Cuando el camarero bloquea un sitio, aparece bajo la conversación una
  propuesta con el lugar y los asientos («Mesa 3 · 3 de 4 asientos» o
  «Barra · Puestos 1 a 3») y dos botones: «Confirmar» y «Rechazar». Envían
  `table.confirmation_decided` con el `proposal_id` y la versión; una frase
  en el chat nunca confirma. Si la reserva ha caducado, la vista muestra el
  aviso y la tarjeta desaparece.
- El plano se dibuja desde `RoomView` y se refresca solo cada 3 segundos
  (`st.fragment`), sin tocar la conversación ni el cuadro de texto: otros
  grupos aparecen como figuras anónimas en sus sillas, los sitios bloqueados
  por otros con un discontinuo ámbar y la propuesta propia en vino. Las mesas
  se asignan a los huecos del dibujo por orden de presentación: las cuatro
  primeras a las mesas redondas, la quinta a la mesa larga y la barra a los
  ocho taburetes; cada mesa dibuja tantas sillas como capacidad y lleva su número, tomado del rótulo «Mesa N» del layout (en la sala decorativa, 1 a 5). Los taburetes no se numeran.
- Al confirmar, los acompañantes aparecen detrás del cliente en la puerta y el
  grupo camina hasta sus sillas o taburetes. Se reproduce una vez en este
  navegador (tras recargar ya están sentados), solo con CSS y respeta
  `prefers-reduced-motion`.
- Sin asientos en el BFF (`seating_enabled=false`) el plano es la sala
  decorativa de la fase 3.
- El falso aplica las mismas reglas en memoria (mesas exclusivas, barra sin
  huecos, caducidad, `/new`), pero solo dentro de una sesión del navegador:
  para ver clientes en paralelo hace falta el BFF.

## Fronteras de arquitectura

- `app.py` solo usa el protocolo `BffClient` a través de `VisitSession`.
  No importa agentes, Agent Framework, Foundry, MCP ni bases de datos; una
  prueba lo comprueba.
- La vista sustituye su proyección por snapshots confirmados. El texto del
  camarero no mueve el plano ni cambia estados; los fragmentos
  `conversation.text_delta` se muestran como provisionales.
- Los controles se habilitan según `allowed_actions`; el servidor (aquí, el
  falso) sigue validando cada comando.
- Cada comando conserva su `event_id`. Si un rerun interrumpe una operación,
  la siguiente ejecución consulta `get_result(event_id)` en lugar de
  reenviarla. Un cursor caducado se recupera con un snapshot nuevo.
- `FakeBffClient` sigue el contrato 3A: resultados `pending`/`completed`/
  `failed`, snapshots válidos con `pending_fields`, `allowed_actions`,
  `process_status` y cursor, eventos correlacionados, idempotencia por
  `(actor_id, event_id)` y errores públicos explícitos. Su stream reproduce el
  registro y termina; el del BFF queda abierto y la vista lo cierra cuando el
  comando se resuelve.

## Módulos

| Módulo | Responsabilidad |
|---|---|
| `app.py` | Vista Streamlit y estados `outside`, `opening` e `inside` |
| `config.py` | Selección explícita del adaptador (`FRONTEND_BFF_CLIENT`, `FRONTEND_BFF_URL`) |
| `http_client.py` | `HttpBffClient`: API HTTP y SSE del BFF con la biblioteca estándar |
| `fake_client.py` | `FakeRestaurant` y `FakeBffClient`: BFF simulado en memoria |
| `visit.py` | Proyección de la visita, comandos, eventos y tarjetas |
| `slash_commands.py` | Comandos escritos con «/» |
| `markup.py` | Fragmentos HTML escapados: burbujas, tarjetas, lateral, escena |
| `facade.py`, `floor_plan.py`, `greeting.py` | Escena y saludo, solo biblioteca estándar |
| `styles/scene.css`, `styles/app.css` | Tokens y animaciones; adaptación al DOM de Streamlit |

Las escenas SVG se insertan con `st.markdown(..., unsafe_allow_html=True)`
porque `st.html` elimina el SVG al sanear. Todo texto dinámico se escapa en
`markup.py`. El CSS se inyecta con `st.html("<style>…</style>")` y se apoya en
las clases `st-key-*` de los contenedores con clave y en atributos
`data-testid` estables, revisados contra el código de Streamlit 1.64.
No se usa JavaScript: todo se anima con keyframes y respeta
`prefers-reduced-motion`.

## Vista previa estática del diseño

`preview/` conserva el prototipo aprobado como herramienta de diseño, sin
dependencias. Importa los módulos de escena del paquete y simula el camarero
en JavaScript:

```bash
cd apps/frontend/preview
python3 build_preview.py
python3 -m http.server 8766
```

Abre `http://127.0.0.1:8766/`. Admite `?estado=dentro&nombre=Ana&muestra=1`,
`?estado=llama` y `?estado=abriendo&pausa=<ms>`. `index.html` es un
artefacto generado y no se versiona.

El sistema visual está descrito en [DESIGN.md](../../DESIGN.md) y
[PRODUCT.md](../../PRODUCT.md).

## Limitaciones

- El camarero simulado solo entiende las frases descritas («somos N»,
  «barra», «mesa» para los asientos); con el BFF y Foundry responde el
  camarero real. Todavía no hay cuenta, pago ni liberación de mesa.
- La vista previa estática muestra la sala decorativa, sin asientos.
- La identidad es el nombre escrito en la puerta: identidad sintética de
  desarrollo, no autenticación.
- El estado simulado vive en la sesión del navegador y se pierde al recargar;
  con el BFF vive en su SQLite.
- El CSS depende de la estructura de Streamlit 1.64; al actualizar Streamlit
  hay que revisar la vista.
