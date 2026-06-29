from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, get_user_store, require_permission
from app.auth.models import LoginRequest, LoginResponse, UserProjectsUpdate, UserPublic
from app.auth.security import create_access_token, to_public_user, verify_password
from app.auth.users import UserStore
from app.config import Settings, get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    settings: Settings = Depends(get_settings),
    store: UserStore = Depends(get_user_store),
) -> LoginResponse:
    user = store.get_by_email(body.email.strip())
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(user, settings)
    return LoginResponse(access_token=token, user=to_public_user(user))


@router.get("/me", response_model=UserPublic)
async def me(user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return user


@router.get("/users", response_model=list[UserPublic])
async def list_users(
    _admin: UserPublic = Depends(require_permission("admin:manage")),
    store: UserStore = Depends(get_user_store),
) -> list[UserPublic]:
    return [to_public_user(user) for user in store.list_users()]


@router.put("/users/{user_id}/projects", response_model=UserPublic)
async def update_user_projects(
    user_id: str,
    body: UserProjectsUpdate,
    _admin: UserPublic = Depends(require_permission("admin:manage")),
    store: UserStore = Depends(get_user_store),
) -> UserPublic:
    user = store.update_projects(user_id, body.projects)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return to_public_user(user)


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"status": "logged_out"}
