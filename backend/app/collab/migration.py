from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.collab.sqlite_store import SQLiteCollaborationStore
from app.collab.store import CollaborationStore


def migrate_json_to_sqlite(
    json_path: Path,
    sqlite_path: Path,
    *,
    replace: bool = False,
    dry_run: bool = False,
    validate: bool = True,
) -> dict[str, Any]:
    """Copy authoritative collaboration state from JSON store to SQLite."""
    if not json_path.exists():
        raise FileNotFoundError(f"JSON collaboration store not found: {json_path}")
    target_exists = sqlite_path.exists()
    if dry_run:
        source = CollaborationStore(json_path)
        counts = _source_counts(source)
        return _report(
            json_path=json_path,
            sqlite_path=sqlite_path,
            dry_run=True,
            replaced=target_exists and replace,
            target_exists=target_exists,
            counts=counts,
            events=0,
            validation={"status": "skipped", "detail": "Dry run did not write SQLite state."},
        )
    if target_exists:
        if not replace:
            raise FileExistsError(f"SQLite store already exists: {sqlite_path}")
        sqlite_path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{sqlite_path}{suffix}")
            if sidecar.exists():
                sidecar.unlink()

    source = CollaborationStore(json_path)
    counts = _source_counts(source)

    target = SQLiteCollaborationStore(sqlite_path)

    if source.get_agent_settings():
        target.set_agent_settings(source.get_agent_settings())

    for room in source._rooms.values():
        target.get_or_create_room(room.id, name=room.name, project=room.project, workspace_path=room.workspace_path)

    for room_id, room_participants in source._participants.items():
        for participant in room_participants.values():
            target.upsert_participant(room_id, participant)

    for room_messages in source._messages.values():
        for message in room_messages:
            target.append_message(message)

    for room_actions in source._actions.values():
        for action in room_actions:
            target.append_action(action)

    for room_memory in source._memory.values():
        for item in room_memory:
            target.append_memory(item)

    for room_id, payloads in source._approval_payloads.items():
        for action_id, payload in payloads.items():
            target.set_approval_payload(room_id, action_id, payload)

    events = len(target.list_events(limit=sum(counts.values()) + 10))
    validation = _validate_sqlite_counts(sqlite_path, counts) if validate else {
        "status": "skipped",
        "detail": "Validation disabled.",
    }
    return _report(
        json_path=json_path,
        sqlite_path=sqlite_path,
        dry_run=False,
        replaced=replace,
        target_exists=target_exists,
        counts=counts,
        events=events,
        validation=validation,
    )


def _source_counts(source: CollaborationStore) -> dict[str, int]:
    return {
        "rooms": len(source._rooms),
        "participants": sum(len(items) for items in source._participants.values()),
        "messages": sum(len(items) for items in source._messages.values()),
        "actions": sum(len(items) for items in source._actions.values()),
        "memory": sum(len(items) for items in source._memory.values()),
        "approval_payloads": sum(len(items) for items in source._approval_payloads.values()),
    }


def _validate_sqlite_counts(sqlite_path: Path, expected: dict[str, int]) -> dict[str, Any]:
    tables = {
        "rooms": "rooms",
        "participants": "participants",
        "messages": "messages",
        "actions": "actions",
        "memory": "memory",
        "approval_payloads": "approval_payloads",
    }
    actual: dict[str, int] = {}
    with sqlite3.connect(sqlite_path) as conn:
        for key, table in tables.items():
            actual[key] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected
        if expected[key] != actual[key]
    }
    return {
        "status": "passed" if not mismatches else "failed",
        "actual": actual,
        "mismatches": mismatches,
    }


def _report(
    *,
    json_path: Path,
    sqlite_path: Path,
    dry_run: bool,
    replaced: bool,
    target_exists: bool,
    counts: dict[str, int],
    events: int,
    validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        **counts,
        "events": events,
        "expected_events": (
            counts["participants"]
            + counts["messages"]
            + counts["actions"]
            + counts["memory"]
            + counts["approval_payloads"]
        ),
        "dry_run": dry_run,
        "replaced": replaced,
        "target_exists": target_exists,
        "json_path": str(json_path),
        "sqlite_path": str(sqlite_path),
        "validation": validation,
    }
