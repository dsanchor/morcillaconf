"""A2A JSON-RPC server for the external kitchen agent."""

from __future__ import annotations

import json
import logging

from a2a.helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Part,
    TaskState,
)
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from restaurant_contracts.activity import ActivityStep
from restaurant_contracts.kitchen import KitchenOrder

from kitchen_agent import progress
from kitchen_agent.config import Settings
from kitchen_agent.service import KitchenService

logger = logging.getLogger(__name__)


def request_text(context: RequestContext) -> str:
    if context.message is None:
        raise ValueError("A2A message is required")
    texts = [
        part.text
        for part in context.message.parts
        if part.WhichOneof("content") == "text"
    ]
    if len(texts) != 1 or not texts[0].strip():
        raise ValueError("Kitchen expects one JSON text part")
    return texts[0]


class KitchenAgentExecutor(AgentExecutor):
    def __init__(self, service: KitchenService) -> None:
        self._service = service

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if context.context_id is None:
            raise ValueError("A2A context id is required")
        updater = TaskUpdater(
            event_queue,
            context.task_id or "",
            context.context_id,
        )
        await updater.cancel()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if context.message is None or context.context_id is None:
            raise ValueError("A2A message and context id are required")
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, context.context_id)
        await updater.submit()
        await updater.start_work()

        async def publish(step: ActivityStep) -> None:
            await updater.update_status(
                state=TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(
                    [Part(text=json.dumps({"activity": step.model_dump(mode="json")}))]
                ),
            )

        progress.install(publish)
        try:
            request = KitchenOrder.model_validate_json(request_text(context))
            result = await self._service.plan(request)
            await updater.add_artifact(
                parts=[Part(text=result.model_dump_json())],
                artifact_id=f"kitchen-{request.order_id}",
            )
            await updater.complete()
        except (ValidationError, ValueError) as exc:
            logger.warning("Invalid A2A kitchen request: %s", type(exc).__name__)
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=updater.new_agent_message(
                    [Part(text="La comanda A2A no tiene un formato válido.")]
                ),
            )
        except Exception as exc:
            logger.exception(
                "Kitchen A2A execution failed: %s",
                type(exc).__name__,
            )
            await updater.update_status(
                state=TaskState.TASK_STATE_FAILED,
                message=updater.new_agent_message(
                    [Part(text="Cocina no puede responder ahora mismo.")]
                ),
            )


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_agent_card(settings: Settings) -> AgentCard:
    public_url = str(settings.kitchen_a2a_public_url).rstrip("/") + "/"
    return AgentCard(
        name="Cocina",
        description=(
            "Coordina la validación y preparación de pedidos del restaurante "
            "mediante chef y especialistas."
        ),
        version="1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
        ),
        supported_interfaces=[
            AgentInterface(url=public_url, protocol_binding="JSONRPC")
        ],
        skills=[
            AgentSkill(
                id="coordinate-kitchen-order",
                name="CoordinateKitchenOrder",
                description=(
                    "Valida una comanda con el conocimiento de la casa, la "
                    "distribuye entre parrilla, fritos y cocina general, y "
                    "devuelve aceptación, sustituciones y tiempo estimado."
                ),
                tags=["kitchen", "restaurant", "agent-framework"],
                examples=[
                    '{"schema_version":1,"order_id":"ko_example","lines":'
                    '[{"line":1,"name":"morcilla a la brasa","quantity":1,'
                    '"modifications":[]}],"restrictions":[]}'
                ],
            )
        ],
    )


def create_app(
    settings: Settings,
    *,
    service: KitchenService | None = None,
) -> Starlette:
    agent_card = create_agent_card(settings)
    request_handler = DefaultRequestHandler(
        agent_executor=KitchenAgentExecutor(
            service or KitchenService(settings)
        ),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            *create_agent_card_routes(agent_card),
            *create_jsonrpc_routes(request_handler, "/"),
        ]
    )
