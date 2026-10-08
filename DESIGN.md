---
name: "Mesón Burgalés Agéntico"
description: "Una interfaz de cliente donde entrar al mesón equivale a identificarse y todo cambio visible confirma un hecho del sistema."
colors:
  noche: "#0a0f1f"
  nogal: "#140e0b"
  roble: "#231811"
  roble-medio: "#3a2719"
  veta: "#8a6644"
  piedra: "#e7d7b5"
  piedra-tenue: "#b9a685"
  tinta: "#24180f"
  luz: "#f2b04a"
  vino: "#8c1c2b"
  forja: "#1d1816"
  carbon: "#0e0907"
  lumbre: "#ffd27a"
  cuero: "#6b4a2f"
  azulejo: "#1d4e89"
  azulejo-hondo: "#153a68"
  pino: "#1f5b3f"
  pino-hondo: "#164530"
  cerveza: "#8a520b"
  cerveza-honda: "#6b3f08"
typography:
  display:
    fontFamily: "Alegreya, Georgia, Times New Roman, serif"
    fontSize: "1.6rem"
    fontWeight: 500
    lineHeight: 1.1
  headline:
    fontFamily: "Alegreya, Georgia, Times New Roman, serif"
    fontSize: "1.24rem"
    fontWeight: 400
    lineHeight: 1.45
  title:
    fontFamily: "Alegreya, Georgia, Times New Roman, serif"
    fontSize: "1.2rem"
    fontWeight: 500
    lineHeight: 1.2
  body:
    fontFamily: "Alegreya Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Alegreya Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "1.05rem"
    fontWeight: 700
    lineHeight: 1
  mono:
    fontFamily: "ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.35
rounded:
  sm: "4px"
  frame: "6px"
  bubble: "12px"
  round: "50%"
spacing:
  xxs: "2px"
  xs: "6px"
  sm: "10px"
  md: "12px"
  lg: "14px"
  xl: "18px"
  xxl: "26px"
components:
  button-primary:
    backgroundColor: "{colors.luz}"
    textColor: "{colors.tinta}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "0 1.5rem"
  button-primary-hover:
    backgroundColor: "#ffc766"
    textColor: "{colors.tinta}"
  input-doorstep:
    backgroundColor: "rgba(14, 9, 7, .9)"
    textColor: "{colors.piedra}"
    typography: "{typography.title}"
    rounded: "{rounded.sm}"
    padding: ".78rem 1rem"
  command-row:
    backgroundColor: "transparent"
    textColor: "{colors.piedra}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "45px"
  command-row-hover:
    backgroundColor: "rgba(242, 176, 74, .08)"
    textColor: "{colors.luz}"
  message-waiter:
    backgroundColor: "{colors.piedra}"
    textColor: "{colors.tinta}"
    typography: "{typography.headline}"
    rounded: "3px 12px 12px 12px"
    padding: ".7rem 1rem"
  message-customer:
    backgroundColor: "{colors.vino}"
    textColor: "#f6ead3"
    rounded: "12px 3px 12px 12px"
    padding: ".7rem 1rem"
  message-chef:
    backgroundColor: "{colors.azulejo}"
    textColor: "#ffffff"
    rounded: "3px 12px 12px 12px"
    padding: ".7rem 1rem"
  message-cashier:
    backgroundColor: "{colors.pino}"
    textColor: "#ffffff"
    rounded: "3px 12px 12px 12px"
    padding: ".7rem 1rem"
  message-bar:
    backgroundColor: "{colors.cerveza}"
    textColor: "#ffffff"
    rounded: "3px 12px 12px 12px"
    padding: ".7rem 1rem"
  card-memory:
    backgroundColor: "rgba(231, 215, 181, .05)"
    textColor: "{colors.piedra}"
    rounded: "{rounded.sm}"
    padding: ".8rem 1.1rem"
---

# Design System: Mesón Burgalés Agéntico

## Overview

**Creative North Star: "La Puerta Confirmada"**

