from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExternalAgentProvider(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"
    LOCAL = "local"


class ExternalAgentAuthMethod(str, Enum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    LOCAL_CLI = "local_cli"


class ExternalAgentRunMode(str, Enum):
    EXPLAIN = "explain"
    PATCH = "patch"
    REVIEW = "review"
    TEST = "test"


class ExternalAgentTaskKind(str, Enum):
    EXPLAIN_CODE = "explain_code"
    FIND_BUG = "find_bug"
    REVIEW_PATCH = "review_patch"
    WRITE_PATCH = "write_patch"
    RUN_TESTS = "run_tests"
    SUMMARIZE_CHANGES = "summarize_changes"
    GIT_STATUS = "git_status"


class ExternalAgentCredentialStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    NEEDS_CONFIGURATION = "needs_configuration"


class ExternalAgentModeReadiness(BaseModel):
    ready: bool
    reason: str
    detail: str
    severity: str = "ok"
    auth_method: ExternalAgentAuthMethod | None = None
    execution_mode: str | None = None
    credential_status: ExternalAgentCredentialStatus = ExternalAgentCredentialStatus.DISCONNECTED
    cli_execution_enabled: bool | None = None
    api_execution_enabled: bool | None = None
    cli_command: str | None = None
    cli_available: bool | None = None
    cli_path: str | None = None
    preview_first: bool = False
    model_required: bool = False


class ExternalAgentPreflightMode(BaseModel):
    mode: ExternalAgentRunMode
    ready: bool
    severity: str
    reason: str
    detail: str
    execution_mode: str | None = None
    preview_first: bool = False
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentProviderPreflight(BaseModel):
    provider: ExternalAgentProvider
    label: str
    connected: bool
    status: ExternalAgentCredentialStatus
    state: str
    summary: str
    auth_method: ExternalAgentAuthMethod | None = None
    account_label: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    modes: list[ExternalAgentPreflightMode] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentPreflightReport(BaseModel):
    status: str
    ready: bool
    generated_at: datetime
    providers: list[ExternalAgentProviderPreflight] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExternalAgentSetupStep(BaseModel):
    id: str
    label: str
    detail: str
    action: str
    recommended: bool = False
    auth_method: ExternalAgentAuthMethod | None = None
    modes: list[ExternalAgentRunMode] = Field(default_factory=list)
    command: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentProviderSetupGuide(BaseModel):
    provider: ExternalAgentProvider
    label: str
    state: str
    recommended_path: str
    next_step: str
    steps: list[ExternalAgentSetupStep] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class ExternalAgentSetupGuideReport(BaseModel):
    generated_at: datetime
    providers: list[ExternalAgentProviderSetupGuide] = Field(default_factory=list)


class ExternalAgentProviderInfo(BaseModel):
    provider: ExternalAgentProvider
    label: str
    status: ExternalAgentCredentialStatus
    connected: bool
    auth_methods: list[ExternalAgentAuthMethod]
    capabilities: list[ExternalAgentRunMode]
    credential_id: str | None = None
    auth_method: ExternalAgentAuthMethod | None = None
    account_label: str | None = None
    token_preview: str | None = None
    last_connected_at: datetime | None = None
    detail: str
    supported_models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    mode_readiness: dict[str, ExternalAgentModeReadiness] = Field(default_factory=dict)
    preflight: ExternalAgentProviderPreflight | None = None
    setup_guide: ExternalAgentProviderSetupGuide | None = None
    local_cli_command: str | None = None
    local_cli_command_template: str | None = None


class ExternalAgentCapability(BaseModel):
    provider: ExternalAgentProvider
    label: str
    connected: bool
    auth_methods: list[ExternalAgentAuthMethod]
    supported_models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    task_kinds: list[ExternalAgentTaskKind] = Field(default_factory=list)
    modes: list[ExternalAgentRunMode] = Field(default_factory=list)
    patch_requires_approval: bool = True
    patch_requires_local_cli: bool = True
    best_for: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    mode_readiness: dict[str, ExternalAgentModeReadiness] = Field(default_factory=dict)


class ExternalAgentCapabilityReport(BaseModel):
    providers: list[ExternalAgentCapability] = Field(default_factory=list)
    task_kinds: list[ExternalAgentTaskKind] = Field(default_factory=list)
    generated_at: datetime


class ExternalAgentRecommendationRequest(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    preferred_provider: ExternalAgentProvider | None = None
    mode: ExternalAgentRunMode | None = None
    model: str | None = Field(default=None, max_length=160)


class ExternalAgentRecommendationAlternative(BaseModel):
    provider: ExternalAgentProvider
    label: str
    ready: bool
    connected: bool
    score: int
    reason: str
    blockers: list[str] = Field(default_factory=list)


class ExternalAgentRecommendationResponse(BaseModel):
    task_kind: ExternalAgentTaskKind
    provider: ExternalAgentProvider
    label: str
    mode: ExternalAgentRunMode
    model: str
    ready: bool
    connected: bool
    approval_required: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    blockers: list[str] = Field(default_factory=list)
    alternatives: list[ExternalAgentRecommendationAlternative] = Field(default_factory=list)


class ExternalAgentCredentialRecord(BaseModel):
    id: str
    user_id: str
    provider: ExternalAgentProvider
    auth_method: ExternalAgentAuthMethod
    account_label: str | None = None
    encrypted_payload: str
    token_preview: str | None = None
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentCredentialPublic(BaseModel):
    id: str
    provider: ExternalAgentProvider
    auth_method: ExternalAgentAuthMethod
    account_label: str | None = None
    token_preview: str | None = None
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentApiKeyConnectRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)
    account_label: str | None = Field(default=None, max_length=120)
    scopes: list[str] = Field(default_factory=list, max_length=20)


class ExternalAgentLocalCliConnectRequest(BaseModel):
    command: str = Field(default="", max_length=240)
    command_template: str | None = Field(default=None, max_length=800)
    account_label: str | None = Field(default=None, max_length=120)


class ExternalAgentOAuthStartRequest(BaseModel):
    scopes: list[str] = Field(default_factory=list, max_length=20)
    redirect_uri: str | None = Field(default=None, max_length=500)


class ExternalAgentOAuthStartResponse(BaseModel):
    provider: ExternalAgentProvider
    authorization_url: str
    state: str
    redirect_uri: str
    mode: str
    detail: str


class ExternalAgentOAuthCallbackRequest(BaseModel):
    provider: ExternalAgentProvider
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=12, max_length=240)
    account_label: str | None = Field(default=None, max_length=120)


class ExternalAgentRunRequest(BaseModel):
    provider: ExternalAgentProvider
    prompt: str = Field(min_length=1, max_length=4000)
    mode: ExternalAgentRunMode = ExternalAgentRunMode.PATCH
    model: str | None = Field(default=None, max_length=160)
    source: str = Field(default="manual", max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalAgentRunResponse(BaseModel):
    provider: ExternalAgentProvider
    provider_run_id: str
    mode: ExternalAgentRunMode
    model: str | None = None
    status: str
    summary: str
    action_id: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    diff: str | None = None
    message_id: str | None = None
    audit: dict[str, Any] = Field(default_factory=dict)
