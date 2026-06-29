import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from app.auth.models import Role, UserPublic
from app.collab.models import (
    AgentAction,
    AgentActionType,
    AgentSettings,
    AgentSettingsUpdate,
    AuditEvent,
    AuditQueryResponse,
    HandoffReviewItem,
    HandoffSummary,
    JoinRoomRequest,
    MemoryItem,
    MemoryKind,
    MemoryHealthResponse,
    MemoryQueryResponse,
    MessageRole,
    Participant,
    ParticipantKind,
    RagCitation,
    RagIndexResponse,
    RagQueryResponse,
    ProvenanceQueryResponse,
    RoomSnapshot,
    RoomTraceEvent,
    RoomTraceResponse,
    TextMessageRequest,
    TimelineMessage,
    WorkDashboardAgent,
    WorkDashboardItem,
    WorkDashboardMetric,
    WorkDashboardSnapshot,
)
from app.collab.store import CollaborationStore, create_collaboration_store
from app.config import Settings, get_settings
from app.rag.service import RagIndexService
from app.redaction import redact_sensitive_text, redact_sensitive_value
from app.voice_agent.models import OrchestratorResult, VoiceProcessResponse
from app.workspace.git import WorkspaceGitService
from app.workspace.github import build_pull_request_plan, create_pull_request
from app.workspace.service import WorkspaceCodeService
from app.workspace.models import CodeQueryResponse
from app.workspace.tools import WorkspaceError, configured_workspace_is_url, resolve_configured_workspace, run_command, write_file

AGENT_PARTICIPANT_ID = "agent-voiceops"

CODE_REFERENCE_RE = re.compile(
    r"\b[\w./-]+\.(?:py|js|jsx|ts|tsx|md|json|ya?ml|css|html|sql|sh)\b",
    re.IGNORECASE,
)
QUESTION_RE = re.compile(r"^\s*(?:what|why|how|when|where|who|can|could|should|is|are|do|does|did)\b", re.IGNORECASE)
MEMORY_QUESTION_MARKERS = (
    "decide",
    "decision",
    "open",
    "todo",
    "task",
    "question",
    "risk",
    "blocker",
    "file",
    "code",
    "mentioned",
    "memory",
    "决定",
    "任务",
    "待办",
    "问题",
    "风险",
    "文件",
    "代码",
    "提到",
    "还有",
    "刚刚",
)


