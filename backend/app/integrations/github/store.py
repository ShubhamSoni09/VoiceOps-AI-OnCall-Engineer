from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel


class GitHubConnection(BaseModel):
    access_token: str
    github_login: str
    github_id: int | None = None
    connected_at: str
    selected_repo: str | None = None


class GitHubTokenStore:
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._connections: dict[str, GitHubConnection] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text("{}", encoding="utf-8")
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        for user_id, item in raw.items():
            self._connections[user_id] = GitHubConnection.model_validate(item)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {uid: conn.model_dump() for uid, conn in self._connections.items()}
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get(self, user_id: str) -> GitHubConnection | None:
        return self._connections.get(user_id)

    def set(self, user_id: str, connection: GitHubConnection) -> None:
        self._connections[user_id] = connection
        self._save()

    def delete(self, user_id: str) -> None:
        self._connections.pop(user_id, None)
        self._save()

    def is_connected(self, user_id: str) -> bool:
        return user_id in self._connections


from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_github_token_store() -> GitHubTokenStore:
    settings = get_settings()
    return GitHubTokenStore(settings.github_tokens_path)
