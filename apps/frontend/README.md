# Frontend: vista de cliente en Streamlit

Carril 3B de la [fase 3](../../PLAN_IMPLEMENTACION.md#fase-3-una-vista-de-cliente-bff-y-continuidad):
una única vista de cliente que habla solo con el contrato público del BFF
([`packages/contracts`](../../packages/contracts/README.md)). La vista se
conecta siempre al [BFF](../bff/README.md) mediante HTTP y SSE.

La planta mantiene la estética del mesón: los recorridos de clientes y servicio
usan curvas suaves y conservan el progreso entre actualizaciones. Las sillas
de las mesas redondas se orientan hacia el centro. En cocina, el halo identifica al chef o
especialista con actividad `running`, no a todas las partidas por igual.
La preferencia del navegador de reducir movimiento desactiva los recorridos
animados y muestra directamente la posición final.
La barra recta, con ocho puestos, queda frente a la entrada, arriba a la izquierda.
El comedor es más largo en horizontal, sin aumentar su altura. La mesa 5,
de seis plazas, queda en vertical a la derecha de la barra, con tres sillas
a cada lado y espacio libre entre ambos muebles.
Las mesas de cuatro (3 y 4) quedan arriba a la derecha y las de dos (1 y 2),
debajo. La cocina mantiene la misma altura y su marco conserva el pase alineado.
La capacidad y los identificadores de las mesas y puestos de barra no cambian.

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
| `FRONTEND_BFF_URL` | URL base del BFF con `http` | `http://127.0.0.1:8000` |
| `FRONTEND_BFF_TIMEOUT_SECONDS` | Espera máxima de cada llamada y del stream SSE, 1-300 | `30` |
| `FRONTEND_FAREWELL_SECONDS` | Tiempo que el recibo y la despedida siguen en pantalla tras pagar, antes de volver a la puerta, 0-30 | `4` |
| `FRONTEND_PORT` | Puerto del servidor Streamlit dentro del contenedor | `8501` |

Ejecutar la imagen:

```bash
docker run --rm --name morcillaconf-frontend \
  --publish 8501:8501 \
  --env-file apps/frontend/.env \
  morcillaconf-frontend:local
```

La vista queda disponible en `http://localhost:8501`.
`FRONTEND_BFF_URL` debe apuntar al BFF accesible desde el contenedor. En
ningún caso hay que configurar credenciales de
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

### Con el BFF

Arranca el BFF en otra terminal (ver [su README](../bff/README.md)) y después:

```bash
FRONTEND_BFF_URL=http://127.0.0.1:8000 ./scripts/run-frontend.sh
```

El cliente HTTP (`http_client.py`) usa solo la biblioteca estándar (`urllib`
en hilos con `asyncio.to_thread`), así que no cambia `uv.lock`. El nombre de la
puerta se envía una sola vez para abrir la sesión de demo; después solo viaja
un token opaco. Si el BFF no responde, la puerta sigue cerrada con un aviso.

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
  --env-vars FRONTEND_BFF_URL=https://<bff-fqdn> FRONTEND_PORT=8501

az containerapp show --name "$APP_NAME" --resource-group "$RG" \
  --query properties.configuration.ingress.fqdn --output tsv
```

Para una versión nueva, `az containerapp update --name "$APP_NAME"
--resource-group "$RG" --image ghcr.io/dsanchor/morcillaconf-frontend:<sha>`.
Para cambiar el [BFF](../bff/README.md), actualiza `FRONTEND_BFF_URL` mediante
`az containerapp update --set-env-vars ...`.

Particularidades de Streamlit:

- El estado de cada sesión vive en la memoria de la réplica y la conexión usa
  WebSockets. Usa una sola réplica (`--min-replicas 1 --max-replicas 1`) o, si
  se escala, activa la afinidad de sesión (`az containerapp ingress sticky-sessions
  set --affinity sticky`).
- Con `--min-replicas 0` la aplicación escala a cero y la primera visita paga
  un arranque en frío.
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
3. **Dentro:** lateral con el nombre y los comandos `/new` y `/exit`, ventana
   de conversación y el plano. Las preferencias se
   consultan o cambian conversando con el agente; el frontend no ofrece un
   panel de memoria. `/exit` vuelve a la puerta cerrada.

Entrar con el mismo nombre recupera la visita activa (la última) en lugar de
abrir otra: `/exit` y volver a entrar continúa la conversación, y solo `/new`
abre una visita nueva. Al recargar y volver a escribir el nombre, el BFF
recupera la misma visita sin reenviar mensajes. Si al
recuperarla el camarero aún responde un mensaje anterior, la vista sigue el
stream hasta que termina.

### Mesas y barra (fase 4)

- Cuando el camarero bloquea un sitio, aparece bajo la conversación una
  propuesta con el lugar y los asientos («Mesa 3 · 3 de 4 asientos» o
  «Barra · Puestos 1 a 3») y dos botones: «Confirmar» y «Rechazar». Envían
  `table.confirmation_decided` con el `proposal_id` y la versión; una frase
  en el chat nunca confirma: si el cliente escribe con la propuesta pendiente,
  el sitio sigue reservado y la tarjeta vuelve a aparecer. Si la reserva ha
  caducado, la vista muestra el
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

### Cocina (chef v1)

- Cuando el camarero pasa el pedido al chef, su plan llega en el snapshot como
  un mensaje con el rol `kitchen` y la vista lo pinta como una burbuja propia
  en la misma conversación: fondo azul con texto blanco y un gorro de
  cocinero, entre el mensaje del cliente y la respuesta del camarero. Muestra
  si cocina acepta el pedido, lo aceptado con su partida, sus adaptaciones y
  sus alérgenos, lo rechazado con su motivo, los avisos, el reparto por
  partidas y las fuentes; si cocina no ha podido revisarlo, lo dice. Todo el
  texto sale del informe tipado y se escapa.

### Barra (Barra v1, 08/10)

- Cuando el camarero sirve bebidas, la ronda de la barra llega en el snapshot
  como un mensaje con el rol `bar` y la vista lo pinta como una burbuja propia
  de la misma familia que la del chef: fondo ámbar tostado de caña con texto
  blanco, el título «Barra» y una caña con su espuma como icono. Va después de
  la burbuja de cocina, si la hay, y antes de la respuesta del camarero.
- Muestra lo servido con sus cantidades («2 × Caña de cerveza»), lo no
  servido con su motivo (una alergia declarada, «No está en la carta.» o, si
  el nombre es ambiguo, las opciones de la carta), los avisos y las fuentes; si
  la barra no ha podido consultar la carta, lo dice y no hay nada servido.

### Caja (acuerdo del 06/10)

- Cuando el cliente pide la cuenta, la respuesta de caja llega en el snapshot
  como un mensaje con el rol `cashier` y la vista lo pinta como una burbuja
  propia, entre el mensaje del cliente y la respuesta del camarero: fondo verde
  pino con texto blanco y una caja registradora como icono, dibujada como el
  gorro del chef. Muestra cada línea con su importe (y el precio por ración si
  hay más de una), el total, la nota «Solo lo ya servido: platos de cocina y
  bebidas de la barra» y las fuentes. Las cifras usan dígitos tabulares.
- Solo la cuenta pendiente (`pending_bill`, con
  `payment.confirmation_decided` permitido) lleva debajo dos botones,
  «Tarjeta» y «Efectivo», como la tarjeta de mesa, y su burbuja dice
  «Pendiente de pago». Una frase nunca paga. Las cuentas ya pagadas lo dicen y
  las que dejaron de valer (por pedir más platos o bebidas) se atenúan como
  anuladas.
- El pago llega como otra burbuja de caja con el recibo (método, importe,
  referencia y hora) y la despedida del camarero. Con `visit_closed`, la vista
  los mantiene `FRONTEND_FAREWELL_SECONDS` segundos y vuelve a la puerta igual
  que «Salir»: abre ya la visita siguiente, así que entrar de nuevo con el
  mismo nombre no recupera la visita pagada.
- En el plano, la caja está dentro del comedor, junto a la puerta de salida,
  en la esquina inferior izquierda: el cajero (la misma figura cenital que el
  resto) detrás de un pequeño mostrador con la caja registradora. Se ilumina
  con el halo ámbar mientras hay una cuenta pendiente de pago.

## Fronteras de arquitectura

- `app.py` solo usa el protocolo `BffClient` a través de `VisitSession`.
  No importa agentes, Agent Framework, Foundry, MCP ni bases de datos; una
  prueba lo comprueba.
- La vista sustituye su proyección por snapshots confirmados. El texto del
  camarero no mueve el plano ni cambia estados; los fragmentos
  `conversation.text_delta` se muestran como provisionales.
- Los controles se habilitan según `allowed_actions`; el BFF sigue validando
  cada comando.
- Cada comando conserva su `event_id`. Si un rerun interrumpe una operación,
  la siguiente ejecución consulta `get_result(event_id)` en lugar de
  reenviarla. Un cursor caducado se recupera con un snapshot nuevo.
- Los dobles de BFF, cocina, barra, caja y asientos viven exclusivamente en
  `tests/support`; no se incluyen en el paquete ni pueden activarse en runtime.

## Módulos

| Módulo | Responsabilidad |
|---|---|
| `app.py` | Vista Streamlit y estados `outside`, `opening` e `inside` |
| `config.py` | Configuración de la conexión HTTP mediante `FRONTEND_BFF_URL` |
| `http_client.py` | `HttpBffClient`: API HTTP y SSE del BFF con la biblioteca estándar |
| `visit.py` | Proyección de la visita, comandos, eventos y tarjetas |
| `slash_commands.py` | Comandos escritos con «/» |
| `markup.py` | Fragmentos HTML escapados: burbujas, tarjetas, lateral, escena |
| `facade.py`, `floor_plan.py` | Escena del restaurante, solo biblioteca estándar |
| `styles/scene.css`, `styles/app.css` | Tokens y animaciones; adaptación al DOM de Streamlit |

Las escenas SVG se insertan con `st.markdown(..., unsafe_allow_html=True)`
porque `st.html` elimina el SVG al sanear. Todo texto dinámico se escapa en
`markup.py`. El CSS se inyecta con `st.html("<style>…</style>")` y se apoya en
las clases `st-key-*` de los contenedores con clave y en atributos
`data-testid` estables, revisados contra el código de Streamlit 1.64.
No se usa JavaScript: todo se anima con keyframes y respeta
`prefers-reduced-motion`.

## Vista previa estática del diseño

`preview/` conserva el prototipo visual aprobado como herramienta de diseño,
sin dependencias. Importa los módulos de escena del paquete y reproduce una
conversación estática en JavaScript:

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

- La vista previa estática muestra la sala decorativa, sin asientos.
- La identidad es el nombre escrito en la puerta: identidad sintética de
  desarrollo, no autenticación.
- El CSS depende de la estructura de Streamlit 1.64; al actualizar Streamlit
  hay que revisar la vista.
