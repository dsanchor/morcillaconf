# Revisión de la fase 3A: resultados y decisiones

- **Fecha:** 28/09/2026
- **Revisión:** Jesús (@jrubiosainz)
- **Código revisado:** `packages/contracts` y `tests/fixtures/phase3a` de
  `d5ac228`. No han cambiado hasta `main` @ `a7442f7`.
- **Entorno:** GitHub Codespace. 3A no llama al modelo, así que Foundry no
  interviene.

## Resultado

**3A cumple el contrato mínimo del anexo y no hay nada que corregir.** Las
pruebas pasan y las 15 reglas probadas a mano se cumplen.

La revisión deja dos decisiones, sobre el nombre y los comensales. No cambian
los contratos, pero sí lo que deben hacer 3C, 3D y la fase 4. La fase 3 sigue
abierta: faltan 3C, 3D y la revisión conjunta.

## Contrato mínimo del anexo

Comprobado frente al
[§4 del anexo de paralelización](PARALELIZACION_FRONTEND.md#4-contrato-mínimo-para-iniciar-el-frontend).

| Punto | Resultado | Dónde se cumple |
|---|---|---|
| Sobre común de comandos: `event_id`, tipo, versión y fecha | ✓ | `CommandEnvelope`; la fecha exige zona horaria |
| Identidad validada, separada del comando | ✓ | `ActorContext` lo construye el servidor; un comando con `actor`, `actor_id` o `authenticated` se rechaza |
| Correlación generada por el servidor | ✓ | `correlation_id` va en resultados, eventos y errores, nunca en comandos |
| Resultados `pending`, `completed` y `failed` | ✓ | `COMMAND_RESULT_ADAPTER` |
| Snapshot de conversación, visita, cliente, borrador, memoria y proceso | ✓ | `RestaurantSnapshot`, con `pending_fields` coherente con los datos del cliente |
| Lista explícita de `allowed_actions` | ✓ | Sin duplicados; un invitado solo puede llegar y hablar |
| Eventos con cursor y estado confirmado | ✓ | `snapshot.updated`, `command.status_changed` y `conversation.text_delta`, este siempre provisional |
| Errores públicos distinguibles de fallos internos | ✓ | `PublicError` con código, mensaje, correlación y recuperación |
| Cursor SSE caducado | ✓ | `cursor_expired` obliga a pedir un snapshot nuevo |
| Fixtures de camino feliz, pendiente, error, reconexión y sin acciones | ✓ | `tests/fixtures/phase3a` |
| Seis comandos, sin consentimiento | ✓ | `memory.consent_granted` y `memory.consent_revoked` se rechazan |
| Sin Streamlit, FastAPI, Agent Framework ni infraestructura | ✓ | Solo depende de Pydantic; una prueba lo comprueba |

Quedan fuera de 3A, como ya recoge `PROGRESO.md`: idempotencia real,
persistencia, SSE y autenticación (3C), y el empaquetado del paquete para el
despliegue remoto (fase 5).

## Pruebas realizadas

| Bloque | Qué se probó | Resultado |
|---|---|---|
| Pruebas | `./scripts/test.sh` en el Codespace, sobre la rama de 3B (mismos contratos que `main`) | ✓ 133 passed |
| Reglas | 15 casos contra los fixtures, con el guion de abajo | ✓ 15 de 15 |
| Lectura | Contratos, README del paquete y fixtures frente al anexo | ✓ Sin hallazgos |

Las 15 reglas:

| # | Caso | Esperado | Resultado |
|---|---|---|---|
| 1-6 | Los seis comandos de ejemplo de los fixtures | Se aceptan | ✓ |
| 7 | Un comando que incluye `actor_id` | Se rechaza | ✓ |
| 8 | Un mensaje formado solo por espacios | Se rechaza | ✓ |
| 9 | El antiguo `memory.consent_granted` | Se rechaza | ✓ |
| 10 | Una fecha sin zona horaria | Se rechaza | ✓ |
| 11 | Un invitado con recuerdos duraderos | Se rechaza | ✓ |
| 12 | Un nombre ausente que no figura en `pending_fields` | Se rechaza | ✓ |
| 13 | Un fallo definitivo que invita a reintentar | Se rechaza | ✓ |
| 14 | Un cursor caducado sin pedir un snapshot nuevo | Se rechaza | ✓ |
| 15 | Texto parcial del camarero marcado como definitivo | Se rechaza | ✓ |

Son los mismos casos que ya cubre
`packages/contracts/tests/test_application_contracts.py`; el guion sirve para
verlos uno a uno.

<details>
<summary>Guion de las 15 reglas</summary>

Guardarlo como `/tmp/check_3a.py` y ejecutarlo desde la raíz del repositorio,
con las dependencias instaladas mediante `./scripts/setup.sh`:

```bash
uv run --frozen --project agents/restaurant/src/restaurant_agent python /tmp/check_3a.py
```

```python
import copy
import json
from pathlib import Path

from pydantic import ValidationError
from restaurant_contracts.application import (
    COMMAND_ADAPTER,
    COMMAND_RESULT_ADAPTER,
    STREAM_EVENT_ADAPTER,
    PublicError,
    RestaurantSnapshot,
)

FIXTURES = Path("tests/fixtures/phase3a")


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def check(rule, should_pass, validate, data):
    try:
        validate(data)
        passed = True
    except ValidationError:
        passed = False
    mark = "✓" if passed == should_pass else "✗ NO SE CUMPLE:"
    print(f"{mark} {rule} ({'aceptado' if passed else 'rechazado'})")


commands = load("commands.json")
message = next(c for c in commands if c["event_type"] == "conversation.message_sent")
snapshots = load("snapshots.json")
failed = next(r for r in load("results.json") if r["status"] == "failed")
delta = next(e for e in load("events.json") if e["event_type"] == "conversation.text_delta")
expired = load("cursor-expired.json")

print("Comandos")
for command in commands:
    check(f"{command['event_type']} de ejemplo", True, COMMAND_ADAPTER.validate_python, command)
check("El cliente no puede mandar su identidad", False, COMMAND_ADAPTER.validate_python,
      {**message, "actor_id": "Majo"})
check("Un mensaje vacío no se admite", False, COMMAND_ADAPTER.validate_python,
      {**message, "payload": {"message": "   "}})
check("El antiguo comando de consentimiento no existe", False, COMMAND_ADAPTER.validate_python,
      {**message, "event_type": "memory.consent_granted", "payload": {}})
check("La fecha debe llevar zona horaria", False, COMMAND_ADAPTER.validate_python,
      {**message, "occurred_at": "2026-09-28T08:00:00"})

print("Snapshot")
guest = copy.deepcopy(next(s for s in snapshots if s["memory"]["memories"]))
guest["identity"]["authenticated"] = False
check("Un invitado no tiene recuerdos duraderos", False, RestaurantSnapshot.model_validate, guest)
unnamed = copy.deepcopy(next(s for s in snapshots if s["customer"]["presented_name"] is None))
unnamed["pending_fields"] = []
check("Si falta el nombre, figura como pendiente", False, RestaurantSnapshot.model_validate, unnamed)

print("Resultados, errores y eventos")
check("Un fallo definitivo no invita a reintentar", False, COMMAND_RESULT_ADAPTER.validate_python,
      {**failed, "error": {**failed["error"], "recovery": "retry_same_command"}})
check("Un cursor caducado obliga a pedir un snapshot nuevo", False, PublicError.model_validate,
      {**expired, "recovery": "none"})
check("El texto parcial del camarero siempre es provisional", False,
      STREAM_EVENT_ADAPTER.validate_python, {**delta, "provisional": False})
```

</details>

## Decisiones

Tomadas por Jesús el 28/09/2026. D1 quedó adoptada ese mismo día en el
[sync](../PROGRESO.md#acuerdos-del-sync-del-28092026), que actualizó SPECS, el
plan y el anexo. D2 confirma la decisión del 27/09.

### D1. El nombre de la puerta es la identidad

Lo que se escribe en la puerta es el único nombre que se usa: identifica al
cliente y es el nombre con el que le trata el camarero.

- **Contratos:** no cambian. El nombre sigue fuera de los comandos: la vista
  lo entrega al abrir el cliente del BFF y el BFF construye `ActorContext`.
  3B ya funciona así con `FakeBffClient`: `actor_id` es el nombre escrito y
  `authenticated=True`.
- **3C:** el BFF necesita una entrada de identidad, fuera de los seis
  comandos, que convierta el nombre en `ActorContext` y lo conserve en la
  sesión. Sustituye a `DEV_FAKE_ACTOR_ID`.
- **3D:** el camarero debe usar ese nombre como `customer.presented_name` y no
  pedirlo. Hoy el servidor local solo se lo indica al modelo con una
  instrucción. Propuesta: que lo fije la aplicación, como ya hace con
  `pending_fields`, para que un «soy Majo» en el chat no lo cambie.
- **Saludo:** «Hombre, {nombre}, ¿qué tal, majo/maja?» lo genera ahora el
  camarero simulado de 3B. En 3C hay que decidir si lo genera el BFF o el
  camarero.
- **Mismo nombre, mismo cliente:** dos personas que escriban el mismo nombre
  comparten recuerdos y visita activa. El sync acordó que el BFF normaliza el
  nombre. En 3B, «Jesús» y «jesus» todavía son clientes distintos. Propuesta
  para 3C: comparar sin distinguir mayúsculas, tildes ni espacios repetidos, y
  saludar con el nombre tal como se escribió.
- **Sin invitados en la web:** la puerta siempre pide un nombre, así que todo
  cliente tiene perfil duradero. El caso de invitado de los contratos queda
  para la CLI.
- **Sin Entra ID:** la revisión detectó que D1 chocaba con las fases 3 y 9
  (identidades de demo solo en desarrollo, Entra ID en Azure). El sync lo
  resolvió: la demo no incorpora Entra ID ni otro proveedor de identidad.
- **SPECS:** entre las tareas del camarero sigue «extraer nombre presentado,
  número de comensales y petición del mensaje» (§5, camarero orquestador). Con
  D1, en la web el nombre ya no sale del mensaje; conviene ajustarlo con 3C.

### D2. Comensales: uno por defecto

El grupo empieza siendo de una persona, la que se ha identificado. Cuando dice
que viene acompañada, pasa a 2, 3, 4…

- Confirma la decisión del 27/09 (revisión de la fase 1). Coincide con SPECS
  («si no se indica el tamaño del grupo, se asume una persona»), con las
  instrucciones del camarero y con el valor por defecto del contrato
  (`party_size = 1`).
- **Contratos:** no cambian. `party_size` sigue admitiendo `None`, que marca
  el dato como pendiente; con D2 no debería aparecer en la web.
- **Plan:** su regla general decía «un numero de comensales desconocido
  permanece desconocido». Se alinea con SPECS en este cambio.
- **Fase 3:** propuesta: dar por resuelto el choque con el criterio «los
  campos conocidos no se confunden con valores por defecto», señalado en H2 de
  la fase 1, porque el uno inicial es la persona identificada.
- **Fase 4:** SPECS mantiene que pedir mesa sin decir cuántos son obliga a
  preguntar; hay que confirmar si sigue así. La variante «Hola, queremos
  comer» del guion (SPECS §4) sirve para mostrar preguntas selectivas; con D1
  y D2 ya no falta el nombre ni el número, así que habrá que revisarla.

## Cambios en la documentación

Con esta revisión se actualizan:

- `PLAN_IMPLEMENTACION.md`: la regla general de comensales, alineada con D2 y
  SPECS.
- `packages/contracts/README.md`: el valor por defecto de `party_size`.
- `PROGRESO.md`: el resumen de esta revisión en la fase 3A y el enlace en la
  tabla.

## Siguiente paso

- Revisión conjunta de 3A y D2. No se marca ninguna casilla.
- Construir 3C con la entrada de identidad por nombre acordada en el sync.
