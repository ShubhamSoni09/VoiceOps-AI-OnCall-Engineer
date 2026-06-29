from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.rag.models import RagIndexDocument, RagIndexStatus


class JsonRagIndexStore:
    def __init__(self, path: Path, *, provider: str) -> None:
        self._path = path.expanduser()
        self._provider = provider

    @property
    def path(self) -> Path:
        return self._path

    def load_room(self, room_id: str) -> list[RagIndexDocument]:
        raw = self._load_raw()
        docs = raw.get("documents", [])
        if not isinstance(docs, list):
            return []
        result = []
        for item in docs:
            if not isinstance(item, dict) or item.get("room_id") != room_id:
                continue
            try:
                result.append(RagIndexDocument.model_validate(item))
            except ValidationError:
                continue
        return result

    def replace_room(self, room_id: str, documents: list[RagIndexDocument]) -> None:
        raw = self._load_raw()
        docs = raw.get("documents", [])
        if not isinstance(docs, list):
            docs = []
        retained = [item for item in docs if isinstance(item, dict) and item.get("room_id") != room_id]
        next_docs = retained + [doc.model_dump(mode="json") for doc in documents]
        payload = {
            "provider": self._provider,
            "updated_at": datetime.now(UTC).isoformat(),
            "documents": next_docs,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)
        os.chmod(self._path, 0o600)

    def status(self) -> RagIndexStatus:
        raw = self._load_raw()
        docs = raw.get("documents", [])
        if not isinstance(docs, list):
            docs = []
        rooms = {str(item.get("room_id")) for item in docs if isinstance(item, dict) and item.get("room_id")}
        return RagIndexStatus(
            path=str(self._path),
            provider=str(raw.get("provider") or self._provider),
            document_count=len(docs),
            room_count=len(rooms),
            updated_at=raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else None,
        )

    def _load_raw(self) -> dict:
        if not self._path.exists():
            return {"provider": self._provider, "documents": []}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"provider": self._provider, "documents": []}
        return raw if isinstance(raw, dict) else {"provider": self._provider, "documents": []}
