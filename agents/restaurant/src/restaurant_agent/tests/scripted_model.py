"""A scripted Agent Framework chat client: the model without Foundry.

Each step answers one model call: tool calls, the structured waiter result, or
both. It records what the model receives so tests can check the conversation,
the tools and the instructions.
"""

from __future__ import annotations

import json
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
)


def waiter_result(
    reply: str, party_size: int | None = 1, name: str | None = "Ana"
) -> dict[str, Any]:
    return {
        "reply": reply,
        "customer": {"presented_name": name, "party_size": party_size},
        "order_draft": {"items": []},
        "memory_candidates": [],
        "memory_intent": "none",
        "remembered_memories": [],
    }


def hold(party_size: int, preference: str = "any", call_id: str = "call_hold", **extra: Any) -> dict[str, Any]:
    return {"calls": [(call_id, "seating_hold_seating", {"party_size": party_size, "preference": preference, **extra})]}


def confirm(call_id: str = "call_confirm", **arguments: Any) -> dict[str, Any]:
    return {"calls": [(call_id, "seating_confirm_seating", arguments or {"assignment_id": "forged", "visit_id": "forged", "expected_version": 9, "idempotency_key": "forged"})]}


def availability(call_id: str = "call_availability") -> dict[str, Any]:
    return {"calls": [(call_id, "seating_get_seating_availability", {})]}


def confirm_solo(call_id: str = "call_confirm_solo", **arguments: Any) -> dict[str, Any]:
    return {
        "calls": [
            (
                call_id,
                "seating_confirm_solo_seating",
                arguments
                or {
                    "assignment_id": "forged",
                    "visit_id": "forged",
                    "expected_version": 9,
                    "idempotency_key": "forged",
                },
            )
        ]
    }


def say(reply: str, party_size: int | None = 1) -> dict[str, Any]:
    return {"result": waiter_result(reply, party_size)}


class ScriptedModel(FunctionInvocationLayer, ChatMiddlewareLayer, BaseChatClient):
    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.script = list(script or [])
        self.calls: list[dict[str, Any]] = []

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        async def respond() -> ChatResponse:
            self.calls.append(
                {
                    "messages": [
                        (
                            message.role,
                            [
                                (
                                    content.type,
                                    getattr(content, "name", None),
                                    getattr(content, "call_id", None),
                                    str(getattr(content, "result", None) or getattr(content, "text", None) or ""),
                                )
                                for content in message.contents
                            ],
                        )
                        for message in messages
                    ],
                    "instructions": str(options.get("instructions") or ""),
                    "tools": sorted(getattr(tool, "name", "") for tool in options.get("tools") or []),
                    # What a real chat client would serialize for this call.
                    "raw_messages": list(messages),
                }
            )
            if not self.script:
                raise AssertionError("The scripted model was called more times than scripted")
            step = self.script.pop(0)
            contents: list[Content] = []
            if "result" in step:
                contents.append(Content.from_text(json.dumps(step["result"], ensure_ascii=False)))
            for call_id, name, arguments in step.get("calls", []):
                # A real model always sends the arguments as a JSON string.
                contents.append(
                    Content.from_function_call(call_id=call_id, name=name, arguments=json.dumps(arguments))
                )
            return ChatResponse(messages=[Message(role="assistant", contents=contents)])

        return respond()