El sistema visual convierte la puerta nocturna de un mesón burgalés en la interfaz. La primera pantalla no anuncia una app: muestra piedra de Hontoria, roble claveteado, forja y un farol; el formulario vive sobre el umbral, de modo que identificarse es literalmente llamar y entrar.

Dentro, la sala sigue siendo una escena de restaurante, no un chatbot con cabecera. Los comandos quedan en un lateral austero, la conversación se aloja en una ventana de roble con esquinas de forja, y el plano cenital muestra solo estados confirmados: cliente, camarero, mesas vacías y barra.

**Key Characteristics:**
- Fachada a sangre antes de cualquier UI interior.
- Ámbar como único color de estado y acción.
- Piedra clara para habla del camarero; vino Ribera para habla del cliente; azulejo para el plan del chef; cerveza tostada para la ronda de la barra; pino para la cuenta de caja.
- Textos mínimos; el restaurante explica el sistema sin carteles.
- Movimiento escénico: llamar, abrir, cruzar, saludar y acercarse.

## Colors

La paleta es nocturna y mineral: casi todo nace de nogal, roble, piedra y forja; el ámbar aparece solo como confirmación o llamada de atención.

### Primary
- **Luz de farol**: acción principal, estado activo, foco visual, puntos de escritura y halo de confirmación.
- **Vino Ribera**: voz del cliente y presencia del cliente en el plano.
- **Azulejo de cocina**: voz del chef; su plan va en blanco sobre azulejo y su gorro descansa en azulejo hondo.
- **Pino de caja**: voz de la caja, el verde de los pinares burgaleses; la cuenta y el recibo van en blanco sobre pino y la caja registradora descansa en pino hondo. El cajero del plano viste del mismo verde.
- **Cerveza de barra**: voz de la barra, el ámbar tostado de una caña a contraluz; la ronda va en blanco sobre cerveza y la caña del icono descansa en cerveza honda. Es mucho más oscuro que la luz de farol y nunca señala acción ni estado: el ámbar de farol sigue siendo el único color de acción.

### Neutral
- **Noche castellana**: fondo exterior y cielo de entrada.
- **Nogal de sala**: fondo general interior.
- **Carbón de comandos**: superficie del lateral, botón de menú móvil y campos interiores.
- **Roble oscuro**: puerta, editor, bordes y superficies de conversación.
- **Veta de madera**: contornos, separadores, bordes de tarjetas y detalles de marco.
- **Piedra de Hontoria**: texto principal, burbuja del camarero y muros del plano.
- **Piedra tenue**: placeholders, argumentos y etiquetas discretas.
- **Tinta tostada**: texto sobre ámbar y sobre burbuja clara.
- **Forja**: clavos, esquinas, herrajes y estructura de ventana.
- **Cuero de taburete**: iconos, mesas y madera secundaria.

### Named Rules
**The Farol Único Rule.** El ámbar no compite con otros acentos: señaliza entrar, hover, foco, error de nombre, escritura y confirmación.

**The Material Native Rule.** No se prohíben texturas, clavos, dovelas ni vetas: son el lenguaje nativo de este mundo y deben seguir subordinados a la escena.

## Typography

**Display Font:** Alegreya (with Georgia and Times New Roman fallback)
**Body Font:** Alegreya Sans (with Segoe UI and system-ui fallback)
**Label/Mono Font:** ui-monospace / SF Mono / Menlo for slash commands only

**Character:** Alegreya da voz de mesón y lectura literaria a nombres, avisos, plano y camarero. Alegreya Sans sostiene la operación diaria: comandos, botones, entradas y texto del cliente.

### Hierarchy
- **Display** (500, 1.6rem, 1.1): nombre del visitante en el lateral; debe sentirse personal y sobrio.
- **Headline** (400, 1.24rem, 1.45): voz del camarero en burbujas claras.
- **Title** (500, 1.2rem, 1.2): campo del umbral, donde el nombre se vuelve identidad.
- **Body** (400, 18px, 1.5): base de sala, redactor, comandos y conversación; baja a 17px en móvil.
- **Label** (700, 1.05rem, 1): llamadas cortas como Entrar y enviar.
- **Mono** (500, 1rem, 1.35): comandos literales con slash, siempre en una sola línea; no se usa como estilo decorativo general.

