from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    MEETING = "meeting"
    MEMORY = "memory"
    CODE = "code"
    REVIEW = "review"
    TEST = "test"
    GIT = "git"


class AgentRunStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    BUDGET_EXHAUSTED = "budget_exhausted"
    RECOVERED = "recovered"


class AgentStepStatus(str, Enum):
    PLANNED = "planned"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentAssignmentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRunRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="manual", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)
    background: bool = False
    max_steps: int = Field(default=20, ge=1, le=80)
    timeout_seconds: float = Field(default=60.0, ge=0.0, le=900.0)


class AgentRunCancelRequest(BaseModel):
    reason: str = Field(default="cancelled by user", max_length=240)


class AgentAssignmentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    agent_label: str = Field(min_length=1, max_length=120)
    agent_kind: str = Field(default="internal", pattern="^(internal|external)$")
    task: str = Field(min_length=1, max_length=1200)
    mode: str = Field(default="patch", pattern="^(explain|patch|review|test|memory)$")
    model: str | None = Field(default=None, max_length=160)
    source: str = Field(default="dashboard", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAssignmentStatusUpdate(BaseModel):
    status: AgentAssignmentStatus
    note: str | None = Field(default=None, max_length=240)


class AgentAssignmentCancelRequest(BaseModel):
    reason: str = Field(default="cancelled from queue", max_length=240)


class AgentAssignmentClearResponse(BaseModel):
    cleared_count: int
    assignments: list[AgentAssignment]


class AgentStep(BaseModel):
    id: str
    role: AgentRole
    status: AgentStepStatus
    title: str
    detail: str
    created_at: datetime
    completed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentFinding(BaseModel):
    role: AgentRole
    title: str
    detail: str
    severity: str = "info"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentExchange(BaseModel):
    id: str
    from_role: AgentRole
    to_role: AgentRole
    title: str
    detail: str
    created_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRun(BaseModel):
    id: str
    room_id: str
    prompt: str
    source: str = "manual"
    requested_by: str
    requested_by_name: str
    status: AgentRunStatus
    summary: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    findings: list[AgentFinding] = Field(default_factory=list)
    exchanges: list[AgentExchange] = Field(default_factory=list)
    action_id: str | None = None
    route: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAssignment(BaseModel):
    id: str
    room_id: str
    agent_id: str
    agent_label: str
    agent_kind: str = "internal"
    task: str
    mode: str = "patch"
    source: str = "dashboard"
    requested_by: str
    requested_by_name: str
    status: AgentAssignmentStatus = AgentAssignmentStatus.QUEUED
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    note: str | None = None
    run_id: str | None = None
    action_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunListResponse(BaseModel):
    runs: list[AgentRun]


class AgentAssignmentListResponse(BaseModel):
    assignments: list[AgentAssignment]
