from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RagIndexDocument(BaseModel):
    room_id: str
    source: str
    source_id: str
    title: str
    text: str
    actor_name: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    vector: dict[str, float] = Field(default_factory=dict)


class RagIndexStatus(BaseModel):
    path: str
    provider: str
    document_count: int
    room_count: int
    updated_at: str | None = None


class RagIndexedRoom(BaseModel):
    room_id: str
    provider: str
    document_count: int
    updated_at: str
    sources: dict[str, int] = Field(default_factory=dict)


class RagSearchHit(BaseModel):
    document: RagIndexDocument
    score: float
    lexical_score: int = 0
    vector_score: float = 0.0
