import json
import os
from pathlib import Path

from app.auth.models import Role, UserRecord
from app.auth.security import hash_password

DEFAULT_USERS: list[dict[str, str]] = [
    {
        "id": "user-priya",
        "email": "priya@voiceops.dev",
        "name": "Priya Nair",
        "initials": "PN",
        "role": Role.ON_CALL.value,
        "password": "oncall123",
    },
    {
        "id": "user-viewer",
        "email": "viewer@voiceops.dev",
        "name": "Alex Chen",
        "initials": "AC",
        "role": Role.VIEWER.value,
        "password": "view123",
    },
    {
        "id": "user-admin",
        "email": "admin@voiceops.dev",
        "name": "Sam Ortiz",
        "initials": "SO",
        "role": Role.ADMIN.value,
        "password": "admin123",
    },
]


class UserStore:
    def __init__(self, store_path: Path, *, seed_demo_users: bool = True) -> None:
        self._path = store_path
        self._seed_demo_users = seed_demo_users
        self._users: dict[str, UserRecord] = {}
        if self._path.exists():
            os.chmod(self._path, 0o600)
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw.get("users", []):
                user = UserRecord.model_validate(item)
                self._users[user.email.lower()] = user
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._seed_demo_users:
            self._path.write_text(json.dumps({"users": []}, indent=2), encoding="utf-8")
            os.chmod(self._path, 0o600)
            return

        seeded: list[dict] = []
        for raw in DEFAULT_USERS:
            item = dict(raw)
            password = item.pop("password")
            record = {**item, "password_hash": hash_password(password)}
            seeded.append(record)
            self._users[item["email"].lower()] = UserRecord.model_validate(record)
        self._path.write_text(json.dumps({"users": seeded}, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"users": [user.model_dump(mode="json") for user in self._users.values()]}
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)

    def list_users(self) -> list[UserRecord]:
        return sorted(self._users.values(), key=lambda user: user.email.lower())

    def get_by_email(self, email: str) -> UserRecord | None:
        return self._users.get(email.lower())

    def get_by_id(self, user_id: str) -> UserRecord | None:
        for user in self._users.values():
            if user.id == user_id:
                return user
        return None

    def update_projects(self, user_id: str, projects: list[str]) -> UserRecord | None:
        normalized = _normalize_projects(projects)
        for email, user in self._users.items():
            if user.id == user_id:
                updated = user.model_copy(update={"projects": normalized})
                self._users[email] = updated
                self._persist()
                return updated
        return None


def _normalize_projects(projects: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for project in projects:
        value = project.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized
