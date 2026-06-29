import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Protocol

from app.speakers.models import SpeakerMapping, VoiceProfile


class SpeakerStorePort(Protocol):
    def upsert_mapping(self, mapping: SpeakerMapping) -> SpeakerMapping:
        ...

    def get_mapping(self, room_id: str, speaker_label: str) -> SpeakerMapping | None:
        ...

    def list_mappings(self, room_id: str) -> list[SpeakerMapping]:
        ...

    def upsert_profile(self, profile: VoiceProfile) -> VoiceProfile:
        ...

    def get_profile(self, user_id: str) -> VoiceProfile | None:
        ...

    def list_profiles(self) -> list[VoiceProfile]:
        ...


class SpeakerStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._lock = RLock()
        self._mappings: dict[str, dict[str, SpeakerMapping]] = {}
        self._profiles: dict[str, VoiceProfile] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._mappings = {
            room_id: {
                label: SpeakerMapping.model_validate(item)
                for label, item in mappings.items()
            }
            for room_id, mappings in raw.get("mappings", {}).items()
        }
        self._profiles = {
            user_id: VoiceProfile.model_validate(item)
            for user_id, item in raw.get("profiles", {}).items()
        }

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mappings": {
                room_id: {
                    label: mapping.model_dump(mode="json")
                    for label, mapping in mappings.items()
                }
                for room_id, mappings in self._mappings.items()
            },
            "profiles": {
                user_id: profile.model_dump(mode="json")
                for user_id, profile in self._profiles.items()
            },
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)

    def upsert_mapping(self, mapping: SpeakerMapping) -> SpeakerMapping:
        with self._lock:
            room_mappings = self._mappings.setdefault(mapping.room_id, {})
            room_mappings[mapping.speaker_label] = mapping
            self._persist()
            return mapping

    def get_mapping(self, room_id: str, speaker_label: str) -> SpeakerMapping | None:
        return self._mappings.get(room_id, {}).get(speaker_label)

    def list_mappings(self, room_id: str) -> list[SpeakerMapping]:
        return list(self._mappings.get(room_id, {}).values())

    def upsert_profile(self, profile: VoiceProfile) -> VoiceProfile:
        with self._lock:
            self._profiles[profile.user_id] = profile
            self._persist()
            return profile

    def get_profile(self, user_id: str) -> VoiceProfile | None:
        return self._profiles.get(user_id)

    def list_profiles(self) -> list[VoiceProfile]:
        return list(self._profiles.values())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_speaker_store(settings) -> SpeakerStorePort:
    backend = settings.speaker_store_backend.lower()
    if backend == "json":
        return SpeakerStore(settings.speaker_store_path)
    if backend == "sqlite":
        from app.speakers.sqlite_store import SQLiteSpeakerStore

        return SQLiteSpeakerStore(settings.speaker_sqlite_path)
    raise ValueError(f"Unsupported speaker store backend: {settings.speaker_store_backend}")
