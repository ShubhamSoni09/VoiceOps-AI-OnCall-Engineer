from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class DemoEvidenceRecord(BaseModel):
    id: str
    label: str
    status: str
    checked_at: str
    source: str = "manual"
    provider: str | None = None
    detail: str | None = None
    error: str | None = None
    duration_ms: int | None = None
    command: list[str] = Field(default_factory=list)
    speaker_labels: list[str] = Field(default_factory=list)
    distinct_speaker_count: int = 0
    requested_chunks: int | None = None
    completed_chunks: int | None = None
    timeline_message_count: int | None = None
    latency: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)


class DemoEvidenceStatus(BaseModel):
    path: str
    exists: bool
    status: str
    checked_at: str | None = None
    records: list[DemoEvidenceRecord] = Field(default_factory=list)
    detail: str
    error: str | None = None


def read_demo_evidence(path: Path) -> DemoEvidenceStatus:
    expanded = path.expanduser()
    if not expanded.exists():
        return DemoEvidenceStatus(
            path=str(expanded),
            exists=False,
            status="not_recorded",
            detail="No real demo evidence report has been recorded",
        )
    try:
        raw = json.loads(expanded.read_text(encoding="utf-8"))
        records_raw = raw.get("records") if isinstance(raw, dict) else None
        if not isinstance(records_raw, list):
            raise ValueError("demo evidence report must contain a records list")
        records = [DemoEvidenceRecord.model_validate(item) for item in records_raw if isinstance(item, dict)]
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        return DemoEvidenceStatus(
            path=str(expanded),
            exists=True,
            status="invalid",
            detail="Demo evidence report is unreadable",
            error=str(exc),
        )
    checked_at = str(raw.get("checked_at") or "") if isinstance(raw, dict) else ""
    latest = checked_at or _latest_checked_at(records)
    return DemoEvidenceStatus(
        path=str(expanded),
        exists=True,
        status="recorded" if records else "empty",
        checked_at=latest or None,
        records=records,
        detail=f"{len(records)} demo evidence record(s) available" if records else "Demo evidence report is empty",
    )


def write_demo_evidence_record(path: Path, record: dict[str, Any]) -> DemoEvidenceStatus:
    expanded = path.expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    checked_at = datetime.now(UTC).isoformat()
    merged: dict[str, DemoEvidenceRecord] = {}
    existing = read_demo_evidence(expanded)
    if existing.status != "invalid":
        merged = {item.id: item for item in existing.records}
    normalized = DemoEvidenceRecord.model_validate(
        {
            "checked_at": checked_at,
            **record,
            "command": [str(item) for item in record.get("command") or []],
            "speaker_labels": [str(item) for item in record.get("speaker_labels") or []],
        }
    )
    merged[normalized.id] = normalized
    ordered = sorted(merged.values(), key=lambda item: (_known_order(item.id), item.id))
    payload = {
        "checked_at": checked_at,
        "records": [item.model_dump(mode="json") for item in ordered],
    }
    tmp = expanded.with_suffix(f"{expanded.suffix}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(expanded)
    return read_demo_evidence(expanded)


def _known_order(record_id: str) -> int:
    order = {
        "mock_e2e": 10,
        "real_live_backend": 20,
        "real_browser_live": 30,
    }
    return order.get(record_id, 100)


def _latest_checked_at(records: list[DemoEvidenceRecord]) -> str:
    values = [item.checked_at for item in records if item.checked_at]
    return max(values) if values else ""
