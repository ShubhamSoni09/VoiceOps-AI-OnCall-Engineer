from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OntologyNode(BaseModel):
    id: str
    kind: str
    label: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyEdge(BaseModel):
    id: str
    source: str
    target: str
    relation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class OntologyGraph(BaseModel):
    room_id: str
    nodes: list[OntologyNode] = Field(default_factory=list)
    edges: list[OntologyEdge] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


class OntologyQueryResponse(BaseModel):
    answer: str
    nodes: list[OntologyNode] = Field(default_factory=list)
    edges: list[OntologyEdge] = Field(default_factory=list)
    mode: str = "deterministic"
