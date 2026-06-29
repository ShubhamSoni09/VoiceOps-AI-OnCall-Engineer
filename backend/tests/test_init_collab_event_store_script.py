from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "init_collab_event_store.py"
SPEC = importlib.util.spec_from_file_location("init_collab_event_store", SCRIPT_PATH)
init_collab_event_store = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = init_collab_event_store
SPEC.loader.exec_module(init_collab_event_store)


def test_init_collab_event_store_creates_schema_without_demo_events(tmp_path, capsys):
    sqlite_path = tmp_path / "collab.sqlite3"

    exit_code = init_collab_event_store.main(["--sqlite", str(sqlite_path), "--report-json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["ready"] is True
    assert output["created"] is True
    assert output["event_count"] == 0
    assert output["stream_count"] == 0
    assert sqlite_path.exists()
    assert any(check["id"] == "schema" and check["ready"] for check in output["checks"])
    assert any("no collaboration events" in warning for warning in output["warnings"])


def test_init_collab_event_store_rejects_existing_file_without_replace(tmp_path, capsys):
    sqlite_path = tmp_path / "collab.sqlite3"
    sqlite_path.write_text("existing", encoding="utf-8")

    exit_code = init_collab_event_store.main(["--sqlite", str(sqlite_path), "--report-json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert output["status"] == "failed"
    assert "already exists" in output["error"]
