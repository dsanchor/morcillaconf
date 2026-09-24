"""Semantic classification for durable-memory usage."""

from typing import Protocol

from agent_framework import AgentResponse
from pydantic import BaseModel, ConfigDict

from restaurant_agent.memory.contracts import MemoryIntent


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
            "Clasifica únicamente si el mensaje actual expresa la intención "
            "semántica de repetir o reutilizar el pedido habitual recordado "
            "del cliente. Expresiones directas, indirectas, coloquiales o "
            "equivalentes cuentan. No decidas disponibilidad ni "
            "confirmación.\n\n"
            f"Mensaje actual: {current_message}"
        ),
        options={"response_format": MemoryIntentDecision},
    )
    return classification.value.memory_intent
