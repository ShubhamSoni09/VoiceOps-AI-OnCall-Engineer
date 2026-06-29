import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from app.collab.models import AgentAction, AgentSettings, MemoryItem, Participant, Room, TimelineMessage
from app.config import Settings


class CollaborationStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._lock = RLock()
        self._rooms: dict[str, Room] = {}
        self._participants: dict[str, dict[str, Participant]] = {}
        self._messages: dict[str, list[TimelineMessage]] = {}
        self._actions: dict[str, list[AgentAction]] = {}
        self._memory: dict[str, list[MemoryItem]] = {}
        self._approval_payloads: dict[str, dict[str, dict]] = {}
        self._agent_settings: AgentSettings | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._rooms = {
            room_id: Room.model_validate(data)
            for room_id, data in raw.get("rooms", {}).items()
        }
        self._participants = {
            room_id: {
                participant_id: Participant.model_validate(item)
                for participant_id, item in participants.items()
            }
            for room_id, participants in raw.get("participants", {}).items()
        }
        self._messages = {
            room_id: [TimelineMessage.model_validate(item) for item in messages]
            for room_id, messages in raw.get("messages", {}).items()
        }
        self._actions = {
            room_id: [AgentAction.model_validate(item) for item in actions]
            for room_id, actions in raw.get("actions", {}).items()
        }
        self._memory = {
            room_id: [MemoryItem.model_validate(item) for item in memory]
            for room_id, memory in raw.get("memory", {}).items()
        }
        self._approval_payloads = {
            room_id: dict(payloads)
            for room_id, payloads in raw.get("approval_payloads", {}).items()
        }
        if raw.get("agent_settings"):
            self._agent_settings = AgentSettings.model_validate(raw["agent_settings"])

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rooms": {
                key: room.model_dump(mode="json")
                for key, room in self._rooms.items()
            },
            "participants": {
                room_id: {
                    participant_id: participant.model_dump(mode="json")
                    for participant_id, participant in participants.items()
                }
                for room_id, participants in self._participants.items()
            },
            "messages": {
                room_id: [message.model_dump(mode="json") for message in messages]
                for room_id, messages in self._messages.items()
            },
            "actions": {
                room_id: [action.model_dump(mode="json") for action in actions]
                for room_id, actions in self._actions.items()
            },
            "memory": {
                room_id: [item.model_dump(mode="json") for item in memory]
                for room_id, memory in self._memory.items()
            },
            "approval_payloads": self._approval_payloads,
            "agent_settings": (
                self._agent_settings.model_dump(mode="json")
                if self._agent_settings is not None
                else None
            ),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)

    def get_agent_settings(self) -> AgentSettings | None:
        return self._agent_settings

    def set_agent_settings(self, settings: AgentSettings) -> AgentSettings:
        with self._lock:
            self._agent_settings = settings
            self._persist()
            return settings

    def get_room(self, room_id: str) -> Room | None:
        return self._rooms.get(room_id)

    def get_or_create_room(
        self,
        room_id: str,
        *,
        name: str | None = None,
        project: str | None = None,
        workspace_path: str | None = None,
    ) -> Room:
        with self._lock:
            now = _now()
            room = self._rooms.get(room_id)
            if room is None:
                room = Room(
                    id=room_id,
                    name=name or room_id,
                    project=project,
                    workspace_path=workspace_path,
                    created_at=now,
                    updated_at=now,
                )
                self._rooms[room_id] = room
                self._participants.setdefault(room_id, {})
                self._messages.setdefault(room_id, [])
                self._actions.setdefault(room_id, [])
                self._memory.setdefault(room_id, [])
                self._approval_payloads.setdefault(room_id, {})
                self._persist()
            elif name or project is not None or workspace_path is not None:
                room = room.model_copy(
                    update={
                        "name": name or room.name,
                        "project": project if project is not None else room.project,
                        "workspace_path": workspace_path if workspace_path is not None else room.workspace_path,
                        "updated_at": now,
                    }
                )
                self._rooms[room_id] = room
                self._persist()
            return room

    def upsert_participant(self, room_id: str, participant: Participant) -> Participant:
        with self._lock:
            self.get_or_create_room(room_id)
            participants = self._participants.setdefault(room_id, {})
            participants[participant.id] = participant
            self._touch_room(room_id)
            self._persist()
            return participant

    def get_participant(self, room_id: str, participant_id: str) -> Participant | None:
        return self._participants.get(room_id, {}).get(participant_id)

    def list_participants(self, room_id: str) -> list[Participant]:
        return list(self._participants.get(room_id, {}).values())

    def append_message(self, message: TimelineMessage) -> TimelineMessage:
        with self._lock:
            self.get_or_create_room(message.room_id)
            self._messages.setdefault(message.room_id, []).append(message)
            self._touch_room(message.room_id)
            self._persist()
            return message

    def list_messages(self, room_id: str, *, limit: int = 100) -> list[TimelineMessage]:
        messages = self._messages.get(room_id, [])
        return messages[-limit:]

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
        with self._lock:
            messages = self._messages.get(room_id, [])
            changed = 0
            for index, message in enumerate(messages):
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
                messages[index] = message.model_copy(
                    update={
                        "actor_id": actor_id,
                        "actor_name": actor_name,
                        "actor_initials": actor_initials,
                        "metadata": metadata,
                    }
                )
                changed += 1
            if changed:
                self._touch_room(room_id)
                self._persist()
            return changed

    def append_action(self, action: AgentAction) -> AgentAction:
        with self._lock:
            self.get_or_create_room(action.room_id)
            self._actions.setdefault(action.room_id, []).append(action)
            self._touch_room(action.room_id)
            self._persist()
            return action

    def list_actions(self, room_id: str, *, limit: int = 50) -> list[AgentAction]:
        actions = self._actions.get(room_id, [])
        return actions[-limit:]

    def get_action(self, room_id: str, action_id: str) -> AgentAction | None:
        for action in self._actions.get(room_id, []):
            if action.id == action_id:
                return action
        return None

    def update_action(self, room_id: str, action_id: str, **updates) -> AgentAction | None:
        with self._lock:
            actions = self._actions.get(room_id, [])
            for index, action in enumerate(actions):
                if action.id != action_id:
                    continue
                updated = action.model_copy(update=updates)
                actions[index] = updated
                self._touch_room(room_id)
                self._persist()
                return updated
        return None

    def set_approval_payload(self, room_id: str, action_id: str, payload: dict) -> None:
        with self._lock:
            self.get_or_create_room(room_id)
            self._approval_payloads.setdefault(room_id, {})[action_id] = payload
            self._touch_room(room_id)
            self._persist()

    def get_approval_payload(self, room_id: str, action_id: str) -> dict | None:
        payload = self._approval_payloads.get(room_id, {}).get(action_id)
        return dict(payload) if payload is not None else None

    def delete_approval_payload(self, room_id: str, action_id: str) -> None:
        with self._lock:
            payloads = self._approval_payloads.get(room_id)
            if not payloads or action_id not in payloads:
                return
            payloads.pop(action_id, None)
            self._touch_room(room_id)
            self._persist()

    def append_memory(self, item: MemoryItem) -> MemoryItem:
        with self._lock:
            self.get_or_create_room(item.room_id)
            memory = self._memory.setdefault(item.room_id, [])
            key = _memory_key(item)
            for existing in memory:
                if _memory_key(existing) == key:
                    return existing
            memory.append(item)
            self._touch_room(item.room_id)
            self._persist()
            return item

    def list_memory(self, room_id: str, *, limit: int = 100) -> list[MemoryItem]:
        memory = self._memory.get(room_id, [])
        return memory[-limit:]

    def update_memory_for_action(self, room_id: str, action_id: str, *, status: str) -> None:
        with self._lock:
            memory = self._memory.get(room_id, [])
            changed = False
            for index, item in enumerate(memory):
                if item.source_action_id != action_id:
                    continue
                memory[index] = item.model_copy(update={"status": status})
                changed = True
            if changed:
                self._touch_room(room_id)
                self._persist()

    def reattribute_memory_for_speaker(
        self,
        room_id: str,
        *,
        speaker_label: str,
        actor_id: str,
        actor_name: str,
    ) -> int:
        with self._lock:
            memory = self._memory.get(room_id, [])
            changed = 0
            for index, item in enumerate(memory):
                if item.metadata.get("speaker_label") != speaker_label:
                    continue
                metadata = {
                    **item.metadata,
                    "original_actor_id": item.metadata.get("original_actor_id") or item.actor_id,
                    "original_actor_name": item.metadata.get("original_actor_name") or item.actor_name,
                    "speaker_reattributed": True,
                }
                memory[index] = item.model_copy(
                    update={
                        "actor_id": actor_id,
                        "actor_name": actor_name,
                        "metadata": metadata,
                    }
                )
                changed += 1
            if changed:
                self._touch_room(room_id)
                self._persist()
            return changed

    def _touch_room(self, room_id: str) -> None:
        room = self._rooms.get(room_id)
        if room is not None:
            self._rooms[room_id] = room.model_copy(update={"updated_at": _now()})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _memory_key(item: MemoryItem) -> tuple[str, str, str]:
    return (item.room_id, item.kind.value, " ".join(item.text.lower().split()))


def create_collaboration_store(settings: Settings):
    if settings.collab_store_backend.lower() == "sqlite":
        from app.collab.sqlite_store import SQLiteCollaborationStore

        return SQLiteCollaborationStore(settings.collab_sqlite_path)
    return CollaborationStore(settings.collab_store_path)
