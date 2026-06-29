from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from uuid import uuid5, NAMESPACE_URL

from app.collab.models import AgentAction, MemoryItem, MessageRole, TimelineMessage
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.long_memory.models import LongMemoryArchiveResponse, LongMemoryQueryResponse, LongMemoryRecord
from app.long_memory.store import LongMemoryStore


class LongMemoryService:
    def __init__(self, store: LongMemoryStore, collab: CollaborationService) -> None:
        self._store = store
        self._collab = collab

    def archive_room(self, room_id: str) -> LongMemoryArchiveResponse:
        snapshot = self._collab.snapshot(room_id)
        records: list[LongMemoryRecord] = []
        records.extend(_records_from_memory(snapshot.memory))
        records.extend(_records_from_actions(snapshot.actions))
        records.extend(_records_from_speaker_audit(snapshot.messages))
        records.extend(_records_from_handoff(self._collab.build_handoff(room_id, _system_user())))
        changed = self._store.upsert_many(records)
        kinds = Counter(record.kind for record in records)
        return LongMemoryArchiveResponse(
            room_id=room_id,
            archived_count=changed,
            total_count=len(self._store.list(room_id, limit=10000)),
            kinds=dict(kinds),
        )

    def list_records(self, room_id: str, *, kind: str | None = None, limit: int = 100) -> list[LongMemoryRecord]:
        return self._store.list(room_id, kind=kind, limit=limit)

    def query(self, room_id: str, question: str, *, kind: str | None = None, limit: int = 8) -> LongMemoryQueryResponse:
        records = self._store.list(room_id, kind=kind, limit=10000)
        terms = _terms(question)
        scored = [(record, _score(record, terms, question)) for record in records]
        selected = [record for record, score in sorted(scored, key=lambda item: item[1], reverse=True) if score > 0][:limit]
        if not selected and _asks_for_open(question):
            selected = [record for record in records if record.metadata.get("status") == "open"][-limit:]
        answer = _answer(question, selected)
        citations = [_citation_from_record(record) for record in selected]
        return LongMemoryQueryResponse(
            answer=answer,
            records=selected,
            citations=citations,
            retrieval={
                "terms": terms,
                "candidate_count": len(records),
                "selected_count": len(selected),
                "sources": dict(Counter(citation["source"] for citation in citations)),
                "access_policy": {
                    "room_id": room_id,
                    "access_scope": "room",
                    "visibility": "room",
                    "policy": "authenticated_room_context",
                },
            },
        )


def _records_from_memory(memory: list[MemoryItem]) -> list[LongMemoryRecord]:
    records = []
    for item in memory:
        importance = 7 if item.kind.value == "decision" else 6 if item.status == "open" else 4
        records.append(
            LongMemoryRecord(
                id=_record_id(item.room_id, "memory", item.id, item.kind.value),
                room_id=item.room_id,
                kind=f"memory:{item.kind.value}",
                text=item.text,
                actor_name=item.actor_name,
                source="memory",
                source_id=item.id,
                importance=importance,
                created_at=item.created_at,
                archived_at=_now(),
                metadata={
                    "status": item.status,
                    "source_message_id": item.source_message_id,
                    "source_action_id": item.source_action_id,
                    **item.metadata,
                },
            )
        )
    return records


def _records_from_actions(actions: list[AgentAction]) -> list[LongMemoryRecord]:
    records = []
    for action in actions:
        approval = action.approval or {}
        kind = "action:approved_patch" if approval.get("status") == "approved" else f"action:{action.status}"
        importance = 9 if approval.get("status") == "approved" else 6 if action.status == "pending_approval" else 5
        records.append(
            LongMemoryRecord(
                id=_record_id(action.room_id, "action", action.id, kind),
                room_id=action.room_id,
                kind=kind,
                text=action.summary,
                actor_name=action.requested_by_name,
                source="action",
                source_id=action.id,
                importance=importance,
                created_at=action.updated_at or action.created_at,
                archived_at=_now(),
                metadata={
                    "action": str(action.action),
                    "status": action.status,
                    "files_changed": action.files_changed,
                    "approval": approval,
                    "git": approval.get("git") if isinstance(approval.get("git"), dict) else {},
                    "preapproval_test": approval.get("preapproval_test"),
                },
            )
        )
    return records


