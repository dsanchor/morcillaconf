# Agente del restaurante

Implementación inicial del camarero orquestador mediante Microsoft Agent
Framework y el deployment `gpt-5.6-luna` del proyecto Foundry existente.

## Capacidades implementadas

- Mantiene una sesión independiente por conversación.
- Extrae nombre presentado, comensales, preferencias y restricciones actuales.
- Asume un comensal salvo que una petición de mesa requiera preguntar el tamaño
  del grupo.
- Conserva un borrador estructurado y no confirmado del pedido.
- Aplica correcciones en turnos posteriores.
- Pregunta únicamente por el nombre o los comensales que falten.
- Limita la cantidad de turnos y valida la propiedad de cada conversación.
- Expone errores de contrato en lugar de aceptar respuestas incompletas.
- Persiste preferencias y restricciones en SQLite únicamente con consentimiento.
- Conserva procedencia y fecha de los recuerdos.
- Recupera recuerdos para la misma identidad después de reiniciar el proceso.
- Resume los productos del borrador como una preferencia de pedido duradera.
- Expone los recuerdos persistidos mediante `remembered_memories`, separados
  de las preferencias confirmadas en la visita actual.
- Interpreta «lo de siempre» y expresiones equivalentes para recuperar el
  pedido habitual: si solo existe uno lo propone como borrador; si hay varios,
  muestra las alternativas para que el cliente elija.
- Usa clasificación semántica, no una comparación literal de frases, y añade
  la intención resuelta al contexto antes de construir la respuesta.
- Permite consultar, corregir, borrar y revocar la memoria.
- Elimina los recuerdos al revocar y bloquea nuevas escrituras.
- Separa sesión, preferencias, restricciones e historial de pedidos completados.

Todavía no existen herramientas de mesas, carta, inventario, cocina, cuenta o
pago. El agente tiene instrucciones explícitas para no afirmar que realizó esas
operaciones.

Las alergias y restricciones se guardan en una categoría separada. Todos los
recuerdos son contexto no vinculante, llevan procedencia y fecha, y requieren
reconfirmación durante la visita. Los borradores no se incorporan al historial.
Si hay consentimiento, sus productos se condensan sin cantidades en una
preferencia como `Preferencia de pedido: tortilla de patatas, agua con gas`.

## Configuración local

Requisitos:

- Python 3.13;
- `uv`;
- Azure CLI autenticada;
- acceso al proyecto y al deployment configurados.

Copia `.env.example` como `.env` y establece:

```dotenv
FOUNDRY_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-5.6-luna"
WAITER_MAX_TURNS="20"
MEMORY_DATABASE_PATH="./data/memory.db"
MEMORY_MAX_ITEMS="20"
```

La configuración local debe coincidir con el entorno de `azd`, porque
`azd ai agent run` da prioridad a sus propias variables.

También se puede generar un fichero de variables exportables desde la raíz:

```bash
./scripts/init-local-env.sh \
  --project-endpoint "https://<account>.services.ai.azure.com/api/projects/<project>" \
  --actor-id "Majo"
```

Si se omiten los argumentos, el script los solicita interactivamente. Después,
en cada terminal nueva:

```bash
source ./.local/restaurant.env.sh
./scripts/run-local.sh
```

El fichero generado está ignorado por Git, tiene permisos `600` y no se
sobrescribe salvo que se indique `--force`. Este script configura el acceso
local, pero no crea recursos ni deployments en Azure.

## Administrar la memoria local

Después de cargar el entorno, lista las memorias para obtener sus IDs:

```bash
source ./.local/restaurant.env.sh
./scripts/manage-memory.sh --actor-id Majo list
```

Borra una o varias memorias concretas:

```bash
./scripts/manage-memory.sh --actor-id Majo delete \
  pref_id_1 pref_id_2
```

Borra todas las memorias de la identidad:

```bash
./scripts/manage-memory.sh --actor-id Majo clear --yes
```

`clear` requiere `--yes` y conserva el consentimiento. Para revocar además el
consentimiento debe utilizarse `/memory revoke` desde la CLI del camarero.

## Preparar el entorno

Desde la raíz del repositorio:

```bash
./scripts/setup.sh
```

## Pruebas unitarias

```bash
./scripts/test.sh
```

Las pruebas unitarias no llaman a Foundry y validan contratos, correcciones,
aislamiento entre sesiones, propiedad de la conversación y límite de turnos.

## CLI de desarrollo

```bash
./scripts/run-waiter-cli.sh \
  --actor-id cliente-local \
  --authenticated
```

