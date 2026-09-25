# Progreso de implementación

Última actualización: **2026-09-25**

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
| 1. Proyecto y primer camarero | Implementada | Pendiente | 42 pruebas locales compartidas con fase 2; inferencia real y servidor local validados |
| 2. Memoria persistente consentida | Implementada | Pendiente | SQLite, consentimiento, aislamiento, revocación, concurrencia y smoke real |
| 3. Vista única, BFF y continuidad | Pendiente | Pendiente | Sin implementación |
| 4. Recorrido local completo y dos HITL | Pendiente | Pendiente | Sin implementación |
| 5. Validación temprana de Hosted Agent | Pendiente | Pendiente | El agente solo se ha ejecutado localmente |
| 6. Carta con fuentes y herramientas MCP | Pendiente | Pendiente | Sin implementación |
| 7. Chef líder y especialistas | Pendiente | Pendiente | Sin implementación |
| 8. Proveedor mediante A2A | Pendiente | Pendiente | Sin implementación |
| 9. Integración duradera en Azure | Pendiente | Pendiente | Sin implementación |
| 10. Evaluación y ensayo final | Pendiente | Pendiente | Sin implementación |

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

### Evidencia

- Dos turnos mantienen datos y aplican correcciones.
- Dos sesiones no comparten estado.
- Los turnos concurrentes no superan el límite ni sobrescriben estado.
- El camarero no afirma haber reservado, preparado o cobrado.
- Inferencia real contra Foundry validada.
- Servidor local e invocación mediante `azd ai agent invoke --local` validados.

### Pendiente de revisión

- Revisar conjuntamente el comportamiento conversacional.
- Aprobar la fase antes de marcar sus casillas en el plan.

## Fase 2: memoria persistente consentida

### Implementado

- Contratos separados para:
  - estado de sesión;
  - memoria duradera;
  - historial de pedidos completados.
- Adaptador SQLite desacoplado mediante un protocolo de repositorio.
- Consentimiento con identidad, procedencia y fecha.
- Consulta, corrección y borrado individual de recuerdos.
- Revocación que elimina recuerdos y bloquea nuevas escrituras.
- Límite configurable aplicado por separado a preferencias y restricciones.
- Aislamiento entre identidades.
- Operaciones SQLite transaccionales y prueba de escrituras concurrentes.
- Agent Framework context provider para recuperar memoria en cada turno.
- Invalidación efectiva después de corrección, borrado o revocación.
- Migración del esquema local anterior de preferencias.
- Resumen automático de los productos del borrador como una preferencia de
  pedido de largo plazo cuando existe consentimiento.
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

Las preferencias y las alergias o restricciones se pueden almacenar con
consentimiento, pero se clasifican por separado:

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
DEV_FAKE_MEMORY_CONSENT="true"
```

Controles:

- no se acepta un `actor_id` enviado por el mensaje HTTP;
- `DEV_FAKE_ACTOR_ID` funciona también como nombre presentado y se inyecta
  desde el primer turno para ofrecer un trato cercano;
- la configuración se rechaza fuera de `development`;
- el mecanismo será sustituido por identidad autenticada en la fase 3.

### Evidencia

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

### Pendiente de revisión

- Revisar conjuntamente la experiencia de consentimiento y reconfirmación.
- Aprobar el uso de identidad falsa durante la fase local.
- Aprobar la fase antes de marcar sus casillas en el plan.

## Próximo trabajo previsto

La fase 3 incorporará:

- vista única de cliente con Streamlit;
- BFF con FastAPI;
- identidad derivada por el servidor;
- comandos HTTP;
- actualizaciones SSE;
- snapshot de estado;
- persistencia y recuperación de la visita activa;
- sustitución de la identidad falsa de desarrollo.

## Ejecución y validación

Preparar dependencias:

```bash
./scripts/setup.sh
```

Ejecutar pruebas locales:

```bash
./scripts/test.sh
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
