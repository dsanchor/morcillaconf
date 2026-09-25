# Restaurante multiagente

Este repositorio contiene el diseño y el plan de implementación de una demo
para explicar, de forma sencilla, cómo funciona una arquitectura multiagente.

La demo utiliza la experiencia de un restaurante: un camarero coordina la
atención al cliente, un chef valida los pedidos y varios agentes especialistas
colaboran para consultar la carta, comprobar existencias, preparar la comida y
gestionar la cuenta.

El objetivo es mostrar conceptos como:

- colaboración y delegación entre agentes;
- memoria y recuperación de contexto;
- uso de herramientas y datos verificables;
- confirmaciones humanas antes de acciones importantes;
- comunicación con sistemas externos;
- observabilidad del flujo completo.

## Documentación

- [Especificación funcional](SPECS.md)
- [Plan de implementación](PLAN_IMPLEMENTACION.md)
- [Progreso de implementación](PROGRESO.md)
- [Convenciones de estructura y nombres](CONVENCIONES.md)

El proyecto está planteado como una demo incremental con Python, Microsoft Agent
Framework, Microsoft Foundry, FastAPI y Streamlit.

## Desarrollo

El camarero y su memoria local consentida viven en
[`agents/restaurant`](agents/restaurant). Para preparar el entorno y ejecutar
sus pruebas:

```bash
./scripts/setup.sh
./scripts/test.sh
```

Para crear una configuración local reutilizable:

```bash
./scripts/init-local-env.sh
source ./.local/restaurant.env.sh
./scripts/run-local.sh
```

Para listar o borrar memorias locales de una identidad:

```bash
./scripts/manage-memory.sh --actor-id Majo list
./scripts/manage-memory.sh --actor-id Majo delete <memory-id>
./scripts/manage-memory.sh --actor-id Majo clear --yes
```

Consulta el README del agente para ejecutar la CLI, el servidor local o el smoke
test opt-in contra Foundry.
