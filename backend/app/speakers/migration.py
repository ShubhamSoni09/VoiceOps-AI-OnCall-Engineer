from __future__ import annotations

from pathlib import Path

from app.speakers.sqlite_store import SQLiteSpeakerStore
from app.speakers.store import SpeakerStore


def migrate_json_to_sqlite(json_path: Path, sqlite_path: Path, *, replace: bool = False) -> dict:
    """Copy authoritative speaker identity state from JSON store to SQLite."""
    if not json_path.exists():
        raise FileNotFoundError(f"JSON speaker store not found: {json_path}")
    if sqlite_path.exists():
        if not replace:
            raise FileExistsError(f"SQLite speaker store already exists: {sqlite_path}")
        sqlite_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{sqlite_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    source = SpeakerStore(json_path)
    target = SQLiteSpeakerStore(sqlite_path)

    mappings = 0
    profiles = 0

    for profile in source.list_profiles():
        target.upsert_profile(profile)
        profiles += 1

    for room_id, room_mappings in source._mappings.items():
        for mapping in room_mappings.values():
            target.upsert_mapping(mapping)
            mappings += 1

    return {
        "profiles": profiles,
        "mappings": mappings,
        "sqlite_path": str(sqlite_path),
    }
