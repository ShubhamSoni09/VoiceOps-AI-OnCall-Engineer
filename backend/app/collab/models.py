from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.auth.models import UserPublic


class ParticipantKind(str, Enum):
    HUMAN = "human"
    AGENT = "agent"


class MessageRole(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AgentActionType(str, Enum):
    INVESTIGATE = "investigate"
    DIAGNOSE = "diagnose"
    EXPLAIN_CODE = "explain_code"
    FIND_BUG = "find_bug"
    SUMMARIZE_CHANGES = "summarize_changes"
    GIT_STATUS = "git_status"
    PATCH = "patch"
    TEST = "test"
    STATUS = "status"
    CREATE_PR = "create_pr"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    QUERY = "query"
    UNKNOWN = "unknown"


class MemoryKind(str, Enum):
    DECISION = "decision"
    TASK = "task"
    QUESTION = "question"
    RISK = "risk"
    CODE_REFERENCE = "code_reference"


class Participant(BaseModel):
    id: str
    name: str
    initials: str
    kind: ParticipantKind = ParticipantKind.HUMAN
    role_label: str | None = None
    online: bool = False
    last_seen_at: datetime | None = None
    joined_at: datetime | None = None


class Room(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    project: str | None = None
    workspace_path: str | None = None


class TimelineMessage(BaseModel):
    id: str
    room_id: str
    role: MessageRole
    actor_id: str
    actor_name: str
    actor_initials: str
    text: str
    created_at: datetime
    source: str = "text"
    speaker_label: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAction(BaseModel):
    id: str
    room_id: str
    requested_by: str
    requested_by_name: str
    action: AgentActionType | str
    summary: str
    created_at: datetime
    files_changed: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    command_output: str | None = None
    pending_approval: bool = False
    status: str = "completed"
    approval: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class ActionDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=240)


class ActionCommitRequest(BaseModel):
    message: str | None = Field(default=None, max_length=200)


class ActionPullRequestRequest(BaseModel):
    dry_run: bool = True
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=5000)
    base_branch: str = Field(default="main", min_length=1, max_length=120)


class ActionPullRequestResponse(BaseModel):
    action_id: str
    provider: str = "github"
    mode: str = "dry_run"
    ready: bool
    title: str
    body: str
    base_branch: str
    head_branch: str | None = None
    remote_url: str | None = None
    web_url: str | None = None
    commit_sha: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    preflight: dict[str, Any] = Field(default_factory=dict)
    commands: list[str] = Field(default_factory=list)
    pull_request_url: str | None = None
    pull_request_number: int | None = None
    draft: bool | None = None
    executed_by: str | None = None
    executed_by_name: str | None = None
    executed_at: str | None = None
    command_results: list[dict[str, Any]] = Field(default_factory=list)


class MemoryItem(BaseModel):
    id: str
    room_id: str
    kind: MemoryKind
    text: str
    actor_id: str
    actor_name: str
    created_at: datetime
    source_message_id: str | None = None
    source_action_id: str | None = None
    status: str = "noted"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryQueryRequest(BaseModel):
    question: str
    limit: int = Field(default=8, ge=1, le=50)


class MemoryQueryResponse(BaseModel):
    answer: str
    items: list[MemoryItem]
    citations: list["RagCitation"] = Field(default_factory=list)
    mode: str = "deterministic"
    retrieval: dict[str, Any] = Field(default_factory=dict)


class MemoryHealthResponse(BaseModel):
    room_id: str
    status: str
    total_items: int
    kind_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    duplicate_count: int = 0
    stale_open_count: int = 0
    orphaned_source_count: int = 0
    source_coverage: dict[str, int] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class RagCitation(BaseModel):
    source: str
    source_id: str
    title: str
    excerpt: str
    actor_name: str | None = None
    created_at: datetime | None = None
    score: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagQueryRequest(BaseModel):
    question: str
    limit: int = Field(default=8, ge=1, le=50)
    include_code: bool = True


class RagQueryResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
    mode: str = "local_hybrid"
    retrieval: dict[str, Any] = Field(default_factory=dict)


class ProvenanceQueryRequest(BaseModel):
    question: str
    limit: int = Field(default=8, ge=1, le=50)
    include_code: bool = True
    include_ontology: bool = True


class ProvenanceQueryResponse(BaseModel):
    answer: str
    citations: list[RagCitation]
    mode: str = "local_provenance"
    retrieval: dict[str, Any] = Field(default_factory=dict)


