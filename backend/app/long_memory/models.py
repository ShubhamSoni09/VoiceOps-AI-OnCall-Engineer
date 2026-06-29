from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LongMemoryRecord(BaseModel):
    id: str
    room_id: str
    kind: str
    text: str
    actor_name: str | None = None
    source: str
    source_id: str
    importance: int = Field(default=1, ge=1, le=10)
    created_at: datetime
    archived_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class LongMemoryArchiveResponse(BaseModel):
    room_id: str
    archived_count: int
    total_count: int
    kinds: dict[str, int] = Field(default_factory=dict)


class LongMemoryQueryResponse(BaseModel):
    answer: str
    records: list[LongMemoryRecord]
    citations: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "deterministic"
    retrieval: dict[str, Any] = Field(default_factory=dict)