Dentro de la CLI:

- `/new` abre una conversación independiente;
- `/memory consent` autoriza la memoria duradera;
- `/memory` o `/memory list` muestra consentimiento y recuerdos;
- `/memory correct <id> <texto>` corrige una preferencia;
- `/memory delete <id>` elimina una preferencia;
- `/memory revoke` elimina los recuerdos y revoca el consentimiento;
- `/exit` termina el proceso.

Si se omite `--authenticated`, la identidad se trata como invitada y no puede
crear un perfil duradero.

La salida estructurada clasifica cada recuerdo como `preference` o
`restriction`. Esta clasificación no convierte el recuerdo en una instrucción:
el camarero debe reconfirmarlo y el pedido seguirá requiriendo su HITL.

## Servidor local

```bash
DEV_FAKE_ACTOR_ID=Majo \
DEV_FAKE_MEMORY_CONSENT=true \
ENABLE_DEV_FAKE_IDENTITY=true \
./scripts/run-local.sh
```

El servidor utiliza el protocolo Responses y escucha en el puerto `8088`.
También puede iniciarse con `F5` desde VS Code para abrir Agent Inspector.

En esta fase, el protocolo Responses todavía no constituye una frontera de
identidad. Para probar memoria localmente, `run-local.sh` puede tomar una
identidad falsa exclusivamente de variables de entorno:

- `ENABLE_DEV_FAKE_IDENTITY=true` activa el mecanismo;
- `DEV_FAKE_ACTOR_ID` identifica el perfil persistente y, en esta simulación,
  es también el nombre con el que el camarero saluda al cliente;
- `DEV_FAKE_MEMORY_CONSENT=true` registra consentimiento de desarrollo al
  arrancar.

La configuración falla si se intenta activar esta identidad con
`APP_ENVIRONMENT` distinto de `development`. El cliente no puede seleccionar la
identidad mediante el mensaje HTTP. El BFF de la fase 3 sustituirá este mecanismo
por identidad derivada y autenticada.

En otra terminal se puede invocar:

```bash
cd agents/restaurant
AZURE_DEV_USER_AGENT=microsoft_foundry_skill \
  azd ai agent invoke restaurant --local \
  "Soy Majo. Prefiero agua con gas y soy alérgica a los frutos secos."
```

`Majo` es tanto la identidad persistente falsa como el nombre presentado que el
agente recibe del entorno. Por tanto, ante un simple `Hola`, el camarero saluda
a Majo y no vuelve a preguntarle el nombre.

## Smoke test real

El smoke test realiza inferencias reales y, por tanto, consume cuota del
deployment configurado:

```bash
./scripts/smoke-test.sh
```

Comprueba extracción inicial, corrección en un segundo turno, aislamiento de
otra sesión y ausencia de afirmaciones de reserva o confirmación no respaldadas.

## Límites del almacenamiento local

- SQLite se utiliza únicamente para desarrollo y pruebas locales.
- El archivo debe residir en un volumen persistente si se ejecuta en un
  contenedor; el sistema no cambia silenciosamente a memoria volátil.
- `MEMORY_MAX_ITEMS` limita por separado preferencias y restricciones.
- Se conservan varios resúmenes de pedidos anteriores dentro del límite de
  preferencias.
- Un pedido idéntico incrementa su contador y actualiza su fecha, en lugar de
  duplicarse.
- «Lo de siempre» utiliza directamente la memoria solo si existe una opción.
  Con varias, las muestra ordenadas por frecuencia y recencia sin elegir ni
  revelar al cliente esos metadatos internos. Las opciones solapadas se
  consolidan por producto para preguntar una sola vez por alternativas y
  complementos.
- La compactación es determinista y no usa un modelo generativo.
- El consentimiento incluye de forma explícita preferencias y restricciones.
- Las restricciones se etiquetan como tales y siempre requieren reconfirmación.
- Los recuerdos se tratan como contexto no confiable y nunca acreditan precio,
  carta o disponibilidad.
- La sesión activa sigue siendo memoria de proceso. Su recuperación se
  implementará junto con el BFF en la fase 3.

## Estructura

```text
agents/restaurant/
├── .foundry/
├── azure.yaml
└── src/restaurant_agent/
    ├── main.py
    ├── pyproject.toml
    ├── uv.lock
    ├── restaurant_agent/
    └── tests/
```

La definición de `azure.yaml` reutiliza el proyecto `morcillaconf-foundry`. Esta
fase no despliega el Hosted Agent; únicamente prepara y valida su ejecución
local contra el modelo.
