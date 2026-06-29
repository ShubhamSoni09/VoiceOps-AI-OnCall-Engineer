from app.auth.models import UserPublic
from app.collab.event_store_readiness import build_event_store_readiness
from app.collab.models import JoinRoomRequest, TextMessageRequest
from app.collab.service import CollaborationService
from app.collab.sqlite_store import SQLiteCollaborationStore
from app.collab.store import CollaborationStore
from app.config import Settings


def test_event_store_readiness_reports_json_migration_path(tmp_path):
    json_path = tmp_path / "collab.json"
    sqlite_path = tmp_path / "collab.sqlite3"
    sqlite_path.write_text("existing target", encoding="utf-8")
    service = CollaborationService(CollaborationStore(json_path))
    user = _user("usr_alice")
    service.join_room("main", user, JoinRoomRequest(room_name="Migration room"))
    service.add_user_message("main", user, TextMessageRequest(text="We decided to preserve audit history."))

    report = build_event_store_readiness(
        Settings(
            collab_store_backend="json",
            collab_store_path=json_path,
            collab_sqlite_path=sqlite_path,
        )
    )

    assert report.ready is False
    assert report.status == "migration_available"
    assert report.runtime_mode == "development_json"
    assert report.production_ready is False
    assert report.local_demo_acceptable is True
    assert report.recommended_backend == "sqlite"
    assert report.bootstrap_command and "bootstrap_local_runtime.py" in report.bootstrap_command
    assert report.migration_available is True
    assert "migrate_collab_json_to_sqlite.py" in report.migration_command
    assert "--report-json" in report.migration_command
    assert report.cutover_env["COLLAB_STORE_BACKEND"] == "sqlite"
    assert report.cutover_env["COLLAB_SQLITE_PATH"] == str(sqlite_path)
    assert report.state_counts["rooms"] == 1
    assert report.state_counts["messages"] == 1
    assert any("already exists" in warning for warning in report.warnings)
    assert any("local demos" in note for note in report.operator_notes)
    assert any(check.id == "migration_script" and check.ready for check in report.checks)


def test_event_store_readiness_validates_sqlite_event_log(tmp_path):
    sqlite_path = tmp_path / "collab.sqlite3"
    service = CollaborationService(SQLiteCollaborationStore(sqlite_path))
    user = _user("usr_bob")
    service.join_room("main", user, JoinRoomRequest(room_name="SQLite room"))
    service.add_user_message("main", user, TextMessageRequest(text="Event log should be ordered."))

    report = build_event_store_readiness(
        Settings(
            collab_store_backend="sqlite",
            collab_sqlite_path=sqlite_path,
        )
    )
    checks = {check.id: check for check in report.checks}

    assert report.ready is True
    assert report.status == "ready"
    assert report.backend == "sqlite"
    assert report.runtime_mode == "durable_sqlite"
    assert report.production_ready is True
    assert report.bootstrap_command is None
    assert report.event_count >= 3
    assert report.stream_count >= 2
    assert report.latest_position == report.event_count
    assert report.event_types["message.appended"] == 1
    assert report.state_counts["rooms"] == 1
    assert checks["schema"].ready is True
    assert checks["event_indexes"].ready is True
    assert checks["global_order"].ready is True


def test_event_store_readiness_points_fresh_sqlite_to_init_command(tmp_path):
    sqlite_path = tmp_path / "collab.sqlite3"

    report = build_event_store_readiness(
        Settings(
            collab_store_backend="sqlite",
            collab_sqlite_path=sqlite_path,
        )
    )

    assert report.ready is False
    assert report.status == "needs_attention"
    assert report.runtime_mode == "sqlite_pending"
    assert report.production_ready is False
    assert report.local_demo_acceptable is False
    assert "init_collab_event_store.py" in report.migration_command
    assert report.init_command == report.migration_command
    assert report.bootstrap_command and "bootstrap_local_runtime.py" in report.bootstrap_command
    assert str(sqlite_path) in report.migration_command
    assert any(check.id == "sqlite_file" and check.status == "missing" for check in report.checks)


def test_event_store_readiness_rejects_invalid_sqlite_file(tmp_path):
    sqlite_path = tmp_path / "collab.sqlite3"
    sqlite_path.write_text("not sqlite", encoding="utf-8")

    report = build_event_store_readiness(
        Settings(
            collab_store_backend="sqlite",
            collab_sqlite_path=sqlite_path,
        )
    )

    assert report.ready is False
    assert any(check.id == "sqlite_open" and not check.ready for check in report.checks)


def _user(user_id: str) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"{user_id}@voiceops.dev",
        name="Test User",
        initials="TU",
        role="on_call",
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
