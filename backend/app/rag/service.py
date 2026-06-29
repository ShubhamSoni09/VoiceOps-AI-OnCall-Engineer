from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Iterable

from app.collab.models import AgentAction, MemoryItem, TimelineMessage
from app.config import Settings
from app.rag.models import RagIndexedRoom, RagIndexDocument, RagIndexStatus, RagSearchHit
from app.rag.providers import EmbeddingProvider, get_embedding_provider
from app.rag.store import JsonRagIndexStore
from app.redaction import redact_sensitive_text, redact_sensitive_value
from app.workspace.service import WorkspaceCodeService
from app.workspace.tools import WorkspaceError


class RagIndexService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._provider: EmbeddingProvider = get_embedding_provider(settings)
        self._store = JsonRagIndexStore(settings.rag_index_path, provider=self._provider.name)

    def status(self) -> RagIndexStatus:
        return self._store.status()

    def index_room(
        self,
        room_id: str,
        *,
        memory: Iterable[MemoryItem],
        messages: Iterable[TimelineMessage],
        actions: Iterable[AgentAction],
        include_code: bool = True,
    ) -> RagIndexedRoom:
        documents: list[RagIndexDocument] = []
        for item in memory:
            documents.append(
                self._document(
                    room_id=room_id,
                    source="memory",
                    source_id=item.id,
                    title=f"{item.kind.value} from {item.actor_name}",
                    text=item.text,
                    actor_name=item.actor_name,
                    created_at=item.created_at,
                    metadata={
                        "kind": item.kind.value,
                        "status": item.status,
                        "source_message_id": item.source_message_id,
                        "source_action_id": item.source_action_id,
                        **item.metadata,
                    },
                )
            )
        for message in messages:
            documents.append(
                self._document(
                    room_id=room_id,
                    source="timeline",
                    source_id=message.id,
                    title=f"{message.actor_name} timeline message",
                    text=message.text,
                    actor_name=message.actor_name,
                    created_at=message.created_at,
                    metadata={
                        "role": message.role.value,
                        "source": message.source,
                        "speaker_label": message.speaker_label,
                        **message.metadata,
                    },
                )
            )
        for action in actions:
            text = " ".join([action.summary, " ".join(action.files_changed), str(action.approval)])
            documents.append(
                self._document(
                    room_id=room_id,
                    source="action",
                    source_id=action.id,
                    title=f"{action.action} action by {action.requested_by_name}",
                    text=text,
                    actor_name=action.requested_by_name,
                    created_at=action.created_at,
                    metadata={
                        "status": action.status,
                        "files_changed": action.files_changed,
                        "approval": action.approval,
                    },
                )
            )
        if include_code:
            documents.extend(self._code_documents(room_id))

        self._store.replace_room(room_id, documents)
        sources = Counter(doc.source for doc in documents)
        return RagIndexedRoom(
            room_id=room_id,
            provider=self._provider.name,
            document_count=len(documents),
            updated_at=datetime.now(UTC).isoformat(),
            sources=dict(sources),
        )

    def search_room(self, room_id: str, query: str, *, limit: int = 8) -> list[RagSearchHit]:
        query_vector = self._provider.embed(query)
        query_terms = set(query_vector)
        hits: list[RagSearchHit] = []
        for document in self._store.load_room(room_id):
            lexical = sum(1 for term in query_terms if term in document.vector)
            vector_score = self._provider.similarity(query_vector, document.vector)
            score = lexical + vector_score
            if score <= 0:
                continue
            hits.append(
                RagSearchHit(
                    document=document,
                    score=score,
                    lexical_score=lexical,
                    vector_score=vector_score,
                )
            )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]

    def _document(
        self,
        *,
        room_id: str,
        source: str,
        source_id: str,
        title: str,
        text: str,
        actor_name: str | None = None,
        created_at=None,
        metadata: dict | None = None,
    ) -> RagIndexDocument:
        safe_text = redact_sensitive_text(text)
        safe_title = redact_sensitive_text(title)
        safe_metadata = redact_sensitive_value(metadata or {})
        return RagIndexDocument(
            room_id=room_id,
            source=source,
            source_id=source_id,
            title=safe_title,
            text=safe_text,
            actor_name=actor_name,
            created_at=created_at,
            metadata=safe_metadata,
            vector=self._provider.embed(" ".join([safe_title, safe_text, _metadata_text(safe_metadata)])),
        )

    def _code_documents(self, room_id: str) -> list[RagIndexDocument]:
        try:
            tree = WorkspaceCodeService(self._settings).tree(limit=80)
        except WorkspaceError:
            return []
        docs = []
        service = WorkspaceCodeService(self._settings)
        for file in tree.files[:80]:
            try:
                content = service.read(file.path).content
            except WorkspaceError:
                continue
            docs.append(
                self._document(
                    room_id=room_id,
                    source="code",
                    source_id=file.path,
                    title=file.path,
                    text=content[:2400],
                    metadata={"path": file.path, "language": file.language},
                )
            )
        return docs

def _metadata_text(metadata: dict) -> str:
    parts = []
    for value in metadata.values():
        if isinstance(value, (str, int, float, bool)):
            parts.append(str(value))
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
    return " ".join(parts)
