from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPTS_ROOT / "bootstrap_local_runtime.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_local_runtime", SCRIPT_PATH)
bootstrap_local_runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = bootstrap_local_runtime
SPEC.loader.exec_module(bootstrap_local_runtime)


def test_bootstrap_local_runtime_writes_env_and_initializes_sqlite(tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path)
    env_path = tmp_path / ".env.local"
    sqlite_path = tmp_path / "data" / "collaboration.sqlite3"
    monkeypatch.setattr(bootstrap_local_runtime, "run_target_readiness", lambda **_kwargs: _target("needs_attention", 71))

    report = bootstrap_local_runtime.bootstrap_local_runtime(
        env_file=env_path,
        workspace=workspace,
        collab_sqlite=sqlite_path,
    )

    env_text = env_path.read_text(encoding="utf-8")
    assert report.env_path == str(env_path.resolve())
    assert report.event_store_ready is True
    assert sqlite_path.exists()
    assert f"VOICEOPS_WORKSPACE={workspace.resolve()}" in env_text
    assert "COLLAB_STORE_BACKEND=sqlite" in env_text
    assert f"COLLAB_SQLITE_PATH={sqlite_path.resolve()}" in env_text
    assert "SPEAKER_STORE_BACKEND=sqlite" in env_text
    assert "LLM_PROVIDER=mock" in env_text
    assert "GITHUB_PR_CREATION_ENABLED=false" in env_text
    assert report.next_command.endswith(f"--env-file {env_path.resolve()} --json")


def test_bootstrap_local_runtime_refuses_existing_env_without_force(tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path)
    env_path = tmp_path / ".env.local"
    env_path.write_text("existing=true\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap_local_runtime, "run_target_readiness", lambda **_kwargs: _target("ready", 100))

    try:
        bootstrap_local_runtime.bootstrap_local_runtime(
            env_file=env_path,
            workspace=workspace,
            collab_sqlite=tmp_path / "data" / "collaboration.sqlite3",
        )
    except ValueError as exc:
        assert "--force" in str(exc)
    else:
        raise AssertionError("existing env file should require --force")


def test_bootstrap_local_runtime_force_overwrites_env(tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path)
    env_path = tmp_path / ".env.local"
    env_path.write_text("existing=true\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap_local_runtime, "run_target_readiness", lambda **_kwargs: _target("ready", 100))

    report = bootstrap_local_runtime.bootstrap_local_runtime(
        env_file=env_path,
        workspace=workspace,
        collab_sqlite=tmp_path / "data" / "collaboration.sqlite3",
        force=True,
    )

    assert report.force is True
    assert "existing=true" not in env_path.read_text(encoding="utf-8")


def test_bootstrap_local_runtime_rejects_non_git_workspace_by_default(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()

    try:
        bootstrap_local_runtime.bootstrap_local_runtime(
            env_file=tmp_path / ".env.local",
            workspace=workspace,
            collab_sqlite=tmp_path / "data" / "collaboration.sqlite3",
        )
    except ValueError as exc:
        assert "local git repository" in str(exc)
    else:
        raise AssertionError("non-git workspace should be rejected")


def test_bootstrap_local_runtime_cli_prints_json(tmp_path, monkeypatch, capsys):
    workspace = _git_workspace(tmp_path)
    monkeypatch.setattr(bootstrap_local_runtime, "run_target_readiness", lambda **_kwargs: _target("ready", 100))

    exit_code = bootstrap_local_runtime.main(
        [
            "--env-file",
            str(tmp_path / ".env.local"),
            "--workspace",
            str(workspace),
            "--collab-sqlite",
            str(tmp_path / "data" / "collaboration.sqlite3"),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["target_readiness_score"] == 100


def test_bootstrap_local_runtime_can_seed_demo_room(tmp_path, monkeypatch):
    workspace = _git_workspace(tmp_path)
    (workspace / "app.py").write_text('print("ok")\n', encoding="utf-8")
    env_path = tmp_path / ".env.local"
    sqlite_path = tmp_path / "data" / "collaboration.sqlite3"
    monkeypatch.setattr(bootstrap_local_runtime, "run_target_readiness", lambda **_kwargs: _target("ready", 100))

    report = bootstrap_local_runtime.bootstrap_local_runtime(
        env_file=env_path,
        workspace=workspace,
        collab_sqlite=sqlite_path,
        seed_demo_room=True,
    )

    settings = bootstrap_local_runtime.Settings(_env_file=env_path)
    service = bootstrap_local_runtime.CollaborationService(
        bootstrap_local_runtime.create_collaboration_store(settings),
        agent_display_name=settings.agent_display_name,
        agent_initials=settings.agent_initials,
        agent_wake_words=settings.agent_wake_words,
    )
    snapshot = service.snapshot("main")
    dashboard = service.work_dashboard("main")

    assert report.demo_seeded is True
    assert snapshot.room.workspace_path == str(workspace.resolve())
    assert len(snapshot.messages) == 3
    assert len(snapshot.actions) == 1
    assert snapshot.actions[0].pending_approval is True
    assert dashboard.approvals


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return workspace


def _target(status: str, score: int):
    return SimpleNamespace(status=status, score=score)
