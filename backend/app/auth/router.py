from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user, get_user_store
from app.auth.models import LoginRequest, LoginResponse, UserPublic
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
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    token = create_access_token(user, settings)
    return LoginResponse(access_token=token, user=to_public_user(user))


@router.get("/me", response_model=UserPublic)
async def me(user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return user


@router.post("/logout")
async def logout() -> dict[str, str]:
    return {"status": "logged_out"}
