import json
import sqlite3
from pathlib import Path
from threading import RLock

from app.speakers.models import SpeakerMapping, VoiceProfile


class SQLiteSpeakerStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._lock = RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS speaker_mappings (
                    room_id TEXT NOT NULL,
                    speaker_label TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (room_id, speaker_label)
                );

                CREATE TABLE IF NOT EXISTS voice_profiles (
                    user_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def upsert_mapping(self, mapping: SpeakerMapping) -> SpeakerMapping:
        payload = json.dumps(mapping.model_dump(mode="json"))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO speaker_mappings (room_id, speaker_label, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(room_id, speaker_label) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    mapping.room_id,
                    mapping.speaker_label,
                    payload,
                    mapping.updated_at.isoformat(),
                ),
            )
        return mapping

    def get_mapping(self, room_id: str, speaker_label: str) -> SpeakerMapping | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM speaker_mappings
                WHERE room_id = ? AND speaker_label = ?
                """,
                (room_id, speaker_label),
            ).fetchone()
        if row is None:
            return None
        return SpeakerMapping.model_validate(json.loads(row["payload"]))

    def list_mappings(self, room_id: str) -> list[SpeakerMapping]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM speaker_mappings
                WHERE room_id = ?
                ORDER BY speaker_label
                """,
                (room_id,),
            ).fetchall()
        return [SpeakerMapping.model_validate(json.loads(row["payload"])) for row in rows]

    def upsert_profile(self, profile: VoiceProfile) -> VoiceProfile:
        payload = json.dumps(profile.model_dump(mode="json"))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO voice_profiles (user_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (profile.user_id, payload, profile.updated_at.isoformat()),
            )
        return profile

    def get_profile(self, user_id: str) -> VoiceProfile | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM voice_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return VoiceProfile.model_validate(json.loads(row["payload"]))

    def list_profiles(self) -> list[VoiceProfile]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload
                FROM voice_profiles
                ORDER BY user_id
                """
            ).fetchall()
        return [VoiceProfile.model_validate(json.loads(row["payload"])) for row in rows]
