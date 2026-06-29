from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any


class JsonTTLCache:
    """Small rebuildable JSON cache for local dev read models."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()

    def get(self, key: str, *, source_token: str | None = None) -> Any | None:
        with self._lock:
            data = self._load()
            item = data.get(key)
            if not item:
                return None
            if float(item.get("expires_at", 0)) <= time.time():
                data.pop(key, None)
                self._persist(data)
                return None
            if source_token is not None and item.get("source_token") != source_token:
                data.pop(key, None)
                self._persist(data)
                return None
            return copy.deepcopy(item.get("value"))

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: float,
        source_token: str | None = None,
        kind: str = "rebuildable",
    ) -> None:
        if ttl_seconds <= 0:
            return
        with self._lock:
            data = self._load()
            data[key] = {
                "value": value,
                "expires_at": time.time() + ttl_seconds,
                "source_token": source_token,
                "kind": kind,
            }
            self._persist(data)

    def delete_prefix(self, prefix: str) -> None:
        with self._lock:
            data = self._load()
            keys = [key for key in data if key.startswith(prefix)]
            if not keys:
                return
            for key in keys:
                data.pop(key, None)
            self._persist(data)

    def clear(self) -> int:
        with self._lock:
            data = self._load()
            count = len(data)
            self._persist({})
            return count

    def status(self) -> dict[str, Any]:
        with self._lock:
            data = self._load()
            now = time.time()
            expired = sum(1 for item in data.values() if float(item.get("expires_at", 0)) <= now)
            size_bytes = self._path.stat().st_size if self._path.exists() else 0
            return {
                "path": str(self._path),
                "exists": self._path.exists(),
                "entries": len(data),
                "expired_entries": expired,
                "rebuildable_entries": sum(1 for item in data.values() if item.get("kind", "rebuildable") == "rebuildable"),
                "fingerprinted_entries": sum(1 for item in data.values() if item.get("source_token")),
                "size_bytes": size_bytes,
            }

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _persist(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)
