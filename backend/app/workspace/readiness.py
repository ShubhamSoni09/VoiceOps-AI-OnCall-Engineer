from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.workspace.git import WorkspaceGitService
from app.workspace.models import WorkspaceReadinessCheck, WorkspaceReadinessResponse
from app.workspace.tools import configured_workspace_is_url, resolve_configured_workspace


class WorkspaceReadinessService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def inspect(self) -> WorkspaceReadinessResponse:
        configured = self._settings.voiceops_workspace
        if configured_workspace_is_url(configured):
            checks = [
                _check(
                    "workspace_configured",
                    False,
                    "error",
                    "VOICEOPS_WORKSPACE must be a local clone path, not a GitHub or git remote URL.",
                )
            ]
            return WorkspaceReadinessResponse(ready=False, workspace=configured, checks=checks)

        root = resolve_configured_workspace(self._settings.voiceops_workspace)
        checks: list[WorkspaceReadinessCheck] = []
        if root is None:
            checks.append(_check("workspace_configured", False, "error", "No workspace is configured or the path does not exist."))
            return WorkspaceReadinessResponse(ready=False, workspace=self._settings.voiceops_workspace, checks=checks)

        checks.append(_check("workspace_configured", True, "ok", "Workspace path exists and is a directory."))
        git_status = WorkspaceGitService(self._settings).status(use_cache=False)
        checks.append(
            _check(
                "git_repo",
                git_status.is_git_repo,
                "ok" if git_status.is_git_repo else "warning",
                git_status.warning or "Workspace is a git repository.",
            )
        )
        test_command = _detect_test_command(root)
        checks.append(
            _check(
                "test_command",
                bool(test_command),
                "ok" if test_command else "warning",
                f"Detected test command: {test_command}" if test_command else "No obvious test command was detected.",
            )
        )
        checks.append(
            _check(
                "branch_workflow",
                git_status.is_git_repo,
                "ok" if git_status.is_git_repo else "warning",
                "Local branch workflow is available." if git_status.is_git_repo else "Branch workflow requires a git repository.",
            )
        )
        blockers = [item for item in checks if item.severity == "error" or (item.id == "git_repo" and not item.ready)]
        return WorkspaceReadinessResponse(
            ready=not blockers,
            workspace=str(root),
            root_name=root.name,
            branch=git_status.branch,
            dirty=git_status.dirty,
            test_command=test_command,
            checks=checks,
        )


def _detect_test_command(root: Path) -> str | None:
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "test_app.py").exists() or (root / "tests").exists():
        return "python -m pytest -q"
    if (root / "package.json").exists():
        return "npm test"
    return None


def _check(id: str, ready: bool, severity: str, detail: str) -> WorkspaceReadinessCheck:
    return WorkspaceReadinessCheck(id=id, ready=ready, severity=severity, detail=detail)
