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
- [Contratos públicos de fase 3A](packages/contracts/README.md)

El proyecto está planteado como una demo incremental con Python, Microsoft Agent
Framework, Microsoft Foundry, FastAPI y Streamlit.

## Desarrollo

El camarero y su memoria local automática viven en
[`agents/restaurant`](agents/restaurant). Para preparar el entorno y ejecutar
sus pruebas:

```bash
./scripts/setup.sh
./scripts/test.sh
```

La fase 3A añade contratos tipados, fixtures y pruebas compartidas en
[`packages/contracts`](packages/contracts). El mismo comando valida el camarero,
los contratos y su compatibilidad. Todavía no hay frontend ni BFF ejecutables.

Para crear una configuración local reutilizable:

```bash
./scripts/init-local-env.sh
source ./.local/restaurant.env.sh
./scripts/run-local.sh
```

La memoria se lee y guarda automáticamente para la identidad autenticada
resuelta por el servidor, incluida la identidad falsa de desarrollo. Los
invitados no tienen perfil duradero. Los recuerdos conservan procedencia y
límites, son no vinculantes y las restricciones se reconfirman en cada visita.
No sustituyen un pedido confirmado ni su historial.

Si ya tienes un `.env` local, elimina `DEV_FAKE_MEMORY_CONSENT` y regenera el
entorno con `./scripts/init-local-env.sh --force`. Vuelve a cargar el fichero
generado; la antigua variable exportada ya no se utiliza y puede retirarse con
`unset DEV_FAKE_MEMORY_CONSENT`.

Para listar o borrar memorias locales de una identidad:

```bash
./scripts/manage-memory.sh --actor-id Majo list
./scripts/manage-memory.sh --actor-id Majo delete <memory-id>
./scripts/manage-memory.sh --actor-id Majo clear --yes
```

El borrado total (también `/memory clear` en la CLI) olvida los recuerdos
existentes; no desactiva el guardado automático de interacciones futuras.

Consulta el README del agente para ejecutar la CLI, el servidor local o el smoke
test opt-in contra Foundry.
