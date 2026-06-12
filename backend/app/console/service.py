import os
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from app.console.mock_data import (
    build_sandbox_mock_artifacts,
    build_sandbox_mock_incidents,
    build_sandbox_mock_metrics,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class WorkspaceInfo(BaseModel):
    connected: bool = False
    path: str | None = None
    name: str | None = None
    branch: str | None = None
    remote_url: str | None = None
    is_git_repo: bool = False
    readme_line: str | None = None


class IntegrationStatus(BaseModel):
    id: str
    label: str
    connected: bool
    detail: str | None = None


class ConsoleBootstrap(BaseModel):
    user: dict
    workspace: WorkspaceInfo
    incidents: list[dict] = Field(default_factory=list)
    metrics: list[dict] = Field(default_factory=list)
    artifacts: list[dict] = Field(default_factory=list)
    integrations: list[IntegrationStatus] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=lambda: {"critical": 0, "active": 0})


def _run_git(workspace: Path, *args: str) -> str | None:
    if not (workspace / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _readme_first_line(workspace: Path) -> str | None:
    for name in ("README.md", "readme.md", "README"):
        readme = workspace / name
        if readme.is_file():
            try:
                first = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
                return next((line.strip() for line in first if line.strip()), None)
            except OSError:
                return None
    return None


def resolve_configured_workspace(configured: str | None) -> Path | None:
    if not configured or not configured.strip():
        return None
    root = Path(configured.strip())
    if not root.is_absolute():
        root = (REPO_ROOT / root).resolve()
    else:
        root = root.resolve()
    if not root.exists() or not root.is_dir():
        return None
    return root


def get_workspace_info(configured: str | None) -> WorkspaceInfo:
    root = resolve_configured_workspace(configured)
    if root is None:
        return WorkspaceInfo(connected=False)

    branch = _run_git(root, "branch", "--show-current")
    remote = _run_git(root, "remote", "get-url", "origin")
    is_git = (root / ".git").exists()

    return WorkspaceInfo(
        connected=True,
        path=str(root),
        name=root.name,
        branch=branch,
        remote_url=remote,
        is_git_repo=is_git,
        readme_line=_readme_first_line(root),
    )


def build_integrations(workspace: WorkspaceInfo) -> list[IntegrationStatus]:
    on_render = bool(os.environ.get("RENDER"))

    return [
        IntegrationStatus(
            id="workspace",
            label="Workspace",
            connected=workspace.connected,
            detail=workspace.name if workspace.connected else "Set VOICEOPS_WORKSPACE",
        ),
        IntegrationStatus(
            id="render",
            label="Render",
            connected=on_render,
            detail="Hosted on Render" if on_render else "Not configured",
        ),
        IntegrationStatus(id="clickhouse", label="ClickHouse", connected=False, detail="Not configured"),
        IntegrationStatus(id="slack", label="Slack / PagerDuty", connected=False, detail="Not configured"),
    ]


def _app_source(workspace: WorkspaceInfo) -> str:
    if not workspace.path:
        return ""
    app_py = Path(workspace.path) / "app.py"
    if not app_py.is_file():
        return ""
    try:
        return app_py.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def run_pytest_status(workspace: WorkspaceInfo) -> tuple[bool, int]:
    """Return (all_tests_pass, failing_test_count)."""
    if not workspace.path:
        return False, 4
    root = Path(workspace.path)
    if not root.is_dir():
        return False, 4
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", "-q", "--tb=no"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=90,
        )
        combined = result.stdout + result.stderr
        if result.returncode == 0:
            return True, 0
        match = re.search(r"^([\.FEx]+)\s*\[", combined, re.MULTILINE)
        if match:
            return False, match.group(1).count("F")
        failed = len(re.findall(r"^FAILED\s+", combined, re.MULTILINE))
        return False, failed or 1
    except (OSError, subprocess.TimeoutExpired):
        return False, 4
