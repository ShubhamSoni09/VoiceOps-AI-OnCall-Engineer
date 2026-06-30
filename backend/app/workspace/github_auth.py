from __future__ import annotations

import json
import os
import secrets
import base64
import hashlib
from datetime import UTC, datetime
from typing import Any
from urllib import error, parse, request

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.redaction import redact_sensitive_text


def github_oauth_status(settings: Settings) -> dict[str, Any]:
    env_token = _env_token(settings)
    if env_token:
        return {
            "connected": True,
            "configured": bool(settings.github_oauth_client_id and settings.github_oauth_client_secret),
            "source": "env",
            "scope": "repo",
            "login": None,
        }
    connection = _read(settings).get("connection") or {}
    return {
        "connected": bool(_stored_token(settings)),
        "configured": bool(settings.github_oauth_client_id and settings.github_oauth_client_secret),
        "source": "oauth" if connection else "none",
        "scope": connection.get("scope") or "",
        "login": connection.get("login") or None,
    }


def start_github_oauth(settings: Settings) -> dict[str, str]:
    _require_oauth_config(settings)
    state = secrets.token_urlsafe(24)
    data = _read(settings)
    data["pending_state"] = state
    data["pending_at"] = _now()
    _write(settings, data)
    redirect_uri = _redirect_uri(settings)
    query = parse.urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": redirect_uri,
            "scope": settings.github_oauth_scope,
            "state": state,
            "allow_signup": "true",
        }
    )
    return {
        "authorize_url": f"{settings.github_oauth_authorize_url}?{query}",
        "redirect_uri": redirect_uri,
        "scope": settings.github_oauth_scope,
    }


def complete_github_oauth(settings: Settings, *, code: str, state: str) -> dict[str, Any]:
    _require_oauth_config(settings)
    data = _read(settings)
    if not state or state != data.get("pending_state"):
        raise ValueError("GitHub OAuth state did not match.")
    token = _exchange_code(settings, code.strip())
    login = _github_login(token)
    data.pop("pending_state", None)
    data.pop("pending_at", None)
    data["connection"] = {
        "encrypted_payload": _encrypt_payload(settings, {"access_token": token}),
        "token_preview": _preview_secret(token),
        "scope": settings.github_oauth_scope,
        "login": login,
        "connected_at": _now(),
    }
    _write(settings, data)
    return github_oauth_status(settings)


def stored_github_token(settings: Settings) -> str | None:
    return _stored_token(settings)


def _env_token(settings: Settings) -> str | None:
    return settings.github_token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _stored_token(settings: Settings) -> str | None:
    connection = _read(settings).get("connection") or {}
    encrypted = str(connection.get("encrypted_payload") or "")
    if not encrypted:
        return None
    try:
        token = str(_decrypt_payload(settings, encrypted).get("access_token") or "").strip()
    except (InvalidToken, ValueError):
        return None
    return token or None


def _exchange_code(settings: Settings, code: str) -> str:
    if not code:
        raise ValueError("GitHub OAuth code is required.")
    body = parse.urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "client_secret": settings.github_oauth_client_secret,
            "code": code,
            "redirect_uri": _redirect_uri(settings),
        }
    ).encode("utf-8")
    oauth_request = request.Request(
        settings.github_oauth_token_url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "VoiceOps",
        },
    )
    try:
        with request.urlopen(oauth_request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except error.HTTPError as exc:
        raise ValueError(f"GitHub OAuth failed ({exc.code}): {_error_detail(exc)}") from exc
    except error.URLError as exc:
        raise ValueError(f"GitHub OAuth unavailable: {exc.reason}") from exc
    if payload.get("error"):
        raise ValueError(redact_sensitive_text(str(payload.get("error_description") or payload.get("error")))[:500])
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise ValueError("GitHub OAuth did not return an access token.")
    return token


def _github_login(token: str) -> str | None:
    user_request = request.Request(
        "https://api.github.com/user",
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "VoiceOps",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with request.urlopen(user_request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return None
    return str(payload.get("login") or "").strip() or None


def _require_oauth_config(settings: Settings) -> None:
    if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
        raise ValueError("Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET first.")


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.external_agent_oauth_redirect_base_url.rstrip('/')}/console/github/oauth/callback"


def _read(settings: Settings) -> dict[str, Any]:
    try:
        data = json.loads(settings.github_connection_path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(settings: Settings, data: dict[str, Any]) -> None:
    settings.github_connection_path.parent.mkdir(parents=True, exist_ok=True)
    settings.github_connection_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(settings.github_connection_path, 0o600)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error_detail(exc: error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8") or "{}")
        return redact_sensitive_text(str(payload.get("error_description") or payload.get("message") or payload))[:500]
    except Exception:
        return redact_sensitive_text(str(exc.reason or "request failed"))[:500]


def _fernet(settings: Settings) -> Fernet:
    secret = settings.external_agent_credential_secret or settings.jwt_secret
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt_payload(settings: Settings, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _fernet(settings).encrypt(raw).decode("utf-8")


def _decrypt_payload(settings: Settings, encrypted_payload: str) -> dict[str, Any]:
    raw = _fernet(settings).decrypt(encrypted_payload.encode("utf-8"))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Credential payload is invalid")
    return data


def _preview_secret(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= 8:
        return "*" * len(stripped)
    return f"{stripped[:4]}...{stripped[-4:]}"