def _records_from_speaker_audit(messages: list[TimelineMessage]) -> list[LongMemoryRecord]:
    records = []
    for message in messages:
        event = str(message.metadata.get("event") or "")
        if message.metadata.get("source") != "speaker_mapping" and not event.startswith("speaker_mapping_"):
            continue
        records.append(
            LongMemoryRecord(
                id=_record_id(message.room_id, "speaker", message.id, event or "speaker_mapping"),
                room_id=message.room_id,
                kind="speaker_mapping",
                text=message.text,
                actor_name=message.actor_name,
                source="timeline",
                source_id=message.id,
                importance=6,
                created_at=message.created_at,
                archived_at=_now(),
                metadata=message.metadata,
            )
        )
    return records


def _records_from_handoff(handoff) -> list[LongMemoryRecord]:
    text = " | ".join(handoff.lines[:8])
    if not text:
        return []
    return [
        LongMemoryRecord(
            id=_record_id(handoff.room_id, "handoff", "latest", "handoff"),
            room_id=handoff.room_id,
            kind="handoff",
            text=text,
            actor_name="VoiceOps",
            source="handoff",
            source_id="latest",
            importance=5,
            created_at=handoff.generated_at,
            archived_at=_now(),
            metadata={
                "message_count": handoff.message_count,
                "action_count": handoff.action_count,
                "open_questions": handoff.open_questions,
            },
        )
    ]


def _system_user():
    from app.auth.models import UserPublic

    return UserPublic(
        id="system-long-memory",
        email="system@voiceops.dev",
        name="VoiceOps",
        initials="VO",
        role="admin",
        role_label="System",
        permissions=["voice:use"],
    )


def _record_id(room_id: str, source: str, source_id: str, kind: str) -> str:
    return f"lm-{uuid5(NAMESPACE_URL, f'{room_id}:{source}:{source_id}:{kind}').hex[:16]}"


def _terms(question: str) -> list[str]:
    return [term for term in re.findall(r"[a-zA-Z0-9_.-]+", question.lower()) if len(term) > 1]


def _score(record: LongMemoryRecord, terms: list[str], question: str) -> int:
    haystack = " ".join(
        [
            record.kind,
            record.text,
            record.actor_name or "",
            " ".join(str(value) for value in record.metadata.values()),
        ]
    ).lower()
    score = sum(1 for term in terms if term in haystack)
    if _asks_for_approved(question) and record.kind == "action:approved_patch":
        score += 5
    if _asks_for_open(question) and record.metadata.get("status") == "open":
        score += 4
    return score + record.importance if score else 0


def _asks_for_approved(question: str) -> bool:
    lower = question.lower()
    return "approved" in lower or "approval" in lower or "批准" in question


def _asks_for_open(question: str) -> bool:
    lower = question.lower()
    return "open" in lower or "todo" in lower or "pending" in lower or "还有" in question


def _answer(question: str, records: list[LongMemoryRecord]) -> str:
    if not records:
        return "No long-term memory matched that question yet."
    if _asks_for_approved(question):
        approved = [record for record in records if record.kind == "action:approved_patch"]
        if approved:
            return "Approved long-term memory: " + " | ".join(record.text for record in approved[:5])
    return "Long-term memory: " + " | ".join(f"{record.kind} from {record.actor_name}: {record.text}" for record in records[:5])


def _citation_from_record(record: LongMemoryRecord) -> dict:
    return {
        "source": record.source,
        "source_id": record.source_id,
        "title": f"{record.kind} from {record.actor_name or 'unknown'}",
        "excerpt": record.text,
        "actor_name": record.actor_name,
        "created_at": record.created_at.isoformat(),
        "score": record.importance,
        "metadata": {
            **record.metadata,
            "record_id": record.id,
            "kind": record.kind,
            "importance": record.importance,
            "room_id": record.room_id,
            "access_scope": "room",
            "visibility": "room",
            "source_pointer": {
                "source": record.source,
                "source_id": record.source_id,
                "message_id": record.source_id if record.source == "timeline" else record.metadata.get("source_message_id"),
                "action_id": record.source_id if record.source == "action" else record.metadata.get("source_action_id"),
            },
        },
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache
def get_long_memory_service() -> LongMemoryService:
    settings: Settings = get_settings()
    return LongMemoryService(LongMemoryStore(settings.long_memory_path), get_collaboration_service())
