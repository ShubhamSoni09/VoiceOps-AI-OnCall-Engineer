import json
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
    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._users: dict[str, UserRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for item in raw.get("users", []):
                user = UserRecord.model_validate(item)
                self._users[user.email.lower()] = user
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        seeded: list[dict] = []
        for raw in DEFAULT_USERS:
            item = dict(raw)
            password = item.pop("password")
            record = {**item, "password_hash": hash_password(password)}
            seeded.append(record)
            self._users[item["email"].lower()] = UserRecord.model_validate(record)
        self._path.write_text(json.dumps({"users": seeded}, indent=2), encoding="utf-8")

    def get_by_email(self, email: str) -> UserRecord | None:
        return self._users.get(email.lower())

    def get_by_id(self, user_id: str) -> UserRecord | None:
        for user in self._users.values():
            if user.id == user_id:
                return user
        return None