### Named Rules
**The Slash Literal Rule.** El monospace se reserva a comandos ejecutables (`/memory`, `/new`, `/exit`) porque son código operativo, no ornamento.

## Layout

La fachada ocupa el viewport completo y no desplaza el documento: se fija sobre la app hasta que la puerta abre. El formulario del nombre queda centrado en el umbral con dos columnas, anchura contenida y sombra proyectada.

El interior usa una grilla de dos zonas: lateral de comandos fijo de 336px y sala flexible. El ancho sale del comando más largo, `/memory correct <id> <texto>` (28 caracteres): el monospace mide hasta 0,602em por carácter, así que a 16px ocupa unos 270px, más 2 × 12px de fila y 2 × 18px de lateral. La sala apila conversación, etiqueta simulada y plano con respiración estrecha. En móvil, el lateral se convierte en cajón de 336px como máximo o el ancho de la pantalla menos 40px; si el cajón es más estrecho, el tamaño de los comandos baja en proporción (28 × 0,61em) para que sigan en una línea. La sala gana margen superior para el botón `/comandos` y reduce padding y altura del plano.

El ritmo espacial favorece saltos de 10-14px para controles, 18px para módulos de sala y 26px para respiración escénica. Las líneas de texto conversacional se contienen alrededor de 62ch o 88% para no romper la sensación de diálogo.

## Elevation & Depth

La profundidad combina penumbra, capas tonales y sombras puntuales. La escena exterior usa gradientes, viñeta y luz radial; el interior reserva sombra real para el umbral, la ventana con marco y el pequeño apoyo de las burbujas.

### Shadow Vocabulary
- **Umbral proyectado** (`drop-shadow(0 12px 18px rgba(0, 0, 0, .75))`): fija el formulario sobre la piedra sin convertirlo en tarjeta.
- **Ventana pesada** (`inset 0 0 0 1px var(--veta), 0 16px 30px -14px rgba(0, 0, 0, .8)`): da masa al marco de roble y forja.
- **Burbuja apoyada** (`0 2px 0 rgba(0, 0, 0, .35)`): separa mensajes sin flotar.

### Named Rules
**The No Card UI Rule.** La profundidad debe parecer puerta, marco, piedra o mesa; no introduzcas tarjetas genéricas con sombras SaaS.

## Shapes

La forma base es austera: controles y tarjetas usan esquinas pequeñas de 4px; la ventana sube a 6px para permitir el recorte de apertura; mesas, iconos, puntos y personas son circulares por su lectura cenital. Las burbujas tienen una esquina seca de 3px y tres esquinas de 12px para señalar dirección sin parecer mensajería genérica.

La geometría distintiva viene de los materiales: arco de dovelas, portón partido, clavos, barras de forja, baldosas de barro y puerta del plano. Esas siluetas son sistema cuando explican estado o lugar.

## Components

### Buttons
- **Shape:** esquina pequeña y funcional (4px).
- **Primary:** ámbar de farol sobre tinta tostada, peso 700 y gesto compacto; en el umbral usa padding horizontal amplio.
- **Hover / Focus:** el hover aclara el ámbar; el foco visible usa aro ámbar claro de 3px con offset.
- **Mobile menu:** superficie carbón, borde de roble medio y texto `/comandos` en monospace.

### Cards / Containers
- **Conversation Frame:** marco grueso de roble y forja con clavos en las esquinas; contiene conversación y redactor sin cabecera.
- **Memory Card:** tarjeta discreta con borde discontinuo de veta, fondo de piedra al 5% y códigos en ámbar.
- **Command Sidebar:** carbón continuo, lista sin viñetas, filas de 45px alineadas a la izquierda y separadas 2px, hover en ámbar tenue; los argumentos son piedra tenue e itálica y ningún comando se parte en dos líneas.

