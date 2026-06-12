from pathlib import Path

from app.workspace.ops import resolve_ops_workspace


def test_resolve_ops_workspace_prefers_nested_sandbox(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sandbox").mkdir()
    (repo / "sandbox" / "app.py").write_text("app = 1\n", encoding="utf-8")

    assert resolve_ops_workspace(repo) == (repo / "sandbox").resolve()


def test_resolve_ops_workspace_uses_root_app_py(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("app = 1\n", encoding="utf-8")

    assert resolve_ops_workspace(ws) == ws.resolve()
