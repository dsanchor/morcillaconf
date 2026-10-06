# Contratos públicos del restaurante: 3A

Paquete Python `morcillaconf-contracts` (importación `restaurant_contracts`),
versión de paquete `0.1.0`, versión de mensajes `schema_version: 1`.
Solo depende de Pydantic. No ejecuta agentes ni implementa servicios.

La retirada de `MemoryConsent` y la sustitución de concesión/revocación por
borrado total son un cambio coordinado previo a la integración de consumidores.
Se conserva el protocolo v1; los comandos antiguos se rechazan.

## Uso local

Desde la raíz del repositorio:

```bash
./scripts/setup.sh
./scripts/test.sh
```

El proyecto del camarero declara este paquete como dependencia local editable y
su lockfile fija las dependencias usadas por ambos. Un futuro frontend o BFF
debe declarar su propia dependencia del paquete, no del proyecto del agente.

```python
from restaurant_contracts.application import COMMAND_ADAPTER

command = COMMAND_ADAPTER.validate_json(
    '{"schema_version":1,"event_id":"cmd_demo",'
    '"event_type":"conversation.message_sent",'
    '"occurred_at":"2026-09-27T08:00:00Z",'
    '"conversation_id":"conv_demo","payload":{"message":"Hola"}}'
)
```

Los otros puntos de entrada son `COMMAND_RESULT_ADAPTER`,
`STREAM_EVENT_ADAPTER`, `RestaurantSnapshot` y `PublicError`. Los adaptadores
validan las uniones discriminadas y generan JSON Schema mediante
`json_schema()`. Los modelos lo generan mediante `model_json_schema()`.

## Fronteras

- `customer.py` y `memory.py`: tipos compartidos extraídos del camarero.
  Las importaciones de cliente, borrador y recuerdos siguen disponibles.
  Se elimina el contrato antiguo de consentimiento. `party_size` vale `1` por
  defecto por compatibilidad; ese valor no acredita que el cliente haya
  confirmado venir solo ni una asignación de mesa. La pregunta inicial
  solo/acompañado es una regla conversacional.
- `application.py`: comandos, resultados, proyección y eventos públicos.
- `client.py`: protocolo asíncrono `BffClient` para futuros clientes falso y
  HTTP/SSE; `BffClientError` transporta errores públicos explícitos.
- `waiter.py`: contrato JSON entre el BFF y el camarero independiente. La
  petición se distingue por `operation`: `take_turn` (un mensaje),
  `decide_seating` (la respuesta del cliente con los botones a la
  confirmación pendiente, con el `proposal_token` que conoce el BFF) y
  `sync_seating` (leer el sitio y la sala sin llamar al modelo). Las
  respuestas correctas llevan un `SeatingReport`: el sitio propio (ninguno,
  propuesto o sentado), `awaiting_decision`, `last_outcome` y la sala anónima,
  sin visitas, asignaciones ni nombres. Solo el camarero habla con el MCP de
  asientos.
- `WaiterModelResult`, `WaiterResponse`, `SessionState`, clasificación semántica
  y repositorio de memoria siguen dentro del agente.

Responses/invocations continúan siendo la entrada de ejecución del agente.
Estos contratos son la futura frontera frontend/BFF, no nuevos endpoints
Responses ni un reemplazo del protocolo de hosting. La adaptación local/remota
pertenece a 3C. El servidor actual construye el agente directamente; no pasa por
`ConversationManager`, utilizado por CLI y pruebas.

## Comandos e identidad

| Clase | `event_type` | Payload |
|---|---|---|
| `ArriveCommand` | `customer.arrived` | `resume_visit_id` opcional |
| `SendMessageCommand` | `conversation.message_sent` | `message` |
| `ReadMemoryCommand` | `memory.read_requested` | Vacío |
| `CorrectMemoryCommand` | `memory.correction_requested` | `memory_id`, `value` |
| `DeleteMemoryCommand` | `memory.deletion_requested` | `memory_id` |
| `ClearMemoryCommand` | `memory.clear_requested` | Vacío |
| `DecideTableCommand` | `table.confirmation_decided` | `proposal_id`, `version`, `decision` (`confirmed` o `rejected`) |
| `DecidePaymentCommand` | `payment.confirmation_decided` | `bill_id`, `version`, `method` (`tarjeta` o `efectivo`) |

Todos llevan versión, `event_id` y `occurred_at` con zona horaria. Salvo llegada,
requieren `conversation_id`. Los nombres de clases son imperativos; los
identificadores externos conservan el estilo de los comandos de SPECS.
Los textos de mensaje se limitan a 2.000 caracteres, los de memoria a 200, y
se rechazan textos vacíos o formados solo por espacios.

