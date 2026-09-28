# Frontend: vista de cliente en Streamlit

Carril 3B de la [fase 3](../../PLAN_IMPLEMENTACION.md#fase-3-una-vista-de-cliente-bff-y-continuidad):
una única vista de cliente que habla solo con el contrato público del BFF
([`packages/contracts`](../../packages/contracts/README.md)). Hasta que exista el
BFF del carril 3C, el camarero es **simulado** mediante `FakeBffClient` y la
vista lo indica con «Camarero simulado».

## Preparar, probar y arrancar (Codespace)

Desde la raíz del repositorio:

```bash
./scripts/setup-frontend.sh   # uv sync --frozen en apps/frontend
./scripts/test-frontend.sh    # pytest, incluido el recorrido con AppTest
./scripts/run-frontend.sh     # streamlit en 0.0.0.0:8501, sin abrir navegador
```

`apps/frontend/uv.lock` está versionado y fija Streamlit 1.64.0; los scripts
usan `--frozen`. Si falta el lockfile, `setup-frontend.sh` lo genera y hay que
versionarlo. En el Codespace abre el puerto 8501 reenviado.

Configuración por variables de entorno:

| Variable | Valores | Por defecto |
|---|---|---|
| `FRONTEND_BFF_CLIENT` | `fake`; `http` falla con un mensaje claro hasta 3C | `fake` |
| `FRONTEND_FAKE_PAUSE_SECONDS` | Pausa simulada del camarero antes de responder, 0-10 | `0.9` |
| `FRONTEND_PORT` | Puerto de `run-frontend.sh` | `8501` |

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
dure la sesión del navegador. Recargar la página empieza de cero; la
recuperación real de la visita llega con 3C/3D.

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
  registro y termina; el cliente HTTP/SSE de 3C mantendrá la conexión abierta.

## Módulos

| Módulo | Responsabilidad |
|---|---|
| `app.py` | Vista Streamlit y estados `outside`, `opening` e `inside` |
| `config.py` | Selección explícita del adaptador (`FRONTEND_BFF_CLIENT`) |
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

- El camarero es simulado y solo entiende las frases descritas; no hay pedido,
  mesas ocupadas, cuenta ni pago (fase 4).
- La identidad es el nombre escrito en la puerta: identidad sintética de
  desarrollo, no autenticación.
- El estado simulado vive en la sesión del navegador y se pierde al recargar.
- El CSS depende de la estructura de Streamlit 1.64; al actualizar Streamlit
  hay que revisar la vista.
