from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ToolPolicy = Literal["none", "read_only", "approval_required"]


class PlanningStep(BaseModel):
    role: str = Field(max_length=80)
    title: str = Field(max_length=160)
    detail: str = Field(max_length=1000)
    tool_policy: ToolPolicy = "none"
    requires_approval: bool = False


class AgentPlan(BaseModel):
    route: str = Field(max_length=120)
    summary: str = Field(max_length=500)
    steps: list[PlanningStep] = Field(default_factory=list)
    provider: str = "deterministic"
    model: str = "local-rules"
    metadata: dict[str, Any] = Field(default_factory=dict)
