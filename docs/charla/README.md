# «No es magia, es arquitectura» · MorcillaConf 2026

La charla de Jesús Rubio y David Sancho sobre este repositorio: sábado 17 de
octubre, a las 11:30, en la Universidad Isabel I (Burgos). Son 30 minutos: 5 de
demo en directo y el resto para abrir la caja y enseñar la arquitectura.

## Qué hay en esta carpeta

| Fichero | Para qué sirve |
|---|---|
| `no-es-magia.pptx` | La presentación editable. Es la versión de referencia: edita esta. |
| `no-es-magia.html` | La versión web, en un solo fichero. Se abre en Chrome o Edge y trae la demo embebida y el reloj de 5 minutos. Es una foto del 14 de octubre: no recoge lo que cambie en el PPTX. |
| `fuentes/` | Work Sans, en seis pesos, y Alegreya, con su licencia OFL. |

## Antes de abrir el PPTX

1. Instala las fuentes de `fuentes/`. En Mac, doble clic y «Instalar»; en
   Windows, botón derecho y «Instalar para todos los usuarios». Sin ellas,
   PowerPoint pone otra letra y los textos se descuadran.
2. Usa PowerPoint 2019 o posterior, o Microsoft 365. La transición
   «Transformación» (Morph) anima las puertas, los naipes y el telón; en
   versiones anteriores se ve como un fundido.

## Cómo está montada

- Son 46 diapositivas. Cada paso de la versión web es una diapositiva, y
  «Transformación» anima el cambio de una a otra.
- Para que esa animación funcione, lo que se mueve se llama igual en las dos
  diapositivas, con un nombre que empieza por `!!`. Los nombres se ven en
  Inicio › Organizar › Panel de selección. Si duplicas una diapositiva para
  añadir un paso, no cambies esos nombres.
- La 2 es la demo. La imagen y el botón «Demo en vivo» abren la web en el
  navegador, porque PowerPoint no puede incrustarla, y la barra «5 minutos de
  demo» se llena sola en 5 minutos.
- De la 3 a la 7 están ocultas: son el plan B, cinco capturas de la demo real.
  Se abren con el botón «Demo grabada» de la 2.
- La 29 (el telón) avanza sola, y de la 31 a la 34 pasan cada 3,8 segundos.
- La 36 («Todo se ve y se mide») lleva a la izquierda el vídeo del panel de
  negocio, en Grafana, y a la derecha la captura de las trazas, en Application
  Insights. El vídeo arranca solo, sin sonido, y se repite hasta que pasas de
  diapositiva. Si en su primera vuelta (unos 30 segundos) pulsas «siguiente» y
  no avanza, pulsa otra vez. Para cambiar la captura, botón derecho y «Cambiar
  imagen», que conserva su nombre y su posición.
- De la 43 a la 46 también están ocultas: son los QR en grande. Cada tarjeta
  del «¡Gracias!» lleva a la suya, y un clic devuelve.
- Todas llevan notas del ponente.
- Los textos y las formas se pueden editar. Los esquemas dibujados a mano, las
  réplicas de la app, los QR y las fotos son imágenes.

## Para que lo nuevo no desentone

- Toda la letra es Work Sans. Lo que nombra (títulos, piezas, rótulos) va en
  mayúsculas; lo que afirma (el secreto, el mecanismo, las reglas), en frase
  normal; los dos en Black. Alegreya solo aparece cuando sale la app o se cita
  al cliente.
- Colores: tinta `#0B0B0D` y un pastel por pieza: mesas `#85DADC`, carta
  `#D9C58B`, cocina `#F8C4FF`, barra `#F89841` y caja `#96C4FF`. El naranja
  `#EF6B3B` se reserva para lo que está pasando o lo que decide una persona.
- En un titular, la respuesta va en blanco sobre una caja negra, y la pregunta,
  en tinta sobre blanco.
- El contenido empieza a 1,69 cm del borde izquierdo y acaba a 1,69 cm del
  derecho.

## Si cambia la URL de la demo

Ahora la demo es el despliegue de David:
<https://morcilla-frontend.thankfulbush-366414a9.italynorth.azurecontainerapps.io/>.

La URL está en los dos hipervínculos de la diapositiva 2 y en el QR «Prueba la
demo» de las diapositivas 42 y 43. Si cambia, edita esos hipervínculos y
sustituye las dos imágenes del QR con «Cambiar imagen», que conserva su nombre
y su posición.

## Cómo editar sin pisaros

Git no puede fusionar dos versiones de un PPTX: si dos personas lo cambian a la
vez, una pierde su trabajo. Avisa antes de editar, baja la última versión de
`main` y sube tus cambios en un solo commit.
