from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, BACKEND_ROOT


LOCAL_RUNTIME_BOOTSTRAP_COMMAND = (
    "cd backend && python scripts/bootstrap_local_runtime.py "
    "--env-file .env.local --workspace /absolute/path/to/team/repository --json"
)


class EventStoreCheck(BaseModel):
    id: str
    label: str
    ready: bool
    status: str
    detail: str


class EventStoreReadinessResponse(BaseModel):
    backend: str
    status: str
    ready: bool
    path: str
    runtime_mode: str
    production_ready: bool
    local_demo_acceptable: bool
    recommended_backend: str = "sqlite"
    bootstrap_command: str | None = None
    init_command: str | None = None
    migration_available: bool
    migration_command: str | None = None
    cutover_env: dict[str, str] = Field(default_factory=dict)
    event_count: int = 0
    stream_count: int = 0
    latest_position: int | None = None
    event_types: dict[str, int] = Field(default_factory=dict)
    state_counts: dict[str, int] = Field(default_factory=dict)
    checks: list[EventStoreCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    operator_notes: list[str] = Field(default_factory=list)


def build_event_store_readiness(settings: Settings) -> EventStoreReadinessResponse:
    backend = settings.collab_store_backend.strip().lower()
    if backend == "sqlite":
        return _sqlite_readiness(settings.collab_sqlite_path)
    return _json_readiness(settings.collab_store_path, settings.collab_sqlite_path, backend)


def _sqlite_readiness(path: Path) -> EventStoreReadinessResponse:
    checks: list[EventStoreCheck] = [
        EventStoreCheck(
            id="backend",
            label="SQLite backend configured",
            ready=True,
            status="ready",
            detail="Collaboration store is configured for SQLite durable state.",
        )
    ]
    warnings: list[str] = []
    if not path.exists():
        checks.append(
            EventStoreCheck(
                id="sqlite_file",
                label="SQLite file exists",
                ready=False,
                status="missing",
                detail="SQLite file has not been initialized yet.",
            )
        )
        return _response("sqlite", path, checks, warnings)

    try:
        info = _inspect_sqlite(path)
    except sqlite3.DatabaseError as exc:
        checks.append(
            EventStoreCheck(
                id="sqlite_open",
                label="SQLite file is readable",
                ready=False,
                status="invalid",
                detail=f"SQLite store could not be opened: {exc}",
            )
        )
        return _response("sqlite", path, checks, warnings)

    required_tables = {"rooms", "participants", "messages", "actions", "memory", "approval_payloads", "event_log"}
    missing_tables = sorted(required_tables - set(info["tables"]))
    checks.append(
        EventStoreCheck(
            id="schema",
            label="Authoritative state schema",
            ready=not missing_tables,
            status="ready" if not missing_tables else "missing_tables",
            detail="All collaboration tables are present." if not missing_tables else f"Missing tables: {', '.join(missing_tables)}",
        )
    )
    required_indexes = {"idx_event_log_room_position", "idx_event_log_stream", "idx_event_log_type"}
    missing_indexes = sorted(required_indexes - set(info["indexes"]))
    checks.append(
        EventStoreCheck(
            id="event_indexes",
            label="Event stream indexes",
            ready=not missing_indexes,
            status="ready" if not missing_indexes else "missing_indexes",
            detail="Event log indexes support room, stream, and type queries." if not missing_indexes else f"Missing indexes: {', '.join(missing_indexes)}",
        )
    )
    checks.append(
        EventStoreCheck(
            id="global_order",
            label="Global event ordering",
            ready=info["global_order_ok"],
            status="ready" if info["global_order_ok"] else "duplicate_positions",
            detail="Event positions are unique and monotonic." if info["global_order_ok"] else "Event log contains duplicate or invalid global positions.",
        )
    )
    if info["event_count"] == 0:
        warnings.append("SQLite event log is initialized but has no collaboration events yet.")
    response = _response("sqlite", path, checks, warnings)
    return response.model_copy(
        update={
            "event_count": info["event_count"],
            "stream_count": info["stream_count"],
            "latest_position": info["latest_position"],
            "event_types": info["event_types"],
            "state_counts": info["state_counts"],
        }
    )


def _json_readiness(json_path: Path, sqlite_path: Path, backend: str) -> EventStoreReadinessResponse:
    checks = [
        EventStoreCheck(
            id="backend",
            label="SQLite backend configured",
            ready=False,
            status="migration_available",
            detail=f"{backend or 'json'} backend is usable for local development but does not provide an append-only event log.",
        ),
        EventStoreCheck(
            id="migration_script",
            label="JSON to SQLite migration script",
            ready=_migration_script_path().exists(),
            status="ready" if _migration_script_path().exists() else "missing",
            detail=str(_migration_script_path()),
        ),
    ]
    state_counts = _json_counts(json_path) if json_path.exists() else {}
    warnings = ["Run the migration before production so approvals and handoffs have durable event positions."]
    if sqlite_path.exists():
        warnings.append("Target SQLite file already exists; inspect it before using --replace.")
    return EventStoreReadinessResponse(
        backend=backend or "json",
        status="migration_available",
        ready=False,
        path=str(json_path),
        runtime_mode="development_json",
        production_ready=False,
        local_demo_acceptable=True,
        recommended_backend="sqlite",
        bootstrap_command=LOCAL_RUNTIME_BOOTSTRAP_COMMAND,
        migration_available=True,
        migration_command=(
            f"cd backend && python scripts/migrate_collab_json_to_sqlite.py "
            f"--json {json_path} --sqlite {sqlite_path} --report-json"
        ),
        cutover_env={
            "COLLAB_STORE_BACKEND": "sqlite",
            "COLLAB_SQLITE_PATH": str(sqlite_path),
        },
        state_counts=state_counts,
        checks=checks,
        warnings=warnings,
        operator_notes=[
            "JSON state is acceptable for local demos, not production collaboration audit.",
            "Use the bootstrap command for a new local runtime or the migration command to preserve existing JSON state.",
        ],
    )


def _inspect_sqlite(path: Path) -> dict[str, Any]:
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indexes = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        positions = [
            int(row["global_position"])
            for row in conn.execute("SELECT global_position FROM event_log ORDER BY global_position")
        ] if "event_log" in tables else []
        event_types = {
            row["event_type"]: int(row["count"])
            for row in conn.execute("SELECT event_type, COUNT(*) AS count FROM event_log GROUP BY event_type")
        } if "event_log" in tables else {}
        state_counts = {
            table: _count_table(conn, table)
            for table in ("rooms", "participants", "messages", "actions", "memory", "approval_payloads")
            if table in tables
        }
        stream_count = (
            int(conn.execute("SELECT COUNT(DISTINCT stream_id) FROM event_log").fetchone()[0])
            if "event_log" in tables
            else 0
        )
    return {
        "tables": tables,
        "indexes": indexes,
        "event_count": len(positions),
        "stream_count": stream_count,
        "latest_position": positions[-1] if positions else None,
        "event_types": event_types,
        "state_counts": state_counts,
        "global_order_ok": len(positions) == len(set(positions)) and positions == sorted(positions),
    }


def _json_counts(path: Path) -> dict[str, int]:
    raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    return {
        "rooms": len(raw.get("rooms", {})),
        "participants": sum(len(items) for items in raw.get("participants", {}).values()),
        "messages": sum(len(items) for items in raw.get("messages", {}).values()),
        "actions": sum(len(items) for items in raw.get("actions", {}).values()),
        "memory": sum(len(items) for items in raw.get("memory", {}).values()),
        "approval_payloads": sum(len(items) for items in raw.get("approval_payloads", {}).values()),
    }


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _response(
    backend: str,
    path: Path,
    checks: list[EventStoreCheck],
    warnings: list[str],
) -> EventStoreReadinessResponse:
    ready = all(check.ready for check in checks)
    command = (
        f"cd backend && python scripts/init_collab_event_store.py --sqlite {path} --report-json"
        if backend == "sqlite"
        else (
            "cd backend && python scripts/migrate_collab_json_to_sqlite.py "
            "--json data/collaboration.json --sqlite data/collaboration.sqlite3 --report-json"
        )
    )
    return EventStoreReadinessResponse(
        backend=backend,
        status="ready" if ready else "needs_attention",
        ready=ready,
        path=str(path),
        runtime_mode="durable_sqlite" if ready else "sqlite_pending",
        production_ready=ready and backend == "sqlite",
        local_demo_acceptable=ready,
        recommended_backend="sqlite",
        bootstrap_command=LOCAL_RUNTIME_BOOTSTRAP_COMMAND if not ready else None,
        init_command=command if backend == "sqlite" and not ready else None,
        migration_available=True,
        migration_command=command,
        cutover_env={
            "COLLAB_STORE_BACKEND": "sqlite",
            "COLLAB_SQLITE_PATH": str(path),
        },
        checks=checks,
        warnings=warnings,
        operator_notes=[] if ready else ["SQLite is configured but must be initialized before collaboration history is durable."],
    )


def _migration_script_path() -> Path:
    return BACKEND_ROOT / "scripts" / "migrate_collab_json_to_sqlite.py"
