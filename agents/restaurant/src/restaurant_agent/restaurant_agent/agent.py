"""Microsoft Agent Framework configuration for the waiter."""

from pathlib import Path
from typing import Any

from agent_framework import Agent, MCPStreamableHTTPTool

from restaurant_contracts.kitchen import KitchenPort

from restaurant_agent.config import Settings
from restaurant_agent.contracts import WaiterModelResult
from restaurant_agent.kitchen import InProcessKitchen
from restaurant_agent.kitchen_tool import create_kitchen_tool
from restaurant_agent.knowledge import KnowledgeToolMiddleware, create_knowledge_tool
from restaurant_agent.memory.context import DurableMemoryContextProvider
from restaurant_agent.memory.intent import MemoryIntentDecision
from restaurant_agent.memory.middleware import (
    HabitualOrderMiddleware,
)
from restaurant_agent.memory.options import HabitualOrderQuestion
from restaurant_agent.memory.store import DurableMemoryRepository
from restaurant_agent.seating import (
    CONFIRM_NAMES,
    SOLO_CONFIRM_NAMES,
    SeatingApprovalChatMiddleware,
    SeatingToolContextMiddleware,
    VisitContextProvider,
)

INSTRUCTIONS_PATH = Path(__file__).with_name("instructions.md")


def load_instructions() -> str:
    """Load versioned waiter instructions."""

    return INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def create_seating_tools(settings: Settings) -> list[MCPStreamableHTTPTool] | None:
    """The waiter's own MCP connection to the seating service.

    The model sees availability, hold and both confirmation paths. Group
    confirmation requires the customer's approval; solo confirmation is
    immediate after an explicit table/bar choice. Cancel and the room map are
    used only by the waiter's own hooks through this same connection.
    """

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
                "confirm_seating",
                "confirm_solo_seating",
            ),
            approval_mode={
                "always_require_approval": list(CONFIRM_NAMES),
                "never_require_approval": [
                    "get_seating_availability",
                    "seating_get_seating_availability",
                    "hold_seating",
                    "seating_hold_seating",
                    *SOLO_CONFIRM_NAMES,
                ],
            },
            request_timeout=settings.seating_mcp_timeout_seconds,
            description="Disponibilidad, bloqueos temporales y confirmación de asientos.",
        )
    ]


def create_waiter_agent(
    settings: Settings,
    *,
    memory_store: DurableMemoryRepository | None = None,
    client: Any | None = None,
    kitchen: KitchenPort | None = None,
) -> Agent:
    """Build the waiter; by default with the configured Microsoft Foundry deployment.

    ``client`` replaces the model (the BFF's scripted waiter), keeping the
    same tools, middleware and context providers. ``kitchen`` is where
    ``pedir_a_cocina`` sends orders: by default the chef in this process.
    """

    if client is None:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import DefaultAzureCredential

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
    seating_tools = create_seating_tools(settings)
    knowledge_tool = create_knowledge_tool(settings)
    tools: list[Any] = [
        *(seating_tools or []),
        *([knowledge_tool] if knowledge_tool else []),
        # Always offered: without a knowledge base the kitchen answers that it
        # cannot consult the carta, instead of the waiter promising anything.
        create_kitchen_tool(kitchen or InProcessKitchen(settings)),
    ]
    context_providers = [VisitContextProvider(seating_tools[0] if seating_tools else None)]
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
    default_options: dict[str, Any] = {"store": False}
    if seating_tools:
        # One call per model response: a hold must run before its confirmation.
        default_options["allow_multiple_tool_calls"] = False
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
            KnowledgeToolMiddleware(),
            SeatingApprovalChatMiddleware(),
        ],
        default_options=default_options,
    )
