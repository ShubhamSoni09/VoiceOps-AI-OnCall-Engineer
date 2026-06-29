from __future__ import annotations

import json
import os
from pathlib import Path

from app.external_agents.models import ExternalAgentCredentialRecord


class ExternalAgentCredentialStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            os.chmod(self.path, 0o600)

    def list_for_user(self, user_id: str) -> list[ExternalAgentCredentialRecord]:
        return [record for record in self._read() if record.user_id == user_id]

    def get(self, user_id: str, provider: str) -> ExternalAgentCredentialRecord | None:
        for record in self._read():
            if record.user_id == user_id and record.provider == provider:
                return record
        return None

    def upsert(self, record: ExternalAgentCredentialRecord) -> ExternalAgentCredentialRecord:
        records = self._read()
        replaced = False
        for index, existing in enumerate(records):
            if existing.user_id == record.user_id and existing.provider == record.provider:
                records[index] = record
                replaced = True
                break
        if not replaced:
            records.append(record)
        self._write(records)
        return record

    def delete(self, user_id: str, provider: str) -> bool:
        records = self._read()
        remaining = [record for record in records if not (record.user_id == user_id and record.provider == provider)]
        changed = len(remaining) != len(records)
        if changed:
            self._write(remaining)
        return changed

    def _read(self) -> list[ExternalAgentCredentialRecord]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        if isinstance(data, dict):
            data = data.get("credentials", [])
        return [ExternalAgentCredentialRecord.model_validate(item) for item in data]

    def _write(self, records: list[ExternalAgentCredentialRecord]) -> None:
        payload = [record.model_dump(mode="json") for record in records]
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self.path, 0o600)
