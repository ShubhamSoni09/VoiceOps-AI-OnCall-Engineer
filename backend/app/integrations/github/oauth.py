from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import Settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"


class GitHubOAuthError(ValueError):
    pass


class OAuthState(BaseModel):
    purpose: str
    user_id: str | None = None


def create_oauth_state(settings: Settings, *, user_id: str | None = None, purpose: str = "github_connect") -> str:
    payload = {
        "purpose": purpose,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
    }
    if user_id:
        payload["sub"] = user_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_login_oauth_state(settings: Settings) -> str:
    return create_oauth_state(settings, purpose="github_login")


def verify_oauth_state(state: str, settings: Settings) -> OAuthState:
    try:
        payload = jwt.decode(
            state,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise GitHubOAuthError("Invalid or expired OAuth state") from exc
    purpose = str(payload.get("purpose") or "")
    if purpose not in {"github_login", "github_connect"}:
        raise GitHubOAuthError("Invalid OAuth state")
    return OAuthState(purpose=purpose, user_id=payload.get("sub"))


def build_authorize_url(state: str, settings: Settings) -> str:
    if not settings.github_client_id or not settings.github_callback_url:
        raise GitHubOAuthError("GitHub OAuth is not configured")
    params = {
        "client_id": settings.github_client_id,
        "redirect_uri": settings.github_callback_url,
        "scope": "repo read:user user:email",
        "state": state,
    }
    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str, settings: Settings) -> str:
    if not settings.github_client_id or not settings.github_client_secret:
        raise GitHubOAuthError("GitHub OAuth is not configured")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            json={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
        )
    if response.status_code >= 400:
        raise GitHubOAuthError("GitHub token exchange failed")
    data = response.json()
    token = data.get("access_token")
    if not token:
        raise GitHubOAuthError(data.get("error_description") or "No access token returned")
    return str(token)


async def fetch_github_user(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{GITHUB_API}/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
    if response.status_code >= 400:
        raise GitHubOAuthError("Could not load GitHub profile")
    return response.json()
