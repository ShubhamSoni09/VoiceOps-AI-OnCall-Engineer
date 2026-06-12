from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, get_user_store
from app.auth.models import UserPublic
from app.auth.security import create_access_token
from app.auth.users import UserStore
from app.config import Settings, get_settings
from app.integrations.github.api import GitHubApiError, list_accessible_repos
from app.integrations.github.oauth import (
    GitHubOAuthError,
    build_authorize_url,
    create_login_oauth_state,
    create_oauth_state,
    exchange_code_for_token,
    fetch_github_user,
    verify_oauth_state,
)
from app.integrations.github.store import GitHubConnection, get_github_token_store
from app.integrations.github.user_repo import (
    get_user_selected_repo,
    normalize_repo_slug,
    set_user_selected_repo,
)
from app.workspace.bootstrap import ensure_user_workspace

router = APIRouter(prefix="/auth/github", tags=["github"])


class SelectRepoRequest(BaseModel):
    repo: str = Field(..., description="owner/repo")


def _github_connection(
    token: str,
    profile: dict,
    *,
    selected_repo: str | None = None,
) -> GitHubConnection:
    return GitHubConnection(
        access_token=token,
        github_login=str(profile.get("login") or "github-user"),
        github_id=profile.get("id"),
        connected_at=datetime.now(timezone.utc).isoformat(),
        selected_repo=normalize_repo_slug(selected_repo) if selected_repo else None,
    )


@router.get("/login")
async def github_login(settings: Settings = Depends(get_settings)) -> RedirectResponse:
    try:
        state = create_login_oauth_state(settings)
        return RedirectResponse(build_authorize_url(state, settings))
    except GitHubOAuthError as exc:
        login = f"{settings.app_public_url.rstrip('/')}/login"
        return RedirectResponse(f"{login}?github=error&reason={quote(str(exc))}")


@router.get("/login-url")
async def github_login_url(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    try:
        state = create_login_oauth_state(settings)
        return {"url": build_authorize_url(state, settings)}
    except GitHubOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/authorize-url")
async def github_authorize_url(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    try:
        state = create_oauth_state(settings, user_id=user.id, purpose="github_connect")
        return {"url": build_authorize_url(state, settings)}
    except GitHubOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/callback")
async def github_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    settings: Settings = Depends(get_settings),
    store: UserStore = Depends(get_user_store),
) -> RedirectResponse:
    login_url = f"{settings.app_public_url.rstrip('/')}/login"
    dashboard = f"{settings.app_public_url.rstrip('/')}/dashboard"

    if not code or not state:
        return RedirectResponse(f"{login_url}?github=error&reason=missing_params")

    try:
        oauth_state = verify_oauth_state(state, settings)
        token = await exchange_code_for_token(code, settings)
        profile = await fetch_github_user(token)
        default_repo = settings.github_repo

        if oauth_state.purpose == "github_login":
            user = store.upsert_github_user(profile)
            selected = normalize_repo_slug(default_repo) if default_repo else None
            get_github_token_store().set(
                user.id,
                _github_connection(token, profile, selected_repo=selected),
            )
            if selected:
                ensure_user_workspace(user.id, token, settings, selected)
            access_token = create_access_token(user, settings)
            suffix = "" if selected else "&select_repo=1"
            return RedirectResponse(f"{login_url}?token={access_token}{suffix}")

        if not oauth_state.user_id:
            raise GitHubOAuthError("Missing user for GitHub connect flow")

        selected = normalize_repo_slug(default_repo) if default_repo else None
        get_github_token_store().set(
            oauth_state.user_id,
            _github_connection(token, profile, selected_repo=selected),
        )
        if selected:
            ensure_user_workspace(oauth_state.user_id, token, settings, selected)
            return RedirectResponse(f"{dashboard}?github=connected")
        return RedirectResponse(f"{dashboard}?select_repo=1")
    except (GitHubOAuthError, HTTPException) as exc:
        return RedirectResponse(
            f"{login_url}?github=error&reason={quote(str(getattr(exc, 'detail', exc)))}"
        )


@router.get("/repos")
async def github_list_repos(
    user: UserPublic = Depends(get_current_user),
) -> dict:
    conn = get_github_token_store().get(user.id)
    if conn is None:
        raise HTTPException(status_code=400, detail="Connect GitHub first")
    try:
        repos = await list_accessible_repos(conn.access_token)
    except GitHubApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"repos": repos, "selected_repo": conn.selected_repo}


@router.put("/repo")
async def github_select_repo(
    body: SelectRepoRequest,
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    conn = get_github_token_store().get(user.id)
    if conn is None:
        raise HTTPException(status_code=400, detail="Connect GitHub first")
    repo = normalize_repo_slug(body.repo)
    try:
        ensure_user_workspace(user.id, conn.access_token, settings, repo)
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    updated = set_user_selected_repo(user.id, repo)
    return {
        "status": "selected",
        "repo": updated.selected_repo,
    }


@router.get("/status")
async def github_status(
    user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    conn = get_github_token_store().get(user.id)
    selected = get_user_selected_repo(user.id, settings) if conn else None
    return {
        "configured": bool(settings.github_client_id),
        "connected": conn is not None,
        "github_login": conn.github_login if conn else None,
        "selected_repo": conn.selected_repo if conn else None,
        "active_repo": selected,
        "repo_required": bool(conn and not selected),
    }


@router.delete("/disconnect")
async def github_disconnect(
    user: UserPublic = Depends(get_current_user),
) -> dict[str, str]:
    get_github_token_store().delete(user.id)
    return {"status": "disconnected"}
