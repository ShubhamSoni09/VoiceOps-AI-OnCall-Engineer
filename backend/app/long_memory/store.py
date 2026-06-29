from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from app.long_memory.models import LongMemoryRecord


class LongMemoryStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._records: dict[str, LongMemoryRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        records = raw.get("records", []) if isinstance(raw, dict) else []
        for item in records:
            try:
                record = LongMemoryRecord.model_validate(item)
            except ValidationError:
                continue
            self._records[record.id] = record

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [
                record.model_dump(mode="json")
                for record in sorted(self._records.values(), key=lambda item: (item.room_id, item.created_at, item.id))
            ]
        }
        tmp = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)
        os.chmod(self._path, 0o600)

    def upsert_many(self, records: list[LongMemoryRecord]) -> int:
        changed = 0
        with self._lock:
            for record in records:
                previous = self._records.get(record.id)
                if previous and _same_memory(previous, record):
                    continue
                self._records[record.id] = record
                changed += 1
            if changed:
                self._persist()
        return changed

    def list(self, room_id: str, *, kind: str | None = None, limit: int = 100) -> list[LongMemoryRecord]:
        records = [
            record
            for record in self._records.values()
            if record.room_id == room_id and (kind is None or record.kind == kind)
        ]
        return sorted(records, key=lambda item: item.created_at)[-limit:]


def _same_memory(left: LongMemoryRecord, right: LongMemoryRecord) -> bool:
    excluded = {"archived_at"}
    if left.kind == "handoff" and right.kind == "handoff":
        excluded.add("created_at")
    return left.model_dump(exclude=excluded) == right.model_dump(exclude=excluded)
