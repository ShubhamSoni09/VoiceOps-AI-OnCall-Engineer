from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


LLMRole = Literal["system", "user", "assistant"]
LLMResponseFormat = Literal["text", "json"]


class LLMMessage(BaseModel):
    role: LLMRole
    content: str


class LLMRequest(BaseModel):
    messages: list[LLMMessage] = Field(min_length=1)
    purpose: str = Field(default="general", max_length=80)
    response_format: LLMResponseFormat = "text"
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=16000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str
    provider: str
    model: str
    usage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
