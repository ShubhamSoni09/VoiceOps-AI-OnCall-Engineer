from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IncidentAction(str, Enum):
    INVESTIGATE = "investigate"
    DIAGNOSE = "diagnose"
    EXPLAIN_CODE = "explain_code"
    FIND_BUG = "find_bug"
    SUMMARIZE_CHANGES = "summarize_changes"
    GIT_STATUS = "git_status"
    PATCH = "patch"
    TEST = "test"
    CREATE_PR = "create_pr"
    DEPLOY = "deploy"
    VERIFY = "verify"
    ROLLBACK = "rollback"
    STATUS = "status"
    UNKNOWN = "unknown"


class VoiceIntent(str, Enum):
    INVESTIGATE_INCIDENT = "investigate_incident"
    FIX_ISSUE = "fix_issue"
    EXPLAIN_CODE = "explain_code"
    FIND_BUG = "find_bug"
    SUMMARIZE_CHANGES = "summarize_changes"
    GIT_STATUS = "git_status"
    DEPLOY_SERVICE = "deploy_service"
    CHECK_STATUS = "check_status"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    RUN_TESTS = "run_tests"
    CREATE_PR = "create_pr"
    GENERAL_QUERY = "general_query"
    UNKNOWN = "unknown"


class TranscriptionResult(BaseModel):
    text: str
    language: str | None = None
    confidence: float | None = None
    provider: str


class ConversationTurn(BaseModel):
    role: str  # user | assistant | system
    content: str
    timestamp: str | None = None


class ExtractedIntent(BaseModel):
    intent: VoiceIntent
    action: IncidentAction
    entities: dict[str, Any] = Field(default_factory=dict)
    raw_summary: str
    spoken_response: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class NormalizedCommand(BaseModel):
    """Standard command format consumed by the Agent Orchestrator."""

    intent: VoiceIntent
    action: IncidentAction
    target: str | None = None  # service, repo, endpoint, etc.
    parameters: dict[str, Any] = Field(default_factory=dict)
    urgency: str = "normal"  # low | normal | high | critical
    requires_approval: bool = True
    original_transcript: str
    normalized_text: str


class EnrichedContext(BaseModel):
    transcript: str
    intent: ExtractedIntent
    command: NormalizedCommand
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
    incident_context: dict[str, Any] = Field(default_factory=dict)
    suggested_next_steps: list[str] = Field(default_factory=list)


class OrchestratorResult(BaseModel):
    executed: bool
    action: str
    summary: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    command_output: str | None = None
    pending_approval: bool = False
    approval: dict[str, Any] = Field(default_factory=dict)
    approval_payload: dict[str, Any] = Field(default_factory=dict, exclude=True)


class VoiceProcessRequest(BaseModel):
    """Process pre-transcribed text (skip STT)."""

    text: str
    session_id: str = "default"
    room_id: str = "main"
    incident_context: dict[str, Any] = Field(default_factory=dict)
    include_tts: bool = False


class SpeechResult(BaseModel):
    text: str
    audio_base64: str | None = None
    mime_type: str = "audio/mpeg"
    provider: str


class VoiceProcessResponse(BaseModel):
    session_id: str
    transcript: str
    intent: ExtractedIntent
    command: NormalizedCommand
    context: EnrichedContext
    response_text: str
    speech: SpeechResult
    orchestrator_result: OrchestratorResult | None = None