La llegada abre o recupera la visita propia; **en fase 3 no asigna una mesa**.
`resume_visit_id` solo solicita un recurso: el BFF debe comprobar pertenencia.
La fase 4 añadirá asignación y los comandos de pedido, cuenta y pago.

`ActorContext` se construye en el BFF tras normalizar el nombre de entrada y
vincularlo a una sesión de demo. No se acepta `actor`, `actor_id` ni
`authenticated` dentro del comando. El nombre presentado en el chat no cambia
la identidad de la sesión. La memoria se lee y escribe automáticamente para la
identidad de demo; la procedencia y fecha de los recuerdos siguen siendo
responsabilidad del servidor. Esta demo no incorpora Entra ID ni otro proveedor
externo de identidad.

Estos son los seis comandos públicos de 3A; la fase 4 añade
`table.confirmation_decided`. No hay API ni comandos de concesión
o revocación: los antiguos `memory.consent_granted` y
`memory.consent_revoked` se rechazan, al igual que campos de consentimiento.
No se reinterpretan como borrado ni se aceptan silenciosamente.

El BFF deberá autorizar también consulta de resultados, snapshot y suscripción
a eventos. Pydantic valida la forma, **no autentica ni autoriza**.

## Idempotencia y resultados

- `event_id` del comando es también su clave de idempotencia. No se introduce
  una segunda clave independiente.
- El ámbito de deduplicación es `(actor_id validado, event_id)`.
- Un reintento conserva todo el comando, incluida fecha y payload.
- Misma clave con otro contenido debe producir `idempotency_conflict`.
- `correlation_id` es generado por servidor y enlaza resultado, error y eventos.
- `occurred_at` del cliente no determina el orden de ejecución ni demuestra
  vigencia. La secuencia la asigna el servidor.

| Resultado | Datos y significado |
|---|---|
| `pending` | Recibido, no resuelto; aún puede no haber visita/conversación |
| `completed` | Operación terminada; devuelve visita, conversación y cursor confirmado |
| `failed` | Resultado terminal con error público correlacionado |

`completed` no significa pedido confirmado, preparado ni pagado. El BFF deberá
persistir resultado y estado antes de confirmarlos. `get_result(event_id)`
recuperará el resultado propio, incluso tras perder la respuesta HTTP.
La idempotencia y la persistencia **no están implementadas en 3A**.

Un error de transporte se expresa mediante `BffClientError`, no mediante éxito
vacío ni una lista de eventos vacía. Si se pierde la conexión, el estado es
desconocido hasta consultar el resultado: no se fabrica un `failed`. Solo un
error de disponibilidad puede indicar `retry_same_command`, y se consulta antes
el resultado. Un `failed` persistido no aconseja reejecutar la misma operación;
repetir su clave devuelve ese resultado terminal.

## Snapshot y memoria

`RestaurantSnapshot` contiene visita, conversación, identidad resuelta,
historial, datos actuales, borrador no verificado, campos pendientes, memoria,
estado de proceso, acciones permitidas y cursor.

- El frontend sustituye su proyección por el snapshot; no interpreta el texto
  del modelo para reconstruir efectos.
- El servidor calcula `allowed_actions`. Deshabilitar controles no reemplaza
  las validaciones del ejecutor. Una lista vacía es un estado válido.
- `idle`, `processing` y `awaiting_customer` describen el proceso, no estados de
  mesa, cocina o pago.
- Recuerdos y restricciones reafirmadas se mantienen separados. Cada recuerdo
  público tiene ID, tipo, valor, procedencia, fecha y reconfirmación obligatoria.
  No se publican contadores internos ni historial de pedidos inventado.
- `MemoryView` y el snapshot interno no contienen estado de consentimiento.
- Un invitado no tiene memoria duradera ni acciones para gestionarla.
- `memory.clear_requested` olvida todos los recuerdos de la identidad propia;
  no impide guardar automáticamente interacciones futuras. Corrección y borrado
  deben reflejarse también en conversaciones activas y tras reiniciar, sin
  restaurar datos eliminados. El borrado total solo elimina preferencias y
  restricciones; conserva visita, borrador y `completed_order_history`.
  Los contratos no añaden herramientas de borrado por lenguaje natural.
- `seating` (fase 4, por defecto `none`) muestra solo el sitio propio:
  `proposed` con la propuesta pendiente (`proposal_id`, versión, lugar,
  comensales y caducidad) o `seated` con lugar, comensales y `seated_at`.
  `table.confirmation_decided` solo figura en `allowed_actions` mientras hay
  una propuesta pendiente. Cuenta, pedido y HITL de pago llegarán con sus
  consumidores. No se añaden diccionarios libres como extensiones futuras.

