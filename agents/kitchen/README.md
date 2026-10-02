# Agente de cocina A2A

Proyecto independiente que publica la cocina mediante A2A JSON-RPC. El
camarero no importa ni ejecuta al chef: su tool `pedir_a_cocina` envía un
`KitchenOrder` a este servicio y valida el `KitchenPlan` o `KitchenFailure`
devuelto.

## Orquestación

1. El chef consulta carta y recetario mediante Foundry IQ.
2. La aplicación valida de forma determinista platos, partidas, cantidades,
   modificaciones y alergias.
3. El chef abre un group chat acotado únicamente con las partidas necesarias:
   `Parrilla`, `Fritos` y `General` —esta última cubre pinchos fríos y barra—.
4. Temporalmente los tres especialistas aceptan siempre sus tareas mediante un
   cliente determinista. Siguen siendo participantes `Agent` de Agent Framework,
   por lo que su razonamiento puede sustituirse sin cambiar los contratos.
5. El chef consolida las decisiones. Una negativa o una respuesta ausente
   rechaza esa línea; nunca puede revivir una línea rechazada por carta o
   seguridad alimentaria.

No hay inventario ni preparación real en este incremento.

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
