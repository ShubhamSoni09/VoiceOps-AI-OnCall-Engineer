from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet

from app.config import Settings


def _fernet(settings: Settings) -> Fernet:
    secret = settings.external_agent_credential_secret or settings.jwt_secret
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_payload(settings: Settings, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _fernet(settings).encrypt(raw).decode("utf-8")


def decrypt_payload(settings: Settings, encrypted_payload: str) -> dict[str, Any]:
    raw = _fernet(settings).decrypt(encrypted_payload.encode("utf-8"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Credential payload is invalid")
    return data


def preview_secret(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= 8:
        return "*" * len(stripped)
    return f"{stripped[:4]}...{stripped[-4:]}"
