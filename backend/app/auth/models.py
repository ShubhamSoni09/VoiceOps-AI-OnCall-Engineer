from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class Role(str, Enum):
    VIEWER = "viewer"
    ON_CALL = "on_call"
    ADMIN = "admin"


ROLE_LABELS = {
    Role.VIEWER: "Viewer",
    Role.ON_CALL: "On-call engineer",
    Role.ADMIN: "Admin",
}

ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.VIEWER: {"dashboard:view"},
    Role.ON_CALL: {"dashboard:view", "voice:use", "incident:approve"},
    Role.ADMIN: {"dashboard:view", "voice:use", "incident:approve", "admin:manage"},
}


class UserRecord(BaseModel):
    id: str
    email: EmailStr
    name: str
    initials: str
    role: Role
    password_hash: str = ""


class UserPublic(BaseModel):
    id: str
    email: EmailStr
    name: str
    initials: str
    role: Role
    role_label: str
    permissions: list[str] = Field(default_factory=list)


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


class TokenPayload(BaseModel):
    sub: str
    email: str
    role: Role
