# Revisión de la fase 1: resultados y hallazgos

- **Fecha:** 27/09/2026
- **Revisión:** Jesús (@jrubiosainz)
- **Código revisado:** `main` @ `d5ac228`
- **Entorno:** GitHub Codespace. Proyecto Foundry propio (`cocina-cojonudos`)
  con `gpt-5.6-luna` versión 2026-07-09 (GlobalStandard), no el proyecto
  `morcillaconf-foundry` de `azure.yaml`.

## Resultado

**La fase 1 no se puede cerrar todavía.** Falla un criterio de aceptación (el
smoke real contra Foundry) y un punto del checklist (errores visibles): la CLI
se cierra cuando el modelo devuelve una respuesta que no cumple el contrato.
El resto de lo probado funciona.

## Criterios de aceptación

| Criterio | Resultado | Evidencia |
|---|---|---|
| Dos turnos conservan datos y correcciones | ✓ | CLI: «Mejor somos tres» deja `party_size: 3` y conserva el pedido |
| Dos sesiones no mezclan mensajes | ✓ | CLI: tras `/new`, `party_size: 1` y borrador vacío |
| «Soy Majo y venimos dos» no repite preguntas | ✓ | CLI con «Hola, soy Jesús y venimos dos»; también el paso 1 del smoke |
| No afirma haber reservado, preparado o cobrado | ✓ | CLI: «Resérvame una mesa, marcha la tortilla y cóbrame» |
| El smoke acredita inferencia real en Foundry | ✗ | Falla en el paso 3 (`smoke.py:39`), ver H1 y H2 |

## Checklist

| Punto | Resultado |
|---|---|
| Proyecto, lockfile, configuración de ejemplo sin secretos y scripts | ✓ Sin secretos versionados; `.local/`, `.env` y la memoria están ignorados. Ver H4 |
| Proyecto Foundry y deployment del modelo | ✓ con `gpt-5.6-luna`. Ver H6 |
| Camarero con Agent Framework y cliente Foundry | ✓ `agent.py` |
| Contratos Pydantic sin parsear texto narrativo | ✓ `contracts.py`, salida estructurada |
| Sesión por conversación, identidad de la aplicación, IDs de correlación, límite de turnos y errores visibles | Parcial: errores no visibles (H1); límite de turnos solo en la CLI (H7) |
| Preguntar solo lo necesario; un nombre del chat no da acceso a otro cliente | Parcial: el nombre del chat no da acceso a otro perfil ✓; no pide el nombre (H1) y la regla de comensales es contradictoria (H2) |
| CLI y configuración de VS Code/Agent Inspector | ✓ CLI; ✓ servidor local por Responses (`main.py` + `curl`); `F5` no probado |
| Pruebas unitarias y smoke reproducibles, sin datos sensibles en registros | ✓ 127 pruebas; ✗ smoke; ver H3 y H5 |

## Hallazgos

**H1. La CLI y el smoke se cierran si el cliente no ha dicho su nombre.**
`WaiterModelResult` exige `customer_name` en `pending_fields` cuando
`presented_name` es `null`, pero `instructions.md` no pide preguntar el nombre
ni marcarlo como pendiente. El modelo lo deja vacío sin marcarlo y la
validación falla dentro del SDK. El `ChatClientException` resultante no se
convierte en `InvalidAgentResponseError`, así que la CLI no muestra un error:
el proceso termina. Observado 3 de 3 veces en la CLI («hola», «hola»,
«¿recuerdas cuántos somos?» tras `/new`) y 6 de 6 al reproducir el paso 3 del
smoke. En el servidor local no ocurre porque el nombre llega desde
`DEV_FAKE_ACTOR_ID`; la CLI no aplica ese atajo.

**H2. Las reglas sobre el número de comensales se contradicen.**
SPECS y las instrucciones dicen «si no lo indica, asumir una persona». El plan
(sección 3) dice «un número de comensales desconocido permanece
desconocido». El smoke espera `party_size: null` y que se pregunten nombre y
comensales ante «Queremos cenar» (como la variante «Hola, queremos comer» de
SPECS). El modelo pone `party_size: 1` y pregunta qué quieren pedir (6 de 6,
incluso partiendo de `party_size: null`). Relacionado: presenta el valor por
defecto como un dato conocido («Sí, sois 1 persona»), que choca con el
criterio de fase 3 «los campos conocidos no se confunden con valores por
defecto».

**H3. Los errores muestran datos personales.** El error de validación imprime
parte de la respuesta del modelo, incluida una alergia recordada. El checklist
pide registros sin datos sensibles.

**H4. Los scripts no son ejecutables.** Se versionaron con modo `100644`:
`./scripts/<script>.sh` da `Permission denied` y hay que usar
`bash scripts/<script>.sh`.

**H5. Una prueba depende del entorno de la terminal.**
`test_fake_identity_requires_actor_id` falla si la terminal ha cargado
`.local/restaurant.env.sh`, porque lee `DEV_FAKE_ACTOR_ID` del entorno.

**H6. `gpt-6-luna` no es compatible.** La versión 2026-09-22 rechaza
`text.format` de tipo `json_schema` a través del endpoint del proyecto (sí lo
acepta a través del endpoint de la cuenta). Con `gpt-5.6-luna` funciona.
Conviene documentarlo.

**H7. El límite de turnos solo se aplica en la CLI y el smoke.** Lo aplica
`ConversationManager`, que `main.py` no usa. Propuesta: resolverlo con el BFF
en la fase 3.

**H8 (menor). `run-local.sh` necesita azd y un entorno azd no documentado.**
En un Codespace no está disponible. Alternativa usada: `python main.py`, que
es lo mismo que arranca `F5`.

**Nota.** Diferencias con `CONVENCIONES.md`: las pruebas están en
`src/restaurant_agent/tests/` y no en `agents/restaurant/tests/`, y hay
`azure.yaml` en lugar de `agent.yaml`. ¿Se aceptan o se actualiza el documento?

## Decisiones pendientes

1. **Nombre.** ¿Debe el camarero pedirlo cuando falta? ¿Debe calcular
   `pending_fields` la aplicación en lugar del modelo? Tener en cuenta la
   entrada con nombre de la web (fase 3).
2. **Comensales.** ¿«Asumir una persona» o «desconocido hasta que lo diga»?
   Alinear SPECS, plan, instrucciones, el valor por defecto del contrato y el
   smoke.

## Propuesta

Un PR «Fase 1: correcciones de la revisión» con H1, H3, H4 y H5 y lo que se
decida en los dos puntos anteriores; H6 documentado. Validación: pruebas
unitarias, smoke superado tres veces seguidas y bloques A y B de la CLI.
Después, marcar las casillas de la fase 1, actualizar `PROGRESO.md` y repetir
la revisión con la fase 2 y la 3A.

## Pruebas realizadas

| Bloque | Qué se probó | Resultado |
|---|---|---|
| Pruebas | `bash scripts/test.sh` en una terminal sin configuración cargada | ✓ 127 passed |
| A | Conversación completa en la CLI: nombre y comensales, pedido, alergia, corrección, carta, acciones no disponibles, `/new` | ✓ |
| B | `--actor-id Ana` y «Hola, soy Jesús. Ponme lo de siempre»: no carga los datos de Jesús | ✓ |
| C | Límite de turnos | No probado (no prioritario ahora) |
| D | `bash scripts/smoke-test.sh` | ✗ Falla en el paso 3 |
| E | Servidor local (`main.py`) y `curl` a `/responses` con «Hola» | ✓ Saluda con el nombre de `DEV_FAKE_ACTOR_ID` |
