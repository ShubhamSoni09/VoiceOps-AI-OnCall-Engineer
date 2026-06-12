import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

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
    if not workspace.connected:
        return [
            IntegrationStatus(id="github", label="GitHub", connected=False, detail="No repo connected"),
            IntegrationStatus(id="mcp", label="MCP workspace", connected=False, detail="Set VOICEOPS_WORKSPACE"),
            IntegrationStatus(id="render", label="Render", connected=False),
            IntegrationStatus(id="clickhouse", label="ClickHouse", connected=False),
            IntegrationStatus(id="slack", label="Slack / PagerDuty", connected=False),
        ]

    return [
        IntegrationStatus(
            id="github",
            label="GitHub",
            connected=workspace.is_git_repo,
            detail=workspace.remote_url or "Local git repo",
        ),
        IntegrationStatus(
            id="mcp",
            label="MCP workspace",
            connected=True,
            detail=workspace.name,
        ),
        IntegrationStatus(id="render", label="Render", connected=False, detail="Not configured"),
        IntegrationStatus(id="clickhouse", label="ClickHouse", connected=False, detail="Not configured"),
        IntegrationStatus(id="slack", label="Slack / PagerDuty", connected=False, detail="Not configured"),
    ]
