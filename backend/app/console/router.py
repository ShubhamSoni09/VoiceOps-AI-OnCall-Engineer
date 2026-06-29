import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.config import Settings, get_settings, persist_workspace_selection
from app.console.service import ConsoleBootstrap, build_integrations, get_workspace_info
from app.redaction import redact_sensitive_text
from app.workspace.github import clone_github_repo

router = APIRouter(prefix="/console", tags=["console"])


class WorkspaceConnectRequest(BaseModel):
    path: str = Field(min_length=1)


class WorkspaceCloneRequest(BaseModel):
    remote_url: str = Field(min_length=1)
    target_path: str | None = None


@router.get("/bootstrap", response_model=ConsoleBootstrap)
async def bootstrap(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    return _bootstrap(user, settings)


@router.post("/workspace", response_model=ConsoleBootstrap)
async def connect_workspace(
    body: WorkspaceConnectRequest,
    user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> ConsoleBootstrap:
    requested = body.path.strip()
    workspace = get_workspace_info(requested)
    if not workspace.connected or not workspace.path:
        raise HTTPException(
            status_code=400,
            detail=workspace.setup_issue or "Workspace path must be an existing local directory.",
        )
    # ponytail: process-local workspace selection; move to per-user/team store when multiple repos are open at once.
    settings.voiceops_workspace = workspace.path
    settings.voiceops_workspace_source = "runtime"
    persist_workspace_selection(settings, workspace.path)
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
    target = _clone_target(body.target_path, remote_url, settings)
    if target.exists() and any(target.iterdir()):
        raise HTTPException(status_code=409, detail="Target path already exists and is not empty.")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(clone_github_repo, remote_url, target)
    if result.returncode != 0:
        detail = redact_sensitive_text(result.stderr or result.stdout or "git clone failed").strip()[-1000:]
        raise HTTPException(status_code=409, detail=detail)
    # ponytail: clone then reuse the local workspace attach path; OAuth/private repo picker can replace this later.
    settings.voiceops_workspace = str(target.resolve())
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


def _clone_target(target_path: str | None, remote_url: str, settings: Settings) -> Path:
    if target_path and target_path.strip():
        target = Path(target_path.strip()).expanduser()
        if not target.is_absolute():
            raise HTTPException(status_code=400, detail="Target path must be absolute.")
        return _ensure_clone_target_inside_root(target, settings)
    repo_name = _github_repo_name(remote_url)
    if not repo_name:
        raise HTTPException(status_code=400, detail="Could not derive repository name from GitHub URL.")
    return _ensure_clone_target_inside_root(settings.workspace_clone_root / repo_name, settings)


def _ensure_clone_target_inside_root(target: Path, settings: Settings) -> Path:
    root = settings.workspace_clone_root.expanduser().resolve()
    resolved = target.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Clone target must be inside WORKSPACE_CLONE_ROOT.") from exc
    return resolved


def _github_repo_name(remote_url: str) -> str:
    match = re.match(r"^(?:https://github\.com/[^/\s]+/|git@github\.com:[^/\s]+/)([^/\s]+?)(?:\.git)?$", remote_url)
    if not match:
        return ""
    return re.sub(r"[^\w.-]", "", match.group(1))
