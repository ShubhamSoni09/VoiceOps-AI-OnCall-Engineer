from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.auth.models import UserPublic
from app.collab.models import JoinRoomRequest, TextMessageRequest
from app.collab.service import CollaborationService
from app.collab.store import CollaborationStore


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "migrate_collab_json_to_sqlite.py"
SPEC = importlib.util.spec_from_file_location("migrate_collab_json_to_sqlite", SCRIPT_PATH)
migrate_script = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = migrate_script
SPEC.loader.exec_module(migrate_script)


def test_migration_script_prints_dry_run_json_report(tmp_path, capsys):
    json_path = tmp_path / "collab.json"
    sqlite_path = tmp_path / "collab.sqlite3"
    _seed_json_store(json_path)

    exit_code = migrate_script.main([
        "--json",
        str(json_path),
        "--sqlite",
        str(sqlite_path),
        "--dry-run",
        "--report-json",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["dry_run"] is True
    assert output["messages"] == 1
    assert output["validation"]["status"] == "skipped"
    assert not sqlite_path.exists()


def test_migration_script_returns_nonzero_for_existing_target(tmp_path, capsys):
    json_path = tmp_path / "collab.json"
    sqlite_path = tmp_path / "collab.sqlite3"
    _seed_json_store(json_path)
    sqlite_path.write_text("placeholder", encoding="utf-8")

    exit_code = migrate_script.main([
        "--json",
        str(json_path),
        "--sqlite",
        str(sqlite_path),
        "--report-json",
    ])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["status"] == "failed"
    assert "already exists" in output["error"]


def _seed_json_store(path: Path) -> None:
    service = CollaborationService(CollaborationStore(path))
    user = UserPublic(
        id="user-test",
        email="test@example.com",
        name="Test User",
        initials="TU",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
    service.join_room("main", user, JoinRoomRequest(room_name="Migration room"))
    service.add_user_message("main", user, TextMessageRequest(text="Migration script dry run."))
