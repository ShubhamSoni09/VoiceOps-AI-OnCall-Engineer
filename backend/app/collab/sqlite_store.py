from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from app.collab.models import AgentAction, AgentSettings, MemoryItem, Participant, Room, TimelineMessage


class SQLiteCollaborationStore:
    """SQLite adapter for authoritative collaboration state."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._lock = RLock()
        self._init_schema()

    def get_agent_settings(self) -> AgentSettings | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM meta WHERE key = ?", ("agent_settings",)).fetchone()
        return AgentSettings.model_validate(_loads(row["data"])) if row else None

    def set_agent_settings(self, settings: AgentSettings) -> AgentSettings:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, data) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                ("agent_settings", _dumps(settings)),
            )
        return settings

    def get_room(self, room_id: str) -> Room | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT data FROM rooms WHERE id = ?", (room_id,)).fetchone()
            return Room.model_validate(_loads(row["data"])) if row else None

    def get_or_create_room(
        self,
        room_id: str,
        *,
        name: str | None = None,
        project: str | None = None,
        workspace_path: str | None = None,
    ) -> Room:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT data FROM rooms WHERE id = ?", (room_id,)).fetchone()
            now = _now()
            if row is None:
                room = Room(
                    id=room_id,
                    name=name or room_id,
                    project=project,
                    workspace_path=workspace_path,
                    created_at=now,
                    updated_at=now,
                )
                conn.execute("INSERT INTO rooms(id, data) VALUES(?, ?)", (room.id, _dumps(room)))
                return room
            room = Room.model_validate(_loads(row["data"]))
            if name or project is not None or workspace_path is not None:
                room = room.model_copy(
                    update={
                        "name": name or room.name,
                        "project": project if project is not None else room.project,
                        "workspace_path": workspace_path if workspace_path is not None else room.workspace_path,
                        "updated_at": now,
                    }
                )
                conn.execute("UPDATE rooms SET data = ? WHERE id = ?", (_dumps(room), room_id))
            return room

    def upsert_participant(self, room_id: str, participant: Participant) -> Participant:
        self.get_or_create_room(room_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO participants(room_id, participant_id, data)
                VALUES(?, ?, ?)
                ON CONFLICT(room_id, participant_id) DO UPDATE SET data = excluded.data
                """,
                (room_id, participant.id, _dumps(participant)),
            )
            self._record_event(
                conn,
                room_id=room_id,
                stream_type="participant",
                stream_id=f"{room_id}:participant:{participant.id}",
                event_type="participant.upserted",
                data=participant.model_dump(mode="json"),
            )
            self._touch_room(conn, room_id)
        return participant

    def get_participant(self, room_id: str, participant_id: str) -> Participant | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM participants WHERE room_id = ? AND participant_id = ?",
                (room_id, participant_id),
            ).fetchone()
        return Participant.model_validate(_loads(row["data"])) if row else None

    def list_participants(self, room_id: str) -> list[Participant]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM participants WHERE room_id = ? ORDER BY rowid",
                (room_id,),
            ).fetchall()
        return [Participant.model_validate(_loads(row["data"])) for row in rows]

    def append_message(self, message: TimelineMessage) -> TimelineMessage:
        self.get_or_create_room(message.room_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(room_id, message_id, data) VALUES(?, ?, ?)",
                (message.room_id, message.id, _dumps(message)),
            )
            self._record_event(
                conn,
                room_id=message.room_id,
                stream_type="message",
                stream_id=f"{message.room_id}:message:{message.id}",
                event_type="message.appended",
                data=message.model_dump(mode="json"),
            )
            self._touch_room(conn, message.room_id)
        return message

    def list_messages(self, room_id: str, *, limit: int = 100) -> list[TimelineMessage]:
        rows = self._list_ordered("messages", room_id, limit)
        return [TimelineMessage.model_validate(_loads(row["data"])) for row in rows]

    def reattribute_speaker_messages(
        self,
        room_id: str,
        *,
        speaker_label: str,
        actor_id: str,
        actor_name: str,
        actor_initials: str,
        identity_confidence: float | None,
        identity_source: str,
    ) -> int:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT rowid, data FROM messages WHERE room_id = ?", (room_id,)).fetchall()
            changed = 0
            for row in rows:
                message = TimelineMessage.model_validate(_loads(row["data"]))
                if message.speaker_label != speaker_label:
                    continue
                metadata = {
                    **message.metadata,
                    "identified_user_id": actor_id,
                    "identified_user_name": actor_name,
                    "identity_confidence": identity_confidence,
                    "identity_source": identity_source,
                    "original_actor_id": message.metadata.get("original_actor_id") or message.actor_id,
                    "original_actor_name": message.metadata.get("original_actor_name") or message.actor_name,
                    "speaker_reattributed": True,
                }
                updated = message.model_copy(
                    update={
                        "actor_id": actor_id,
                        "actor_name": actor_name,
                        "actor_initials": actor_initials,
                        "metadata": metadata,
                    }
                )
                conn.execute("UPDATE messages SET data = ? WHERE rowid = ?", (_dumps(updated), row["rowid"]))
                changed += 1
            if changed:
                self._touch_room(conn, room_id)
            return changed

    def append_action(self, action: AgentAction) -> AgentAction:
        self.get_or_create_room(action.room_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO actions(room_id, action_id, data) VALUES(?, ?, ?)",
                (action.room_id, action.id, _dumps(action)),
            )
            self._record_event(
                conn,
                room_id=action.room_id,
                stream_type="action",
                stream_id=f"{action.room_id}:action:{action.id}",
                event_type="action.appended",
                data=action.model_dump(mode="json"),
            )
            self._touch_room(conn, action.room_id)
        return action

    def list_actions(self, room_id: str, *, limit: int = 50) -> list[AgentAction]:
        rows = self._list_ordered("actions", room_id, limit)
        return [AgentAction.model_validate(_loads(row["data"])) for row in rows]

    def get_action(self, room_id: str, action_id: str) -> AgentAction | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM actions WHERE room_id = ? AND action_id = ?",
                (room_id, action_id),
            ).fetchone()
        return AgentAction.model_validate(_loads(row["data"])) if row else None

    def update_action(self, room_id: str, action_id: str, **updates) -> AgentAction | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM actions WHERE room_id = ? AND action_id = ?",
                (room_id, action_id),
            ).fetchone()
            if row is None:
                return None
            updated = AgentAction.model_validate(_loads(row["data"])).model_copy(update=updates)
            conn.execute(
                "UPDATE actions SET data = ? WHERE room_id = ? AND action_id = ?",
                (_dumps(updated), room_id, action_id),
            )
            self._record_event(
                conn,
                room_id=room_id,
                stream_type="action",
                stream_id=f"{room_id}:action:{action_id}",
                event_type="action.updated",
                data=updated.model_dump(mode="json"),
                metadata={"updated_fields": sorted(updates)},
            )
            self._touch_room(conn, room_id)
        return updated

    def set_approval_payload(self, room_id: str, action_id: str, payload: dict) -> None:
        self.get_or_create_room(room_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO approval_payloads(room_id, action_id, data)
                VALUES(?, ?, ?)
                ON CONFLICT(room_id, action_id) DO UPDATE SET data = excluded.data
                """,
                (room_id, action_id, json.dumps(payload)),
            )
            self._record_event(
                conn,
                room_id=room_id,
                stream_type="approval_payload",
                stream_id=f"{room_id}:approval_payload:{action_id}",
                event_type="approval_payload.set",
                data=payload,
            )
            self._touch_room(conn, room_id)

    def get_approval_payload(self, room_id: str, action_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM approval_payloads WHERE room_id = ? AND action_id = ?",
                (room_id, action_id),
            ).fetchone()
        return dict(_loads(row["data"])) if row else None

    def delete_approval_payload(self, room_id: str, action_id: str) -> None:
        with self._lock, self._connect() as conn:
            result = conn.execute(
                "DELETE FROM approval_payloads WHERE room_id = ? AND action_id = ?",
                (room_id, action_id),
            )
            if result.rowcount:
                self._record_event(
                    conn,
                    room_id=room_id,
                    stream_type="approval_payload",
                    stream_id=f"{room_id}:approval_payload:{action_id}",
                    event_type="approval_payload.deleted",
                    data={"action_id": action_id},
                )
                self._touch_room(conn, room_id)

    def append_memory(self, item: MemoryItem) -> MemoryItem:
        self.get_or_create_room(item.room_id)
        key = _memory_key(item)
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT data FROM memory WHERE room_id = ? AND memory_key = ?",
                (item.room_id, key),
            ).fetchone()
            if existing:
                return MemoryItem.model_validate(_loads(existing["data"]))
            conn.execute(
                "INSERT INTO memory(room_id, memory_id, memory_key, data) VALUES(?, ?, ?, ?)",
                (item.room_id, item.id, key, _dumps(item)),
            )
            self._record_event(
                conn,
                room_id=item.room_id,
                stream_type="memory",
                stream_id=f"{item.room_id}:memory:{item.id}",
                event_type="memory.appended",
                data=item.model_dump(mode="json"),
            )
            self._touch_room(conn, item.room_id)
        return item

    def list_events(self, room_id: str | None = None, *, from_position: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if room_id:
                rows = conn.execute(
                    """
                    SELECT global_position, room_id, stream_type, stream_id, event_type, data, metadata, created_at
                    FROM event_log
                    WHERE room_id = ? AND global_position > ?
                    ORDER BY global_position
                    LIMIT ?
                    """,
                    (room_id, from_position, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT global_position, room_id, stream_type, stream_id, event_type, data, metadata, created_at
                    FROM event_log
                    WHERE global_position > ?
                    ORDER BY global_position
                    LIMIT ?
                    """,
                    (from_position, limit),
                ).fetchall()
        return [
            {
                "global_position": row["global_position"],
                "room_id": row["room_id"],
                "stream_type": row["stream_type"],
                "stream_id": row["stream_id"],
                "event_type": row["event_type"],
                "data": _loads(row["data"]),
                "metadata": _loads(row["metadata"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_memory(self, room_id: str, *, limit: int = 100) -> list[MemoryItem]:
        rows = self._list_ordered("memory", room_id, limit)
        return [MemoryItem.model_validate(_loads(row["data"])) for row in rows]

    def update_memory_for_action(self, room_id: str, action_id: str, *, status: str) -> None:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT rowid, data FROM memory WHERE room_id = ?", (room_id,)).fetchall()
            changed = False
            for row in rows:
                item = MemoryItem.model_validate(_loads(row["data"]))
                if item.source_action_id != action_id:
                    continue
                updated = item.model_copy(update={"status": status})
                conn.execute("UPDATE memory SET data = ? WHERE rowid = ?", (_dumps(updated), row["rowid"]))
                changed = True
            if changed:
                self._touch_room(conn, room_id)

    def reattribute_memory_for_speaker(
        self,
        room_id: str,
        *,
        speaker_label: str,
        actor_id: str,
        actor_name: str,
    ) -> int:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT rowid, data FROM memory WHERE room_id = ?", (room_id,)).fetchall()
            changed = 0
            for row in rows:
                item = MemoryItem.model_validate(_loads(row["data"]))
                if item.metadata.get("speaker_label") != speaker_label:
                    continue
                metadata = {
                    **item.metadata,
                    "original_actor_id": item.metadata.get("original_actor_id") or item.actor_id,
                    "original_actor_name": item.metadata.get("original_actor_name") or item.actor_name,
                    "speaker_reattributed": True,
                }
                updated = item.model_copy(
                    update={
                        "actor_id": actor_id,
                        "actor_name": actor_name,
                        "metadata": metadata,
                    }
                )
                conn.execute("UPDATE memory SET data = ? WHERE rowid = ?", (_dumps(updated), row["rowid"]))
                changed += 1
            if changed:
                self._touch_room(conn, room_id)
            return changed

    def _list_ordered(self, table: str, room_id: str, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT data FROM {table} WHERE room_id = ? ORDER BY rowid DESC LIMIT ?",
                (room_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def _touch_room(self, conn: sqlite3.Connection, room_id: str) -> None:
        row = conn.execute("SELECT data FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not row:
            return
        room = Room.model_validate(_loads(row["data"])).model_copy(update={"updated_at": _now()})
        conn.execute("UPDATE rooms SET data = ? WHERE id = ?", (_dumps(room), room_id))

    def _record_event(
        self,
        conn: sqlite3.Connection,
        *,
        room_id: str,
        stream_type: str,
        stream_id: str,
        event_type: str,
        data: dict,
        metadata: dict | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO event_log(room_id, stream_type, stream_id, event_type, data, metadata, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (room_id, stream_type, stream_id, event_type, json.dumps(data), json.dumps(metadata or {}), _now().isoformat()),
        )

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS participants (
                    room_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(room_id, participant_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    room_id TEXT NOT NULL,
                    message_id TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    room_id TEXT NOT NULL,
                    action_id TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory (
                    room_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL UNIQUE,
                    memory_key TEXT NOT NULL,
                    data TEXT NOT NULL,
                    UNIQUE(room_id, memory_key)
                );
                CREATE TABLE IF NOT EXISTS approval_payloads (
                    room_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY(room_id, action_id)
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS event_log (
                    global_position INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL,
                    stream_type TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data TEXT NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_log_room_position
                    ON event_log(room_id, global_position);
                CREATE INDEX IF NOT EXISTS idx_event_log_stream
                    ON event_log(stream_id, global_position);
                CREATE INDEX IF NOT EXISTS idx_event_log_type
                    ON event_log(event_type);
                """
            )


def _dumps(model: Any) -> str:
    if hasattr(model, "model_dump"):
        return json.dumps(model.model_dump(mode="json"))
    return json.dumps(model)


def _loads(value: str) -> Any:
    return json.loads(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_key(item: MemoryItem) -> str:
    return "|".join((item.room_id, item.kind.value, " ".join(item.text.lower().split())))
