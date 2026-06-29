import subprocess

from app.config import Settings
from app.workspace.readiness import WorkspaceReadinessService


def test_workspace_readiness_reports_missing_workspace(tmp_path):
    settings = Settings(voiceops_workspace=str(tmp_path / "missing"))

    result = WorkspaceReadinessService(settings).inspect()

    assert result.ready is False
    assert result.checks[0].id == "workspace_configured"
    assert result.checks[0].severity == "error"


def test_workspace_readiness_rejects_github_url_as_workspace():
    settings = Settings(voiceops_workspace="https://github.com/team/app.git")

    result = WorkspaceReadinessService(settings).inspect()

    assert result.ready is False
    assert result.workspace == "https://github.com/team/app.git"
    assert result.checks[0].id == "workspace_configured"
    assert result.checks[0].severity == "error"
    assert "local clone path" in result.checks[0].detail
    assert "not a GitHub or git remote URL" in result.checks[0].detail


def test_workspace_readiness_rejects_ssh_git_remote_as_workspace():
    settings = Settings(voiceops_workspace="git@github.com:team/app.git")

    result = WorkspaceReadinessService(settings).inspect()

    assert result.ready is False
    assert "local clone path" in result.checks[0].detail


def test_workspace_readiness_reports_git_and_test_command(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "initial", "-q"], cwd=workspace, check=True)
    settings = Settings(voiceops_workspace=str(workspace), voiceops_cache_path=tmp_path / "cache.json")

    result = WorkspaceReadinessService(settings).inspect()

    assert result.ready is True
    assert result.root_name == "repo"
    assert result.branch in {"main", "master"}
    assert result.test_command == "python -m pytest -q"
    assert {check.id: check.ready for check in result.checks}["git_repo"] is True