## Mesas y barra (fase 4)

`restaurant_contracts.seating` define los modelos públicos de asientos:

- `SeatingPlace`: mesa o barra del layout (`place_id`, tipo, rótulo, capacidad
  y, en barra, las posiciones contiguas del grupo). Una mesa se asigna entera.
- `SeatingProposal` y `SeatingView`: la propuesta y el sitio propios del
  snapshot.
- `RoomView`, `RoomPlace` y `RoomSeat`: la sala anónima del plano. Cada lugar
  tiene estado `free`, `held` u `occupied`, tamaño del grupo en mesas, estado
  por taburete en barra y marcas `mine` solo para quien consulta. Nunca lleva
  nombres, visitas ni asignaciones del servicio de asientos.
- `BffClient.get_room(conversation_id)` devuelve esa sala;
  `seating_enabled=false` indica que el plano es decorativo.

Todo es aditivo y se mantiene `schema_version: 1`: los consumidores viven en
este repositorio y se publican desde el mismo commit, y los campos nuevos
tienen valores por defecto que mantienen válidos los payloads de fase 3.

## Cocina (chef v1)

`restaurant_contracts.kitchen` define lo que se cruzan el camarero y el chef:

- `KitchenOrder`: solo el pedido, con líneas numeradas (plato, cantidad y
  modificaciones) y las alergias o intolerancias declaradas. No admite datos
  del cliente.
- `KitchenPlan`: lo aceptado (`AcceptedItem`, con su identificador de carta,
  partida, adaptaciones y alérgenos), lo rechazado con su motivo
  (`RejectedItem`), los avisos, el reparto por partidas (`StationPlan` con sus
  `StationTask`), las fuentes y la versión del plan. Las partidas son un
  conjunto fijo (`brasa`, `fritos`, `pinchos_frios` y `barra`), ninguna línea
  puede estar aceptada y rechazada a la vez y cada línea aceptada tiene una
  tarea, y solo una, en su partida.
- `KitchenFailure`: el motivo explícito cuando cocina no ha podido revisar el
  pedido; nunca se sustituye por un plan inventado.
- `KitchenReport`: el pedido, el resultado y su texto en español; comprueba que
  el plan decide todas las líneas con las cantidades pedidas.
- `KitchenPort`: la frontera entre camarero y chef, hoy en el mismo proceso.

`ChatMessage` admite el rol `kitchen`: esos mensajes, y solo ellos, llevan el
`KitchenReport` en `kitchen` y su texto puede llegar a 6.000 caracteres; el
resto sigue limitado a 2.000. `WaiterTurnSuccess.kitchen` lleva el informe del
turno. Ambos campos se omiten del JSON cuando faltan, así que los mensajes y
turnos sin cocina mantienen su forma anterior.

## Caja (cobro v1, acuerdo del 06/10)

`restaurant_contracts.cashier` define lo que se cruzan el camarero y la caja,
un agente A2A en su propio contenedor:

- `ServedLine`: un plato de cocina ya servido (pedido, línea, identificador de
  carta, nombre y cantidad). Es lo único que se cobra: el BFF lo calcula con
  los planes de cocina cuyos pedidos están en `served_orders`; ni lo rechazado
  ni lo que sigue en el pase. Las bebidas todavía no se cobran.
- `BillRequest`: la cuenta pedida, con esas líneas y nada del cliente; cada
  línea servida aparece una sola vez.
- `Bill`: la cuenta presentada, con `bill_id`, versión, `stage`, líneas con
  precio unitario y total de línea, total, moneda `EUR`, opciones de pago
  (`tarjeta`, `efectivo`), fuentes y la nota «Solo platos de cocina; las
  bebidas todavía no se cobran». Los importes son `Decimal` exactos: el total
  es la suma de las líneas y cada línea, su precio por su cantidad.
- `BillStage`: `awaiting_payment` ofrece tarjeta o efectivo;
  `awaiting_review` es el gancho preparado para que una persona en caja revise
  el ticket antes (`ReviewDecision`), desactivado por defecto y sin interfaz.
- `PaymentChoice`: el botón del cliente, con la clave de idempotencia del
  primer intento. `Receipt`: método, importe, referencia y hora del pago;
  pagar otra vez la misma cuenta devuelve ese mismo recibo.
- `CashierFailure`: el motivo explícito (precio ausente o incoherente, carta no
  consultada, caja no disponible, cuenta inexistente...); nunca un importe
  inventado.
- `CashierReport`: el resultado, su texto en español y la tarea A2A
  (`CashierTask`) que guarda la cuenta, para que el pago reanude esa misma
  tarea. Comprueba que la cuenta cobra exactamente las líneas servidas.
