# Convenciones de estructura y nombres

Este documento define dónde debe vivir cada tipo de código y cómo se nombran los
elementos del repositorio. La estructura se ampliará de forma incremental:
solo se crearán las carpetas que necesite cada fase.

## Estructura objetivo

```text
morcillaconf-multiagents/
├── .github/
│   └── workflows/
│       ├── frontend-image.yml
│       ├── bff-image.yml
│       ├── restaurant-agent-image.yml
│       ├── mcp-image.yml
│       └── supplier-image.yml
├── agents/
│   ├── restaurant/
│   │   ├── agent.yaml
│   │   ├── Dockerfile
│   │   ├── .foundry/
│   │   ├── src/restaurant_agent/
│   │   │   ├── main.py
│   │   │   ├── workflow.py
│   │   │   ├── waiter/
│   │   │   └── kitchen/
│   │   └── tests/
│   └── supplier/
│       ├── agent.yaml
│       ├── .foundry/
│       ├── src/supplier_agent/
│       └── tests/
├── apps/
│   ├── frontend/
│   │   ├── Dockerfile
│   │   ├── src/frontend/
│   │   └── tests/
│   └── bff/
│       ├── Dockerfile
│       ├── src/bff/
│       └── tests/
├── services/
│   └── mcp/
│       ├── Dockerfile
│       ├── src/restaurant_mcp/
│       └── tests/
├── packages/
│   ├── contracts/
│   ├── domain/
│   ├── persistence/
│   └── observability/
├── infra/
│   ├── modules/
│   └── environments/
├── scripts/
├── tests/
│   ├── integration/
│   ├── end_to_end/
│   ├── contract/
│   └── fixtures/
├── data/
│   ├── menu/
│   ├── seed/
│   └── evaluation/
└── docs/
    ├── architecture/
    ├── decisions/
    └── demo/
```

## Responsabilidad de cada carpeta

### `agents/`

Contiene componentes con comportamiento de agente: instrucciones, configuración
del modelo, herramientas disponibles, workflows y adaptadores de Agent Framework
y Foundry.

- `restaurant` agrupa el camarero, el chef líder y los especialistas del workflow
  desplegable del restaurante.
- `supplier` contiene el agente proveedor independiente comunicado mediante A2A.
- Cada unidad desplegable mantiene su configuración `.foundry/` en su raíz.

### `apps/`

Contiene aplicaciones orientadas al usuario o puntos de entrada HTTP:

- `frontend` contiene la interfaz Streamlit.
- `bff` contiene la API FastAPI, SSE, resolución de identidad de demo por
  sesión y coordinación con el workflow.

### Contenedores y workflows de imagen

Cada componente ejecutable mantiene un `Dockerfile` en la raíz de su carpeta:
los agentes que se ejecuten como contenedor, `apps/frontend`, `apps/bff` y los
servidores de `services/`. Los paquetes de biblioteca no reciben imagen ni
workflow propios; se reconstruyen a través de los componentes que los incluyen.

Los workflows de imágenes viven en `.github/workflows/` y se nombran
`<componente>-image.yml`. Cada workflow:

1. se activa en `push` solo con `paths` que incluyan la carpeta de su
   componente, las dependencias compartidas que copie su `Dockerfile` y el
   propio YAML;
2. ejecuta las pruebas y comprobaciones del componente antes de la construcción;
3. construye y publica una imagen etiquetada con el SHA del commit, sin usar
   etiquetas no trazables como única referencia, en GitHub Packages asociado a
   este repositorio.

No se agrupan componentes independientes en un único workflow ni se activa una
publicación por cambios que no puedan afectar a su imagen.

El frontend se comunica con el BFF mediante su contrato público y no importa
código interno de agentes o servicios.

### `services/`

Contiene servicios ejecutables que no son agentes. `mcp` expone herramientas
deterministas para mesas, inventario, cuentas, pagos y otras operaciones de
negocio. No contiene prompts ni decisiones propias de un agente.

### `packages/`

Contiene código Python reutilizable que no se despliega por separado:

- `contracts`: comandos, eventos y modelos Pydantic compartidos.
- `domain`: reglas y estados del restaurante.
- `persistence`: interfaces y adaptadores de almacenamiento.
- `observability`: telemetría, trazas y eventos compartidos.

No se crearán paquetes genéricos como `common` ni módulos como `utils.py`.

### `infra/`

Contiene la infraestructura declarativa, los módulos Bicep y la configuración de
los distintos entornos. La infraestructura que pueda expresarse de forma
declarativa no debe ocultarse en scripts.

### `scripts/`

Contiene automatizaciones para preparar el entorno, ejecutar la solución,
validarla y realizar pruebas de humo. Los scripts coordinan comandos, pero no
implementan lógica de negocio.

### `tests/`

Las pruebas unitarias viven junto a cada componente. Esta carpeta raíz queda
reservada para pruebas de integración, contratos y recorridos de extremo a
extremo que atraviesen varios componentes.

### `data/`

Contiene datos versionables de la demo, semillas y conjuntos de evaluación. No
se almacenan aquí bases de datos locales, resultados generados ni secretos.

### `docs/`

Contiene documentación adicional de arquitectura, decisiones técnicas y
preparación de la demo. Las decisiones relevantes pueden registrarse como ADR.

## Convenciones de nombres

| Elemento | Convención | Ejemplo |
|---|---|---|
| Carpetas de componentes | `kebab-case` | `cold-kitchen` |
| Paquetes y módulos Python | `snake_case` | `restaurant_contracts` |
| Clases y modelos Pydantic | `PascalCase` | `ConfirmOrderCommand` |
| Funciones y variables | `snake_case` | `confirm_order` |
| Constantes | `UPPER_SNAKE_CASE` | `MAX_RETRY_COUNT` |
| Archivos de prueba | `test_<comportamiento>.py` | `test_order_confirmation.py` |
| Scripts | `<verbo>-<objetivo>.sh` | `run-local.sh` |
| Variables de entorno | `UPPER_SNAKE_CASE` | `FOUNDRY_PROJECT_ENDPOINT` |
| Eventos de dominio | Pasado | `OrderConfirmed` |
| Comandos | Imperativo | `ConfirmOrder` |
| Identificadores externos | `kebab-case` estable | `kitchen-lead` |

Las carpetas conceptuales pueden usar `kebab-case`, pero los paquetes importables
de Python siempre usan `snake_case`.

## Estructura interna de un agente

Cuando un agente necesite todos estos elementos, seguirá esta estructura:

```text
<agent>/
├── agent.py
├── instructions.md
├── tools.py
├── state.py
└── tests/
```

- `agent.py` construye y configura el agente.
- `instructions.md` mantiene las instrucciones versionadas fuera del código.
- `tools.py` declara las herramientas expuestas al agente.
- `state.py` define su estado propio cuando sea necesario.
- `tests/` valida instrucciones, herramientas y comportamiento.

No se crearán archivos vacíos para completar esta plantilla: cada elemento
aparecerá cuando tenga una responsabilidad real.

## Reglas de dependencia

1. El frontend solo se comunica con el BFF.
2. El BFF invoca el workflow mediante su contrato público.
3. Los agentes consumen herramientas y no acceden directamente a bases de datos.
4. El MCP implementa operaciones deterministas y no contiene prompts.
5. Los contratos compartidos no importan aplicaciones, agentes o infraestructura.
6. La lógica de dominio no depende de FastAPI, Streamlit, Foundry o MCP.
7. El proveedor A2A se trata como un sistema independiente.
8. Cada componente desplegable tendrá su propio punto de entrada, README y
   pruebas cuando se implemente.
