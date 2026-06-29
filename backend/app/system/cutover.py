from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.auth.users import DEFAULT_USERS
from app.config import Settings
from app.system.security import DEFAULT_JWT_SECRET, StartupSecurityError, validate_startup_security


DEFAULT_USER_EMAILS = {item["email"].lower() for item in DEFAULT_USERS}
PLACEHOLDER_MARKERS = ("replace-with", "change-me", "example.com")


class ProductionCutoverCheck(BaseModel):
    id: str
    label: str
    ready: bool
    status: str
    severity: str
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    next_step: str | None = None


class ProductionCutoverReport(BaseModel):
    status: str
    ready: bool
    checked_at: str
    env_file: str | None = None
    checks: list[ProductionCutoverCheck]
    next_steps: list[str] = Field(default_factory=list)
    acceptance_command: str | None = None


def build_production_cutover_report(
    settings: Settings,
    *,
    env_file: str | Path | None = None,
) -> ProductionCutoverReport:
    env_path = Path(env_file).expanduser() if env_file else None
    manifest = _read_manifest(env_path) if env_path else None
    checks = [
        _env_file_check(env_path),
        _production_mode_check(settings),
        _secret_check(settings),
        _users_check(settings.users_store_path),
        _bootstrap_removed_check(settings, manifest, env_path),
        _hf_token_check(settings.hf_token),
        _startup_gate_check(settings),
    ]
    failed = [check for check in checks if not check.ready]
    acceptance_command = (
        f"python scripts/production_trial_acceptance.py --env-file {env_path} --json --require-accepted"
        if env_path
        else "python scripts/production_trial_acceptance.py --env-file ../voiceops-production-trial/.env --json --require-accepted"
    )
    return ProductionCutoverReport(
        status="ready" if not failed else "needs_attention",
        ready=not failed,
        checked_at=datetime.now(UTC).isoformat(),
        env_file=str(env_path) if env_path else None,
        checks=checks,
        next_steps=_dedupe(check.next_step for check in failed if check.next_step),
        acceptance_command=acceptance_command,
    )


def _check(
    *,
    id: str,
    label: str,
    ready: bool,
    severity: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    next_step: str | None = None,
) -> ProductionCutoverCheck:
    return ProductionCutoverCheck(
        id=id,
        label=label,
        ready=ready,
        status="passed" if ready else "failed",
        severity=severity,
        summary=summary,
        evidence=evidence or {},
        next_step=next_step,
    )


def _env_file_check(env_path: Path | None) -> ProductionCutoverCheck:
    if env_path is None:
        return _check(
            id="runtime_settings_loaded",
            label="Running backend settings are loaded",
            ready=True,
            severity="medium",
            summary="Runtime settings are active; env-file permissions are checked by the CLI cutover gate.",
            evidence={"mode": "runtime"},
            next_step="Run production_cutover_check.py against the generated production .env before real team cutover.",
        )
    exists = env_path.exists()
    mode = _file_mode(env_path)
    ready = exists and mode == "0o600"
    return _check(
        id="env_file_private",
        label="Production env file is private",
        ready=ready,
        severity="critical",
        summary=f"{env_path} mode {mode}" if exists else f"{env_path} does not exist.",
        evidence={"path": str(env_path), "mode": mode, "exists": exists},
        next_step=f"Create the bundle with prepare_production_trial.py and run chmod 600 {env_path}.",
    )


def _production_mode_check(settings: Settings) -> ProductionCutoverCheck:
    environment = settings.deployment_environment.strip().lower() or "local"
    ready = environment == "production" and bool(settings.production_startup_security_gate)
    return _check(
        id="production_mode",
        label="Production mode and fail-closed startup gate are enabled",
        ready=ready,
        severity="critical",
        summary=(
            "DEPLOYMENT_ENVIRONMENT=production and PRODUCTION_STARTUP_SECURITY_GATE=true."
            if ready
            else "Production environment or startup gate is not enabled."
        ),
        evidence={
            "deployment_environment": environment,
            "production_startup_security_gate": bool(settings.production_startup_security_gate),
        },
        next_step="Set DEPLOYMENT_ENVIRONMENT=production and PRODUCTION_STARTUP_SECURITY_GATE=true in the generated .env.",
    )


def _secret_check(settings: Settings) -> ProductionCutoverCheck:
    jwt_secret = settings.jwt_secret or ""
    credential_secret = settings.external_agent_credential_secret or ""
    jwt_ready = _is_real_secret(jwt_secret) and jwt_secret != DEFAULT_JWT_SECRET
    credential_ready = _is_real_secret(credential_secret) and credential_secret != jwt_secret
    ready = jwt_ready and credential_ready
    return _check(
        id="generated_secrets",
        label="JWT and external-agent credential secrets are generated and distinct",
        ready=ready,
        severity="critical",
        summary=(
            "JWT_SECRET and EXTERNAL_AGENT_CREDENTIAL_SECRET are strong and distinct."
            if ready
            else "JWT_SECRET or EXTERNAL_AGENT_CREDENTIAL_SECRET is missing, placeholder, weak, or reused."
        ),
        evidence={
            "jwt_secret_length": len(jwt_secret),
            "jwt_default": jwt_secret == DEFAULT_JWT_SECRET,
            "credential_secret_length": len(credential_secret),
            "secrets_distinct": bool(credential_secret and credential_secret != jwt_secret),
        },
        next_step="Regenerate the bundle or rotate both secrets with generated 32+ character values.",
    )


