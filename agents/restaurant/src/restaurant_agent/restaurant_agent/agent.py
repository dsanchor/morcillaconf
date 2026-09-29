"""Microsoft Agent Framework configuration for the waiter."""

from pathlib import Path

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from restaurant_agent.config import Settings
from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.memory.context import DurableMemoryContextProvider
from restaurant_agent.memory.intent import MemoryIntentDecision
from restaurant_agent.memory.middleware import (
    HabitualOrderMiddleware,
)
from restaurant_agent.memory.options import HabitualOrderQuestion
from restaurant_agent.memory.store import DurableMemoryRepository
from restaurant_agent.seating import (
    SeatingToolContextMiddleware,
    VisitContextProvider,
)

INSTRUCTIONS_PATH = Path(__file__).with_name("instructions.md")


def load_instructions() -> str:
    """Load versioned waiter instructions."""

    return INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def create_seating_tools(settings: Settings) -> list[MCPStreamableHTTPTool] | None:
    """Direct MCP seating tools for the model: availability and hold only."""

    if settings.seating_mcp_url is None:
        return None
    return [
        MCPStreamableHTTPTool(
            name="seating",
            url=str(settings.seating_mcp_url),
            tool_name_prefix="seating",
            allowed_tools=(
                "get_seating_availability",
                "hold_seating",
            ),
            request_timeout=settings.seating_mcp_timeout_seconds,
            description="Disponibilidad y bloqueos temporales de asientos.",
        )
    ]


def create_waiter_agent(
    settings: Settings,
    *,
    memory_store: DurableMemoryRepository | None = None,
) -> Agent:
    """Build the waiter using the configured Microsoft Foundry deployment."""

    client = FoundryChatClient(
        project_endpoint=str(settings.foundry_project_endpoint),
        model=settings.azure_ai_model_deployment_name,
        credential=DefaultAzureCredential(),
    )
    intent_classifier = Agent(
        id="memory-intent-classifier",
        name="Clasificador de intención de memoria",
        description="Clasifica si el cliente quiere repetir su pedido habitual.",
        client=client,
        instructions=(
            "Devuelve reuse_latest_order cuando el mensaje exprese intención "
            "de repetir, reutilizar o pedir lo habitual recordado, aunque use "
            "lenguaje coloquial o indirecto. Devuelve none para cualquier "
            "otra intención. No converses ni tomes decisiones adicionales."
        ),
        default_options={
            "store": False,
            "response_format": MemoryIntentDecision,
        },
    )
    option_merger = Agent(
        id="habitual-order-option-merger",
        name="Consolidador de pedidos habituales",
        description=(
            "Fusiona pedidos solapados en preguntas breves por producto."
        ),
        client=client,
        instructions=(
            "Consolida opciones de pedido solapadas sin enumerar combinaciones "
            "completas. Agrupa alternativas equivalentes y pregunta aparte por "
            "complementos opcionales. No inventes productos ni reveles "
            "metadatos internos."
        ),
        default_options={
            "store": False,
            "response_format": HabitualOrderQuestion,
        },
    )
    context_providers = [VisitContextProvider()]
    if memory_store:
        context_providers.append(
            DurableMemoryContextProvider(
                memory_store,
                fallback_actor_id=(
                    settings.dev_fake_actor_id
                    if settings.enable_dev_fake_identity
                    else None
                ),
                persist_fallback_candidates=settings.enable_dev_fake_identity,
                intent_classifier=intent_classifier,
                option_merger=option_merger,
            )
        )
    tools = create_seating_tools(settings)
    default_options = {"store": False}
    if settings.enable_dev_fake_identity:
        default_options["response_format"] = WaiterModelResult
    return Agent(
        id="waiter",
        name="Camarero",
        description="Atiende al cliente y mantiene un borrador del pedido.",
        client=client,
        instructions=load_instructions(),
        tools=tools,
        context_providers=context_providers,
        middleware=[
            HabitualOrderMiddleware(intent_classifier),
            SeatingToolContextMiddleware(),
        ],
        default_options=default_options,
    )
