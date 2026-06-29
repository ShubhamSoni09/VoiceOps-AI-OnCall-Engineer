from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.config import Settings, get_settings
from app.workspace.git import WorkspaceGitService
from app.workspace.models import (
    GitDiffResponse,
    GitStatusResponse,
    WorkspaceFileResponse,
    WorkspaceReadinessResponse,
    WorkspaceSearchResponse,
    WorkspaceTreeResponse,
)
from app.workspace.readiness import WorkspaceReadinessService
from app.workspace.service import WorkspaceCodeService
from app.workspace.tools import WorkspaceError

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _ensure_global_workspace_access(user: UserPublic) -> None:
    if user.projects:
        raise HTTPException(status_code=403, detail="Use a project room workspace endpoint for scoped users.")


def get_workspace_code_service(settings: Settings = Depends(get_settings)) -> WorkspaceCodeService:
    return WorkspaceCodeService(settings)


def get_workspace_git_service(settings: Settings = Depends(get_settings)) -> WorkspaceGitService:
    return WorkspaceGitService(settings)


def get_workspace_readiness_service(settings: Settings = Depends(get_settings)) -> WorkspaceReadinessService:
    return WorkspaceReadinessService(settings)


@router.get("/readiness", response_model=WorkspaceReadinessResponse)
async def workspace_readiness(
    user: UserPublic = Depends(get_current_user),
    service: WorkspaceReadinessService = Depends(get_workspace_readiness_service),
) -> WorkspaceReadinessResponse:
    _ensure_global_workspace_access(user)
    return service.inspect()


@router.get("/tree", response_model=WorkspaceTreeResponse)
async def workspace_tree(
    limit: int = Query(default=120, ge=1, le=500),
    user: UserPublic = Depends(require_permission("voice:use")),
    service: WorkspaceCodeService = Depends(get_workspace_code_service),
) -> WorkspaceTreeResponse:
    _ensure_global_workspace_access(user)
    try:
        return service.tree(limit=limit)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/git/status", response_model=GitStatusResponse)
async def workspace_git_status(
    user: UserPublic = Depends(require_permission("voice:use")),
    service: WorkspaceGitService = Depends(get_workspace_git_service),
) -> GitStatusResponse:
    _ensure_global_workspace_access(user)
    try:
        return service.status()
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/git/diff", response_model=GitDiffResponse)
async def workspace_git_diff(
    user: UserPublic = Depends(require_permission("voice:use")),
    service: WorkspaceGitService = Depends(get_workspace_git_service),
) -> GitDiffResponse:
    _ensure_global_workspace_access(user)
    try:
        return service.diff()
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/search", response_model=WorkspaceSearchResponse)
async def workspace_search(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: UserPublic = Depends(require_permission("voice:use")),
    service: WorkspaceCodeService = Depends(get_workspace_code_service),
) -> WorkspaceSearchResponse:
    _ensure_global_workspace_access(user)
    try:
        return service.search(q, limit=limit)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/files/{path:path}", response_model=WorkspaceFileResponse)
async def workspace_file(
    path: str,
    max_chars: int = Query(default=12000, ge=100, le=50000),
    user: UserPublic = Depends(require_permission("voice:use")),
    service: WorkspaceCodeService = Depends(get_workspace_code_service),
) -> WorkspaceFileResponse:
    _ensure_global_workspace_access(user)
    try:
        return service.read(path, max_chars=max_chars)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
