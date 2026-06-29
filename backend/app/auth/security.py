from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.auth.models import ROLE_LABELS, ROLE_PERMISSIONS, Role, TokenPayload, UserPublic, UserRecord
from app.config import Settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user: UserRecord, settings: Settings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role.value,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> TokenPayload:
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return TokenPayload(sub=data["sub"], email=data["email"], role=Role(data["role"]))
    except (JWTError, KeyError, ValueError) as exc:
        raise ValueError("Invalid or expired token") from exc


def to_public_user(user: UserRecord) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        name=user.name,
        initials=user.initials,
        role=user.role,
        role_label=ROLE_LABELS[user.role],
        permissions=sorted(ROLE_PERMISSIONS[user.role]),
        projects=list(user.projects),
    )
