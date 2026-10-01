# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- Público de una charla técnica que ve la demo proyectada y quiere entender, en
  3-5 minutos, cómo colaboran varios agentes con responsabilidades claras.
- Asistentes que, después de la charla, prueban la aplicación en sus portátiles
  y móviles, cada uno con su propio nombre.
- Las personas que presentan y conducen el recorrido guiado.

## Product Purpose

Demostrar una arquitectura multiagente contándola como la visita a un
restaurante de Burgos. Un camarero orquestador atiende al cliente, recuerda sus
preferencias y coordina mesa, cocina, cuenta y pago con agentes y herramientas
especialistas. El éxito es que el público entienda quién decide qué, cómo viaja
el contexto y dónde interviene la persona, especialmente en la revisión de caja
antes del pago.

## Positioning

La interfaz no es un chatbot con decorado: representa un sistema agéntico.
Cada capacidad técnica (memoria, delegación, herramientas, confirmaciones
humanas) corresponde a algo visible en el restaurante, y el restaurante solo
cambia cuando el sistema confirma un hecho.

## Operating Context

- Se proyecta durante la charla y después se usa en portátiles y móviles.
- Una única vista de cliente (SPECS §3), hecha con Streamlit y conectada a un
  BFF con FastAPI (PLAN §2).
- La vista puede usar un camarero `scripted` para pruebas locales; el modo se
  identifica como simulado.

## Capabilities and Constraints

- La entrada pide el nombre del cliente; ese nombre es la identidad de la demo
  (identidad sintética de desarrollo; el acceso controlado se planifica en la
  fase 9).
- El camarero saluda con «Hombre [nombre], ¿qué tal majo?» o «maja», deducido
  del nombre. Cuando se conecte el camarero real, lo decidirá el modelo.
- Comandos de la conversación: `/new` y `/exit`. La memoria se consulta y
  administra conversando con el camarero, sin panel ni comandos propios del
  frontend.
- Plano cenital del restaurante: cliente en la entrada, camarero, mesas con las
  sillas vacías por defecto y una barra con taburetes para fases posteriores.
- El frontal solo habla con el contrato del BFF (`packages/contracts`); nunca
  invoca agentes, MCP ni Foundry, ni deduce transiciones de negocio del texto.
- Camarero y chef reutilizan una única base de conocimiento de Foundry IQ,
  accesible mediante MCP, para carta, recetas e ingredientes.
- La confirmación de mesa y pedido es explícita; el HITL diferenciado se sitúa
  en la revisión humana del ticket y el importe antes del pago.
- Sin decidir: nombre comercial del restaurante y superficie final del control
  de caja.

## Brand Commitments

- Estilo rústico burgalés y minimalista: pocos textos y ningún cartel que desvíe
  la atención.
- Voz del camarero cercana y castiza («Hombre…», «majo», «maja»).

## Evidence on Hand

- `SPECS.md`, `PLAN_IMPLEMENTACION.md`, `docs/PARALELIZACION_FRONTEND.md`.
- Contratos públicos en `packages/contracts` y fixtures en `tests/fixtures/phase3a`.
- No hay logotipo, fotografías ni nombre comercial del restaurante: no se
  inventan.

## Product Principles

1. Lo visible representa el sistema: cada elemento corresponde a una capacidad
   real o a un estado confirmado.
2. Solo lo confirmado cambia el restaurante; el texto del modelo nunca mueve
   mesas, pedidos ni pagos.
3. El recorrido se entiende sin carteles.
4. Lo simulado se dice, con discreción.

## Accessibility & Inclusion

- Legible proyectado a distancia y cómodo en móvil.
- Contraste suficiente y animaciones que respetan `prefers-reduced-motion`.