def _users_check(users_path: Path) -> ProductionCutoverCheck:
    users, error = _load_users(users_path)
    default_users = sorted(
        str(user.get("email", "")).lower()
        for user in users
        if str(user.get("email", "")).lower() in DEFAULT_USER_EMAILS
    )
    real_admins = [
        user
        for user in users
        if str(user.get("role", "")).lower() == "admin"
        and str(user.get("email", "")).lower() not in DEFAULT_USER_EMAILS
        and not str(user.get("email", "")).lower().endswith("@voiceops.dev")
    ]
    ready = error is None and not default_users and bool(real_admins)
    if error:
        summary = error
    elif default_users:
        summary = f"Seeded demo users still exist: {', '.join(default_users)}."
    elif not real_admins:
        summary = "No real admin user exists in the production user store."
    else:
        summary = f"{len(real_admins)} real admin user(s); no seeded demo users."
    return _check(
        id="real_users",
        label="Real admin exists and seeded demo users are absent",
        ready=ready,
        severity="critical",
        summary=summary,
        evidence={
            "path": str(users_path),
            "user_count": len(users),
            "real_admin_count": len(real_admins),
            "default_users": default_users,
        },
        next_step="Run prepare_production_trial.py with a real admin email, then keep SEED_DEMO_USERS=false.",
    )


def _bootstrap_removed_check(
    settings: Settings,
    manifest: dict[str, Any] | None,
    env_path: Path | None,
) -> ProductionCutoverCheck:
    manifest_path = env_path.resolve().parent / "bundle-manifest.json" if env_path else None
    bootstrap_path = _bootstrap_path_from_manifest(manifest) if manifest else _bootstrap_path_from_settings(settings)
    exists = bootstrap_path.exists()
    mode = _file_mode(bootstrap_path)
    ready = not exists
    return _check(
        id="bootstrap_secret_removed",
        label="Temporary bootstrap password file is deleted",
        ready=ready,
        severity="critical",
        summary=(
            "bootstrap-admin.txt has been deleted after account rotation."
            if ready
            else f"Temporary bootstrap password file still exists at {bootstrap_path}."
        ),
        evidence={
            "manifest_path": str(manifest_path) if manifest_path else None,
            "bootstrap_path": str(bootstrap_path),
            "exists": exists,
            "mode": mode,
        },
        next_step="Log in once, rotate the bootstrap admin password, invite real users, then delete bootstrap-admin.txt.",
    )


def _hf_token_check(hf_token: str | None) -> ProductionCutoverCheck:
    token = hf_token or ""
    ready = bool(token) and not _contains_placeholder(token)
    return _check(
        id="hf_token_replaced",
        label="HF_TOKEN has been replaced for WhisperX speaker diarization",
        ready=ready,
        severity="high",
        summary="HF_TOKEN is configured." if ready else "HF_TOKEN still looks missing or placeholder.",
        evidence={"configured": bool(token), "placeholder": _contains_placeholder(token)},
        next_step="Set HF_TOKEN to a Hugging Face token with accepted pyannote model access.",
    )


def _startup_gate_check(settings: Settings) -> ProductionCutoverCheck:
    try:
        validate_startup_security(settings)
        error = None
    except StartupSecurityError as exc:
        error = str(exc)
    ready = error is None
    return _check(
        id="startup_security_gate",
        label="Production startup security gate passes",
        ready=ready,
        severity="critical",
        summary="Startup gate would allow this production configuration." if ready else error or "Startup gate failed.",
        evidence={"startup_gate": "passed" if ready else "blocked", "error": error},
        next_step="Run security_readiness.py --env-file with --require-ready and fix the reported blockers.",
    )


def _read_manifest(env_path: Path) -> dict[str, Any] | None:
    manifest_path = env_path.resolve().parent / "bundle-manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _bootstrap_path_from_manifest(manifest: dict[str, Any]) -> Path:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    bootstrap = files.get("bootstrap") if isinstance(files.get("bootstrap"), dict) else {}
    raw_path = bootstrap.get("path")
    return Path(raw_path) if raw_path else Path("bootstrap-admin.txt")


def _bootstrap_path_from_settings(settings: Settings) -> Path:
    users_path = settings.users_store_path.expanduser()
    if users_path.parent.name == "data":
        return users_path.parent.parent / "bootstrap-admin.txt"
    return users_path.parent / "bootstrap-admin.txt"


def _load_users(users_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not users_path.exists():
        return [], f"User store does not exist at {users_path}."
    try:
        data = json.loads(users_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], f"User store is not valid JSON: {exc}."
    users = data.get("users")
    if not isinstance(users, list):
        return [], "User store must contain a users list."
    return [user for user in users if isinstance(user, dict)], None


def _is_real_secret(value: str) -> bool:
    return len(value) >= 32 and not _contains_placeholder(value)


def _contains_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _file_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(stat.S_IMODE(path.stat().st_mode))


def _dedupe(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
