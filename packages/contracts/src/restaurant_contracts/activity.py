"""What the system did for one conversation message, step by step.

Steps carry curated labels, counters and durations for the «bajo el capó»
panel: which component acted (waiter model, memory, seating MCP, the A2A
kitchen, Foundry IQ, the chef and its specialists), never raw prompts.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ActivityComponent = Literal[
    "camarero",
    "memoria",
    "mcp",
    "cocina",
    "foundry_iq",
    "chef",
    "especialista",
    "entrega",
]
ActivityText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
]
MAX_ACTIVITY_STEPS = 60


class ActivityStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    component: ActivityComponent
    label: ActivityText
    detail: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    status: Literal["running", "done", "failed"] = "done"
    duration_ms: Annotated[int, Field(ge=0)] | None = None


def merge_steps(steps: list[ActivityStep], step: ActivityStep) -> list[ActivityStep]:
    """Replace the step with the same id, or append it, keeping the order of first appearance."""

    merged = [step if current.step_id == step.step_id else current for current in steps]
    if not any(current.step_id == step.step_id for current in steps):
        merged.append(step)
    return merged[-MAX_ACTIVITY_STEPS:]
