import os
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from app.workspace.tools import configured_workspace_is_url

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


class WorkspaceInfo(BaseModel):
    connected: bool = False
    configured_workspace: str | None = None
    source: str = "none"
    persistence_note: str | None = None
    setup_issue: str | None = None
    path: str | None = None
    name: str | None = None
    branch: str | None = None
    remote_url: str | None = None
    remote_kind: str = "none"
    remote_web_url: str | None = None
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


def get_workspace_info(configured: str | None, *, source: str = "none") -> WorkspaceInfo:
    configured_value = configured.strip() if configured and configured.strip() else None
    if configured_workspace_is_url(configured_value):
        return WorkspaceInfo(
            connected=False,
            configured_workspace=configured_value,
            source=source,
            persistence_note=_workspace_persistence_note(source),
            setup_issue="VOICEOPS_WORKSPACE must point to a local clone path, not a GitHub or git remote URL.",
        )

    root = resolve_configured_workspace(configured)
    if root is None:
        return WorkspaceInfo(
            connected=False,
            configured_workspace=configured_value,
            source=source,
            persistence_note=_workspace_persistence_note(source),
        )

    branch = _run_git(root, "branch", "--show-current")
    remote = _run_git(root, "remote", "get-url", "origin")
    is_git = (root / ".git").exists()
    remote_kind, remote_web_url = _remote_metadata(remote)

    return WorkspaceInfo(
        connected=True,
        configured_workspace=configured_value,
        source=source,
        persistence_note=_workspace_persistence_note(source),
        path=str(root),
        name=root.name,
        branch=branch,
        remote_url=remote,
        remote_kind=remote_kind,
        remote_web_url=remote_web_url,
        is_git_repo=is_git,
        readme_line=_readme_first_line(root),
    )


def _workspace_persistence_note(source: str) -> str | None:
    if source == "configured":
        return "Configured by VOICEOPS_WORKSPACE; UI switches affect the current backend process only unless env changes."
    if source == "runtime":
        return "Switched from the admin UI and saved for restart when VOICEOPS_WORKSPACE is empty."
    if source == "saved":
        return "Loaded from the saved admin workspace selection."
    return None


def build_integrations(workspace: WorkspaceInfo) -> list[IntegrationStatus]:
    if not workspace.connected:
        detail = workspace.setup_issue or "Set VOICEOPS_WORKSPACE"
        return [
            IntegrationStatus(id="github", label="GitHub", connected=False, detail="No local clone connected"),
            IntegrationStatus(id="mcp", label="MCP workspace", connected=False, detail=detail),
            IntegrationStatus(id="render", label="Render", connected=False),
            IntegrationStatus(id="clickhouse", label="ClickHouse", connected=False),
            IntegrationStatus(id="slack", label="Slack", connected=False),
        ]

    github_connected = workspace.is_git_repo and workspace.remote_kind == "github"
    github_detail = _github_integration_detail(workspace)
    return [
        IntegrationStatus(
            id="github",
            label="GitHub",
            connected=github_connected,
            detail=github_detail,
        ),
        IntegrationStatus(
            id="mcp",
            label="MCP workspace",
            connected=True,
            detail=workspace.name,
        ),
        IntegrationStatus(id="render", label="Render", connected=False, detail="Not configured"),
        IntegrationStatus(id="clickhouse", label="ClickHouse", connected=False, detail="Not configured"),
        IntegrationStatus(id="slack", label="Slack", connected=False, detail="Not configured"),
    ]


def _remote_metadata(remote_url: str | None) -> tuple[str, str | None]:
    remote = (remote_url or "").strip()
    if not remote:
        return "none", None
    github_web_url = _github_web_url(remote)
    if github_web_url:
        return "github", github_web_url
    return "git", None


def _github_web_url(remote_url: str) -> str | None:
    patterns = (
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, remote_url)
        if match:
            return f"https://github.com/{match.group('owner')}/{match.group('repo')}"
    return None


def _github_integration_detail(workspace: WorkspaceInfo) -> str:
    if not workspace.is_git_repo:
        return "Local folder, git not initialized"
    if workspace.remote_kind == "github":
        return workspace.remote_web_url or "GitHub remote"
    if workspace.remote_kind == "git":
        return "Non-GitHub git remote"
    return "Local git repo, no GitHub remote"
