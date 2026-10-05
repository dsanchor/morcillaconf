"""Semantic classification for durable-memory usage."""

from typing import Protocol

from agent_framework import AgentResponse
from pydantic import BaseModel, ConfigDict

from restaurant_agent.memory.contracts import MemoryIntent

INTENT_CLASSIFIER_INSTRUCTIONS = """\
Eres un clasificador. Decides si el mensaje del cliente pide repetir su pedido
habitual recordado como un todo. Responde solo con la clasificación; no
converses ni tomes otras decisiones.

Devuelve reuse_latest_order únicamente cuando el cliente se refiere a su pedido
de siempre, o al de una visita anterior, en conjunto y sin enumerar platos ni
bebidas. Por ejemplo:
- «lo de siempre», «ponme lo de siempre», «lo mismo de siempre», «como siempre»;
- «lo habitual», «mi pedido habitual», «lo de costumbre», «¿me pones lo mío?»;
- «lo mismo que la última vez», «lo mismo que la otra vez», «repíteme lo del
  otro día», «¿puedo repetir lo de antes?».

Devuelve none en cualquier otro caso y, en particular:
- Cuando el mensaje nombra platos o bebidas concretos, aunque formen parte del
  pedido habitual y aunque use «otra», «otro», «más» o «repite»: «ponme otra de
  croquetas», «otra ración de morcilla», «dos cañas más», «repíteme las
  croquetas», «quiero unas croquetas y un vino». Es un pedido nuevo de esos
  productos, no el pedido habitual, aunque coincida con alguno de sus platos.
- Cuando pide repetir algo de esta misma visita: «otra ronda», «otra vez lo
  mismo».
- Cuando «siempre» o «habitual» no se refieren al pedido del cliente: «¿siempre
  tenéis croquetas?», «siempre pido la morcilla sin cebolla».
- Saludos, preguntas sobre la carta, confirmaciones como «sí» o «confirmo» y
  cualquier otro mensaje.

Si dudas, devuelve none: es mejor que el camarero pregunte que llenar el pedido
con productos que el cliente no ha pedido."""


class MemoryIntentDecision(BaseModel):
    """Focused semantic classification for use of habitual-order memory."""

    model_config = ConfigDict(extra="forbid")

    memory_intent: MemoryIntent


class IntentClassifier(Protocol):
    """Agent subset required by memory-intent classification."""

    async def run(
        self,
        messages: str,
        *,
        options: dict[str, object],
    ) -> AgentResponse[MemoryIntentDecision]: ...


async def classify_memory_intent(
    classifier: IntentClassifier,
    current_message: str,
) -> MemoryIntent:
    """Classify whether a message semantically reuses habitual-order memory."""

    classification = await classifier.run(
        (
            "Clasifica el mensaje actual según tus instrucciones: "
            "reuse_latest_order solo si el cliente pide su pedido habitual, o "
            "el de una visita anterior, en conjunto y sin nombrar platos ni "
            "bebidas; none en cualquier otro caso, también cuando nombra "
            "productos concretos con «otra», «otro» o «más». No decidas "
            "disponibilidad ni confirmación.\n\n"
            f"Mensaje actual: {current_message}"
        ),
        options={"response_format": MemoryIntentDecision},
    )
    return classification.value.memory_intent
