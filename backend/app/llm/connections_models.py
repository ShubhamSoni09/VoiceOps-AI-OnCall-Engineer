from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LLMConnectionProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class LLMConnectionProviderInfo(BaseModel):
    provider: LLMConnectionProvider
    label: str
    status: LLMConnectionStatus
    connected: bool
    credential_id: str | None = None
    account_label: str | None = None
    model: str | None = None
    base_url: str | None = None
    token_preview: str | None = None
    last_connected_at: datetime | None = None
    detail: str


class LLMConnectionRecord(BaseModel):
    id: str
    user_id: str
    provider: LLMConnectionProvider
    account_label: str | None = None
    model: str | None = None
    base_url: str | None = None
    encrypted_payload: str
    token_preview: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMConnectionPublic(BaseModel):
    id: str
    provider: LLMConnectionProvider
    account_label: str | None = None
    model: str | None = None
    base_url: str | None = None
    token_preview: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMApiKeyConnectRequest(BaseModel):
    api_key: str = Field(default="", max_length=4096)
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    account_label: str | None = Field(default=None, max_length=120)


class LLMProviderPreflightRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=4096)
    model: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    require_json: bool = True


class LLMProviderPreflightResponse(BaseModel):
    provider: LLMConnectionProvider
    ready: bool
    model: str | None = None
    base_url: str | None = None
    latency_ms: int | None = None
    text_ok: bool = False
    json_ok: bool = False
    blockers: list[str] = Field(default_factory=list)
    detail: str
