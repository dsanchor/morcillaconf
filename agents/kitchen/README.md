# Agente de cocina A2A

Proyecto independiente que publica la cocina mediante A2A JSON-RPC. El
camarero no importa ni ejecuta al chef: su tool `pedir_a_cocina` envía un
`KitchenOrder` a este servicio y valida el resultado final `KitchenPlan` con
estado `cooked`, o el `KitchenFailure` devuelto. No existe una aceptación
intermedia ni una operación de consulta de estado.

## Orquestación

1. El chef consulta carta y recetario mediante Foundry IQ.
2. La aplicación valida de forma determinista platos, partidas, cantidades,
   modificaciones y alergias.
3. El chef asigna únicamente las partidas necesarias a `Parrilla`, `Fritos` y
   `General` —esta última cubre pinchos fríos y barra—.
4. Los especialistas son agentes Foundry con instrucciones propias y revisan
   sus tareas en paralelo mediante `ConcurrentBuilder`.
5. El chef consolida las decisiones. Una negativa o una respuesta ausente
   rechaza esa línea; nunca puede revivir una línea rechazada por carta o
   seguridad alimentaria.
6. Cada plato aceptado recibe una duración de demo de 10 a 20 segundos. La
   llamada A2A permanece abierta hasta la duración máxima y sólo entonces
   devuelve los platos como cocinados y listos para servir.

No hay inventario ni cocción física; la espera representa la preparación de la
demo. El camarero no hace polling a cocina.

## A2A

Endpoints:

- `GET /health`
- `GET /.well-known/agent-card.json`
- `POST /` para A2A JSON-RPC

La Agent Card publica la skill `coordinate-kitchen-order`. La entrada y salida
viajan como JSON tipado dentro de una parte de texto A2A.

## Desarrollo local

```bash
cd agents/kitchen/src/kitchen_agent
cp .env.example .env
uv sync --all-groups
.venv/bin/python main.py
```

Variables principales:

- `FOUNDRY_PROJECT_ENDPOINT`
- `AZURE_AI_MODEL_DEPLOYMENT_NAME`
- `AZURE_SEARCH_ENDPOINT` y `KNOWLEDGE_BASE_NAME`
- `KITCHEN_TIMEOUT_SECONDS`
- `KITCHEN_A2A_PUBLIC_URL`

Pruebas:

```bash
.venv/bin/python -m pytest
```

La imagen se construye desde la raíz del repositorio:

```bash
docker build --file agents/kitchen/Dockerfile --target test .
docker build --file agents/kitchen/Dockerfile --target runtime .
```