### Inputs / Fields
- **Doorstep Field:** fondo carbón translúcido, borde de veta, tipo Alegreya y placeholder de piedra tenue.
- **Composer Field:** fondo roble oscuro, borde de roble medio y tipografía de cuerpo.
- **Focus / Invalid:** foco global ámbar claro; el nombre inválido cambia el borde al ámbar y hace temblar la puerta.

### Navigation
- **Style:** navegación por comandos literales, no por pestañas. En escritorio permanece a la izquierda; en móvil entra desde el borde como cajón.
- **State:** hover apenas ilumina el fondo y colorea el código en ámbar.

### Conversation Bubbles
- **Waiter:** piedra clara, tinta tostada, Alegreya serif y radio direccional hacia el icono del camarero.
- **Customer:** vino Ribera, crema cálida y radio direccional hacia el lado derecho.
- **Chef:** azulejo con texto blanco, Alegreya Sans y gorro de cocinero en el icono; va entre el pedido del cliente y la respuesta del camarero. Muestra lo aceptado, lo rechazado con su motivo, los avisos, las partidas y las fuentes, o por qué cocina no ha podido revisar el pedido.
- **Bar:** cerveza tostada con texto blanco, Alegreya Sans, el título «Barra» en Alegreya y una caña con su espuma en el icono, dibujada como el gorro del chef; va después del plan del chef, si lo hay, y antes de la respuesta del camarero. Se lee como el plan: lo servido con sus cantidades, lo no servido con su motivo, los avisos y las fuentes, o por qué la barra no ha podido servir.
- **Cashier:** pino con texto blanco, Alegreya Sans y una caja registradora en el icono, dibujada como el gorro del chef; va entre la petición del cliente y la respuesta del camarero. Se lee como un ticket: cada línea con su importe a la derecha en dígitos tabulares, una raya discontinua sobre el total en Alegreya, la nota de que solo se cobra lo ya servido y las fuentes. La cuenta pendiente lo dice en lumbre y se paga con los botones de su tarjeta; las ya pagadas lo dicen y las anuladas se atenúan. El recibo usa la misma burbuja.
- **Payment card:** como la tarjeta de mesa, borde discontinuo y fondo de pino tenue bajo la conversación, con el total y dos botones ámbar iguales, «Tarjeta» y «Efectivo»; solo existe mientras la cuenta espera el pago.
- **Typing:** tres puntos ámbar circulares con pulso vertical; se elimina con reducción de movimiento.

### Plan Scene
- **Style:** plano cenital sobre baldosa de barro, muros de piedra, madera para mesas y barra, vino para cliente, negro para camarero y pino para el cajero, que espera junto a la puerta de salida tras un pequeño mostrador con la caja registradora y su rótulo «caja».
- **Till:** la caja se enciende con el halo ámbar de farol y su pantalla en ámbar solo mientras hay una cuenta pendiente de pago; sin cuenta queda en reposo.
- **Behavior:** cliente y camarero solo se mueven cuando la entrada se confirma; el estado reducido fija el resultado sin animación.

## Do's and Don'ts

### Do:
- **Do** tratar la puerta, el plano y la conversación como la interfaz principal, no como fondo decorativo.
- **Do** usar ámbar solo para acción, estado, foco o confirmación.
- **Do** mantener pocos textos visibles y hacer que las formas del mesón expliquen el sistema.
- **Do** respetar `prefers-reduced-motion` en toda apertura, llegada y escritura.
- **Do** etiquetar lo simulado con discreción, como `Camarero simulado`.

### Don't:
- **Don't** añadir una cabecera de chatbot, panel de mando genérico o burbujas sin materialidad del mesón.
- **Don't** introducir acentos nuevos para estados que ya resuelve el farol.
- **Don't** usar monospace fuera de comandos literales o identificadores de memoria.
- **Don't** mover mesa, camarero, cliente, pedido o memoria por texto no confirmado.
- **Don't** canonizar ornamentos de una sola aparición como tokens globales.
