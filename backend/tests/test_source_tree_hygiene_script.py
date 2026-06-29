from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "source_tree_hygiene.py"
SPEC = importlib.util.spec_from_file_location("source_tree_hygiene", SCRIPT_PATH)
source_tree_hygiene = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = source_tree_hygiene
SPEC.loader.exec_module(source_tree_hygiene)


def test_source_tree_hygiene_accepts_current_ignore_rules():
    report = source_tree_hygiene.run_source_tree_hygiene()

    assert report["ready"] is True, report["next_steps"]


def test_source_tree_hygiene_reports_visible_generated_artifacts(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("backend/data/*.json\n", encoding="utf-8")
    artifact = tmp_path / "backend" / "data" / "users.sqlite3"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("runtime", encoding="utf-8")

    report = source_tree_hygiene.run_source_tree_hygiene(tmp_path)

    assert report["ready"] is False
    assert any("backend/data/users.sqlite3" in step for step in report["next_steps"])


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
