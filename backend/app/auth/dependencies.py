from functools import lru_cache

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.models import ROLE_PERMISSIONS, UserPublic, UserRecord
from app.auth.security import decode_access_token, to_public_user
from app.auth.users import UserStore
from app.config import Settings, get_settings
from app.integrations.github.store import get_github_token_store

security = HTTPBearer(auto_error=False)


@lru_cache
def get_user_store() -> UserStore:
    settings = get_settings()
    return UserStore(settings.users_store_path)


def _resolve_user(
    credentials: HTTPAuthorizationCredentials | None,
    settings: Settings,
    store: UserStore,
) -> UserRecord:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(credentials.credentials, settings)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = store.get_by_id(payload.sub)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    settings: Settings = Depends(get_settings),
    store: UserStore = Depends(get_user_store),
) -> UserPublic:
    user = _resolve_user(credentials, settings, store)
    github_login = None
    conn = get_github_token_store().get(user.id)
    if conn:
        github_login = conn.github_login
    return to_public_user(user, github_login=github_login)


def require_permission(permission: str):
    async def _checker(user: UserPublic = Depends(get_current_user)) -> UserPublic:
        allowed = ROLE_PERMISSIONS.get(user.role, set())
        if permission not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' cannot perform this action",
            )
        return user

    return _checker
