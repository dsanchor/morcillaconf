# Agente de caja A2A

Proyecto independiente que publica la caja del restaurante mediante A2A
JSON-RPC (acuerdo del 06/10). El camarero no importa ni ejecuta la caja: su
tool `pedir_la_cuenta` envía un `BillRequest` con los platos de cocina y las
bebidas de la barra ya servidos y relaya después el botón de pago del cliente a
la misma tarea A2A.

## Flujo

1. **Cuenta.** El primer mensaje es un `BillRequest`: las líneas servidas
   (pedido o ronda, línea, identificador de carta, nombre y cantidad) que el BFF
   calcula con los planes de cocina y las rondas de barra ya servidos. Desde
   Barra v1 (08/10) también se cobran las bebidas servidas.
2. **Consulta.** Un agente de Agent Framework sin sesión ni memoria busca esas
   entradas en la carta con su única herramienta, la base de conocimiento de
   Foundry IQ. El modelo solo consulta: no recibe cantidades ni calcula nada.
3. **Importes.** El código lee el precio de cada plato o bebida en los
   fragmentos de la carta recuperados («Precio: 8,50 € la ración», «Precio:
   2,50 € la caña de 20 cl»; las bebidas están en la sección «Barra:
   bebidas»). Solo vale la carta de la casa: fragmentos de un documento de la
   casa cuya entrada declara «(documento: carta)». La web, el recetario y la
   ficha de ingredientes nunca ponen precio. Cada línea se cobra una vez con su cantidad servida, con
   aritmética `Decimal` en euros. Un precio ausente, ilegible o distinto en dos
   fragmentos da un `CashierFailure` explícito, nunca un importe inventado.
4. **`input-required`.** La tarea A2A queda en `TASK_STATE_INPUT_REQUIRED` con
   el artefacto `Bill`: `bill_id`, versión, etapa, líneas con precio unitario y
   total de línea, total, `EUR`, opciones de pago (`tarjeta`, `efectivo`),
   fuentes y la nota «Solo lo ya servido: platos de cocina y bebidas de la
   barra».
5. **Pago.** El botón del cliente llega como `PaymentChoice` a la misma tarea
   (mismos `task_id` y `context_id`). Caja registra el pago simulado, siempre
   aprobado en esta versión, y completa la tarea con el artefacto `Receipt`:
   método, importe, referencia y hora.
6. **Idempotencia.** Cada cuenta se cobra una sola vez: un segundo pago, con la
   misma clave de idempotencia u otra, devuelve el mismo recibo. Una tarea
   completada no admite más mensajes; el camarero lee entonces el recibo
   guardado con `tasks/get`.
7. **Anulación.** Si el cliente pide más platos o le sirven más bebidas con la
   cuenta pendiente, el camarero cancela la tarea (`tasks/cancel`) y esa cuenta
   ya no se puede pagar.

### Revisión en caja (preparada, desactivada)

`CASHIER_REQUIRE_REVIEW=true` activa el gancho para la revisión humana de
SPECS («una persona en caja revisa el ticket»): la cuenta nace en la etapa
`awaiting_review`, sin opciones de pago, y la tarea espera otro
`input-required` hasta recibir un `ReviewDecision`. Aprobada, pasa a
`awaiting_payment` con tarjeta y efectivo; rechazada, se cierra sin cobro. Por
defecto está desactivada y todavía no tiene interfaz; el contrato ya incluye la
etapa, así que activarla no cambia contratos.

## A2A

Endpoints:

- `GET /health`
- `GET /.well-known/agent-card.json`
- `POST /` para A2A JSON-RPC

La Agent Card publica la skill `cobrar`. Las entradas (`BillRequest`,
`PaymentChoice`, `ReviewDecision`, distinguidas por `kind`) y los artefactos
(`Bill`, `Receipt`, `CashierFailure`) viajan como JSON tipado dentro de una
parte de texto A2A. Mientras trabaja, caja publica sus pasos («Caja: consulta
precios en la carta», «Caja: cobra con tarjeta») como mensajes `working`, que
el camarero muestra en «Bajo el capó».

Las tareas, las cuentas y los pagos viven en memoria, como en cocina: un
reinicio olvida las cuentas abiertas y el cliente vuelve a pedir la cuenta. Por
eso el contenedor se despliega con una sola réplica.

## Conexión con Foundry IQ

`cashier_agent/knowledge.py` es una copia del `knowledge.py` de cocina (que a
su vez simplifica el del camarero): cada agente es un proyecto y un contenedor
propio, así que la conexión se duplica a propósito en lugar de compartirse en
una biblioteca. Un cambio en uno debe revisarse en los otros.

## Desarrollo local

```bash
cd agents/cashier/src/cashier_agent
cp .env.example .env
uv sync --all-groups
.venv/bin/python main.py
```

Variables principales:

- `FOUNDRY_PROJECT_ENDPOINT` y `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- `AZURE_SEARCH_ENDPOINT` y `KNOWLEDGE_BASE_NAME`; sin ellas, caja responde
  que no puede consultar la carta
- `KNOWLEDGE_BASE_TIMEOUT_SECONDS`
- `CASHIER_TIMEOUT_SECONDS` (30 por defecto): límite de la consulta de precios
- `CASHIER_REQUIRE_REVIEW` (`false` por defecto)
- `CASHIER_A2A_PUBLIC_URL`, `CASHIER_HOST` y `CASHIER_PORT` (8090)

La identidad es la misma que la del camarero y la cocina: `Foundry User` en el
proyecto y `Search Index Data Reader` en el servicio de búsqueda.

Pruebas:

```bash
.venv/bin/python -m pytest
```

La imagen se construye desde la raíz del repositorio:

```bash
docker build --file agents/cashier/Dockerfile --target test .
docker build --file agents/cashier/Dockerfile --target runtime .
```