class RagIndexResponse(BaseModel):
    room_id: str
    provider: str
    document_count: int
    updated_at: str
    sources: dict[str, int] = Field(default_factory=dict)


class AuditQueryRequest(BaseModel):
    question: str
    limit: int = Field(default=8, ge=1, le=50)


class AgentSettings(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    initials: str = Field(min_length=1, max_length=4)
    wake_words: list[str] = Field(default_factory=list)
    updated_at: datetime | None = None


class AgentSettingsUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)
    initials: str | None = Field(default=None, max_length=4)
    wake_words: list[str] | None = None


class AuditEvent(BaseModel):
    id: str
    kind: str
    tone: str
    title: str
    status: str
    detail: str
    actor_name: str | None = None
    created_at: datetime
    source_id: str
    chips: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditQueryResponse(BaseModel):
    answer: str
    items: list[AuditEvent]
    mode: str = "deterministic"


class RoomTraceEvent(BaseModel):
    sequence: int
    kind: str
    event_type: str
    summary: str
    actor_name: str | None = None
    action_id: str | None = None
    source_id: str | None = None
    source: str | None = None
    status: str | None = None
    created_at: datetime | None = None
    event_position: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RoomTraceResponse(BaseModel):
    room_id: str
    generated_at: datetime
    backend: str
    event_store_ready: bool
    event_store_event_count: int = 0
    coverage: dict[str, bool] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    events: list[RoomTraceEvent] = Field(default_factory=list)


class CommandRouteRequest(BaseModel):
    text: str
    memory_question_mode: str = Field(default="broad", pattern="^(broad|narrow)$")


class CommandRouteResponse(BaseModel):
    route: str
    source: str
    reason: str
    read_only: bool
    action_policy: str
    matched_item_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HandoffReviewItem(BaseModel):
    id: str
    kind: str
    status: str
    title: str
    detail: str
    actor_name: str
    created_at: datetime
    action_id: str | None = None
    memory_id: str | None = None
    route_trace: dict[str, Any] | None = None


class HandoffSummary(BaseModel):
    room_id: str
    user_id: str
    since: datetime | None = None
    generated_at: datetime
    message_count: int = 0
    action_count: int = 0
    lines: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    open_review_items: list[HandoffReviewItem] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)


class WorkDashboardMetric(BaseModel):
    label: str
    value: int
    tone: str = "neutral"
    detail: str | None = None


class WorkDashboardItem(BaseModel):
    id: str
    kind: str
    title: str
    status: str
    actor_name: str | None = None
    created_at: datetime
    detail: str | None = None
    files: list[str] = Field(default_factory=list)
    action_id: str | None = None
    memory_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkDashboardAgent(BaseModel):
    id: str
    name: str
    kind: str
    status: str
    role_label: str | None = None
    last_seen_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkDashboardReadiness(BaseModel):
    ready: bool
    state: str
    summary: str
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkDashboardSnapshot(BaseModel):
    room_id: str
    generated_at: datetime
    metrics: list[WorkDashboardMetric]
    agents: list[WorkDashboardAgent]
    open_items: list[WorkDashboardItem] = Field(default_factory=list)
    approvals: list[WorkDashboardItem] = Field(default_factory=list)
    recent_actions: list[WorkDashboardItem] = Field(default_factory=list)
    recent_decisions: list[WorkDashboardItem] = Field(default_factory=list)
    audit_counts: dict[str, int] = Field(default_factory=dict)
    queue_health: list[WorkDashboardMetric] = Field(default_factory=list)
    readiness: WorkDashboardReadiness | None = None


class RoomSnapshot(BaseModel):
    room: Room
    participants: list[Participant]
    messages: list[TimelineMessage]
    actions: list[AgentAction]
    memory: list[MemoryItem] = Field(default_factory=list)
    handoff: HandoffSummary | None = None


class JoinRoomRequest(BaseModel):
    room_name: str | None = None
    project: str | None = None


class RoomWorkspaceUpdate(BaseModel):
    path: str


class TextMessageRequest(BaseModel):
    text: str
    source: str = "text"
    speaker_label: str | None = None
    confidence: float | None = None


class LeaveRoomResponse(BaseModel):
    status: str
    participant: Participant


def participant_from_user(user: UserPublic, *, online: bool = True) -> Participant:
    return Participant(
        id=user.id,
        name=user.name,
        initials=user.initials,
        role_label=user.role_label,
        online=online,
    )
