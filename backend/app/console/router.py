import html
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.config import Settings, get_settings, persist_workspace_selection
from app.console.service import ConsoleBootstrap, build_integrations, get_workspace_info
from app.workspace.github_auth import complete_github_oauth, github_oauth_status, start_github_oauth

router = APIRouter(prefix="/console", tags=["console"])


class WorkspaceConnectRequest(BaseModel):
    path: str = Field(min_length=1)


class WorkspaceCloneRequest(BaseModel):
    remote_url: str = Field(min_length=1)
    target_path: str | None = None


class GitHubOAuthStatus(BaseModel):
    connected: bool
    configured: bool
    source: str
    scope: str = ""
    login: str | None = None


class GitHubOAuthStartResponse(BaseModel):
    authorize_url: str
    redirect_uri: str
    scope: str


@router.get("/bootstrap", response_model=ConsoleBootstrap)
async def bootstrap(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    return _bootstrap(user, settings)


@router.get("/github/oauth/status", response_model=GitHubOAuthStatus)
async def github_oauth_connection_status(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return github_oauth_status(settings)


@router.post("/github/oauth/start", response_model=GitHubOAuthStartResponse)
async def start_github_oauth_login(
    user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        return start_github_oauth(settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/github/oauth/callback")
async def complete_github_oauth_login(
    code: str = Query(default=""),
    state: str = Query(default=""),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    try:
        status = complete_github_oauth(settings, code=code, state=state)
    except ValueError as exc:
        return HTMLResponse(
            f"<h1>GitHub connection failed</h1><p>{html.escape(str(exc))}</p>",
            status_code=400,
        )
    account = html.escape(str(status.get("login") or "GitHub"))
    return HTMLResponse(f"<h1>GitHub connected</h1><p>{account} is ready. You can close this window.</p>")


@router.post("/workspace", response_model=ConsoleBootstrap)
async def connect_workspace(
    body: WorkspaceConnectRequest,
    user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    requested = body.path.strip()
    workspace = get_workspace_info(requested)
    if not workspace.connected or (not workspace.path and workspace.remote_kind != "github"):
        raise HTTPException(
            status_code=400,
            detail=workspace.setup_issue or "Workspace must be an existing local directory or GitHub repository URL.",
        )
    # ponytail: process-local workspace selection; move to per-user/team store when multiple repos are open at once.
    settings.voiceops_workspace = workspace.path or requested
    settings.voiceops_workspace_source = "runtime"
    persist_workspace_selection(settings, settings.voiceops_workspace)
    return _bootstrap(user, settings)


@router.post("/workspace/clone", response_model=ConsoleBootstrap)
async def clone_workspace(
    body: WorkspaceCloneRequest,
    user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    remote_url = body.remote_url.strip()
    if not _is_github_clone_url(remote_url):
        raise HTTPException(status_code=400, detail="Clone URL must be a GitHub HTTPS or SSH repository URL.")
    # ponytail: keep the old endpoint shape; connecting a GitHub URL no longer creates a local clone.
    settings.voiceops_workspace = remote_url
    settings.voiceops_workspace_source = "runtime"
    persist_workspace_selection(settings, settings.voiceops_workspace)
    return _bootstrap(user, settings)


def _bootstrap(user: UserPublic, settings: Settings) -> ConsoleBootstrap:
    workspace = get_workspace_info(
        settings.voiceops_workspace,
        source=settings.voiceops_workspace_source,
    )
    return ConsoleBootstrap(
        user=user.model_dump(),
        workspace=workspace,
        incidents=[],
        metrics=[],
        artifacts=[],
        integrations=build_integrations(workspace),
        status_counts={"critical": 0, "active": 0},
    )


def _is_github_clone_url(value: str) -> bool:
    return bool(
        re.match(r"^https://github\.com/[^/\s]+/[^/\s]+?(?:\.git)?$", value)
        or re.match(r"^git@github\.com:[^/\s]+/[^/\s]+?(?:\.git)?$", value)
    )
