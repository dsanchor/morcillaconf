# Progreso de implementación

Última actualización: **2026-09-28**

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
| 3. Vista única, BFF y continuidad | Parcial: 3A implementada y validada localmente; 3B implementada y validada en el Codespace | En curso: [revisión de 3A del 28/09](docs/revision-fase-3a-jesus.md) | 3A: 127 pruebas locales totales, incluidos CLI y contratos actualizados; en la revisión, 133 pruebas y 15 reglas del contrato superadas en el Codespace. 3B: [vista Streamlit](#fase-3b-vista-del-cliente) contra `FakeBffClient`, lockfile versionado y 106 pruebas superadas en el Codespace. 3C/3D priorizados |
| 4. Recorrido local completo y dos HITL | Pendiente; diseño de mesas acordado | Pendiente | Bloqueo temporal atómico, confirmación de mesa y concurrencia se pueden implementar en paralelo con 3C |
| 5. Validación temprana de Hosted Agent | Pendiente | Pendiente | El agente solo se ha ejecutado localmente |
| 6. Carta con fuentes y herramientas MCP | Pendiente | Pendiente | Sin implementación |
| 7. Chef líder y especialistas | Pendiente | Pendiente | Sin implementación |
| 8. Proveedor mediante A2A | Pendiente | Pendiente | Sin implementación |
| 9. Integración duradera en Azure | Pendiente | Pendiente | Sin implementación |
| 10. Evaluación y ensayo final | Pendiente | Pendiente | Sin implementación |

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

Decisión sobre el número de comensales: se asume un mínimo de una persona,
porque siempre hay al menos un cliente usando la aplicación. El smoke se
alinea con esta regla: ante «Queremos cenar» espera una persona y solo el
nombre pendiente.

### Cierre

Fase 1 completada y validada conjuntamente el **28/09/2026**.

## Fase 2: memoria persistente automática

### Cambio aprobado: implementado y validado localmente

La política vigente sustituye la memoria consentida por el recuerdo automático
propio del rol de camarero. Se lee y escribe para la identidad de demo que el
BFF resolverá desde el nombre de entrada; la configuración local actual usa el
mismo criterio. Se mantienen aislamiento, procedencia, límites, contexto no
vincante y reconfirmación de restricciones.

Se eliminan APIs, comandos, configuración y campos de consentimiento, tanto en
`MemoryView` como en el snapshot interno. Consulta, corrección y borrado siguen
disponibles. `/memory clear` olvida todos los recuerdos existentes, pero no
revoca permanentemente la memoria: las interacciones futuras vuelven a guardarse
automáticamente. Las entradas antiguas de alta/revocación se rechazan.
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
- el pedido seguirá requiriendo confirmación HITL en una fase posterior.
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
- corrección, borrado y `/memory clear`;
- rechazo de `/memory consent`.

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
- Antes del `remote_build` de fase 5 debe incluirse el paquete compartido en el
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
  incluidas las 6 de `AppTest`. Las correcciones posteriores añaden 2 pruebas
  (108 en total), que todavía no se han vuelto a ejecutar ni a informar.
- El recorrido manual en la vista real se superó en escritorio y en emulación
  móvil: la puerta tiembla con el nombre vacío, animación de apertura y saludo,
  comandos de memoria, `/new`, `/exit` y aislamiento frente a otra identidad.
- El primer render mostró cuatro diferencias con el prototipo, ya corregidas y
  verificadas en la vista real: comandos centrados, el comando largo partido en
  dos líneas y el botón de envío debajo del campo (db5e9b3 y 57c80e0), y la
  pared izquierda del plano ausente (a5aefc8).
- El CSS depende de detalles internos de Streamlit 1.64.0, fijado por el
  lockfile; hay que revisarlo al actualizar Streamlit.
- Revisión conjunta pendiente; no se marca ninguna casilla de la fase 3.

## Próximo trabajo previsto

Con 3A implementada y la vista 3B validada en el Codespace contra
`FakeBffClient`, los carriles 3C y 3D incorporarán:

- conexión de la vista Streamlit al BFF real;
- BFF con FastAPI;
- identidad de demo derivada por el BFF del nombre escrito en la puerta
  ([acuerdos del sync](#acuerdos-del-sync-del-28092026));
- comandos HTTP;
- actualizaciones SSE;
- snapshot de estado;
- persistencia y recuperación de la visita activa;
- sustitución de la identidad falsa de desarrollo.

En paralelo, la fase 4 puede construir el servicio determinista de mesas y su
contrato de bloqueo temporal; su aceptación extremo a extremo sigue dependiendo
de la integración de fase 3.

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

Arrancar el agente local:

```bash
./scripts/run-local.sh
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