class CollaborationService:
    def __init__(
        self,
        store: CollaborationStore,
        *,
        agent_display_name: str = "VoiceOps",
        agent_initials: str | None = None,
        agent_wake_words: str | None = None,
    ) -> None:
        self._store = store
        self._approval_payloads: dict[str, dict] = {}
        self._default_agent_settings = AgentSettings(
            display_name=agent_display_name.strip() or "VoiceOps",
            initials=_normalize_initials(agent_initials, agent_display_name),
            wake_words=_normalize_wake_words(agent_wake_words, agent_display_name),
        )
        self._apply_agent_settings(self._store.get_agent_settings() or self._default_agent_settings)

    @property
    def agent_wake_words(self) -> list[str]:
        return list(self._agent_wake_words)

    def agent_settings(self) -> AgentSettings:
        return AgentSettings(
            display_name=self._agent_display_name,
            initials=self._agent_initials,
            wake_words=self.agent_wake_words,
            updated_at=(self._store.get_agent_settings() or self._default_agent_settings).updated_at,
        )

    def update_agent_settings(self, body: AgentSettingsUpdate, *, room_id: str | None = None) -> AgentSettings:
        display_name = " ".join(body.display_name.strip().split())
        if not display_name:
            raise ValueError("Display name cannot be empty")
        settings = AgentSettings(
            display_name=display_name,
            initials=_normalize_initials(body.initials, display_name),
            wake_words=_normalize_wake_words(body.wake_words, display_name),
            updated_at=_now(),
        )
        self._store.set_agent_settings(settings)
        self._apply_agent_settings(settings)
        if room_id:
            self._ensure_agent(room_id)
        return self.agent_settings()

    def _apply_agent_settings(self, settings: AgentSettings) -> None:
        self._agent_display_name = settings.display_name.strip() or "VoiceOps"
        self._agent_initials = _normalize_initials(settings.initials, self._agent_display_name)
        self._agent_wake_words = _normalize_wake_words(settings.wake_words, self._agent_display_name)

    def get_participant(self, room_id: str, participant_id: str) -> Participant | None:
        return self._store.get_participant(room_id, participant_id)

    def list_participants(self, room_id: str) -> list[Participant]:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        return self._store.list_participants(room_id)

    def user_can_access_room(self, room_id: str, user: UserPublic) -> bool:
        room = self._store.get_room(room_id) if hasattr(self._store, "get_room") else None
        if room is None:
            return "voice:use" in user.permissions and not _user_project_scope(user)
        if room and not _user_can_access_project(user, room.project):
            return False
        if user.role == Role.ADMIN:
            return True
        participant = self._store.get_participant(room_id, user.id)
        if participant and participant.kind == ParticipantKind.HUMAN:
            return True
        human_participants = [
            item for item in self._store.list_participants(room_id)
            if item.kind == ParticipantKind.HUMAN
        ]
        return not human_participants and not self._room_has_human_activity(room_id)

    def _room_has_human_activity(self, room_id: str) -> bool:
        return (
            any(message.role == MessageRole.USER for message in self._store.list_messages(room_id, limit=500))
            or bool(self._store.list_actions(room_id, limit=1))
            or bool(self._store.list_memory(room_id, limit=1))
        )

    def update_room_workspace(self, room_id: str, path: str) -> RoomSnapshot:
        workspace_path = _validated_room_workspace_path(path)
        self._store.get_or_create_room(room_id, workspace_path=workspace_path)
        return self.snapshot(room_id)

    def settings_for_room(self, room_id: str, settings: Settings) -> Settings:
        room = self._store.get_or_create_room(room_id)
        if not room.workspace_path:
            return settings
        return settings.model_copy(update={"voiceops_workspace": room.workspace_path, "voiceops_workspace_source": "room"})

    def list_messages(self, room_id: str, *, limit: int = 100) -> list[TimelineMessage]:
        return self._store.list_messages(room_id, limit=limit)

    def list_events(
        self,
        room_id: str | None = None,
        *,
        from_position: int = 0,
        limit: int = 100,
    ) -> list[dict]:
        list_events = getattr(self._store, "list_events", None)
        if not callable(list_events):
            return []
        return list_events(room_id, from_position=from_position, limit=limit)

    def reattribute_speaker_history(
        self,
        room_id: str,
        *,
        speaker_label: str,
        actor_id: str,
        actor_name: str,
        actor_initials: str,
        identity_confidence: float | None,
        identity_source: str,
    ) -> dict[str, int]:
        messages = self._store.reattribute_speaker_messages(
            room_id,
            speaker_label=speaker_label,
            actor_id=actor_id,
            actor_name=actor_name,
            actor_initials=actor_initials,
            identity_confidence=identity_confidence,
            identity_source=identity_source,
        )
        memory = self._store.reattribute_memory_for_speaker(
            room_id,
            speaker_label=speaker_label,
            actor_id=actor_id,
            actor_name=actor_name,
        )
        return {"messages": messages, "memory": memory}

    def list_memory(
        self,
        room_id: str,
        *,
        q: str | None = None,
        kind: MemoryKind | str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        self._store.get_or_create_room(room_id)
        items = self._store.list_memory(room_id, limit=500)
        if kind:
            memory_kind = _coerce_memory_kind(kind)
            items = [item for item in items if item.kind == memory_kind]
        if status:
            wanted_status = status.lower()
            items = [item for item in items if item.status.lower() == wanted_status]
        if q:
            terms = _query_terms(q)
            if terms:
                items = [
                    item
                    for item in items
                    if _memory_score(item, terms) > 0
                ]
                items.sort(key=lambda item: (_memory_score(item, terms), item.created_at), reverse=True)
        return items[-limit:] if not q else items[:limit]

    def query_memory(
        self,
        room_id: str,
        question: str,
        *,
        limit: int = 8,
    ) -> MemoryQueryResponse:
        self._store.get_or_create_room(room_id)
        inferred_kind = _infer_memory_kind(question)
        inferred_status = "open" if _asks_for_open(question) else None
        items = self.list_memory(
            room_id,
            q=question if inferred_kind is None else None,
            kind=inferred_kind,
            status=inferred_status,
            limit=limit,
        )
        if inferred_kind is None and not items:
            items = self.list_memory(room_id, limit=limit)
        answer = _memory_answer(question, items, inferred_kind=inferred_kind, inferred_status=inferred_status)
        citations = _scope_rag_citations(
            room_id,
            [
                _memory_item_citation(item, score=_memory_score(item, _query_terms(question)) or 1)
                for item in items
            ],
        )
        return MemoryQueryResponse(
            answer=answer,
            items=items,
            citations=citations,
            mode="deterministic",
            retrieval={
                "terms": _query_terms(question),
                "inferred_kind": inferred_kind.value if inferred_kind else None,
                "inferred_status": inferred_status,
                "candidate_count": len(items),
                "sources": _citation_source_counts(citations),
                "access_policy": _retrieval_access_policy(room_id),
            },
        )

    def memory_health(self, room_id: str, *, stale_days: int = 7) -> MemoryHealthResponse:
        self._store.get_or_create_room(room_id)
        items = self._store.list_memory(room_id, limit=1000)
        messages = {message.id for message in self._store.list_messages(room_id, limit=1000)}
        actions = {action.id for action in self._store.list_actions(room_id, limit=500)}
        kind_counts = Counter(item.kind.value for item in items)
        status_counts = Counter(item.status for item in items)
        duplicate_count = sum(count - 1 for count in Counter(_memory_identity(item) for item in items).values() if count > 1)
        cutoff = _now() - timedelta(days=max(stale_days, 1))
        stale_open_count = sum(1 for item in items if item.status == "open" and item.created_at < cutoff)
        orphaned_source_count = sum(
            1
            for item in items
            if (item.source_message_id and item.source_message_id not in messages)
            or (item.source_action_id and item.source_action_id not in actions)
        )
        source_coverage = {
            "message": sum(1 for item in items if item.source_message_id),
            "action": sum(1 for item in items if item.source_action_id),
            "implicit": sum(1 for item in items if not item.source_message_id and not item.source_action_id),
        }

        blockers: list[str] = []
        warnings: list[str] = []
        recommendations: list[str] = []
        if not items:
            blockers.append("No meeting memory has been recorded for this room.")
            recommendations.append("Ingest meeting speech or timeline messages before relying on memory answers.")
        if duplicate_count:
            warnings.append(f"{duplicate_count} duplicate memory item(s) detected.")
            recommendations.append("Rebuild or compact the memory store to remove duplicate projections.")
        if status_counts.get("open", 0):
            warnings.append(f"{status_counts['open']} open memory item(s) need review.")
            recommendations.append("Close or assign open tasks/questions/risks before declaring the meeting resolved.")
        if stale_open_count:
            warnings.append(f"{stale_open_count} open memory item(s) are older than {stale_days} day(s).")
            recommendations.append("Review stale open tasks/questions/risks during handoff.")
        if orphaned_source_count:
            warnings.append(f"{orphaned_source_count} memory item(s) reference missing timeline/action sources.")
            recommendations.append("Run a memory rebuild from the authoritative timeline and action log.")
        if kind_counts.get(MemoryKind.CODE_REFERENCE.value, 0) == 0:
            warnings.append("No code references have been captured yet.")
            recommendations.append("Ask teammates to mention concrete files when discussing implementation work.")

        status = "blocked" if blockers else "needs_review" if warnings else "healthy"
        return MemoryHealthResponse(
            room_id=room_id,
            status=status,
            total_items=len(items),
            kind_counts=dict(kind_counts),
            status_counts=dict(status_counts),
            duplicate_count=duplicate_count,
            stale_open_count=stale_open_count,
            orphaned_source_count=orphaned_source_count,
            source_coverage=source_coverage,
            blockers=blockers,
            warnings=warnings,
            recommendations=list(dict.fromkeys(recommendations)),
        )

    def query_rag(
        self,
        room_id: str,
        question: str,
        settings: Settings,
        *,
        limit: int = 8,
        include_code: bool = True,
    ) -> RagQueryResponse:
        self._store.get_or_create_room(room_id)
        settings = self.settings_for_room(room_id, settings)
        terms = _query_terms(question)
        inferred_kind = _infer_memory_kind(question)
        rag_index = RagIndexService(settings)
        indexed = rag_index.index_room(
            room_id,
            memory=self._store.list_memory(room_id, limit=500),
            messages=self._store.list_messages(room_id, limit=500),
            actions=self._store.list_actions(room_id, limit=200),
            include_code=include_code,
        )
        short_citations = self._short_memory_citations(
            room_id,
            question,
            terms,
            inferred_kind=inferred_kind,
        )
        index_hits = rag_index.search_room(room_id, question, limit=max(limit * 4, 20))
        if index_hits:
            long_citations = self._long_memory_citations(
                index_hits,
                inferred_kind=inferred_kind,
                question=question,
            )
            ranked = _scope_rag_citations(room_id, _rank_rag_citations([*short_citations, *long_citations], limit=limit))
            answer = _rag_answer(question, ranked)
            return RagQueryResponse(
                answer=answer,
                citations=ranked,
                retrieval={
                    "terms": terms,
                    "inferred_kind": inferred_kind.value if inferred_kind else None,
                    "include_code": include_code,
                    "provider": settings.rag_embedding_provider,
                    "indexed_documents": indexed.document_count,
                    "indexed_sources": indexed.sources,
                    "candidate_count": len(index_hits),
                    "short_memory_hits": len(short_citations),
                    "long_memory_hits": len(long_citations),
                    "memory_tiers": _rag_memory_tiers(ranked),
                    "sources": _citation_source_counts(ranked),
                    "citation_safety": _citation_safety_counts(ranked),
                    "access_policy": _retrieval_access_policy(room_id),
                },
            )

        citations: list[RagCitation] = [*short_citations]
        for item in self._store.list_memory(room_id, limit=500):
            score = _rag_score(
                question,
                terms,
                item.text,
                item.actor_name,
                item.kind.value,
                item.status,
                " ".join(str(value) for value in item.metadata.values()),
            )
            if inferred_kind and item.kind == inferred_kind:
                score += 4
            if _asks_for_open(question) and item.status == "open":
                score += 3
            if score > 0:
                citations.append(
                    RagCitation(
                        source="memory",
                        source_id=item.id,
                        title=f"{_memory_label(item.kind)} from {item.actor_name}",
                        excerpt=item.text,
                        actor_name=item.actor_name,
                        created_at=item.created_at,
                        score=score,
                        metadata={
                            "memory_tier": "long",
                            "kind": item.kind.value,
                            "status": item.status,
                            "source_message_id": item.source_message_id,
                            "source_action_id": item.source_action_id,
                            **item.metadata,
                        },
                    )
                )

        for message in self._store.list_messages(room_id, limit=200):
            score = _rag_score(
                question,
                terms,
                message.text,
                message.actor_name,
                message.source,
                message.speaker_label or "",
                " ".join(str(value) for value in message.metadata.values()),
            )
            if score > 0:
                citations.append(
                    RagCitation(
                        source="timeline",
                        source_id=message.id,
                        title=f"{message.actor_name} timeline message",
                        excerpt=message.text,
                        actor_name=message.actor_name,
                        created_at=message.created_at,
                        score=score,
                        metadata={
                            "memory_tier": "long",
                            "role": message.role.value,
                            "source": message.source,
                            "speaker_label": message.speaker_label,
                            **message.metadata,
                        },
                    )
                )

        for action in self._store.list_actions(room_id, limit=120):
            score = _rag_score(
                question,
                terms,
                action.summary,
                action.requested_by_name,
                str(action.action.value if isinstance(action.action, AgentActionType) else action.action),
                action.status,
                " ".join(action.files_changed),
                " ".join(str(value) for value in action.approval.values()),
            )
            if score > 0:
                citations.append(
                    RagCitation(
                        source="action",
                        source_id=action.id,
                        title=f"{action.action} action by {action.requested_by_name}",
                        excerpt=action.summary,
                        actor_name=action.requested_by_name,
                        created_at=action.created_at,
                        score=score,
                        metadata={
                            "memory_tier": "long",
                            "status": action.status,
                            "files_changed": action.files_changed,
                            "approval": action.approval,
                        },
                    )
                )

        code_error: str | None = None
        if include_code:
            try:
                code_matches = WorkspaceCodeService(settings).search(question, limit=min(limit, 8)).matches
            except WorkspaceError as exc:
                code_matches = []
                code_error = str(exc)
            for match in code_matches:
                score = max(1, _rag_score(question, terms, match.path, match.snippet, str(match.line))) + 2
                citations.append(
                    RagCitation(
                        source="code",
                        source_id=f"{match.path}:{match.line}",
                        title=f"{match.path}:{match.line}",
                        excerpt=match.snippet,
                        score=score,
                        metadata={"memory_tier": "long", "path": match.path, "line": match.line},
                    )
                )

        deduped = _dedupe_rag_citations(citations)
        ranked = _scope_rag_citations(room_id, _rank_rag_citations(deduped, limit=limit))
        answer = _rag_answer(question, ranked)
        return RagQueryResponse(
            answer=answer,
            citations=ranked,
            retrieval={
                "terms": terms,
                "inferred_kind": inferred_kind.value if inferred_kind else None,
                "include_code": include_code,
                "provider": "live_hybrid_fallback",
                "indexed_documents": indexed.document_count,
                "code_error": code_error,
                "candidate_count": len(deduped),
                "short_memory_hits": len(short_citations),
                "long_memory_hits": max(0, len(deduped) - len(short_citations)),
                "memory_tiers": _rag_memory_tiers(ranked),
                "sources": _citation_source_counts(ranked),
                "citation_safety": _citation_safety_counts(ranked),
                "access_policy": _retrieval_access_policy(room_id),
            },
        )

    def query_provenance(
        self,
        room_id: str,
        question: str,
        settings: Settings,
        *,
        limit: int = 8,
        include_code: bool = True,
        include_ontology: bool = True,
    ) -> ProvenanceQueryResponse:
        rag = self.query_rag(room_id, question, settings, limit=limit, include_code=include_code)
        ontology_citations: list[RagCitation] = []
        ontology_error: str | None = None
        if include_ontology:
            try:
                ontology_citations = _ontology_citations(self, room_id, question, limit=limit)
            except Exception as exc:
                ontology_error = str(exc)
        combined = _scope_rag_citations(room_id, _rank_rag_citations([*rag.citations, *ontology_citations], limit=limit))
        retrieval = {
            **rag.retrieval,
            "provider": "local_provenance",
            "rag_provider": rag.retrieval.get("provider"),
            "ontology_hits": len(ontology_citations),
            "ontology_error": ontology_error,
            "memory_tiers": _rag_memory_tiers(combined),
            "sources": _citation_source_counts(combined),
            "citation_safety": _citation_safety_counts(combined),
            "access_policy": _retrieval_access_policy(room_id),
        }
        return ProvenanceQueryResponse(
            answer=_rag_answer(question, combined),
            citations=combined,
            retrieval=retrieval,
        )

    def _short_memory_citations(
        self,
        room_id: str,
        question: str,
        terms: list[str],
        *,
        inferred_kind: MemoryKind | None,
    ) -> list[RagCitation]:
        citations: list[RagCitation] = []
        recent_question = _asks_for_recent(question)
        for item in self._store.list_memory(room_id, limit=20):
            score = _rag_score(
                question,
                terms,
                item.text,
                item.actor_name,
                item.kind.value,
                item.status,
                " ".join(str(value) for value in item.metadata.values()),
            )
            if inferred_kind and item.kind == inferred_kind:
                score += 4
            if _asks_for_open(question) and item.status == "open":
                score += 3
            if recent_question and score == 0:
                score = 1
            if score > 0:
                citations.append(
                    RagCitation(
                        source="memory",
                        source_id=item.id,
                        title=f"{_memory_label(item.kind)} from {item.actor_name}",
                        excerpt=item.text,
                        actor_name=item.actor_name,
                        created_at=item.created_at,
                        score=score + 1000,
                        metadata={
                            "memory_tier": "short",
                            "kind": item.kind.value,
                            "status": item.status,
                            "source_message_id": item.source_message_id,
                            "source_action_id": item.source_action_id,
                            **item.metadata,
                        },
                    )
                )

        for message in self._store.list_messages(room_id, limit=20):
            score = _rag_score(
                question,
                terms,
                message.text,
                message.actor_name,
                message.source,
                message.speaker_label or "",
                " ".join(str(value) for value in message.metadata.values()),
            )
            if recent_question and score == 0:
                score = 1
            if score > 0:
                citations.append(
                    RagCitation(
                        source="timeline",
                        source_id=message.id,
                        title=f"{message.actor_name} timeline message",
                        excerpt=message.text,
                        actor_name=message.actor_name,
                        created_at=message.created_at,
                        score=score + 1000,
                        metadata={
                            "memory_tier": "short",
                            "role": message.role.value,
                            "source": message.source,
                            "speaker_label": message.speaker_label,
                            **message.metadata,
                        },
                    )
                )

        for action in self._store.list_actions(room_id, limit=10):
            score = _rag_score(
                question,
                terms,
                action.summary,
                action.requested_by_name,
                str(action.action.value if isinstance(action.action, AgentActionType) else action.action),
                action.status,
                " ".join(action.files_changed),
                " ".join(str(value) for value in action.approval.values()),
            )
            if recent_question and score == 0:
                score = 1
            if score > 0:
                citations.append(
                    RagCitation(
                        source="action",
                        source_id=action.id,
                        title=f"{action.action} action by {action.requested_by_name}",
                        excerpt=action.summary,
                        actor_name=action.requested_by_name,
                        created_at=action.created_at,
                        score=score + 1000,
                        metadata={
                            "memory_tier": "short",
                            "status": action.status,
                            "files_changed": action.files_changed,
                            "approval": action.approval,
                        },
                    )
                )
        return citations

    def _long_memory_citations(
        self,
        index_hits,
        *,
        inferred_kind: MemoryKind | None,
        question: str,
    ) -> list[RagCitation]:
        citations: list[RagCitation] = []
        for hit in index_hits:
            score = hit.score
            if inferred_kind and hit.document.source == "memory" and hit.document.metadata.get("kind") == inferred_kind.value:
                score += 6
            if _asks_for_open(question) and hit.document.metadata.get("status") == "open":
                score += 3
            citations.append(
                RagCitation(
                    source=hit.document.source,
                    source_id=hit.document.source_id,
                    title=hit.document.title,
                    excerpt=_truncate(hit.document.text, 260),
                    actor_name=hit.document.actor_name,
                    created_at=hit.document.created_at,
                    score=round(score * 100),
                    metadata={
                        **hit.document.metadata,
                        "memory_tier": "long",
                        "lexical_score": hit.lexical_score,
                        "vector_score": round(hit.vector_score, 4),
                    },
                )
            )
        return citations

    def rebuild_rag_index(self, room_id: str, settings: Settings, *, include_code: bool = True) -> RagIndexResponse:
        self._store.get_or_create_room(room_id)
        settings = self.settings_for_room(room_id, settings)
        indexed = RagIndexService(settings).index_room(
            room_id,
            memory=self._store.list_memory(room_id, limit=500),
            messages=self._store.list_messages(room_id, limit=500),
            actions=self._store.list_actions(room_id, limit=200),
            include_code=include_code,
        )
        return RagIndexResponse(
            room_id=indexed.room_id,
            provider=indexed.provider,
            document_count=indexed.document_count,
            updated_at=indexed.updated_at,
            sources=indexed.sources,
        )

    def query_code(
        self,
        room_id: str,
        user: UserPublic,
        question: str,
        settings: Settings,
        *,
        limit: int = 8,
        record_user_message: bool = True,
    ) -> CodeQueryResponse:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        settings = self.settings_for_room(room_id, settings)
        workspace = WorkspaceCodeService(settings)
        result = workspace.query(question, limit=limit)
        references = [item.model_dump(mode="json") for item in result.references]
        if record_user_message:
            self.add_user_message(
                room_id,
                user,
                TextMessageRequest(text=question, source="code_query"),
                metadata={"source": "code_query"},
            )
        self.add_agent_message(
            room_id,
            result.answer,
            metadata={
                "source": "code_query",
                "question": question,
                "references": references,
                "mode": result.mode,
            },
        )
        return result

    def list_audit_events(
        self,
        room_id: str,
        *,
        kind: str | None = None,
        limit: int = 50,
    ) -> list[AuditEvent]:
        self._store.get_or_create_room(room_id)
        requested_kind = kind.strip().lower() if kind else None
        events = [
            *_audit_events_from_actions(self._store.list_actions(room_id, limit=250)),
            *_audit_events_from_messages(self._store.list_messages(room_id, limit=500)),
        ]
        if requested_kind:
            events = [event for event in events if event.kind == requested_kind]
        events.sort(key=lambda event: event.created_at, reverse=True)
        return events[:limit]

    def query_audit(
        self,
        room_id: str,
        question: str,
        *,
        limit: int = 8,
    ) -> AuditQueryResponse:
        events = self.list_audit_events(room_id, limit=200)
        kind = _infer_audit_kind(question)
        status = _infer_audit_status(question)
        if kind:
            events = [event for event in events if event.kind == kind]
        if status:
            events = [event for event in events if event.status == status or event.metadata.get("approval_status") == status]
        if _is_audit_approval_question(question):
            events = [
                event
                for event in events
                if event.metadata.get("decided_by_name") or event.metadata.get("approval_status") == "approved"
            ]
        terms = _query_terms(question)
        scored = [
            (event, _audit_score(event, terms))
            for event in events
        ]
        if terms and any(score for _, score in scored):
            scored = [item for item in scored if item[1] > 0]
            scored.sort(key=lambda item: (item[1], item[0].created_at), reverse=True)
            events = [event for event, _score in scored]
        answer_items = events[:limit]
        answer = _audit_answer(question, answer_items, kind=kind, status=status)
        return AuditQueryResponse(answer=answer, items=answer_items, mode="deterministic")

    def build_room_trace(self, room_id: str, *, limit: int = 200) -> RoomTraceResponse:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        messages = self._store.list_messages(room_id, limit=500)
        actions = self._store.list_actions(room_id, limit=200)
        memory = self._store.list_memory(room_id, limit=500)
        handoff = self.build_handoff(room_id, _system_user())
        event_rows = self.list_events(room_id, limit=500)

        events: list[RoomTraceEvent] = []
        for row in event_rows:
            events.append(_trace_event_from_store_row(row))
        for message in messages:
            events.append(_trace_event_from_message(message))
        for item in memory:
            events.append(_trace_event_from_memory(item))
        for action in actions:
            events.extend(_trace_events_from_action(action))
        events.append(_trace_event_from_handoff(handoff))

        events.sort(key=_trace_sort_key)
        events = [
            event.model_copy(update={"sequence": index + 1})
            for index, event in enumerate(events[-limit:])
        ]
        coverage = _trace_coverage(
            messages=messages,
            actions=actions,
            memory=memory,
            handoff=handoff,
            event_rows=event_rows,
        )
        backend = "sqlite" if event_rows or hasattr(self._store, "list_events") else "json"
        return RoomTraceResponse(
            room_id=room_id,
            generated_at=_now(),
            backend=backend,
            event_store_ready=bool(event_rows),
            event_store_event_count=len(event_rows),
            coverage=coverage,
            missing=[key for key, ready in coverage.items() if not ready],
            events=events,
        )

    def join_room(self, room_id: str, user: UserPublic, body: JoinRoomRequest) -> RoomSnapshot:
        existing_room = self._store.get_room(room_id) if hasattr(self._store, "get_room") else None
        target_project = body.project if body.project is not None else existing_room.project if existing_room else None
        if not _user_can_access_project(user, target_project):
            raise ValueError("User is not allowed to join this project room")
        room = self._store.get_or_create_room(
            room_id,
            name=body.room_name,
            project=body.project,
        )
        self._ensure_agent(room_id)
        existing = self._store.get_participant(room_id, user.id)
        since = existing.last_seen_at if existing else None
        participant = _participant_from_user(user, existing=existing, online=True)
        self._store.upsert_participant(room_id, participant)
        handoff = self.build_handoff(room_id, user, since=since)
        return self.snapshot(room_id, handoff=handoff)

    def leave_room(self, room_id: str, user: UserPublic) -> Participant:
        self._ensure_agent(room_id)
        existing = self._store.get_participant(room_id, user.id)
        participant = _participant_from_user(user, existing=existing, online=False)
        self._store.upsert_participant(room_id, participant)
        return participant

    def snapshot(self, room_id: str, *, handoff: HandoffSummary | None = None) -> RoomSnapshot:
        room = self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        return RoomSnapshot(
            room=room,
            participants=self._store.list_participants(room_id),
            messages=self._store.list_messages(room_id),
            actions=self._store.list_actions(room_id),
            memory=self._store.list_memory(room_id),
            handoff=handoff,
        )

    def work_dashboard(self, room_id: str) -> WorkDashboardSnapshot:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        participants = self._store.list_participants(room_id)
        memory = self._store.list_memory(room_id, limit=250)
        actions = self._store.list_actions(room_id, limit=120)
        messages = self._store.list_messages(room_id, limit=120)
        audit_events = [
            *_audit_events_from_actions(actions),
            *_audit_events_from_messages(messages),
        ]
        pending_actions = [
            action for action in actions
            if action.pending_approval or action.status == "pending_approval"
        ]
        completed_patches = [
            action for action in actions
            if _action_value(action) == AgentActionType.PATCH.value and action.status == "completed"
        ]
        rejected_actions = [action for action in actions if action.status == "rejected"]
        open_memory = [
            item for item in memory
            if item.status == "open" and item.kind in {MemoryKind.TASK, MemoryKind.QUESTION, MemoryKind.RISK}
        ]
        decisions = [item for item in memory if item.kind == MemoryKind.DECISION]
        audit_counts: dict[str, int] = {}
        for event in audit_events:
            audit_counts[event.kind] = audit_counts.get(event.kind, 0) + 1

        return WorkDashboardSnapshot(
            room_id=room_id,
            generated_at=_now(),
            metrics=[
                WorkDashboardMetric(label="online", value=sum(1 for item in participants if item.online), tone="info"),
                WorkDashboardMetric(label="open work", value=len(open_memory), tone="warning" if open_memory else "neutral"),
                WorkDashboardMetric(label="pending approvals", value=len(pending_actions), tone="warning" if pending_actions else "neutral"),
                WorkDashboardMetric(label="completed patches", value=len(completed_patches), tone="success" if completed_patches else "neutral"),
                WorkDashboardMetric(label="rejected", value=len(rejected_actions), tone="danger" if rejected_actions else "neutral"),
            ],
            agents=[
                WorkDashboardAgent(
                    id=participant.id,
                    name=participant.name,
                    kind=participant.kind.value,
                    status="online" if participant.online else "away",
                    role_label=participant.role_label,
                    last_seen_at=participant.last_seen_at,
                    metadata={"initials": participant.initials},
                )
                for participant in participants
                if participant.kind == ParticipantKind.AGENT
            ],
            open_items=[_dashboard_item_from_memory(item) for item in open_memory[-8:]][::-1],
            approvals=[_dashboard_item_from_action(action) for action in pending_actions[-8:]][::-1],
            recent_actions=[_dashboard_item_from_action(action) for action in actions[-8:]][::-1],
            recent_decisions=[_dashboard_item_from_memory(item) for item in decisions[-5:]][::-1],
            audit_counts=audit_counts,
        )

    def add_user_message(
        self,
        room_id: str,
        user: UserPublic,
        body: TextMessageRequest,
        *,
        metadata: dict | None = None,
    ) -> TimelineMessage:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        self._store.upsert_participant(room_id, _participant_from_user(user, online=True))
        message = self._store.append_message(
            TimelineMessage(
                id=_id("msg"),
                room_id=room_id,
                role=MessageRole.USER,
                actor_id=user.id,
                actor_name=user.name,
                actor_initials=user.initials,
                text=body.text.strip(),
                source=body.source,
                speaker_label=body.speaker_label,
                confidence=body.confidence,
                metadata=metadata or {},
                created_at=_now(),
            )
        )
        self._capture_memory_from_message(message)
        return message

    def add_agent_message(
        self,
        room_id: str,
        text: str,
        *,
        metadata: dict | None = None,
    ) -> TimelineMessage:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        message = self._store.append_message(
            TimelineMessage(
                id=_id("msg"),
                room_id=room_id,
                role=MessageRole.AGENT,
                actor_id=AGENT_PARTICIPANT_ID,
                actor_name=self._agent_display_name,
                actor_initials=self._agent_initials,
                text=text.strip(),
                source="agent",
                metadata=metadata or {},
                created_at=_now(),
            )
        )
        self._capture_memory_from_message(message)
        return message

    def add_system_message(
        self,
        room_id: str,
        text: str,
        *,
        metadata: dict | None = None,
    ) -> TimelineMessage:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        message = self._store.append_message(
            TimelineMessage(
                id=_id("msg"),
                room_id=room_id,
                role=MessageRole.SYSTEM,
                actor_id="system",
                actor_name="System",
                actor_initials="SYS",
                text=text.strip(),
                source="system",
                metadata=metadata or {},
                created_at=_now(),
            )
        )
        self._capture_memory_from_message(message)
        return message

    def add_speaker_message(
        self,
        room_id: str,
        *,
        text: str,
        speaker_label: str,
        confidence: float | None,
        identified_user_id: str | None,
        identified_user_name: str | None,
        identity_confidence: float | None,
        source: str,
        metadata: dict | None = None,
    ) -> TimelineMessage:
        self._store.get_or_create_room(room_id)
        self._ensure_agent(room_id)
        participant = (
            self._store.get_participant(room_id, identified_user_id)
            if identified_user_id
            else None
        )
        actor_id = participant.id if participant else f"speaker:{speaker_label}"
        actor_name = participant.name if participant else identified_user_name or "Unknown speaker"
        actor_initials = participant.initials if participant else "?"
        message_metadata = {
            "speaker_label": speaker_label,
            "identified_user_id": identified_user_id,
            "identified_user_name": identified_user_name,
            "identity_confidence": identity_confidence,
            **(metadata or {}),
        }
        message = self._store.append_message(
            TimelineMessage(
                id=_id("msg"),
                room_id=room_id,
                role=MessageRole.USER,
                actor_id=actor_id,
                actor_name=actor_name,
                actor_initials=actor_initials,
                text=text.strip(),
                source=source,
                speaker_label=speaker_label,
                confidence=confidence,
                metadata=message_metadata,
                created_at=_now(),
            )
        )
        self._capture_memory_from_message(message)
        return message

    def add_action(
        self,
        room_id: str,
        user: UserPublic,
        result: OrchestratorResult,
    ) -> AgentAction | None:
        if not result.executed and not result.pending_approval:
            return None
        self._store.upsert_participant(room_id, _participant_from_user(user, online=True))
        try:
            action_type: AgentActionType | str = AgentActionType(result.action)
        except ValueError:
            action_type = result.action
        status = "pending_approval" if result.pending_approval else "completed"
        if not result.executed and not result.pending_approval:
            status = "not_executed"
        action = self._store.append_action(
            AgentAction(
                id=_id("act"),
                room_id=room_id,
                requested_by=user.id,
                requested_by_name=user.name,
                action=action_type,
                summary=result.summary,
                files_changed=result.files_changed,
                artifacts=result.artifacts,
                command_output=result.command_output,
                pending_approval=result.pending_approval,
                status=status,
                approval=result.approval,
                created_at=_now(),
            )
        )
        if result.approval_payload:
            self._store.set_approval_payload(room_id, action.id, result.approval_payload)
            self._approval_payloads[action.id] = result.approval_payload
        self._capture_memory_from_action(action)
        return action

    def approve_action(
        self,
        room_id: str,
        action_id: str,
        user: UserPublic,
        settings: Settings,
        *,
        note: str | None = None,
    ) -> AgentAction:
        settings = self.settings_for_room(room_id, settings)
        action = self._store.get_action(room_id, action_id)
        if action is None:
            raise ValueError("Action not found")
        if action.status != "pending_approval" or not action.pending_approval:
            raise ValueError("Action is not waiting for approval")

        payload = self._store.get_approval_payload(room_id, action_id) or self._approval_payloads.get(action_id)
        if not payload:
            raise ValueError("Approval proposal expired; ask the agent to regenerate the patch")
        if payload.get("kind") != "patch":
            raise ValueError("This action type cannot be executed by approval yet")

        files = payload.get("files") or {}
        test_command = payload.get("test_command") or "python -m pytest -q"
        if not files:
            raise ValueError("Approval proposal has no files to apply")

        git_service = WorkspaceGitService(settings)
        git_metadata: dict = {}
        try:
            git_metadata = git_service.create_action_branch(action.id, action.summary)
            for path, content in files.items():
                write_file(str(path), str(content), configured=settings.voiceops_workspace)
            WorkspaceCodeService(settings).invalidate_cache()
            test_result = run_command(test_command, configured=settings.voiceops_workspace)
            after_status = git_service.status()
            after_diff = git_service.diff()
            git_metadata.update(
                {
                    "dirty_after": after_status.dirty,
                    "status_after": [item.model_dump() for item in after_status.files],
                    "diff_after": after_diff.diff,
                    "files_changed": after_diff.files_changed or list(files.keys()),
                }
            )
        except WorkspaceError as exc:
            updated = self._finalize_action(
                room_id,
                action,
                status="failed",
                pending_approval=False,
                summary=f"Approval failed before tests ran: {exc}",
                command_output=str(exc),
                approval_status="failed",
                decided_by=user,
                note=note,
                approval_extra={"git": git_metadata} if git_metadata else None,
            )
            self._store.delete_approval_payload(room_id, action_id)
            self._approval_payloads.pop(action_id, None)
            self.add_agent_message(
                room_id,
                updated.summary,
                metadata={"source": "action_approval", "action_id": action_id, "status": updated.status},
            )
            return updated

        passed = test_result["exit_code"] == 0
        status = "completed" if passed else "failed"
        branch = git_metadata.get("branch_name")
        branch_part = f" on {branch}" if branch else ""
        summary = (
            f"{user.name} approved the patch{branch_part}. Applied {', '.join(files.keys())}; tests passed."
            if passed
            else f"{user.name} approved the patch{branch_part}. Applied {', '.join(files.keys())}; tests are still failing."
        )
        updated = self._finalize_action(
            room_id,
            action,
            status=status,
            pending_approval=False,
            summary=summary,
            command_output=_truncate(test_result["stdout"] + test_result["stderr"], 800),
            approval_status="approved",
            decided_by=user,
            note=note,
            approval_extra={"git": git_metadata},
        )
        self._store.delete_approval_payload(room_id, action_id)
        self._approval_payloads.pop(action_id, None)
        self.add_agent_message(
            room_id,
            summary,
            metadata={
                "source": "action_approval",
                "action_id": action_id,
                "status": status,
                "approved_by": user.id,
            },
        )
        _archive_long_memory(room_id, self, settings)
        return updated

    def reject_action(
        self,
        room_id: str,
        action_id: str,
        user: UserPublic,
        *,
        note: str | None = None,
    ) -> AgentAction:
        action = self._store.get_action(room_id, action_id)
        if action is None:
            raise ValueError("Action not found")
        if action.status != "pending_approval" or not action.pending_approval:
            raise ValueError("Action is not waiting for approval")
        updated = self._finalize_action(
            room_id,
            action,
            status="rejected",
            pending_approval=False,
            summary=f"{user.name} rejected the patch proposal.",
            command_output=action.command_output,
            approval_status="rejected",
            decided_by=user,
            note=note,
        )
        self._store.delete_approval_payload(room_id, action_id)
        self._approval_payloads.pop(action_id, None)
        self.add_agent_message(
            room_id,
            updated.summary,
            metadata={
                "source": "action_approval",
                "action_id": action_id,
                "status": "rejected",
                "rejected_by": user.id,
            },
        )
        return updated

    def commit_action(
        self,
        room_id: str,
        action_id: str,
        user: UserPublic,
        settings: Settings,
        *,
        message: str | None = None,
    ) -> AgentAction:
        settings = self.settings_for_room(room_id, settings)
        action = self._store.get_action(room_id, action_id)
        if action is None:
            raise ValueError("Action not found")
        if action.action != AgentActionType.PATCH and str(action.action) != AgentActionType.PATCH.value:
            raise ValueError("Only patch actions can be committed")
        if action.status != "completed" or action.pending_approval:
            raise ValueError("Only completed approved patches can be committed")

        approval = {**action.approval}
        git = {**approval.get("git", {})}
        if git.get("commit_sha"):
            raise ValueError("Action has already been committed")

        files = _commit_files_for_action(action, git)
        try:
            commit_metadata = WorkspaceGitService(settings).commit_files(
                summary=action.summary,
                files=files,
                message=message,
            )
        except WorkspaceError as exc:
            raise ValueError(str(exc)) from exc

        now = _now()
        git.update(
            {
                **commit_metadata,
                "committed_by": user.id,
                "committed_by_name": user.name,
                "committed_at": now.isoformat(),
            }
        )
        approval["git"] = git
        summary = f"{action.summary} Commit {commit_metadata['commit_sha']} created by {user.name}."
        updated = self._store.update_action(
            room_id,
            action.id,
            summary=summary,
            approval=approval,
            updated_at=now,
        )
        if updated is None:
            raise ValueError("Action not found")
        self.add_agent_message(
            room_id,
            f"{user.name} committed approved patch {commit_metadata['commit_sha']}.",
            metadata={
                "source": "action_commit",
                "action_id": action_id,
                "commit_sha": commit_metadata["commit_sha"],
                "committed_by": user.id,
            },
        )
        return updated

    def create_pull_request_plan(
        self,
        room_id: str,
        action_id: str,
        user: UserPublic,
        settings: Settings,
        *,
        dry_run: bool = True,
        title: str | None = None,
        body: str | None = None,
        base_branch: str = "main",
    ) -> dict:
        settings = self.settings_for_room(room_id, settings)
        action = self._store.get_action(room_id, action_id)
        if action is None:
            raise ValueError("Action not found")
        if action.action != AgentActionType.PATCH and str(action.action) != AgentActionType.PATCH.value:
            raise ValueError("Only patch actions can be prepared as pull requests")
        if action.status != "completed" or action.pending_approval:
            raise ValueError("Only completed approved patches can be prepared as pull requests")
        try:
            plan = (
                build_pull_request_plan(
                    settings,
                    action,
                    title=title,
                    body=body,
                    base_branch=base_branch,
                )
                if dry_run
                else create_pull_request(
                    settings,
                    action,
                    title=title,
                    body=body,
                    base_branch=base_branch,
                )
            )
        except WorkspaceError as exc:
            if not dry_run:
                now = _now()
                approval = {**action.approval}
                approval["pull_request"] = {
                    "provider": "github",
                    "mode": "failed",
                    "error": str(exc),
                    "created_by": user.id,
                    "created_by_name": user.name,
                    "created_at": now.isoformat(),
                }
                self._store.update_action(
                    room_id,
                    action.id,
                    summary=f"{action.summary} Pull request creation failed: {exc}",
                    approval=approval,
                    updated_at=now,
                )
                self.add_agent_message(
                    room_id,
                    f"{user.name} tried to open a GitHub pull request, but creation failed: {exc}",
                    metadata={
                        "source": "action_pull_request",
                        "action_id": action_id,
                        "status": "failed",
                        "created_by": user.id,
                        "error": str(exc),
                    },
                )
            raise ValueError(str(exc)) from exc
        plan["requested_by"] = user.id
        plan["requested_by_name"] = user.name
        if not dry_run:
            now = _now()
            approval = {**action.approval}
            pull_request = {
                "provider": "github",
                "mode": plan.get("mode"),
                "url": plan.get("pull_request_url"),
                "number": plan.get("pull_request_number"),
                "title": plan.get("title"),
                "base_branch": plan.get("base_branch"),
                "head_branch": plan.get("head_branch"),
                "commit_sha": plan.get("commit_sha"),
                "draft": plan.get("draft"),
                "created_by": user.id,
                "created_by_name": user.name,
                "created_at": now.isoformat(),
                "command_results": plan.get("command_results") or [],
            }
            approval["pull_request"] = pull_request
            updated = self._store.update_action(
                room_id,
                action.id,
                summary=f"{action.summary} Pull request created: {plan.get('pull_request_url') or 'GitHub PR'}.",
                approval=approval,
                updated_at=now,
            )
            if updated is None:
                raise ValueError("Action not found")
            self.add_agent_message(
                room_id,
                f"{user.name} opened GitHub pull request {plan.get('pull_request_url') or ''}.",
                metadata={
                    "source": "action_pull_request",
                    "action_id": action_id,
                    "pull_request_url": plan.get("pull_request_url"),
                    "pull_request_number": plan.get("pull_request_number"),
                    "created_by": user.id,
                },
            )
            plan["executed_by"] = user.id
            plan["executed_by_name"] = user.name
            plan["executed_at"] = now.isoformat()
        return plan

    def _finalize_action(
        self,
        room_id: str,
        action: AgentAction,
        *,
        status: str,
        pending_approval: bool,
        summary: str,
        command_output: str | None,
        approval_status: str,
        decided_by: UserPublic,
        note: str | None,
        approval_extra: dict | None = None,
    ) -> AgentAction:
        now = _now()
        approval = {
            **action.approval,
            "status": approval_status,
            "decided_by": decided_by.id,
            "decided_by_name": decided_by.name,
            "decided_at": now.isoformat(),
        }
        if approval_extra:
            approval.update(approval_extra)
        if note:
            approval["note"] = note
        updated = self._store.update_action(
            room_id,
            action.id,
            summary=summary,
            artifacts=_finalized_artifacts(action.artifacts, status),
            command_output=command_output,
            pending_approval=pending_approval,
            status=status,
            approval=approval,
            updated_at=now,
        )
        if updated is None:
            raise ValueError("Action not found")
        self._store.update_memory_for_action(room_id, action.id, status="noted")
        self._capture_memory_from_action(updated)
        return updated

    def record_voice_response(
        self,
        room_id: str,
        user: UserPublic,
        response: VoiceProcessResponse,
        *,
        source: str = "voice",
        agent_metadata: dict | None = None,
    ) -> None:
        self.add_user_message(
            room_id,
            user,
            TextMessageRequest(
                text=response.transcript,
                source=source,
                speaker_label="SPEAKER_SELF" if source == "audio" else None,
                confidence=1.0 if source == "audio" else None,
            ),
            metadata={
                "intent": response.intent.intent.value,
                "action": response.command.action.value,
                "target": response.command.target,
                "identified_user_id": user.id if source == "audio" else None,
                "identified_user_name": user.name if source == "audio" else None,
                "identity_confidence": 1.0 if source == "audio" else None,
                "identity_source": "authenticated" if source == "audio" else None,
            },
        )
        if response.response_text:
            self.add_agent_message(
                room_id,
                response.response_text,
                metadata={
                    "intent": response.intent.intent.value,
                    "action": response.command.action.value,
                    **(agent_metadata or {}),
                },
            )
        if response.orchestrator_result:
            self.add_action(room_id, user, response.orchestrator_result)

    def build_handoff(
        self,
        room_id: str,
        user: UserPublic,
        *,
        since: datetime | None = None,
    ) -> HandoffSummary:
        messages = [
            message
            for message in self._store.list_messages(room_id, limit=500)
            if _after(message.created_at, since) and message.actor_id != user.id
        ]
        actions = [
            action
            for action in self._store.list_actions(room_id, limit=250)
            if _after(_action_activity_at(action), since)
        ]
        memory = [
            item
            for item in self._store.list_memory(room_id, limit=250)
            if _after(item.created_at, since)
        ]
        open_memory = [
            item
            for item in memory
            if item.kind in {MemoryKind.QUESTION, MemoryKind.TASK, MemoryKind.RISK}
            and item.status == "open"
        ]
        audit_events = [
            *_audit_events_from_actions(actions),
            *_audit_events_from_messages(messages),
        ]
        audit_events.sort(key=lambda event: event.created_at, reverse=True)

        lines: list[str] = []
        for action in actions[-5:]:
            lines.append(_handoff_action_line(action, self._agent_display_name))
        for event in [event for event in audit_events if event.kind != "action"][:5]:
            line = _handoff_audit_event_line(event)
            if line and line not in lines:
                lines.append(line)
        for message in messages[-5:]:
            if message.role == MessageRole.SYSTEM:
                continue
            line = f"{_message_actor_label(message)}: {message.text}"
            if line not in lines:
                lines.append(line)
        for item in memory[-6:]:
            if item.source_action_id:
                continue
            line = f"{item.actor_name} noted {_memory_label(item.kind)}: {item.text}"
            if line not in lines:
                lines.append(line)
        for item in open_memory[-3:]:
            line = f"Open {_memory_label(item.kind)} from {item.actor_name}: {item.text}"
            if line not in lines:
                lines.append(line)
        if not lines:
            lines.append("No teammate activity to catch up on yet.")

        open_questions = [
            action.summary
            for action in actions
            if action.pending_approval or action.status == "pending_approval"
        ]
        open_questions.extend(item.text for item in open_memory)
        open_questions = open_questions[-3:]
        open_review_items = _handoff_review_items(actions, open_memory)[-6:]

        return HandoffSummary(
            room_id=room_id,
            user_id=user.id,
            since=since,
            generated_at=_now(),
            message_count=len(messages),
            action_count=len(actions),
            lines=lines,
            open_questions=open_questions,
            open_review_items=open_review_items,
            audit_events=audit_events[:8],
        )

    def resolve_recent_task_context(self, room_id: str, prompt: str) -> dict | None:
        lower = prompt.lower()
        if "that" not in lower and "这个" not in prompt and "那个" not in prompt:
            return None
        candidates = [
            item
            for item in self._store.list_memory(room_id, limit=80)
            if item.kind in {MemoryKind.TASK, MemoryKind.RISK, MemoryKind.QUESTION, MemoryKind.CODE_REFERENCE}
        ]
        if candidates:
            item = candidates[-1]
            return {
                "type": "memory",
                "id": item.id,
                "kind": item.kind.value,
                "text": item.metadata.get("context") or item.text,
                "actor_name": item.actor_name,
            }
        messages = [
            message
            for message in self._store.list_messages(room_id, limit=40)
            if message.role == MessageRole.USER and message.text.strip()
        ]
        if not messages:
            return None
        message = messages[-1]
        return {
            "type": "message",
            "id": message.id,
            "text": message.text,
            "actor_name": message.actor_name,
        }

    def summarize_changes(self, room_id: str, question: str) -> str:
        actor = _requested_actor(question, self._store.list_participants(room_id))
        actions = self._store.list_actions(room_id, limit=120)
        memory = self._store.list_memory(room_id, limit=120)
        if actor:
            actions = [action for action in actions if actor.lower() in action.requested_by_name.lower()]
            memory = [item for item in memory if actor.lower() in item.actor_name.lower()]

        lines: list[str] = []
        for action in actions[-5:]:
            files = f" Files: {', '.join(action.files_changed)}." if action.files_changed else ""
            status = f" Status: {action.status}."
            lines.append(f"{action.requested_by_name}: {_action_label(action.action)}. {action.summary}{files}{status}")
        for item in memory[-5:]:
            if item.kind not in {MemoryKind.DECISION, MemoryKind.TASK, MemoryKind.CODE_REFERENCE}:
                continue
            line = f"{item.actor_name}: {_memory_label(item.kind)} - {item.text}"
            if line not in lines:
                lines.append(line)
        if not lines:
            target = f" for {actor}" if actor else ""
            return f"I do not have recorded changes{target} yet."
        target = f" for {actor}" if actor else ""
        return f"Recorded changes{target}: " + " | ".join(lines[:6])

    def _ensure_agent(self, room_id: str) -> None:
        existing = self._store.get_participant(room_id, AGENT_PARTICIPANT_ID)
        participant = Participant(
            id=AGENT_PARTICIPANT_ID,
            name=self._agent_display_name,
            initials=self._agent_initials,
            kind=ParticipantKind.AGENT,
            role_label="AI teammate",
            online=True,
            joined_at=existing.joined_at if existing else _now(),
            last_seen_at=_now(),
        )
        self._store.upsert_participant(room_id, participant)

    def _capture_memory_from_message(self, message: TimelineMessage) -> None:
        if not message.text.strip():
            return
        for kind, text, status, metadata in _extract_memory(message.text):
            self._store.append_memory(
                MemoryItem(
                    id=_id("mem"),
                    room_id=message.room_id,
                    kind=kind,
                    text=text,
                    actor_id=message.actor_id,
                    actor_name=message.actor_name,
                    source_message_id=message.id,
                    status=status,
                    metadata={
                        "source": message.source,
                        "speaker_label": message.speaker_label,
                        **metadata,
                    },
                    created_at=message.created_at,
                )
            )

    def _capture_memory_from_action(self, action: AgentAction) -> None:
        if not action.summary.strip():
            return
        status = "open" if action.pending_approval or action.status == "pending_approval" else "noted"
        self._store.append_memory(
            MemoryItem(
                id=_id("mem"),
                room_id=action.room_id,
                kind=MemoryKind.TASK,
                text=action.summary,
                actor_id=action.requested_by,
                actor_name=action.requested_by_name,
                source_action_id=action.id,
                status=status,
                metadata={
                    "action": str(action.action.value if isinstance(action.action, AgentActionType) else action.action),
                    "files_changed": action.files_changed,
                    "approval": action.approval,
                },
                created_at=action.created_at,
            )
        )


def _audit_events_from_actions(actions: list[AgentAction]) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    for action in actions:
        if not _audit_should_show_action(action):
            continue
        approval = action.approval or {}
        git = approval.get("git") if isinstance(approval.get("git"), dict) else {}
        status = action.status or ("pending_approval" if action.pending_approval else "completed")
        files = action.files_changed or git.get("files_changed") or []
        branch = git.get("branch_name") or git.get("branch_created") or ""
        commit = git.get("commit_sha") or ""
        chips = [
            f"branch {branch}" if branch else "",
            f"commit {commit}" if commit else "",
            f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "",
            f"test {approval.get('test_command')}" if approval.get("test_command") else "",
        ]
        events.append(
            AuditEvent(
                id=f"action:{action.id}:{status}:{commit or branch}",
                kind="action",
                tone=_audit_tone(status),
                title=_audit_action_title(action.action),
                status=_audit_label_status(status),
                detail=action.summary or "Agent action recorded",
                actor_name=action.requested_by_name,
                created_at=_action_activity_at(action),
                source_id=action.id,
                chips=[chip for chip in chips if chip],
                metadata={
                    "action": str(action.action.value if isinstance(action.action, AgentActionType) else action.action),
                    "pending_approval": action.pending_approval,
                    "files_changed": files,
                    "approval_status": approval.get("status"),
                    "decided_by_name": approval.get("decided_by_name"),
                    "decided_by": approval.get("decided_by"),
                    "test_command": approval.get("test_command"),
                    "route_trace": approval.get("route_trace"),
                    "git": git,
                },
            )
        )
    return events


def _audit_events_from_messages(messages: list[TimelineMessage]) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    for message in messages:
        metadata = message.metadata or {}
        source = metadata.get("source")
        event_name = str(metadata.get("event", ""))
        if source == "demo_gate_result":
            events.append(_audit_demo_gate_result_event(message, metadata))
        elif source == "demo_gate_run":
            events.append(_audit_demo_gate_run_event(message, metadata))
        elif source == "speaker_mapping" or event_name.startswith("speaker_mapping_"):
            events.append(_audit_speaker_mapping_event(message, metadata))
        elif source in {"action_approval", "action_commit", "action_pull_request"}:
            events.append(_audit_action_message_event(message, metadata))
    return events


def _audit_should_show_action(action: AgentAction) -> bool:
    return (
        action.pending_approval
        or action.status in {"completed", "failed", "rejected", "pending_approval"}
        or bool((action.approval or {}).get("git"))
    )


def _audit_demo_gate_result_event(message: TimelineMessage, metadata: dict) -> AuditEvent:
    status = metadata.get("gate_status") or metadata.get("evidence_status") or "finished"
    passed = status == "succeeded" or metadata.get("evidence_status") == "passed"
    failed = status == "failed" or metadata.get("evidence_status") == "failed"
    labels = metadata.get("speaker_labels") if isinstance(metadata.get("speaker_labels"), list) else []
    return AuditEvent(
        id=f"message:{message.id}",
        kind="gate",
        tone="ok" if passed else "warn" if failed else "info",
        title="Demo gate result",
        status="passed" if passed else "failed" if failed else _audit_label_status(str(status)),
        detail=str(metadata.get("error") or metadata.get("detail") or message.text or "Demo gate finished"),
        actor_name=message.actor_name,
        created_at=message.created_at,
        source_id=message.id,
        chips=[
            chip
            for chip in [
                str(metadata.get("gate_label") or ""),
                str(metadata.get("gate_job_id") or ""),
                f"{len(labels)} labels" if labels else "",
                _audit_chunk_chip(metadata),
            ]
            if chip
        ],
        metadata=metadata,
    )


def _audit_demo_gate_run_event(message: TimelineMessage, metadata: dict) -> AuditEvent:
    return AuditEvent(
        id=f"message:{message.id}",
        kind="gate",
        tone="warn" if metadata.get("error") else "info",
        title="Demo gate started",
        status=_audit_label_status(str(metadata.get("gate_status") or "running")),
        detail=message.text or str(metadata.get("gate_label") or "Demo gate started"),
        actor_name=message.actor_name,
        created_at=message.created_at,
        source_id=message.id,
        chips=[str(item) for item in (metadata.get("gate_label"), metadata.get("gate_job_id")) if item],
        metadata=metadata,
    )


def _audit_speaker_mapping_event(message: TimelineMessage, metadata: dict) -> AuditEvent:
    return AuditEvent(
        id=f"message:{message.id}",
        kind="speaker",
        tone="info",
        title="Speaker mapping",
        status=_audit_mapping_status(str(metadata.get("event") or "")),
        detail=_audit_speaker_mapping_detail(metadata),
        actor_name=str(metadata.get("actor_name") or message.actor_name),
        created_at=message.created_at,
        source_id=message.id,
        chips=[
            str(item)
            for item in (
                metadata.get("speaker_label"),
                metadata.get("mapped_user_name") or metadata.get("identified_user_name") or metadata.get("user_name"),
                metadata.get("mapping_source"),
            )
            if item
        ],
        metadata=metadata,
    )


def _audit_action_message_event(message: TimelineMessage, metadata: dict) -> AuditEvent:
    source = metadata.get("source")
    status = str(metadata.get("status") or source or "recorded")
    title = "GitHub pull request" if source == "action_pull_request" else "Git commit" if source == "action_commit" else "Approval decision"
    return AuditEvent(
        id=f"message:{message.id}",
        kind="action",
        tone=_audit_tone(status),
        title=title,
        status=_audit_label_status(status),
        detail=message.text or "Action audit recorded",
        actor_name=message.actor_name,
        created_at=message.created_at,
        source_id=message.id,
        chips=[
            str(item)
            for item in (
                metadata.get("action_id"),
                metadata.get("commit_sha"),
                metadata.get("pull_request_number"),
                metadata.get("pull_request_url"),
            )
            if item
        ],
        metadata=metadata,
    )


def _audit_chunk_chip(metadata: dict) -> str:
    completed = int(metadata.get("completed_chunks") or 0)
    requested = int(metadata.get("requested_chunks") or 0)
    if completed > 0 and requested > 0:
        return f"{completed}/{requested} chunks"
    if completed > 0:
        return f"{completed} chunks"
    return ""


def _audit_mapping_status(event: str) -> str:
    if event == "speaker_mapping_corrected":
        return "corrected"
    if event == "speaker_mapping_confirmed":
        return "confirmed"
    return "mapped"


def _audit_speaker_mapping_detail(metadata: dict) -> str:
    label = str(metadata.get("speaker_label") or "speaker")
    user = str(
        metadata.get("mapped_user_name")
        or metadata.get("identified_user_name")
        or metadata.get("user_name")
        or "teammate"
    )
    previous = metadata.get("previous_user_name")
    if metadata.get("event") == "speaker_mapping_corrected" and previous:
        return f"{label} reassigned from {previous} to {user}"
    if metadata.get("event") == "speaker_mapping_confirmed":
        return f"{label} confirmed as {user}"
    return f"{label} mapped to {user}"


def _audit_action_title(action: AgentActionType | str) -> str:
    labels = {
        "patch": "Patch proposal",
        "test": "Test run",
        "verify": "Verification gate",
        "rollback": "Rollback request",
        "deploy": "Deploy request",
        "create_pr": "PR request",
    }
    value = action.value if isinstance(action, AgentActionType) else str(action)
    return labels.get(value, f"{value.replace('_', ' ').title()} action")


def _audit_tone(status: str) -> str:
    if status in {"completed", "succeeded", "approved", "passed", "action_commit"}:
        return "ok"
    if status in {"failed", "rejected"}:
        return "warn"
    if status == "pending_approval":
        return "accent"
    return "info"


def _audit_label_status(status: str) -> str:
    return str(status or "recorded").replace("_", " ")


def _infer_audit_kind(question: str) -> str | None:
    lower = question.lower()
    if any(word in lower for word in ("speaker", "voice", "mapping", "mapped", "diarization", "识别", "说话")):
        return "speaker"
    if any(word in lower for word in ("gate", "demo", "readiness", "verification", "verified", "e2e")):
        return "gate"
    if any(word in lower for word in ("approve", "approved", "reject", "rejected", "patch", "commit", "branch", "git", "test", "action")):
        return "action"
    return None


def _infer_audit_status(question: str) -> str | None:
    lower = question.lower()
    if any(word in lower for word in ("pending", "waiting", "awaiting")):
        return "pending approval"
    if any(word in lower for word in ("failed", "fail", "broken")):
        return "failed"
    if any(word in lower for word in ("rejected", "reject")):
        return "rejected"
    if any(word in lower for word in ("passed", "succeeded", "success")):
        return "passed"
    return None


def _is_audit_approval_question(question: str) -> bool:
    lower = question.lower()
    return any(marker in lower for marker in ("who approved", "approved", "approve", "谁批准"))


def _audit_score(event: AuditEvent, terms: list[str]) -> int:
    if not terms:
        return 1
    haystack = " ".join(
        [
            event.kind,
            event.title,
            event.status,
            event.detail,
            event.actor_name or "",
            " ".join(event.chips),
            " ".join(str(value) for value in event.metadata.values()),
        ]
    ).lower()
    return sum(1 for term in terms if term in haystack)


def _audit_answer(
    question: str,
    events: list[AuditEvent],
    *,
    kind: str | None,
    status: str | None,
) -> str:
    if not events:
        target = f" {status}" if status else ""
        subject = f" {kind}" if kind else ""
        return f"I do not have any{target}{subject} audit events in this room yet."

    lower = question.lower()
    if kind == "action" and any(word in lower for word in ("who approved", "approved", "approve")):
        approvals = [
            event
            for event in events
            if event.metadata.get("decided_by_name") or event.metadata.get("approval_status") == "approved"
        ]
        if approvals:
            lines = []
            for event in approvals[:5]:
                approver = event.metadata.get("decided_by_name") or "A teammate"
                branch = (event.metadata.get("git") or {}).get("branch_name")
                branch_part = f" on {branch}" if branch else ""
                lines.append(f"{approver} approved {event.title.lower()}{branch_part}")
            return "Approvals: " + "; ".join(lines) + "."

    if kind == "gate":
        gate_events = [event for event in events if event.kind == "gate"]
        return "Demo gates: " + "; ".join(
            f"{event.chips[0] if event.chips else event.title} {event.status}"
            for event in gate_events[:6]
        ) + "."

    if kind == "speaker":
        return "Speaker audit: " + "; ".join(event.detail for event in events[:6]) + "."

    return "Recent audit: " + "; ".join(
        f"{event.title} {event.status}: {event.detail}" for event in events[:6]
    ) + "."


def _trace_event_from_store_row(row: dict) -> RoomTraceEvent:
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    event_type = str(row.get("event_type") or "event")
    stream_type = str(row.get("stream_type") or "event_store")
    action_id = data.get("id") if stream_type == "action" else data.get("action_id")
    actor_name = data.get("actor_name") or data.get("requested_by_name") or metadata.get("actor_name")
    status = data.get("status") or metadata.get("status")
    summary_target = data.get("summary") or data.get("text") or data.get("name") or row.get("stream_id")
    return RoomTraceEvent(
        sequence=0,
        kind="event_store",
        event_type=event_type,
        summary=_truncate(f"{event_type}: {summary_target}", 180),
        actor_name=str(actor_name) if actor_name else None,
        action_id=str(action_id) if action_id else None,
        source_id=str(row.get("stream_id") or ""),
        source=stream_type,
        status=str(status) if status else None,
        created_at=row.get("created_at"),
        event_position=row.get("global_position"),
        metadata={
            "stream_type": stream_type,
            "stream_id": row.get("stream_id"),
            "room_id": row.get("room_id"),
            "metadata": metadata,
        },
    )


def _trace_event_from_message(message: TimelineMessage) -> RoomTraceEvent:
    metadata = message.metadata or {}
    source = str(metadata.get("source") or message.source or "timeline")
    if message.speaker_label:
        event_type = "speaker.segment"
    elif message.role == MessageRole.AGENT:
        event_type = "agent.message"
    elif message.role == MessageRole.SYSTEM:
        event_type = "system.message"
    else:
        event_type = "message.appended"
    return RoomTraceEvent(
        sequence=0,
        kind="timeline",
        event_type=event_type,
        summary=_truncate(f"{message.actor_name}: {message.text}", 180),
        actor_name=message.actor_name,
        action_id=_trace_action_id(metadata),
        source_id=message.id,
        source=source,
        status=str(metadata.get("status")) if metadata.get("status") else None,
        created_at=message.created_at,
        metadata={
            "role": message.role.value if isinstance(message.role, MessageRole) else str(message.role),
            "speaker_label": message.speaker_label,
            "confidence": message.confidence,
            "metadata": metadata,
        },
    )


def _trace_event_from_memory(item: MemoryItem) -> RoomTraceEvent:
    kind = item.kind.value if isinstance(item.kind, MemoryKind) else str(item.kind)
    return RoomTraceEvent(
        sequence=0,
        kind="memory",
        event_type=f"memory.{kind}",
        summary=_truncate(f"{item.actor_name} recorded {kind}: {item.text}", 180),
        actor_name=item.actor_name,
        action_id=item.source_action_id,
        source_id=item.id,
        source="memory",
        status=item.status,
        created_at=item.created_at,
        metadata={
            "source_message_id": item.source_message_id,
            "source_action_id": item.source_action_id,
            "metadata": item.metadata,
        },
    )


def _trace_events_from_action(action: AgentAction) -> list[RoomTraceEvent]:
    approval = action.approval or {}
    git = approval.get("git") if isinstance(approval.get("git"), dict) else {}
    action_type = _action_value(action)
    events = [
        RoomTraceEvent(
            sequence=0,
            kind="action",
            event_type=f"action.{action_type}",
            summary=_truncate(action.summary or "Agent action recorded", 180),
            actor_name=action.requested_by_name,
            action_id=action.id,
            source_id=action.id,
            source="agent_action",
            status=action.status,
            created_at=_action_activity_at(action),
            metadata={
                "requested_by": action.requested_by,
                "files_changed": action.files_changed,
                "pending_approval": action.pending_approval,
                "approval_status": approval.get("status"),
                "route_trace": approval.get("route_trace"),
            },
        )
    ]
    if action.pending_approval or action.status == "pending_approval" or approval.get("diff"):
        events.append(
            RoomTraceEvent(
                sequence=0,
                kind="approval",
                event_type="approval.patch_proposed",
                summary=_truncate(f"Patch proposal recorded for {', '.join(action.files_changed) or action.summary}", 180),
                actor_name=action.requested_by_name,
                action_id=action.id,
                source_id=action.id,
                source="approval",
                status="pending_approval" if action.pending_approval else action.status,
                created_at=action.created_at,
                metadata={
                    "diff_present": bool(approval.get("diff")),
                    "test_command": approval.get("test_command"),
                    "files_changed": action.files_changed,
                },
            )
        )
    if approval.get("status") in {"approved", "rejected", "failed"} or approval.get("decided_by_name"):
        events.append(
            RoomTraceEvent(
                sequence=0,
                kind="approval",
                event_type="approval.decision",
                summary=_truncate(
                    f"{approval.get('decided_by_name') or 'A teammate'} {approval.get('status') or action.status} {action.id}",
                    180,
                ),
                actor_name=approval.get("decided_by_name"),
                action_id=action.id,
                source_id=action.id,
                source="approval",
                status=approval.get("status") or action.status,
                created_at=action.updated_at or action.created_at,
                metadata={
                    "decided_by": approval.get("decided_by"),
                    "decided_at": approval.get("decided_at"),
                    "note": approval.get("note"),
                },
            )
        )
    if git.get("branch_name"):
        events.append(
            RoomTraceEvent(
                sequence=0,
                kind="git",
                event_type="git.branch_created",
                summary=f"Local branch {git['branch_name']} captured for {action.id}",
                actor_name=approval.get("decided_by_name"),
                action_id=action.id,
                source_id=action.id,
                source="git",
                status=action.status,
                created_at=action.updated_at or action.created_at,
                metadata={
                    "branch_name": git.get("branch_name"),
                    "previous_branch": git.get("previous_branch"),
                    "dirty_before": git.get("dirty_before"),
                    "dirty_after": git.get("dirty_after"),
                    "files_changed": git.get("files_changed"),
                },
            )
        )
    return events


def _trace_event_from_handoff(handoff: HandoffSummary) -> RoomTraceEvent:
    return RoomTraceEvent(
        sequence=0,
        kind="handoff",
        event_type="handoff.generated",
        summary=_truncate(" | ".join(handoff.lines[:4]) or "No handoff activity recorded.", 220),
        actor_name=None,
        source_id=handoff.room_id,
        source="handoff",
        status="ready" if handoff.lines else "empty",
        created_at=handoff.generated_at,
        metadata={
            "message_count": handoff.message_count,
            "action_count": handoff.action_count,
            "open_review_items": len(handoff.open_review_items),
        },
    )


def _trace_coverage(
    *,
    messages: list[TimelineMessage],
    actions: list[AgentAction],
    memory: list[MemoryItem],
    handoff: HandoffSummary,
    event_rows: list[dict],
) -> dict[str, bool]:
    return {
        "speaker_segments": any(message.speaker_label or message.metadata.get("speaker_label") for message in messages),
        "memory_items": bool(memory),
        "patch_proposal": any(
            _action_value(action) == AgentActionType.PATCH.value
            and (action.pending_approval or action.status == "pending_approval" or bool((action.approval or {}).get("diff")))
            for action in actions
        ),
        "approval_decision": any(
            (action.approval or {}).get("status") in {"approved", "rejected", "failed"}
            or bool((action.approval or {}).get("decided_by_name"))
            for action in actions
        ),
        "git_branch": any(
            isinstance((action.approval or {}).get("git"), dict)
            and bool((action.approval or {})["git"].get("branch_name"))
            for action in actions
        ),
        "handoff_summary": bool(handoff.lines),
        "event_store": bool(event_rows),
    }


def _trace_sort_key(event: RoomTraceEvent) -> tuple[datetime, int, str]:
    created_at = event.created_at or datetime.min.replace(tzinfo=timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (created_at, event.event_position or 0, event.source_id or "")


def _trace_action_id(metadata: dict) -> str | None:
    value = metadata.get("action_id")
    return str(value) if value else None


def _system_user() -> UserPublic:
    return UserPublic(
        id="system-trace",
        email="system-trace@example.com",
        name="System trace",
        initials="ST",
        role="viewer",
        role_label="System",
        permissions=["dashboard:view"],
    )


def looks_like_audit_question(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "who approved",
            "approved",
            "rejected",
            "pending approval",
            "patch status",
            "what changed",
            "which branch",
            "git branch",
            "demo gate",
            "gate passed",
            "readiness gate",
            "speaker mapping",
            "audit",
        )
    ) or any(marker in text for marker in ("谁批准", "审计", "哪个分支", "语音映射"))


def _archive_long_memory(room_id: str, collab: CollaborationService, settings: Settings) -> None:
    try:
        from app.long_memory.service import LongMemoryService
        from app.long_memory.store import LongMemoryStore

        LongMemoryService(LongMemoryStore(settings.long_memory_path), collab).archive_room(room_id)
    except Exception:
        return


def _validated_room_workspace_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        raise ValueError("Room workspace path is required.")
    if configured_workspace_is_url(value):
        raise ValueError("Room workspace must be a local clone path, not a git remote URL.")
    root = resolve_configured_workspace(value)
    if root is None:
        raise ValueError("Room workspace path must be an existing local directory.")
    return str(Path(root).resolve())


def _user_can_access_project(user: UserPublic, project: str | None) -> bool:
    allowed = _user_project_scope(user)
    if allowed:
        return bool(project) and project in allowed
    return True


def _user_project_scope(user: UserPublic) -> set[str]:
    return {item.strip() for item in getattr(user, "projects", []) if item.strip()}


@lru_cache
def get_collaboration_service() -> CollaborationService:
    settings = get_settings()
    return CollaborationService(
        create_collaboration_store(settings),
        agent_display_name=settings.agent_display_name,
        agent_initials=settings.agent_initials,
        agent_wake_words=settings.agent_wake_words,
    )


def _participant_from_user(
    user: UserPublic,
    *,
    existing: Participant | None = None,
    online: bool,
) -> Participant:
    now = _now()
    return Participant(
        id=user.id,
        name=user.name,
        initials=user.initials,
        role_label=user.role_label,
        online=online,
        joined_at=existing.joined_at if existing else now,
        last_seen_at=now,
    )


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _initials(name: str) -> str:
    words = [word for word in re.findall(r"[\w]+", name) if word]
    if not words:
        return "AI"
    if len(words) == 1:
        return words[0][:2]
    return "".join(word[0] for word in words[:2])


def _normalize_initials(initials: str | None, display_name: str) -> str:
    value = (initials or _initials(display_name)).strip().upper()
    value = re.sub(r"[^A-Z0-9]", "", value)
    if not value:
        value = _initials(display_name).upper()
    return value[:4] or "AI"


def _normalize_wake_words(wake_words: str | list[str] | None, display_name: str) -> list[str]:
    if isinstance(wake_words, str):
        raw_words = wake_words.split(",")
    else:
        raw_words = wake_words or []
    raw_words = [*raw_words, display_name, "assistant", "agent"]
    seen: set[str] = set()
    words: list[str] = []
    for word in raw_words:
        clean = " ".join(str(word).strip().split())
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        words.append(clean[:40])
    return words[:12]


def _action_label(action: AgentActionType | str) -> str:
    value = action.value if isinstance(action, AgentActionType) else str(action)
    return value.replace("_", " ")


def _memory_label(kind: MemoryKind) -> str:
    return kind.value.replace("_", " ")


def _handoff_review_items(actions: list[AgentAction], open_memory: list[MemoryItem]) -> list[HandoffReviewItem]:
    items: list[HandoffReviewItem] = []
    for action in actions:
        if not (action.pending_approval or action.status == "pending_approval"):
            continue
        approval = action.approval or {}
        route_trace = approval.get("route_trace")
        items.append(
            HandoffReviewItem(
                id=f"action:{action.id}",
                kind="approval",
                status="pending_approval",
                title=f"{_action_label(action.action).title()} awaiting approval",
                detail=action.summary,
                actor_name=action.requested_by_name,
                created_at=_action_activity_at(action),
                action_id=action.id,
                route_trace=route_trace if isinstance(route_trace, dict) else None,
            )
        )
    for item in open_memory:
        items.append(
            HandoffReviewItem(
                id=f"memory:{item.id}",
                kind=item.kind.value,
                status=item.status,
                title=f"Open {_memory_label(item.kind)}",
                detail=item.text,
                actor_name=item.actor_name,
                created_at=item.created_at,
                memory_id=item.id,
            )
        )
    items.sort(key=lambda item: item.created_at)
    return items


def _requested_actor(question: str, participants: list[Participant]) -> str | None:
    lower = question.lower()
    for participant in participants:
        if participant.kind != ParticipantKind.HUMAN:
            continue
        if participant.name.lower() in lower:
            return participant.name
        first = participant.name.split()[0]
        if first and first.lower() in lower:
            return participant.name
    match = re.search(r"\b([A-Z][a-z]+)\b", question)
    return match.group(1) if match else None


def _speaker_mapping_audit_messages(messages: list[TimelineMessage]) -> list[TimelineMessage]:
    return [
        message
        for message in messages
        if str(message.metadata.get("event", "")).startswith("speaker_mapping_")
        or message.metadata.get("source") == "speaker_mapping"
    ]


def _message_actor_label(message: TimelineMessage) -> str:
    label = message.actor_name
    speaker_label = message.speaker_label or message.metadata.get("speaker_label")
    if label == "Unknown speaker" and speaker_label:
        label = f"Unknown speaker ({speaker_label})"
    confidence = message.confidence
    if confidence is not None and speaker_label:
        label = f"{label} [{round(confidence * 100)}%]"
    return label


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _after(value: datetime, since: datetime | None) -> bool:
    return since is None or value > since


def _action_activity_at(action: AgentAction) -> datetime:
    return action.updated_at or action.created_at


def _handoff_action_line(action: AgentAction, agent_name: str) -> str:
    approval = action.approval or {}
    git = approval.get("git") if isinstance(approval.get("git"), dict) else {}
    parts = [
        f"{action.requested_by_name} asked {agent_name} to {_action_label(action.action)}.",
        action.summary,
    ]
    if action.files_changed:
        parts.append(f"Changed: {', '.join(action.files_changed)}.")
    if action.status == "pending_approval" or action.pending_approval:
        parts.append("Waiting for approval.")
    elif approval.get("status") == "approved":
        approver = approval.get("decided_by_name")
        parts.append(f"Approved by {approver}." if approver else "Approved.")
    elif approval.get("status") == "rejected":
        rejecter = approval.get("decided_by_name")
        parts.append(f"Rejected by {rejecter}." if rejecter else "Rejected.")
    if git.get("branch_name"):
        parts.append(f"Branch: {git['branch_name']}.")
    if git.get("commit_sha"):
        committer = git.get("committed_by_name")
        commit = f"Commit: {git['commit_sha']}."
        parts.append(f"{commit} Committed by {committer}." if committer else commit)
    pull_request = approval.get("pull_request") if isinstance(approval.get("pull_request"), dict) else {}
    if pull_request.get("url"):
        parts.append(f"PR: {pull_request['url']}.")
    route_line = _route_trace_handoff_line(approval.get("route_trace"))
    if route_line:
        parts.append(route_line)
    return " ".join(part for part in parts if part)


def _route_trace_handoff_line(route_trace: object) -> str:
    if not isinstance(route_trace, dict):
        return ""
    route = str(route_trace.get("route") or "").replace("_", " ")
    policy = str(route_trace.get("action_policy") or "").replace("_", " ")
    if not route and not policy:
        return ""
    reason = str(route_trace.get("reason") or "").strip()
    base = f"Route: {route}"
    if policy:
        base = f"{base} ({policy})"
    if reason:
        return f"{base} because {reason}"
    return f"{base}."


def _handoff_audit_event_line(event: AuditEvent) -> str:
    chips = [chip for chip in event.chips if chip]
    suffix = f" ({', '.join(chips[:2])})" if chips else ""
    if event.kind == "speaker":
        return _handoff_speaker_audit_event_line(event)
    if event.kind == "gate":
        return f"{event.title}: {event.status}. {event.detail}{suffix}."
    return f"{event.title}: {event.status}. {event.detail}{suffix}."


def _handoff_speaker_audit_event_line(event: AuditEvent) -> str:
    metadata = event.metadata or {}
    actor = str(metadata.get("actor_name") or event.actor_name or "A teammate")
    label = str(metadata.get("speaker_label") or "speaker")
    user = str(
        metadata.get("mapped_user_name")
        or metadata.get("identified_user_name")
        or metadata.get("user_name")
        or "teammate"
    )
    previous = metadata.get("previous_user_name")
    if metadata.get("event") == "speaker_mapping_corrected" and previous:
        return f"Speaker identity: {actor} corrected {label} from {previous} to {user}."
    if metadata.get("event") == "speaker_mapping_confirmed":
        return f"Speaker identity: {actor} confirmed {label} as {user}."
    return f"Speaker identity: {actor} mapped {label} to {user}."


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _finalized_artifacts(artifacts: list[dict], status: str) -> list[dict]:
    subtitle_by_status = {
        "completed": "approved and applied",
        "failed": "approved; verification failed",
        "rejected": "rejected",
    }
    subtitle = subtitle_by_status.get(status)
    if not subtitle:
        return artifacts
    if not artifacts:
        return artifacts
    return [{**artifact, "subtitle": subtitle} for artifact in artifacts]


def _commit_files_for_action(action: AgentAction, git: dict) -> list[str]:
    files = action.files_changed or git.get("files_changed") or git.get("committed_files") or []
    return [str(path) for path in files if str(path).strip()]


def _extract_memory(text: str) -> list[tuple[MemoryKind, str, str, dict]]:
    clean = " ".join(text.strip().split())
    if not clean:
        return []
    lower = clean.lower()
    clipped = clean[:260]
    items: list[tuple[MemoryKind, str, str, dict]] = []

    if any(marker in lower for marker in ("we decided", "decision", "decided to", "let's go with", "we will use")):
        items.append((MemoryKind.DECISION, clipped, "noted", {}))
    if any(marker in lower for marker in ("todo", "to do", "follow up", "action item", "need to", "needs to", "please ", "can you")):
        items.append((MemoryKind.TASK, clipped, "open", {}))
    if "?" in clean or QUESTION_RE.search(clean):
        items.append((MemoryKind.QUESTION, clipped, "open", {}))
    if any(marker in lower for marker in ("risk", "blocker", "blocked", "concern", "failing", "broken", "regression")):
        items.append((MemoryKind.RISK, clipped, "open", {}))

    for match in CODE_REFERENCE_RE.finditer(clean):
        file_ref = _normalize_file_reference(match.group(0))
        items.append(
            (
                MemoryKind.CODE_REFERENCE,
                file_ref,
                "noted",
                {"context": clipped},
            )
        )
    return items


def _coerce_memory_kind(kind: MemoryKind | str) -> MemoryKind:
    if isinstance(kind, MemoryKind):
        return kind
    return MemoryKind(kind)


def _action_value(action: AgentAction) -> str:
    if isinstance(action.action, AgentActionType):
        return action.action.value
    return str(action.action)


def _dashboard_item_from_memory(item: MemoryItem) -> WorkDashboardItem:
    return WorkDashboardItem(
        id=item.id,
        kind=item.kind.value,
        title=_truncate(item.text, 96),
        status=item.status,
        actor_name=item.actor_name,
        created_at=item.created_at,
        detail=item.text,
        memory_id=item.id,
        metadata=item.metadata,
    )


def _dashboard_item_from_action(action: AgentAction) -> WorkDashboardItem:
    git = action.approval.get("git") if isinstance(action.approval, dict) else None
    branch = git.get("branch_name") if isinstance(git, dict) else None
    detail = action.summary
    if branch:
        detail = f"{detail} Branch: {branch}."
    return WorkDashboardItem(
        id=action.id,
        kind=_action_value(action),
        title=_truncate(action.summary, 96),
        status=action.status,
        actor_name=action.requested_by_name,
        created_at=action.updated_at or action.created_at,
        detail=detail,
        files=action.files_changed,
        action_id=action.id,
        metadata={
            "pending_approval": action.pending_approval,
            "approval_status": action.approval.get("status") if isinstance(action.approval, dict) else None,
            "approver": action.approval.get("decided_by_name") if isinstance(action.approval, dict) else None,
            "branch": branch,
        },
    )


def _query_terms(text: str) -> list[str]:
    words = re.findall(r"[\w./-]+", text.lower())
    stop = {
        "a",
        "an",
        "and",
        "are",
        "did",
        "do",
        "for",
        "is",
        "it",
        "of",
        "the",
        "to",
        "we",
        "what",
        "were",
        "with",
    }
    return [word for word in words if len(word) > 1 and word not in stop]


def _memory_score(item: MemoryItem, terms: list[str]) -> int:
    haystack = " ".join(
        [
            item.text,
            item.actor_name,
            item.kind.value,
            item.status,
            " ".join(str(value) for value in item.metadata.values()),
        ]
    ).lower()
    return sum(1 for term in terms if term in haystack)


def _memory_identity(item: MemoryItem) -> tuple[str, str, str, str | None, str | None]:
    text = " ".join(item.text.lower().split())
    return (item.kind.value, text, item.status.lower(), item.source_message_id, item.source_action_id)


def _rag_score(question: str, terms: list[str], *parts: str) -> int:
    haystack = " ".join(part for part in parts if part).lower()
    score = sum(2 for term in terms if term in haystack)
    lower_question = question.lower()
    for actor in ("alice", "bob", "sam", "priya"):
        if actor in lower_question and actor in haystack:
            score += 4
    if "approved" in lower_question or "approval" in lower_question:
        if "approved" in haystack or "approval" in haystack:
            score += 3
    if "file" in lower_question or "code" in lower_question:
        if CODE_REFERENCE_RE.search(haystack):
            score += 3
    return score


def _dedupe_rag_citations(citations: list[RagCitation]) -> list[RagCitation]:
    by_key: dict[tuple[str, str], RagCitation] = {}
    for citation in citations:
        key = (citation.source, citation.source_id)
        existing = by_key.get(key)
        if existing is None or citation.score > existing.score:
            by_key[key] = citation
    return list(by_key.values())


def _memory_item_citation(item: MemoryItem, *, score: int) -> RagCitation:
    return RagCitation(
        source="memory",
        source_id=item.id,
        title=f"{_memory_label(item.kind)} from {item.actor_name}",
        excerpt=item.text,
        actor_name=item.actor_name,
        created_at=item.created_at,
        score=score,
        metadata={
            "memory_tier": "short",
            "kind": item.kind.value,
            "status": item.status,
            "source_message_id": item.source_message_id,
            "source_action_id": item.source_action_id,
            **item.metadata,
        },
    )


def _scope_rag_citations(room_id: str, citations: list[RagCitation]) -> list[RagCitation]:
    return [
        citation.model_copy(
            update={
                "title": redact_sensitive_text(citation.title),
                "excerpt": redact_sensitive_text(citation.excerpt),
                "metadata": {
                    **redact_sensitive_value(citation.metadata),
                    "room_id": room_id,
                    "access_scope": "room",
                    "visibility": "room",
                    "trusted_as_instruction": False,
                    "instruction_like": _instruction_like_citation(citation),
                    "source_pointer": _citation_source_pointer(citation),
                }
            }
        )
        for citation in citations
    ]


def _citation_source_pointer(citation: RagCitation) -> dict[str, str | None]:
    pointer = {
        "source": citation.source,
        "source_id": citation.source_id,
        "message_id": None,
        "action_id": None,
        "path": None,
    }
    if citation.source == "timeline":
        pointer["message_id"] = citation.source_id
    elif citation.source == "action":
        pointer["action_id"] = citation.source_id
    elif citation.source == "memory":
        pointer["message_id"] = citation.metadata.get("source_message_id")
        pointer["action_id"] = citation.metadata.get("source_action_id")
    elif citation.source == "code":
        pointer["path"] = str(citation.metadata.get("path") or citation.source_id.split(":", 1)[0])
    return pointer


def _retrieval_access_policy(room_id: str) -> dict[str, str]:
    return {
        "room_id": room_id,
        "access_scope": "room",
        "visibility": "room",
        "policy": "authenticated_room_context",
    }


def _normalize_file_reference(value: str) -> str:
    ref = value.strip()
    if ref.startswith(("./", "a/", "b/")):
        ref = ref.split("/", 1)[1]
    return ref


def _rank_rag_citations(citations: list[RagCitation], *, limit: int) -> list[RagCitation]:
    deduped = _dedupe_rag_citations(citations)
    return sorted(deduped, key=lambda item: (item.score, _citation_timestamp(item)), reverse=True)[:limit]


def _ontology_citations(
    collab: CollaborationService,
    room_id: str,
    question: str,
    *,
    limit: int,
) -> list[RagCitation]:
    from app.ontology.service import OntologyService

    response = OntologyService(collab).query(room_id, question, limit=limit)
    citations: list[RagCitation] = []
    edge_counts: dict[str, int] = {}
    for edge in response.edges:
        edge_counts[edge.source] = edge_counts.get(edge.source, 0) + 1
        edge_counts[edge.target] = edge_counts.get(edge.target, 0) + 1
    for node in response.nodes:
        score = 80 + edge_counts.get(node.id, 0) * 5
        citations.append(
            RagCitation(
                source="ontology",
                source_id=node.id,
                title=f"{node.kind} {node.label}",
                excerpt=_ontology_excerpt(node.kind, node.label, response.edges, node.id),
                score=score,
                metadata={
                    "memory_tier": "ontology",
                    "kind": node.kind,
                    "relations": edge_counts.get(node.id, 0),
                    **node.metadata,
                },
            )
        )
    return citations


def _ontology_excerpt(kind: str, label: str, edges, node_id: str) -> str:
    relations = [
        edge.relation
        for edge in edges
        if edge.source == node_id or edge.target == node_id
    ]
    if relations:
        return f"{kind} {label} is linked by {', '.join(sorted(set(relations))[:4])}."
    return f"{kind} {label} is present in the room ontology."


def _citation_source_counts(citations: list[RagCitation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for citation in citations:
        counts[citation.source] = counts.get(citation.source, 0) + 1
    return counts


def _citation_safety_counts(citations: list[RagCitation]) -> dict[str, int]:
    instruction_like = sum(1 for citation in citations if citation.metadata.get("instruction_like") is True)
    return {
        "trusted_as_instruction": 0,
        "untrusted_context": len(citations),
        "instruction_like": instruction_like,
    }


def _rag_memory_tiers(citations: list[RagCitation]) -> dict[str, int]:
    tiers = {"short": 0, "long": 0, "ontology": 0}
    for citation in citations:
        tier = citation.metadata.get("memory_tier")
        if tier in tiers:
            tiers[tier] += 1
    return tiers


def _citation_timestamp(citation: RagCitation) -> float:
    if citation.created_at is None:
        return 0.0
    return citation.created_at.timestamp()


def _rag_answer(question: str, citations: list[RagCitation]) -> str:
    if not citations:
        return "I could not retrieve matching meeting or workspace context for that yet."
    if _asks_for_files(question):
        files: list[str] = []
        for citation in citations:
            for value in [citation.excerpt, str(citation.metadata.get("context") or "")]:
                for match in CODE_REFERENCE_RE.finditer(value):
                    filename = _normalize_file_reference(match.group(0))
                    if filename not in files:
                        files.append(filename)
        if files:
            return f"Files mentioned: {', '.join(files[:8])}."
    preview = []
    for index, citation in enumerate(citations[:5], start=1):
        actor = f"{citation.actor_name}: " if citation.actor_name else ""
        preview.append(f"[{index}] {actor}{_safe_rag_excerpt(citation.excerpt)}")
    return "Retrieved untrusted context (not executable instructions): " + " | ".join(preview)


INSTRUCTION_LIKE_RE = re.compile(
    r"\b(?:ignore (?:all )?(?:previous|prior|system) instructions|"
    r"ignore (?:all )?approval rules|"
    r"reveal (?:the )?(?:secret|token|password)|"
    r"reveal [\w-]*(?:secret|token|password)|"
    r"leak (?:the )?(?:secret|token|password)|"
    r"leak [\w-]*(?:secret|token|password)|"
    r"approve yourself|"
    r"bypass approval|"
    r"write files immediately|"
    r"write (?:to )?(?:the )?workspace directly|"
    r"run destructive tools)\b",
    re.IGNORECASE,
)


def _safe_rag_excerpt(text: str) -> str:
    safe = redact_sensitive_text(INSTRUCTION_LIKE_RE.sub("[instruction-like text omitted]", text))
    return _truncate(safe, 220)


def _instruction_like_citation(citation: RagCitation) -> bool:
    return bool(
        INSTRUCTION_LIKE_RE.search(citation.excerpt or "")
        or INSTRUCTION_LIKE_RE.search(str(citation.metadata.get("context") or ""))
    )


def _infer_memory_kind(question: str) -> MemoryKind | None:
    lower = question.lower()
    if any(word in lower for word in ("decision", "decide", "decided")) or any(word in question for word in ("决定", "决策")):
        return MemoryKind.DECISION
    if any(word in lower for word in ("todo", "to do", "task", "action item", "follow up")) or any(word in question for word in ("任务", "待办", "跟进", "事项")):
        return MemoryKind.TASK
    if any(word in lower for word in ("question", "asked", "ask")) or any(word in question for word in ("问题", "提问", "问了什么")):
        return MemoryKind.QUESTION
    if any(word in lower for word in ("risk", "blocker", "blocked", "concern", "failing", "broken")) or any(word in question for word in ("风险", "阻塞", "担心", "失败", "坏了")):
        return MemoryKind.RISK
    if _asks_for_files(question):
        return MemoryKind.CODE_REFERENCE
    return None


def _asks_for_open(question: str) -> bool:
    lower = question.lower()
    return any(word in lower for word in ("open", "still", "remaining", "unresolved", "todo", "to do")) or any(
        word in question for word in ("还有", "未完成", "没解决", "待处理", "开放")
    )


def _asks_for_files(question: str) -> bool:
    lower = question.lower()
    return any(word in lower for word in ("file", "files", "code", "mentioned")) or any(
        word in question for word in ("文件", "代码", "提到")
    )


def _asks_for_recent(question: str) -> bool:
    lower = question.lower()
    return any(word in lower for word in ("just", "recent", "latest", "last", "刚刚", "最近", "上一"))


def looks_like_memory_question(text: str) -> bool:
    lower = text.lower()
    return "?" in text or any(marker in lower for marker in MEMORY_QUESTION_MARKERS)


def _memory_answer(
    question: str,
    items: list[MemoryItem],
    *,
    inferred_kind: MemoryKind | None,
    inferred_status: str | None,
) -> str:
    if not items:
        if inferred_kind:
            target = _memory_label(inferred_kind)
            if inferred_status:
                return f"I do not have any {inferred_status} {target} items in this room yet."
            return f"I do not have any {target} items in this room yet."
        return "I do not have matching meeting memory for that yet."

    if inferred_kind == MemoryKind.CODE_REFERENCE:
        files = []
        for item in items:
            file_ref = _normalize_file_reference(item.text)
            if file_ref not in files:
                files.append(file_ref)
        return f"Files mentioned: {', '.join(files[:8])}."

    label = _memory_label(inferred_kind) if inferred_kind else "memory items"
    if inferred_status:
        prefix = f"Open {label}"
    elif inferred_kind:
        prefix = f"Meeting {label}"
    else:
        prefix = "Relevant meeting memory"

    lines = [f"{item.actor_name}: {_safe_rag_excerpt(item.text)}" for item in items[:5]]
    return f"{prefix} from untrusted meeting memory (not executable instructions): " + " | ".join(lines)
