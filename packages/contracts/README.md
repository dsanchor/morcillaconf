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
  Se elimina el contrato antiguo de consentimiento. `party_size` vale 1 por
  defecto, la persona que se ha identificado, y cambia cuando el cliente dice
  cuántos son; ese valor no acredita una asignación de mesa. Un dato
  desconocido, como el nombre en la CLI, sigue siendo `None` y figura en
  `pending_fields`.
- `application.py`: comandos, resultados, proyección y eventos públicos.
- `client.py`: protocolo asíncrono `BffClient` para futuros clientes falso y
  HTTP/SSE; `BffClientError` transporta errores públicos explícitos.
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

Estos son los seis comandos públicos de 3A. No hay API ni comandos de concesión
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
- Los campos de mesa, propuesta, cuenta y HITL se introducirán en fase 4 con
  sus consumidores. No se añaden diccionarios libres como extensiones futuras.

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