- `CashierPort`: la frontera entre camarero y caja (presentar, pagar, anular).

`ChatMessage` admite el rol `cashier` con su `CashierReport`. El snapshot añade
`pending_bill` (`BillView`) y `visit_closed`, y
`payment.confirmation_decided` solo figura en `allowed_actions` con una cuenta
pendiente de pago. `WaiterTurnRequest` lleva `served`, `orders_at_pass` y
`pending_bill`; `WaiterTurnSuccess.cashier`, el informe del turno, y la
operación `pay_bill` (`WaiterPayRequest`/`WaiterPaySuccess`) relaya el pago sin
modelo. Todos los campos nuevos se omiten del JSON cuando faltan.

## Eventos, SSE y recuperación

La versión 1 define tres eventos:

| Tipo | Propósito |
|---|---|
| `snapshot.updated` | Proyección de estado confirmado |
| `command.status_changed` | Resultado pendiente o terminal correlacionado |
| `conversation.text_delta` | Fragmento provisional, sin estado de negocio |

Cada evento tiene un `event_id` propio, distinto del `command_event_id` que lo
originó; incluye conversación, correlación y fecha del servidor. `cursor` es un
entero positivo, monótono por conversación. El cursor 0 del snapshot representa
el comienzo del stream. El futuro adaptador SSE codificará ese cursor en decimal
en el campo `id`; `Last-Event-ID` se interpreta en esa misma conversación.

El BFF de 3C deberá garantizar:

1. Persistir antes de publicar. El snapshot y su cursor deben ser una lectura
   consistente del estado y la secuencia, sin ventana de pérdida de eventos.
2. `events(conversation_id, after_cursor=S)` entrega eventos posteriores a S.
3. Mantener orden y permitir deduplicar reentregas por cursor. El frontend no
   vuelve a aplicar un evento con cursor ya visto.
4. Cuando el cursor ha caducado, emitir un error público `cursor_expired` con
   `recovery: fetch_snapshot`. El cliente obtiene un snapshot nuevo y reabre
   el stream desde su cursor, sin reenviar comandos.
5. Rechazar un cursor futuro o de ámbito inválido explícitamente; no degradarlo
   silenciosamente a cero. La autorización se comprueba antes del replay.
6. Los deltas son provisionales: no alteran borrador ni negocio. El mensaje
   definitivo del snapshot sustituye esos fragmentos por `message_id`.
7. Si un stream falla, propagar el error; el cierre de una suscripción liberará
   recursos del adaptador. Reruns y cierre/reconexión se probarán en 3B/3C.

El resultado completado apunta al cursor que confirma el estado; su evento de
notificación puede llegar después. La reconexión no necesita repetir efectos.
Los deltas también consumen cursor: en 3C habrá que conservarlos mientras sean
reproducibles o declarar caducado el cursor, nunca saltar huecos silenciosamente.

## Ejemplos, evidencia y límites

Los [fixtures JSON](../../tests/fixtures/phase3a) son datos sintéticos:

- `commands.json`: cada comando de fase 3;
- `snapshots.json`: visita inicial, borrador y escenario independiente de memoria;
- `results.json`: pendiente, completado y fallo independiente;
- `events.json`: secuencia de un mensaje con texto parcial y estado confirmado;
- `cursor-expired.json`: recuperación explícita.

Los [fixtures de fase 4](../../tests/fixtures/phase4) cubren la decisión de
mesa, snapshots con propuesta de mesa y de barra y con grupo sentado, y dos
salas (sin asientos y con otros grupos).

Las [pruebas unitarias](tests/test_application_contracts.py) validan formas e
invariantes. Las [pruebas de compatibilidad](../../tests/contract/test_waiter_public_contracts.py)
comprueban reexportaciones, formato del camarero y coherencia de los fixtures.
No constituyen una prueba de autenticación, durabilidad o reconexión real.

No se implementan BFF, cliente falso, frontend, almacenamiento ni autenticación.
La extracción inicial de 3A no cambió prompts ni entradas de hosting; la
política vigente ya no depende de consentimiento. El paquete se resuelve desde el
checkout completo en local. El `remote_build` del agente aún apunta solo a su
carpeta de servicio: antes del despliegue de fase 5 será necesario incluir este
paquete en el artefacto remoto o distribuir su wheel; la ruta editable local no
es una estrategia válida de empaquetado remoto.

Cambiar payloads o su interpretación exige revisar consumidores y fixtures.
Los modelos rechazan campos desconocidos; cualquier ampliación debe coordinarse,
sin interpretar contratos futuros como versión 1 por defecto.
