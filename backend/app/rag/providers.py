from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections import Counter

from app.config import Settings

TOKEN_RE = re.compile(r"[\w./-]+")


class EmbeddingProviderError(ValueError):
    pass


class EmbeddingProvider(ABC):
    name: str

    @abstractmethod
    def embed(self, text: str) -> dict[str, float]:
        """Return a deterministic vector payload that can be persisted in the RAG index."""

    @abstractmethod
    def similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        """Return a normalized similarity score for two provider vectors."""


class LocalSparseEmbeddingProvider(EmbeddingProvider):
    name = "local_sparse"

    def embed(self, text: str) -> dict[str, float]:
        counts = Counter(self._tokens(text))
        if not counts:
            return {}
        total = sum(counts.values())
        return {term: count / total for term, count in counts.items()}

    def similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        dot = sum(weight * right.get(term, 0.0) for term, weight in left.items())
        left_norm = math.sqrt(sum(weight * weight for weight in left.values()))
        right_norm = math.sqrt(sum(weight * weight for weight in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _tokens(self, text: str) -> list[str]:
        stop = {"a", "an", "and", "are", "did", "do", "for", "is", "it", "of", "the", "to", "we", "what", "with"}
        tokens: list[str] = []
        for raw in (match.group(0).lower() for match in TOKEN_RE.finditer(text)):
            if len(raw) <= 1 or raw in stop:
                continue
            tokens.append(_normalize_token(raw))
        return tokens


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider = settings.rag_embedding_provider.strip().lower()
    if provider == "local_sparse":
        return LocalSparseEmbeddingProvider()
    raise EmbeddingProviderError(
        f"Unsupported RAG embedding provider '{settings.rag_embedding_provider}'. "
        "Supported providers: local_sparse."
    )


def _normalize_token(token: str) -> str:
    token = token.strip(".,!?;:()[]{}\"'")
    aliases = {
        "decided": "decide",
        "decision": "decide",
        "decisions": "decide",
        "files": "file",
        "mentioned": "mention",
        "mentions": "mention",
        "approved": "approve",
        "approval": "approve",
        "approvals": "approve",
        "tasks": "task",
        "risks": "risk",
        "questions": "question",
    }
    return aliases.get(token, token)
