# Progreso de implementación

Última actualización: **2026-10-06**

Este documento ofrece una vista compartida del estado real del repositorio. No
sustituye a [SPECS.md](SPECS.md) ni a
[PLAN_IMPLEMENTACION.md](PLAN_IMPLEMENTACION.md):

- `SPECS.md` define el comportamiento esperado.
- `PLAN_IMPLEMENTACION.md` define fases, tareas y criterios de aceptación.
- `PROGRESO.md` registra qué está implementado, qué evidencia existe y qué queda
  pendiente de revisión.

Las casillas del plan solo se marcan después de la validación y revisión conjunta
indicadas en el propio plan.

## Resumen

| Fase | Estado de implementación | Revisión conjunta | Evidencia principal |
|---|---|---|---|
| 1. Proyecto y primer camarero | Completada | Validada conjuntamente el 28/09 | Evidencia histórica: 42 pruebas locales compartidas con fase 2; inferencia real y servidor local validados. Correcciones de la revisión validadas |
| 2. Memoria persistente automática | Completada | Validada conjuntamente el 28/09 | 127 pruebas locales totales: memoria automática, aislamiento, migración y borrado |
| 3. Vista única, BFF y continuidad | Implementada: 3A, 3B, 3C y 3D validadas en el Codespace el 28/09 | Pendiente; validada por Jesús el 28/09 ([revisión de 3A](docs/revision-fase-3a-jesus.md), [validación de 3C y 3D](#validación-en-el-codespace-28092026)) | 3A: 133 pruebas y 15 reglas del contrato. 3B: [vista Streamlit](#fase-3b-vista-del-cliente) e imagen Docker publicada. 3C/3D: [BFF](#fase-3c-bff) con el camarero en Foundry; 146 + 8, 123 y 96 pruebas, smoke real y recorrido manual superados |
| 4. Mesas y recorrido local con control de caja | Parcial: asientos integrados en `main`; [caja v1](#caja-v1-cuenta-y-pago-06102026) cobra lo servido con tarjeta o efectivo y cierra la visita liberando la mesa; confirmación del pedido e interfaz de la revisión humana de caja pendientes | Revisión conjunta del alcance completo pendiente | Bloqueo temporal, confirmación/rechazo, concurrencia, plano y recorrido E2E implementados; caja A2A con cuenta desde la carta, pago simulado idempotente y recorrido real con Foundry |
| 5. Conocimiento compartido con Foundry IQ y MCP | Implementada en `main` (PR #10): base de conocimiento con carta, recetario en PDF con ficha de ingredientes y web de respaldo; el camarero la consulta por su endpoint MCP | Pendiente | [Fase 5](#fase-5-carta-con-foundry-iq-30092026): base aprovisionada y consultada por REST y MCP; camarero local con Foundry respondiendo desde cada fuente con su cita; 263 + 30 pruebas locales y recorrido E2E de asientos |
| 6. Chef líder y especialistas | Parcial: cocina es un agente A2A independiente; el chef valida con carta/recetario y coordina parrilla, fritos y general mediante group chat; los especialistas aceptan temporalmente siempre | Pendiente | [Cocina v1](#cocina-v1-el-chef-01102026): frontera A2A, Docker propio, validación determinista y especialistas internos; despensa, tiempos y confirmación del pedido pendientes |
| 7. Validación de Hosted Agent | Pospuesta hasta disponer de carta y orquestación representativas | Pendiente | El agente actual se ha ejecutado localmente y como contenedor remoto, pero no acredita el workflow objetivo completo |
| 8. Proveedor mediante A2A | Pendiente | Pendiente | Sin implementación |
| 9. Integración duradera en Azure | Pendiente | Pendiente | Sin implementación |
| 10. Evaluación y ensayo final | Pendiente | Pendiente | Sin implementación |

## Separación de procesos y primer despliegue en Container Apps

- El BFF ya no construye el camarero de Foundry en su proceso productivo e
  invoca siempre el endpoint Responses 2.0 del contenedor del agente mediante
  `WAITER_AGENT_URL`. Los dobles guionizados quedan solo como infraestructura
  interna de pruebas y no son un modo configurable.
- El contrato remoto tipado transporta estado confirmado, sesión opaca e
  informe de asientos, pero no recuerdos ni candidatos de memoria. El agente
  es la única autoridad de preferencias mediante su Memory Context Provider.
  El BFF conserva identidad, visita, snapshots y SSE, pero no abre una base de
  memoria ni lee, crea o modifica preferencias.
- Se retiró del frontend el panel y los comandos `/memory`; la memoria se
  utiliza y administra conversando con el camarero.
- El agente valida `x-agent-user-id`, ejecuta `ConversationManager`, llama al
  modelo de Foundry y a las tools MCP, y devuelve el resultado estructurado
  dentro de una respuesta estándar.
- `scripts/deploy-container-apps.sh` despliega imágenes públicas e inmutables
  de GHCR en seis Container Apps independientes dentro del mismo entorno.
  Solo el frontend tiene ingress externo; BFF, camarero, cocina, caja y MCP
  usan ingress interno. Camarero, cocina y caja comparten una identidad
  administrada con `Foundry User`.
- Mientras BFF y MCP sigan usando SQLite, cada uno lo guarda en el
  almacenamiento efímero de su propio contenedor (`/data`), sin Azure Files, y
  todas las aplicaciones quedan limitadas a una réplica. Los datos se pierden al
  reiniciar la réplica o desplegar una revisión nueva. Es una topología inicial
  de demo, no el diseño de persistencia duradera de fase 9 (Cosmos DB).

## Acuerdos del 06/10/2026

Jesús y dsanchor acuerdan la caja v1:

- La caja es un agente A2A en su propio contenedor (`agents/cashier`), como la
  cocina, y usa la misma base de conocimiento.
- El cliente pide la cuenta en la conversación y elige tarjeta o efectivo con
  dos botones; una frase nunca paga.
- Tras el pago, el camarero se despide y la visita se cierra liberando el
  sitio, por el mismo camino que «Salir».
- En la v1 solo se cobran los platos de cocina ya servidos; las bebidas
  todavía no.
- El pago es simulado y se aprueba siempre.
- La revisión del ticket por una persona en caja (SPECS) queda preparada como
  etapa opcional, desactivada por defecto y sin interfaz.

## Caja v1: cuenta y pago (06/10/2026)

No se marca ninguna casilla del plan: queda pendiente de la revisión conjunta.

### Implementado

- **Contratos** (`restaurant_contracts.cashier`): líneas servidas, petición de
  cuenta, cuenta con importes `Decimal` en euros y su etapa
  (`awaiting_review` opcional, `awaiting_payment`), elección de pago con clave
  de idempotencia, recibo, fallos explícitos de caja e informe con la tarea A2A.
  `ChatMessage` admite el rol `cashier`, el snapshot lleva `pending_bill` y
  `visit_closed`, y hay un comando nuevo, `payment.confirmation_decided`. El
  turno del camarero lleva `served`, `orders_at_pass` y `pending_bill`, y hay
  una operación nueva, `pay_bill`. Los campos nuevos se omiten del JSON cuando
  faltan.
- **Caja** (`agents/cashier`): un agente de Agent Framework cuya única
  herramienta es la base de conocimiento (`knowledge.py` copiado de cocina).
  El modelo solo busca las entradas de la carta; el código lee los precios de
  los fragmentos de la carta, nunca de la web ni del recetario, y calcula la
  cuenta con aritmética `Decimal`. Un precio ausente o incoherente es un fallo
  explícito. La tarea A2A queda en `input-required` con la cuenta; el pago la
  reanuda y la completa con el recibo. Cada cuenta se cobra una sola vez.
  Tiene Dockerfile, workflow de imagen y un `uv.lock` derivado del de cocina.
- **Camarero:** la tool `pedir_la_cuenta`, sin argumentos. Se niega con platos
  en el pase, sin nada servido o con una cuenta ya presentada. El puerto
  `A2ACashier` lleva la tarea y retransmite los pasos de caja. `pay_bill`
  funciona sin modelo: lee la tarea y, si ya está pagada, devuelve el recibo
  guardado. Con el recibo hay una despedida fija. Un pedido nuevo con platos
  anula la cuenta pendiente. Las instrucciones dicen que un mensaje nunca paga
  y que el camarero no calcula importes.
- **BFF:** envía las líneas servidas (platos aceptados de pedidos en
  `served_orders`) y los pedidos en el pase. Guarda la cuenta en la tabla
  `bills` y el mensaje de caja entre la petición y la respuesta. El comando de
  pago usa la clave del primer intento, de modo que un segundo clic no cobra
  otra vez. Tras el recibo termina la visita con `release_seating`, como
  «Salir».
- **Vista:** la burbuja verde de caja con caja registradora y los botones
  «Tarjeta» y «Efectivo» solo en la cuenta pendiente. Tras pagar, el recibo y
  la despedida se ven unos segundos (`FRONTEND_FAREWELL_SECONDS`) y la vista
  vuelve a la puerta. En el plano, el cajero junto a la salida se ilumina con
  una cuenta pendiente. «Bajo el capó» muestra los pasos de caja.
- **Despliegue:** sexta Container App (`CASHIER_AGENT_APP_NAME`, por defecto
  `morcilla-cashier`; `CASHIER_AGENT_IMAGE`), ingress interno en el puerto
  8090, `CASHIER_TIMEOUT_SECONDS` y `CASHIER_A2A_URL` en el camarero.

### Evidencia

- **Pruebas locales:** 364 del camarero, los contratos y la carta, y 30 del
  MCP (`./scripts/test.sh`); 28 de caja; 5 de cocina; 159 del BFF; 165 del
  frontend. En el recorrido E2E pasan 8 de 9, incluido el nuevo recorrido de
  caja: la app A2A real de cocina con un chef guionizado y la app A2A real de
  caja con los precios de la carta versionada. Solo falla
  `test_kitchen_flow.py`, que ya fallaba en `main`: espera un texto de cocina
  que cambió el 02/10.
- **CI de la rama:** las imágenes de caja, camarero, cocina, BFF y frontend
  pasan sus pruebas y se publican; el recorrido E2E en CI solo falla en
  `test_kitchen_flow.py`.
- **Foundry real** (`gpt-5.6-luna`), con la base `conocimiento-restaurante`, el
  MCP de asientos, la cocina A2A real y la caja A2A real, por el BFF como el
  navegador:
  1. «Hola, somos dos para comer»: Mesa 1 propuesta y confirmada con el botón.
  2. Morcilla a la brasa y croquetas de morcilla: el camarero pide
     confirmación, cocina las acepta y el camarero las sirve tras el pase.
  3. «La cuenta, por favor»: 8,50 € + 9,00 € = 17,50 € leídos de la carta
     versión 1, con una consulta a la base. Burbuja de caja entre la petición y
     la respuesta.
  4. «Pago con tarjeta» escrito: no paga; el camarero indica el botón. Con
     «Tarjeta» llegan el recibo y la despedida, la visita queda cerrada y la
     Mesa 1, libre.
  5. Un segundo clic con otro evento no añade mensajes ni cobra. Un `pay_bill`
     repetido directamente al camarero devuelve el mismo recibo, con la misma
     referencia y hora, leído de la tarea completada.

  Tiempos: la mesa, 21 s; el turno que pasa el pedido, 59 s (chef y
  preparación de cocina incluidos); la cuenta, 26 s (16 s de caja, de ellos
  11 s de consulta de precios); el pago, 0,1 s. Como el turno de cocina ronda
  los 60 s, en Azure `WAITER_AGENT_TIMEOUT_SECONDS` debe ser de al menos 80 s
  (el ejemplo de despliegue usa 90).

### Límites y pendientes

- La revisión humana de caja está preparada en el agente pero desactivada y
  sin interfaz.
- Las bebidas no se cobran todavía; el pago es simulado y siempre aprobado.
- Las tareas, las cuentas y los pagos de caja viven en memoria: un reinicio
  olvida la cuenta pendiente y el cliente tiene que volver a pedirla.
- Pendientes: la revisión conjunta, redesplegar con la sexta aplicación y
  probar en el navegador.

## Acuerdos del sync del 01/10/2026

- Empezar ya con el chef; los especialistas se incorporaron después como
  participantes temporales que siempre aceptan.
- Todavía sin despensa ni inventario.
- La decisión inicial de usar el mismo contenedor fue reemplazada por un
  agente de cocina independiente, expuesto por A2A, con su orquestación
  interna.
- Jesús y dsanchor acuerdan dejar los PR: el trabajo va en una rama, se
  publica para que pase la CI de cada componente y, con la CI en verde, se
  integra en `main` con un commit de merge. La evidencia de CI queda en esas
  ejecuciones.

## Cocina v1: el chef (01/10/2026)

Primera porción de la fase 6, más estrecha que la fase completa. No se marca
ninguna casilla del plan: queda pendiente de la revisión conjunta.

### Implementado

- **Contratos** (`restaurant_contracts.kitchen`): `KitchenOrder`, solo con el
  pedido; `KitchenPlan`, con lo aceptado, lo rechazado, los avisos, las
  partidas con sus tareas, las fuentes y la versión; `KitchenFailure`;
  `KitchenReport`, y el puerto `KitchenPort`. `ChatMessage` admite el rol
  `kitchen` y `WaiterTurnSuccess` lleva el informe de cocina del turno.
- **Camarero:** la tool tipada `pedir_a_cocina`, como mucho una vez por turno,
  y sus instrucciones: enviar solo el pedido, resumir el veredicto del chef
  sin alterarlo y no inventar nunca un resultado de cocina.
- **Chef** (`kitchen-lead`): un agente de Agent Framework sin sesión, memoria
  ni contacto con el cliente, con salida estructurada y la base de
  conocimiento como única herramienta. Comprueba la carta, consulta el
  recetario, aplica modificaciones y alergias sin deducir alérgenos, reparte el
  trabajo por partidas y cita sus fuentes. Usa el mismo proyecto Foundry y,
  si se configura, su propio deployment.
- **Validación determinista** del plan contra lo que devolvió la base:
  partidas fijas, platos aceptados que aparecen en la carta consultada, cada
  línea decidida una sola vez con la cantidad pedida, ninguna alergia ni
  modificación «sin X» de un alérgeno servida con un plato que lo contiene,
  puede contenerlo o no lo ha verificado, y ninguna modificación pedida que
  desaparezca en silencio.
- **Fallos explícitos:** sin base configurada, base caída, chef sin consultar
  la carta, respuesta inválida, error del modelo o más de
  `KITCHEN_TIMEOUT_SECONDS` (30 s). El plazo corta el plan entero.
- **BFF:** guarda la respuesta del chef como un mensaje propio entre el pedido
  y la respuesta del camarero, y la publica en el snapshot y los eventos.
- **Vista:** la burbuja del chef, azul con texto blanco y un gorro de cocinero,
  en la misma conversación. El falso tiene un chef simulado para «Pido…».
- **Despliegue:** imagen y Container App propias para cocina;
  `KITCHEN_A2A_URL` en el camarero y `KITCHEN_TIMEOUT_SECONDS` como límite del
  recorrido remoto.

### Evidencia

- **Pruebas:** 311 del camarero, los contratos y la carta, y 30 del MCP
  (`./scripts/test.sh`); 144 del BFF; 144 del frontend, incluida una AppTest
  de la burbuja; 8 del recorrido E2E, incluido un pedido que llega a cocina
  sin base y vuelve como fallo explícito antes de la respuesta del camarero.
- **Foundry real** (`gpt-5.6-luna`), con la base `conocimiento-restaurante` y el
  MCP de asientos, por el camino desplegado (`RemoteWaiterService`), después
  de sentar al grupo con «Confirmar»:
  - «Una morcilla a la brasa y unas croquetas de morcilla sin cebolla, y soy
    celíaco»: acepta la morcilla (brasa, ninguno de los 14 alérgenos) y
    rechaza las croquetas, que llevan gluten y cuya cebolla va dentro de la
    morcilla. Reparte la morcilla a la brasa con sus pasos y cita la carta y
    la receta R02.
  - «Una hamburguesa sin queso»: la rechaza porque no está en la carta y cita
    la carta consultada sin resultados.
  - «Un chorizo a la brasa, por favor»: lo acepta con los alérgenos
    pendientes de verificar y lo avisa.

  El camarero resume cada veredicto sin cambiarlo. El chef tarda entre 9 y
  17 s y el turno completo, entre 18 y 24 s; con el pedido como primer mensaje
  de la visita, 28 s. Caben en los 60 s de `WAITER_AGENT_TIMEOUT_SECONDS`.

### Límites y pendientes

- No hay pinches, tiempos, despensa ni confirmación del pedido: el plan de
  cocina no compromete nada y cada cambio es un pedido nuevo.
- Sin base de conocimiento, cocina responde que no puede consultar la carta.
- Pendientes: la revisión conjunta, redesplegar y probar en el navegador.

## Acuerdos del sync del 30/09/2026

Estos acuerdos cambian el diseño objetivo; cuando todavía no están en `main` se
registran como pendientes y no como evidencia implementada:

- Priorizar la carta antes de la orquestación del chef y posponer la validación
  completa del Hosted Agent hasta que el flujo tenga conocimiento real.
- Implementar una única base de conocimiento de dominio en Foundry IQ,
  accesible mediante MCP y compartida por camarero y chef. La primera versión
  puede usar una carta estática con documentos separados para recetas e
  ingredientes; la búsqueda web es un fallback identificado.
- Mantener carta/conocimiento separados del inventario: el chef contrasta los
  ingredientes con la despensa operacional y puede rechazar un plato presente
  en carta.
- Tratar confirmaciones de mesa y pedido como decisiones explícitas de negocio.
  El HITL diferenciado se sitúa en caja, donde una persona revisa petición,
  ticket, consumos, cargos e importe antes del pago.
- Para desbloquear el despliegue provisional, retirar los montajes Azure Files
  de BFF y MCP y aceptar SQLite efímero dentro del contenedor. Perder datos al
  reiniciar es aceptable en esta etapa y no acredita durabilidad.
- Mantener la capa de repositorio para sustituir SQLite por Cosmos DB en la fase
  de integración duradera.
- El JSON del layout es configuración fija; ocupación y disponibilidad son
  estado dinámico. Solo cambiar el ID reinicializa los datos de asientos; un
  JSON distinto debe publicarse con un ID nuevo.
- Foundry y el grupo de recursos se consideran prerrequisitos del script actual;
  no se provisiona Foundry automáticamente.

## Fase 5: carta con Foundry IQ (30/09/2026)

Propuesta en un PR, pendiente de la revisión conjunta; no se marca ninguna
casilla del plan.

### Implementado

- **Contenido** ([`data/knowledge`](data/knowledge/README.md)), original,
  sintético y en español:
  - una carta estática, sin dependencia de la fecha, por partidas (brasa,
    fritos y pinchos fríos) y barra, con identificadores estables, precios en
    euros, los 14 alérgenos de la UE, advertencias y procedencia en cada
    plato; el chorizo a la brasa tiene los alérgenos «pendientes de verificar»
    para demostrar la información ausente;
  - un recetario de cocina burgalesa en HTML, impreso a PDF con Chromium;
  - una ficha de ingredientes.

  Una prueba de contrato comprueba la coherencia entre los tres documentos.
- **Base de conocimiento** (`./scripts/provision-knowledge.sh`, con az CLI y
  REST, idempotente): una base de Foundry IQ con exactamente tres fuentes:
  - la carta, desde Blob Storage;
  - el recetario y los ingredientes, en un índice propio alimentado por un
    indexador desde el PDF;
  - la web con Bing, como respaldo.

  La base usa `gpt-4.1-mini` para planificar las consultas y resumir la web.
  Sus instrucciones de recuperación reservan la web para información pública
  general. Las definiciones de búsqueda están en [`infra/knowledge`](infra/knowledge).
- **Camarero:** un segundo `MCPStreamableHTTPTool` hacia el endpoint MCP propio
  de la base.
  - `knowledge_base_retrieve` es de solo lectura y no pide aprobación.
  - Usa Entra ID: la identidad administrada en Azure y `DefaultAzureCredential`
    en local.
  - Los tiempos están acotados.
  - Etiqueta cada resultado como documento de la casa o fuente externa (web).
  - Una caída de la base nunca bloquea el turno ni los asientos, y el
    proceso no reintenta la conexión hasta pasados 30 s.
  - Las instrucciones exigen citar la fuente y no deducir alérgenos, y reservan
    la web a un respaldo identificado.
  - `decide_seating` y `sync_seating` no se conectan a la base.
  - Sin configuración, el camarero funciona como antes.
- **Despliegue:** tres variables opcionales y juntas. Con ellas, la identidad del
  agente recibe `Search Index Data Reader` y el agente, la configuración.
- **Pendiente para la fase 6:** el chef reutilizará la misma base y el mismo
  endpoint; la despensa seguirá siendo un MCP operacional aparte.

### Recursos de Azure creados

En `rg-morcillaconf-aca`, región `swedencentral` (España Central no ofrece
recuperación agéntica):

- **Almacenamiento:** la cuenta `stmorcillaconf`, con los contenedores `carta` y
  `recetario`.
- **Azure AI Search Basic:** el servicio `srch-morcillaconf`, con identidad
  administrada y solo autenticación Entra ID.
- **Base de conocimiento** `conocimiento-restaurante`, con:
  - sus fuentes `carta-de-la-casa`, `recetario-de-la-casa` y `web-bing`;
  - el índice `recetario-index`, con su indexador.
- **Roles:**
  - el buscador lee el almacenamiento y usa el modelo del proyecto Foundry;
  - el camarero desplegado ya tiene `Search Index Data Reader`.

No se ha tocado el despliegue de Container Apps.

### Evidencia

- **Aprovisionamiento:** se ejecutó varias veces sin cambios inesperados. El
  indexador del recetario procesa 2 documentos y el de la carta, 1, sin
  fallos.
- **Consultas por REST y MCP:**
  - «¿Qué tenéis típico de Burgos?» solo consulta la carta.
  - «¿Qué lleva la morcilla?» consulta el recetario y la carta.
  - «¿Qué es la IGP Morcilla de Burgos?» solo consulta la web. Hace tres
    búsquedas y tarda unos 8 s.
- **Camarero local** con `gpt-5.6-luna`, la base y el MCP de asientos. Responde
  a las tres preguntas con su cita:
  - «Fuente: carta de la casa, versión 1»;
  - «Fuente: recetario de la casa, versión 1, receta R01»;
  - «Fuente externa, web: …».

  Además:
  - Admite los alérgenos pendientes del chorizo.
  - Con una base inexistente, dice que no puede consultar la carta.
  - Mantiene el bloqueo y la confirmación de mesa.
- **Pruebas:** 263 del camarero, los contratos y la carta, y 30 del MCP
  (`./scripts/test.sh`); 6 del recorrido E2E de asientos; 146 del BFF.
- **CI:** la evidencia está en el PR.

### Límites y pendientes

- **Versión preview:** la base y su endpoint MCP usan la versión preview
  `2026-08-01-preview`, sin SLA.
- **Web:**
  - Sale del límite de datos de Azure y cuesta por uso.
  - Sus resúmenes llegan en inglés; el camarero responde en español.
- **Fuentes en la respuesta:** las citas van en el texto de la respuesta. No se
  ha cambiado el contrato ni la vista.
- **Borrador del pedido:** sus productos siguen sin verificar hasta que cocina
  confirme la disponibilidad.
- **Pendientes tras el PR:**
  - la revisión conjunta;
  - redesplegar el camarero con las variables de la base;
  - probar en el navegador.

## Fase 4: solo el camarero habla con el MCP (29/09/2026)

Decisión de Jesús y dsanchor del 29/09: el MCP de asientos solo habla con el
agente. El camarero es quien confirma; ningún otro componente es cliente del
MCP, tampoco en pruebas.

- **Camarero.** `confirm_seating` es una tool del modelo que siempre requiere
  aprobación de Agent Framework: tras bloquear, la ejecución se detiene
  y la petición de aprobación queda en `session_json`. La decisión de los
  botones vuelve como respuesta de aprobación, nunca como texto. El propio
  camarero cancela el bloqueo al rechazar, lee la sala antes y después de cada
  ejecución y devuelve un informe de asientos (sitio propio, `last_outcome` y
  sala anónima). Salvaguardas deterministas: una sola petición de
  confirmación por bloqueo (la añade si el modelo la olvida), argumentos
  autoritativos, comprobación de vigencia y versión, y respuestas fijas sin
  modelo tras una decisión. Si el cliente escribe con la propuesta pendiente,
  el bloqueo se mantiene y el camarero vuelve a pedir la confirmación con los
  botones; solo «Rechazar» y `/new` lo cancelan.
- **Flujo individual.** `party_size` conserva `1` como valor técnico por
  defecto, pero el camarero pregunta si el cliente viene solo o acompañado y no
  busca sitio hasta recibir la respuesta. Para una persona consulta
  disponibilidad, ofrece únicamente mesa, barra o ambas según el MCP y, tras la
  elección explícita, usa `confirm_solo_seating` para bloquear y confirmar sin
  tarjeta de aprobación. Los grupos conservan `confirm_seating` y sus botones.
- **Contrato.** `WaiterRequest` distingue `take_turn`, `decide_seating` y
  `sync_seating`; las respuestas llevan `SeatingReport`. Se retiran los campos
  que solo servían a la reconciliación del BFF con el MCP.
- **BFF.** Sin cliente MCP ni dependencia `mcp`; rechaza `SEATING_MCP_URL`.
  Persiste y presenta la propuesta y una sala compartida con la última lectura
  de cualquier llamada al camarero; envía «Confirmar» y «Rechazar» como
  `decide_seating` y, tras la llegada, `sync_seating` en segundo plano. `/new`
  rechaza una propuesta pendiente a través del camarero o se niega si no
  puede. El modo `scripted` simula los asientos en memoria.
- **MCP.** `expires_at` en los sitios bloqueados del mapa y documentación del
  camarero como único cliente.
- **Vista.** «Camarero simulado» solo con el camarero `scripted`.
- **Despliegue.** El script deja de pasar `SEATING_MCP_URL` al BFF.
- **Recorrido sin Foundry.** `./scripts/test-e2e-seating.sh` levanta MCP,
  camarero independiente con un modelo guionizado y BFF remoto.
- **Corrección del 01/10/2026.** Tras confirmar una mesa con «Confirmar»,
  todos los mensajes posteriores fallaban con HTTP 400 de Foundry
  (`invalid_payload`). La causa:
  - La llamada de confirmación se quedaba en el historial con los argumentos
    como objeto JSON.
  - El esquema de Responses exige una cadena.

  Ahora los argumentos autoritativos se guardan como cadena JSON, también en
  la confirmación directa individual que añade la salvaguarda. Las pruebas de
  regresión validan el `input` que construye el cliente de Foundry. El error
  venía de 661112e (PR #8).
- Evidencia de CI y validación en el Codespace: en el PR correspondiente.
  Revisión conjunta pendiente; no se marca ninguna casilla.

## Acuerdos del sync del 28/09/2026

Los acuerdos siguientes actualizan el alcance futuro; no constituyen evidencia
de implementación ni asignan responsables:

- La aplicación prioriza un recorrido funcional antes de mejoras explicativas o
  visuales. La historia de la demo se adapta a las capacidades realmente
  disponibles.
- El nombre introducido en la entrada será la única identidad de cliente en toda
  la demo. El BFF lo normalizará y conservará en sesión; no habrá un segundo
  parámetro de identidad, proveedor externo ni `actor_id` dentro de comandos.
- 3C construirá el BFF entre Streamlit y el camarero: recibirá la llegada,
  creará o recuperará visita y conversación y enviará mensajes reales al
  agente. 3D validará recarga, memoria, SSE y no duplicación de mensajes.
- El frontend debe poder empaquetarse y desplegarse independientemente mediante
  Docker y Container Apps, configurando por variables de entorno el BFF y el
  proyecto Foundry de cada entorno.
- Todo componente ejecutable de la demo debe incluir un `Dockerfile` propio y
  un workflow de GitHub Actions. En `push`, el workflow solo se activa por
  cambios en la ruta del componente, sus dependencias compartidas empaquetadas
  o su propio YAML; valida, construye y publica una imagen trazable en GitHub
  Packages asociado a este repositorio.
- La gestión de mesas puede avanzar en paralelo con 3C. Un servicio único será
  la autoridad de disponibilidad y creará bloqueos temporales atómicos por
  grupo; confirmar ocupa, rechazar o caducar libera. Una cola visible de
  llegadas es opcional y no interviene en la exclusión mutua.
- Un gateway de modelos o agentes se considera una mejora opcional si queda
  tiempo; no bloquea el recorrido ni reemplaza al BFF.
- El MCP de asientos se inicia con SQLite y tendrá adaptador Cosmos DB al final
  de esta implementación. Gestionará mesas y puestos contiguos de barra desde
  un layout JSON configurado por entorno e identificado por ID; un cambio de ID
  invalida y reinicializa sus propios datos al arrancar.
- Iniciado el MCP de asientos en `services/mcp/`: servidor oficial `mcp` con
  FastMCP y Streamable HTTP, repositorio SQLite transaccional, bloqueos y
  confirmaciones idempotentes, barra contigua que minimiza huecos, Dockerfile y
  workflow de imagen filtrado por ruta. La primera versión de la integración
  directa con el camarero está implementada: declara `MCPStreamableHTTPTool`
  únicamente para disponibilidad y bloqueo, inicializa `visit_id` y
  idempotencia en la sesión del servidor, y conserva la propuesta devuelta por
  el MCP. Debe revisarse con una conversación real contra ambos procesos
  locales antes de considerarla aceptada.
- Esta primera versión solo crea una propuesta temporal; no confirma una
  ocupación. Pendiente: incorporar la decisión explícita del usuario, validar
  vigencia y versión antes de habilitar `confirm_seating`,
  permitir rechazo o cancelación de la propuesta, y habilitar
  `release_seating` exclusivamente después de la verificación del pago. El BFF
  y la UI proyectarán resultados de negocio sin exponer el contrato MCP; pedido,
  pago y el adaptador final Cosmos DB permanecen posteriores.
- Relación actual entre sesión y visita: `ResponsesHostServer` recupera o crea
  una `AgentSession` a partir de la conversación Responses; en la primera
  ejecución de esa sesión, `VisitContextProvider` genera un `visit_id` nuevo
  (`visit_<uuid>`) y lo guarda bajo `session.state["visit_context"]`. El
  `visit_id` no copia ni deriva del identificador público de la conversación:
  es un identificador operacional interno y estable mientras se recupere la
  misma sesión. Por tanto, todos los turnos de una conversación comparten
  visita, secuencia de bloqueos y propuestas; una conversación/sesión nueva
  crea otra visita. Antes de cada `hold_seating`, el middleware construye o
  reutiliza una clave `seating:<visit_id>:<secuencia>` por combinación de
  comensales y preferencia y sustituye los argumentos equivalentes del modelo.
  El BFF futuro será responsable de persistir y recuperar el vínculo entre su
  sesión de cliente, la conversación Responses y esta visita.
- Alineado el camarero con el empaquetado local: Dockerfile con etapas `test` y
  `runtime`, contexto que incorpora contratos compartidos, workflow de imagen
  filtrado por agente/contratos y guía de variables de entorno, volumen SQLite
  y ejecución local antes de conectar Agent Inspector.
- Alineado el frontend: Dockerfile con etapas `test` y `runtime`, README y
  variables de entorno en su raíz, y workflow GHCR filtrado por frontend y
  contratos. Las pruebas locales de los tres componentes siguen ahora el mismo
  patrón de contenedor.

Durante la demostración del sync, la memoria visible y la respuesta del agente
no mostraron el comportamiento esperado. Debe verificarse qué versión e
integración están en ejecución antes de atribuirlo a la implementación de fases
1 o 2. Esta comprobación forma parte de 3C/3D y no invalida sus cierres
conjuntos.

## Fase 1: proyecto y primer camarero

### Implementado

- Proyecto Python 3.13 gestionado con `uv` y lockfile.
- Camarero construido con Microsoft Agent Framework.
- Conexión local con el deployment `gpt-5.6-luna` del proyecto Foundry
  existente.
- Contratos Pydantic para cliente, borrador, campos pendientes y respuesta.
- Un comensal por defecto; solo se pregunta el tamaño del grupo ante una
  petición explícita de mesa sin ese dato.
- Sesiones independientes con propiedad por `actor_id`.
- Límite configurable de turnos.
- Serialización de turnos concurrentes dentro de una misma conversación.
- CLI de desarrollo.
- Servidor local mediante el protocolo Responses.
- Configuración de VS Code y Agent Inspector.
- Scripts reproducibles de preparación, ejecución, pruebas y smoke.
- Generador de configuración local exportable mediante
  `scripts/init-local-env.sh`, con fichero privado e ignorado por Git.
- Utilidad `scripts/manage-memory.sh` para listar y eliminar memorias concretas
  o todas las memorias de una identidad local.

### Evidencia histórica previa al cambio de memoria

- Dos turnos mantienen datos y aplican correcciones.
- Dos sesiones no comparten estado.
- Los turnos concurrentes no superan el límite ni sobrescriben estado.
- El camarero no afirma haber reservado, preparado o cobrado.
- Inferencia real contra Foundry validada.
- Servidor local e invocación mediante `azd ai agent invoke --local` validados.

### Revisión del 27/09/2026

Resultados y hallazgos en
[docs/revision-fase-1-jesus.md](docs/revision-fase-1-jesus.md). La fase quedó
validada conjuntamente el 28/09/2026 tras revisar sus correcciones.

Correcciones validadas el 27/09/2026 en un Codespace con `gpt-5.6-luna`:

- `pending_fields` se deriva de los datos del cliente en lugar de rechazar la
  respuesta del modelo, y el camarero pide el nombre cuando no lo conoce.
- Las respuestas que no cumplen el contrato y los fallos del servicio del
  modelo se muestran como errores visibles, sin cerrar la CLI ni imprimir el
  contenido de la respuesta.
- Los scripts tienen permiso de ejecución y las pruebas de configuración no
  dependen de las variables cargadas en la terminal.
- Se documenta que `gpt-6-luna` no admite la salida estructurada del camarero.

Evidencia: `./scripts/test.sh` superado; `./scripts/smoke-test.sh` superado
(«Foundry smoke test passed.»); en la CLI, «hola» sin nombre hace que el
camarero pida el nombre sin cerrarse, y con `gpt-6-luna` se muestra el error
sin cerrar la conversación.

Se conserva la decisión histórica de usar una persona como valor técnico por
defecto. El flujo individual/grupo de la fase 4 añade una pregunta obligatoria
al inicio: ese `1` no permite buscar sitio hasta que el cliente confirme que
viene solo o comunique el total del grupo.

### Cierre

Fase 1 completada y validada conjuntamente el **28/09/2026**.

## Fase 2: memoria persistente automática

### Cambio aprobado: implementado y validado localmente

La política vigente sustituye la memoria consentida por el recuerdo automático
propio del rol de camarero. Se lee y escribe para la identidad de demo que el
BFF resolverá desde el nombre de entrada; la configuración local actual usa el
mismo criterio. Se mantienen aislamiento, procedencia, límites, contexto no
vincante y reconfirmación de restricciones.

Se eliminaron APIs, configuración y campos de consentimiento. Posteriormente,
la autoridad completa se movió al agente y se retiraron del BFF/frontend la
consulta, corrección y borrado mediante panel. La CLI local del agente conserva
operaciones administrativas para desarrollo.
El borrado afecta solo a preferencias y restricciones, no a visita, borrador
ni `completed_order_history`. La gestión explícita se realiza mediante las
operaciones disponibles de CLI/administración y los contratos públicos;
no se han añadido herramientas de borrado por lenguaje natural.

La migración SQLite transaccional elimina el acoplamiento al consentimiento,
conserva recuerdos existentes, contadores e historial y no restaura recuerdos
previamente eliminados. Memoria y borradores siguen separados de pedidos
completados.

### Evidencia actual

- `./scripts/test.sh`: **127 pruebas superadas**.
- Escritura automática desde el primer turno, identidad falsa de desarrollo
  como alternativa local y aislamiento de invitados.
- Borrado total seguido de nuevas memorias sin desactivar el aprendizaje.
- Migración con conservación de datos y rollback ante fallo, usando esquemas
  antiguos reales con claves foráneas.
- Pruebas de CLI y contratos públicos actualizados.

Las cifras y smoke anteriores se conservan como evidencia histórica; no
representan una nueva ejecución del smoke real con esta política.

### Implementado

- Contratos separados para:
  - estado de sesión;
  - memoria duradera;
  - historial de pedidos completados.
- Adaptador SQLite desacoplado mediante un protocolo de repositorio.
- Lectura/escritura automática por identidad de demo, con procedencia y fecha.
- Consulta, corrección y borrado individual o total de recuerdos.
- Borrado total sin bloqueo permanente de escrituras futuras.
- Límite configurable aplicado por separado a preferencias y restricciones.
- Aislamiento entre identidades.
- Operaciones SQLite transaccionales y prueba de escrituras concurrentes.
- Agent Framework context provider para recuperar memoria en cada turno.
- Invalidación después de corrección y borrado.
- Migración transaccional del esquema local sin acoplamiento al consentimiento,
  conservando datos existentes y sin recuperar recuerdos eliminados.
- Resumen automático de los productos del borrador como una preferencia de
  pedido de largo plazo para la identidad de demo.
- Campo `remembered_memories` en la respuesta local con el contenido realmente
  recuperado de SQLite, separado del estado reafirmado en la visita.
- Resolución de intención para «lo de siempre», «como siempre» y expresiones
  equivalentes: aplica directamente una única opción y, si hay varias, las
  presenta por frecuencia y recencia para que el cliente elija, sin mostrarle
  contadores ni otros metadatos internos. Las combinaciones solapadas se
  consolidan por producto para no repetir alternativas.
- Clasificador semántico especializado para decidir si el mensaje reutiliza el
  pedido habitual; el context provider entrega esa decisión y la memoria al
  camarero antes de generar el borrador.
- **Corrección del 05/10/2026.** El clasificador tomaba «ponme otra de
  croquetas» como «lo de siempre»: sus instrucciones aceptaban cualquier forma
  indirecta o coloquial de «repetir», y el pedido habitual sustituía al nuevo.
  Ahora solo reutiliza el pedido habitual cuando el cliente se refiere a él en
  conjunto, sin nombrar platos. Devuelve `none` al pedir otra ración de un plato
  concreto, al repetir algo de la misma visita y con otros usos de «siempre».
  Evaluado contra `gpt-5.6-luna`, con 3 ejecuciones por frase:
  - 75/75 en 25 frases (antes, 56/75);
  - 45/45 en 15 frases que no aparecen en las instrucciones (antes, 30/45).
- Historial acotado de resúmenes de pedido dentro de la cuota de preferencias:
  conserva pedidos distintos, cuenta duplicados exactos y utiliza frecuencia y
  recencia para ordenar las alternativas de «lo de siempre», sin sumarización
  generativa.

### Decisión vigente sobre alergias y restricciones

Las preferencias y las alergias o restricciones se almacenan automáticamente
para la identidad de demo, pero se clasifican por separado:

- `preference`;
- `restriction`.

Todos los recuerdos son **no vinculantes**:

- llevan procedencia y fecha;
- se inyectan como contexto no confiable;
- las restricciones requieren reconfirmación en la visita actual;
- una instrucción actual prevalece sobre el recuerdo;
- la memoria no acredita carta, precio, stock o disponibilidad;
- el pedido seguirá requiriendo confirmación explícita en una fase posterior.
- el resumen de pedido omite cantidades y no convierte el borrador en historial
  de pedidos completados.

### Identidad falsa para desarrollo local

Mientras no exista el BFF de la fase 3, el servidor local puede usar una
identidad falsa obtenida exclusivamente de variables de entorno:

```dotenv
APP_ENVIRONMENT="development"
ENABLE_DEV_FAKE_IDENTITY="true"
DEV_FAKE_ACTOR_ID="Cliente local"
```

Controles:

- no se acepta un `actor_id` enviado por el mensaje HTTP;
- `DEV_FAKE_ACTOR_ID` funciona también como nombre presentado y se inyecta
  desde el primer turno para ofrecer un trato cercano;
- la configuración se rechaza fuera de `development`;
- la fase 3 unificará este mecanismo con el nombre de entrada y la sesión de
  demo del BFF; no se incorporará Entra ID.

Para actualizar el entorno existente: eliminar `DEV_FAKE_MEMORY_CONSENT` del
`.env` local, ejecutar `./scripts/init-local-env.sh --force` y volver a cargar
`.local/restaurant.env.sh`. La variable antigua exportada ya no se necesita
(`unset DEV_FAKE_MEMORY_CONSENT` la retira de la terminal actual).

### Evidencia histórica de la política anterior

Los siguientes resultados se obtuvieron antes del cambio aprobado. Las
comprobaciones de consentimiento y revocación describen exclusivamente aquella
versión y deben sustituirse por pruebas de memoria automática y borrado total.

- Sin consentimiento no se escribe memoria.
- Un invitado no crea un perfil duradero.
- Una identidad no recupera recuerdos de otra.
- Preferencias y restricciones sobreviven al reinicio del adaptador.
- Corregir y borrar modifica lo recuperado.
- Revocar elimina recuerdos y afecta a las conversaciones activas.
- Los recuerdos no se copian al historial de mensajes del usuario.
- El context provider revalida consentimiento y memoria en cada turno.
- El smoke real contra Foundry persiste y recupera una preferencia.
- `run-local.sh` guarda y recupera preferencias y restricciones con la identidad
  falsa de entorno, solicitando reconfirmación de la restricción recordada.
- Los productos de cada borrador se persisten juntos en un resumen de
  preferencia; los resúmenes distintos se conservan dentro del límite.
- Las preferencias y restricciones recuperadas se muestran en
  `remembered_memories`; no se copian a `customer.preferences` ni
  `customer.restrictions` hasta que el cliente las reafirma.
- Una petición de «lo de siempre» reafirma el pedido habitual y crea un borrador
  no verificado; no confirma la comanda ni reafirma restricciones recordadas.
- Validación real con la identidad `david`: «Ponme lo de siempre» recupera
  tortilla de patata y agua con gas y los añade a `order_draft`.

### Revisión del 27/09/2026

Revisión manual en un Codespace con `gpt-5.6-luna`. Funcionaron:

- guardado automático y recuperación después de reiniciar;
- aislamiento entre identidades y para invitados;
- «lo de siempre» con uno y con varios pedidos recordados;
- persistencia y aislamiento dentro del agente;
- rechazo de los comandos de memoria retirados en el frontend.

Se encontró un fallo: el resumen del pedido se contaba por turno, no por
conversación. En una sola conversación, «Hola, soy Ana. Quiero una tortilla de
patatas», «Somos dos» y «Gracias» dejaron «Preferencia de pedido: tortilla de
patatas» con `occurrence_count: 3`. Además, después de `/memory clear`, el
siguiente mensaje de esa conversación volvía a crear el resumen a partir del
borrador sin cambios.

El ajuste posterior «contar cada pedido una vez por conversación» se incorporó
a `main` con la PR #2 (`cc4675b`). No bloquea el cierre: la fase quedó validada
conjuntamente el 28/09/2026. Falta registrar su validación: `./scripts/test.sh`
(138 pruebas esperadas) y repetir la prueba manual del resumen de pedido.

### Cierre

Fase 2 completada y validada conjuntamente el **28/09/2026**.

## Fase 3A: contratos publicos

### Implementado

- Paquete instalable `morcillaconf-contracts` en
  [`packages/contracts`](packages/contracts), dependiente solo de Pydantic.
- Comandos tipados de llegada, mensaje, consulta/corrección/borrado de memoria,
  y borrado total (`memory.clear_requested`), sin concesión ni revocación.
- Identidad validada por servidor separada del comando enviado por el cliente.
- Resultados discriminados `pending`, `completed` y `failed`, con errores
  públicos y correlación verificable.
- Snapshot de visita, conversación, cliente, borrador, memoria, proceso,
  acciones permitidas y cursor.
- Eventos de snapshot confirmado, estado de comando y texto provisional,
  distinguidos para no inferir efectos de negocio desde el texto.
- Contrato de recuperación por cursor caducado y protocolo asíncrono
  `BffClient`; no se implementan aún sus adaptadores.
- Extracción de tipos compartidos de cliente, borrador y recuerdos. El cambio
  actual retira los tipos/campos de consentimiento de la proyección pública
  y del snapshot interno; cliente y borrador conservan su representación.
- Dependencia local fijada en el lockfile existente y pruebas conectadas a
  `scripts/test.sh`.
- [Documentación del contrato](packages/contracts/README.md) y
  [fixtures sintéticos](tests/fixtures/phase3a) para trabajar el frontend.

### Evidencia histórica de 3A antes del cambio de memoria

La actualización de los seis comandos públicos y la eliminación de
`MemoryConsent` están implementadas y validadas dentro de las **127 pruebas**
de `./scripts/test.sh`. Se mantiene el protocolo v1 como cambio coordinado
previo a integrar consumidores, rechazando los comandos antiguos.
La evidencia siguiente corresponde a la extracción inicial de 3A, no al
contrato modificado.

- `./scripts/test.sh`: **120 pruebas superadas**, incluyendo las pruebas
  existentes del camarero, contratos nuevos y compatibilidad entre paquetes.
- Se rechazan identidad en comandos, payloads inválidos, errores incoherentes,
  cursores inválidos y discrepancias entre evento y snapshot/resultado.
- Se validan round trips JSON, generación de esquemas y secuencia de fixtures
  para reconstrucción/reconexión; no es una reconexión de red real.
- Se conserva la separación entre restricciones recordadas y datos actuales,
  y se rechaza memoria duradera para invitados.
- Wheel del paquete construido correctamente con `uv build --wheel --offline`.
- Entrada Responses importada sin errores, sin arrancar servidor ni llamar al
  modelo. No se han modificado prompts ni `main.py`.

### Revisión del 28/09/2026

Resultados y decisiones en
[docs/revision-fase-3a-jesus.md](docs/revision-fase-3a-jesus.md). No hay
hallazgos que corregir: los contratos cubren el mínimo del anexo.

Evidencia en un Codespace, sobre la rama de 3B y con los mismos contratos que
`main`: `./scripts/test.sh` dio **133 pruebas superadas**. Un guion con 15
casos contra los fixtures confirmó que se aceptan los seis comandos de ejemplo
y se rechazan la identidad dentro del comando, el mensaje vacío, el
consentimiento retirado, la fecha sin zona horaria, los recuerdos de un
invitado, el nombre ausente sin marcar como pendiente, el reintento tras un
fallo definitivo, el cursor caducado sin snapshot nuevo y el texto parcial
marcado como definitivo.

Decisiones:

- **Nombre (D1):** el nombre escrito en la puerta es el único que se usa: es
  la identidad y el nombre con el que trata el camarero. Quedó adoptada en el
  [sync del 28/09](#acuerdos-del-sync-del-28092026): el BFF lo normaliza y no
  se incorpora Entra ID. No cambia los contratos; la aplican 3C y 3D.
- **Comensales (D2):** se confirma la decisión del 27/09: el grupo empieza en
  una persona, la identificada, y sube cuando dice que viene acompañada. La
  regla general del plan se alinea con SPECS.

### Límites y revisión pendiente

- 3A y el cambio de memoria tienen evidencia local; la revisión conjunta sigue
  pendiente.
  No se marca como completada la fase 3 ni sus criterios de aceptación integral.
- No existen aún BFF, SSE real, identidad de demo persistida por sesión,
  persistencia de visitas/eventos ni deduplicación de efectos. Los contratos describen esas
  obligaciones, pero no las ejecutan.
- No se añaden mesas, pagos, HITL ni contratos completos de fase 4.
- El servidor Responses sigue creando el agente directamente; el adaptador al
  BFF debe resolver esa integración sin asumir que usa `ConversationManager`.
- Antes del `remote_build` de fase 7 debe incluirse el paquete compartido en el
  artefacto remoto o distribuir su wheel. La ruta editable funciona en el
  checkout local, no acredita un despliegue remoto.

## Fase 3B: vista del cliente

### Implementado

- Aplicación Streamlit en [`apps/frontend`](apps/frontend) con una única vista:
  puerta, sala con comandos, conversación y plano, según el diseño aprobado.
- `FakeBffClient` implementa el protocolo `BffClient` de 3A con un camarero
  simulado, identificado en la vista como «Camarero simulado». El adaptador se
  elige con `FRONTEND_BFF_CLIENT`; `http` falla con un mensaje claro hasta 3C.
- Identidad sintética de desarrollo: el nombre escrito en la puerta, vinculado
  por el adaptador y nunca enviado en los comandos.
- Proyección desde snapshots confirmados, eventos consumidos por cursor,
  snapshot nuevo al caducar el cursor y consulta por `event_id` de un comando
  interrumpido por un rerun, sin reenviarlo.
- Consulta, corrección y borrado individual o total de recuerdos; tras el
  borrado total la vista recuerda que las interacciones futuras se guardarán.
- Vista previa estática del diseño en `apps/frontend/preview` y sistema visual
  en [DESIGN.md](DESIGN.md) y [PRODUCT.md](PRODUCT.md).

### Evidencia y pendientes

- El Codespace generó `apps/frontend/uv.lock` (Streamlit 1.64.0, 49 paquetes),
  versionado en el commit f7b300c.
- `./scripts/test-frontend.sh` dio **106 pruebas superadas** en f7b300c,
  incluidas las 6 de `AppTest`. Con las 2 pruebas de las correcciones
  posteriores, las 108 pasaron en la etapa `test` de la imagen en la
  [ejecución 36452320460](https://github.com/dsanchor/morcillaconf/actions/runs/36452320460).
- El recorrido manual en la vista real se superó en escritorio y en emulación
  móvil: la puerta tiembla con el nombre vacío, animación de apertura y saludo,
  comandos de memoria, `/new`, `/exit` y aislamiento frente a otra identidad.
- El primer render mostró cuatro diferencias con el prototipo, ya corregidas y
  verificadas en la vista real: comandos centrados, el comando largo partido en
  dos líneas y el botón de envío debajo del campo (db5e9b3 y 57c80e0), y la
  pared izquierda del plano ausente (a5aefc8).
- El CSS depende de detalles internos de Streamlit 1.64.0, fijado por el
  lockfile; hay que revisarlo- Imagen de contenedor: la imagen publicada desde main en c5dd00b no
  funcionaba: las etapas `test` y `runtime` solo instalaban dependencias
  (`--no-install-project`) y fallaban con `No module named 'frontend'`. El
  PR #5 instala el propio frontend en ambas etapas y añade al workflow una
  comprobación de importación de la etapa `runtime` antes de publicar. La
  [ejecución 36452320460](https://github.com/dsanchor/morcillaconf/actions/runs/36452320460) pasó las 108 pruebas, incluidas las de `AppTest`, y la comprobación, y
  publicó `ghcr.io/dsanchor/morcillaconf-frontend:a46c0b040e1bd289a12f45442a8e1f1441bcefbc`
  (`sha256:1b19511f801dd17194586fc6b566735e05325e53638ba49719b95a8840b6c902`).
  El despliegue en Container Apps está documentado en
  [apps/frontend](apps/frontend/README.md#imagen-de-contenedor-y-despliegue-en-container-apps),
  pero todavía no se ha ejecutado.
davía no se ha ejecutado.
- Revisión conjunta pendiente; no se marca ninguna casilla de la fase 3.

## Fase 3C: BFF

### Implementado

- BFF FastAPI en [`apps/bff`](apps/bff) con su propio proyecto `uv`,
  dependiente de los contratos y del paquete del camarero. Configuración solo
  por variables de entorno o `.env` opcional, sin cargar entornos locales.
- API `/v1`: sesión de demo desde el nombre de la puerta (token opaco guardado
  como hash), comandos, resultado por `event_id`, snapshot y SSE con cursor,
  `Last-Event-ID`, latidos y caducidad explícita (`cursor_expired` con
  `fetch_snapshot`). Pertenencia comprobada en resultados, snapshot y stream.
- Identidad D1: el actor se deriva del nombre normalizado (sin mayúsculas,
  tildes ni espacios repetidos; conserva la ñ). El nombre presentado lo fija la
  aplicación en cada turno, aunque en el chat se diga otro; el camarero no lo
  pregunta ni vuelve a saludar.
- Llegada con saludo determinista e instantáneo; la vista recupera la visita
  activa al entrar con el mismo nombre y `/new` abre otra.
- Mensajes con resultado `pending`, estado `processing` y turno en segundo
  plano, uno por conversación; límite de turnos, idempotencia por
  `(actor, event_id)` con huella y fallos públicos en español sin contenido
  del modelo.
- El camarero se ejecuta en el proceso mediante `ConversationManager`
  (adaptador local de fase 3), con memoria automática, guarda del resumen de
  pedido por conversación y su historial de Agent Framework guardado en
  SQLite. Los tests inyectan directamente un camarero determinista, sin
  ofrecerlo como modo de ejecución del BFF.
- Comandos de memoria contra el almacén del camarero, con ids cortos por
  cliente (`m1`, `m2`…) que no se reutilizan.
- Cliente HTTP/SSE del frontend (`FRONTEND_BFF_CLIENT=http`,
  `FRONTEND_BFF_URL`) con la biblioteca estándar: `apps/frontend/uv.lock` no
  cambia.
- Recuperación al arrancar de turnos interrumpidos, spans sin contenido,
  `Dockerfile` con etapas `test` y `runtime`, workflow `bff-image.yml` filtrado
  por ruta y scripts `setup-bff.sh`, `test-bff.sh` y `run-bff.sh`.

### Evidencia y pendientes

- Evidencia no oficial en un Mac sin acceso a PyPI, con wheels en caché y sin
  `uv run`: 95 pruebas del BFF (se omite la que construye el camarero de Foundry), 116 del frontend (sin `AppTest`, porque no hay
  Streamlit en caché) y 140 del camarero y los contratos superadas. Queda fuera
  una prueba que lanza Python aislado y el módulo de la CLI, que importa
  Foundry.
- Recorrido no oficial contra el BFF real con el camarero simulado y el
  cliente HTTP: saludo, nombre fijado, comensales, memoria con `m1`/`m2`,
  corrección y borrado, recuperación con «  ANA », aislamiento de «Luis»,
  límite de turnos y `/new` conservando recuerdos.
- `apps/bff/uv.lock` se generó en GitHub Actions con uv 0.11.7 y Python 3.13
  y fija las mismas versiones que el lockfile del camarero para todos los
  paquetes compartidos (`constraint-dependencies` y
  `tests/test_lock_alignment.py`).
- CI tras integrar `main` (56321d6, asientos por MCP): «Publish BFF image»
  superado en cee51fb
  ([run 36464211413](https://github.com/dsanchor/morcillaconf/actions/runs/36464211413)),
  con **96 pruebas** en la etapa `test`, incluida la que construye el camarero
  de Foundry con `VisitContextProvider` y sin herramienta MCP, y la imagen
  `ghcr.io/dsanchor/morcillaconf-bff:cee51fb45f6e47fb39f37e3b30c6709739980833` publicada. «Publish restaurant
  agent image» (69 pruebas) y «Publish MCP image» (8) también se superaron.
- Asientos fuera de la fase 3: el BFF construye el camarero con
  `VisitContextProvider` y su middleware, pero ignora `SEATING_MCP_URL`
  aunque esté exportada. Pendiente para la fase 4: sembrar
  `session.state["visit_context"]["visit_id"]` con el id de visita del BFF.
  Hoy el provider inventa su propio `visit_<uuid>` y los bloqueos de asiento
  no coincidirían con la visita del BFF. El historial serializado conserva ese
  `visit_context` entre turnos.
- La validación de recarga, memoria, SSE y no duplicación extremo a extremo
  corresponde a 3D; la cubre la validación en el Codespace descrita abajo.

### Validación en el Codespace (28/09/2026)

Jesús validó la rama integrada con `main` (56321d6) en su Codespace, con
`gpt-5.6-luna` y `SEATING_MCP_URL` sin definir:

- `./scripts/test.sh`: 146 y 8 pruebas superadas (camarero y contratos;
  `services/mcp`). `./scripts/test-frontend.sh`: 123. `./scripts/test-bff.sh`:
  96.
- `./scripts/smoke-test.sh` superado contra Foundry: las líneas nuevas de
  `instructions.md` no cambian el comportamiento de las fases 1 y 2.
- Recorrido manual completo con el BFF y el camarero real:
  - saludo instantáneo;
  - conversación sin preguntar el nombre ni repetir el saludo;
  - nombre fijado aunque se diga otro en el chat;
  - comandos de memoria con `m1`/`m2`;
  - `/new`;
  - recarga sin duplicados ni visita nueva;
  - aislamiento entre dos nombres;
  - límite de turnos;
  - reinicio del BFF conservando conversación e historial; la memoria pertenece
    al ciclo de vida del agente.

La validación cubre también las comprobaciones de 3D acordadas en el sync:
recarga, memoria, SSE y no duplicación. Revisión conjunta pendiente; no se
marca ninguna casilla.

## Fase 4: mesas y barra integradas

El trabajo terminó integrado en `main` mediante el PR #8. Conecta el MCP de
asientos con la web: el camarero bloquea un sitio, la vista lo propone con dos
botones y el plano muestra la sala.

### Implementado

- **MCP de asientos.** Una mesa, un grupo: una mesa bloqueada u ocupada no está
  disponible para otra visita aunque le queden sillas. La
  barra elige el tramo que deja el menor hueco y, a igualdad, la posición más
  baja. Un bloqueo nuevo de la misma visita sustituye en la misma transacción
  al pendiente. Nuevas tools para la aplicación: `cancel_seating_hold` y
  `get_seating_map` (sala anónima con marcas `mine`). Errores con código
  estable. Layout de demostración versionado (mesas de 2, 2, 4, 4 y 6 y barra
  de 8) y `./scripts/run-mcp.sh [--reset]`.
- **Contratos.** `table.confirmation_decided` (`proposal_id`, versión,
  `confirmed`/`rejected`), `RestaurantSnapshot.seating` (sin sitio, propuesta
  o sentado) y `RoomView` para el plano. Aditivo: sigue `schema_version: 1`.
- **Camarero.** `restaurant_agent.seating_gateway` para el código de
  aplicación (bloquear, confirmar, cancelar y leer la sala por Streamable
  HTTP). El BFF siembra su id de visita y el estado de asiento en cada turno;
  el middleware solo reutiliza la clave de bloqueo mientras la propuesta
  pendiente responde a la misma petición. Se corrige la lectura del resultado
  de `hold_seating`: el MCP devuelve dos contenidos de texto y la propuesta no
  se guardaba. Instrucciones: bloquear en cuanto se sabe cuántos son, mesa
  primero, barra si se pide o si no quedan mesas, y confirmar solo con los
  botones.
- **BFF.** Propuesta pendiente persistida; decisión con pertenencia, versión,
  caducidad, bloqueo por conversación e idempotencia por evento y por
  propuesta; mensajes fijos del camarero; reconciliación con el MCP; `/new`
  cancela una propuesta y se rechaza con el grupo sentado;
  `GET /v1/conversations/{id}/room`. El camarero simulado bloquea con «somos
  N», «barra» y «mesa».
- **Vista.** Tarjeta de propuesta con «Confirmar» y «Rechazar», plano desde
  `RoomView` que se refresca solo cada 3 segundos, otros grupos anónimos,
  sitios reservados en ámbar, propuesta propia en vino y paseo del grupo hasta
  sus sillas al confirmar (una vez, solo CSS, con `prefers-reduced-motion`).
- **Recorrido sin Foundry.** `./scripts/test-e2e-seating.sh` levanta el MCP y
  el BFF simulado y los recorre con el cliente HTTP de la vista; workflow
  `seating-e2e.yml` solo de pruebas (fuera de la convención de imágenes, en
  commit propio para decidir si se conserva).

### Evidencia y pendientes

- CI de la rama tras las correcciones del Codespace: MCP 27 pruebas,
  camarero 92, BFF 134, frontend 146 (con `AppTest`) y recorrido de asientos
  3, todas superadas; enlaces e imágenes en el PR. En el Codespace:
  `./scripts/test.sh` 205 + 27, `./scripts/test-frontend.sh` 146,
  `./scripts/test-bff.sh` 134 y `./scripts/test-e2e-seating.sh` 3.
- Evidencia no oficial en el Mac sin PyPI: el recorrido de asientos contra el
  MCP y el BFF reales (camarero simulado) y capturas del plano y del paseo con
  Chromium sin interfaz.
- Validación de Jesús en el Codespace (29/09/2026), con Foundry y
  `gpt-5.6-luna`: escenarios S1 a S10 del PR superados. Dos hallazgos,
  corregidos en la misma rama:
  - **Una propuesta rechazada volvía en boca del camarero.** Tras rechazar
    Marta la Mesa 4 (que luego ocupó Pablo), el camarero le seguía hablando de
    esa mesa y de sus botones, sin tarjeta. Causa confirmada con una prueba del
    camino real de la tool: el historial del modelo acababa en su propia
    propuesta (resultado de `hold_seating` con estado `held`) y nada le
    contaba el rechazo, que solo era un mensaje del BFF; el contexto decía
    `none` sin motivo. La tool no fallaba: una llamada en ese turno usa una
    clave nueva y obtiene un bloqueo nuevo, y la ausencia de tarjeta muestra
    que el modelo respondió de memoria. Corrección: `last_outcome` en el estado
    de asiento, los mensajes fijos del camarero se añaden a su historial y la
    regla de que solo puede describir la propuesta presente en ese estado.
  - **El plano desaparecía mientras el camarero pensaba.** Se dibujaba al
    final del turno; ahora se dibuja antes de cualquier espera y se vuelve a
    dibujar en cuanto el turno cambia el sitio del cliente.
  - Mejora pedida: cada mesa muestra su número («Mesa N» del layout; 1 a 5 en
    la sala decorativa).
- Pendiente: revisión conjunta del alcance completo de la fase; las casillas no
  cubiertas por pedido, caja, pago y liberación siguen abiertas.
- Pendiente de fase 4: liberar la mesa tras el pago (`table.release_requested`),
  pedido, cuenta y pago; adaptador Cosmos DB del MCP.
- Riesgos: si el MCP está caído, cada turno del camarero de Foundry falla con
  un aviso claro (Agent Framework conecta sus tools antes de llamar al
  modelo); la redacción del modelo puede variar, pero la tarjeta es la que
  manda.

## Próximo trabajo previsto

1. Revisar conjuntamente la fase 5, la cocina v1 y la caja v1, y redesplegar
   con la base de conocimiento, el chef y la caja.
2. Añadir los pinches de brasa, fritos y pinchos fríos y la despensa por MCP:
   el chef contrastará los ingredientes con el inventario.
3. Dar interfaz a la revisión humana de caja, ya preparada como etapa
   opcional, y cobrar también las bebidas.
4. Validar el Hosted Agent después de que carta y orquestación formen un flujo
   representativo.

## Ejecución y validación

Preparar dependencias:

```bash
./scripts/setup.sh
```

Ejecutar pruebas locales:

```bash
./scripts/test.sh
```

Preparar, probar y arrancar la vista de cliente (camarero simulado):

```bash
./scripts/setup-frontend.sh
./scripts/test-frontend.sh
./scripts/run-frontend.sh
```

Preparar, probar y arrancar el BFF (ver [su README](apps/bff/README.md)):

```bash
./scripts/setup-bff.sh
./scripts/test-bff.sh
./scripts/run-bff.sh
```

Arrancar el agente local:

```bash
./scripts/run-local.sh
```

Aprovisionar y consultar la base de conocimiento (ver el
[README](README.md#base-de-conocimiento-foundry-iq)):

```bash
./scripts/provision-knowledge.sh scripts/knowledge.env
./scripts/query-knowledge.sh "¿Qué tenéis típico de Burgos?"
```

Ejecutar el smoke real contra Foundry:

```bash
./scripts/smoke-test.sh
```

El smoke real consume cuota del deployment configurado.

## Reglas para actualizar este documento

Al completar trabajo:

1. Actualizar la fecha.
2. Cambiar el estado de la fase sin ocultar tareas parciales.
3. Añadir evidencia reproducible: prueba, comando o demostración.
4. Registrar decisiones que cambien comportamiento o requisitos.
5. Mantener visibles riesgos y limitaciones.
6. No marcar una fase como revisada sin revisión conjunta.
7. Actualizar también `SPECS.md` si cambia el comportamiento.
8. Actualizar también `PLAN_IMPLEMENTACION.md` si cambia el alcance de una fase.
