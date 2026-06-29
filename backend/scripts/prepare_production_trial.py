from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.auth.models import Role
from app.auth.security import hash_password
from app.auth.users import DEFAULT_USERS


DEFAULT_USER_EMAILS = {item["email"].lower() for item in DEFAULT_USERS}


@dataclass
class ProductionTrialBundle:
    output_dir: str
    env_path: str
    users_path: str
    demo_evidence_path: str
    bootstrap_path: str
    next_steps_path: str
    manifest_path: str
    admin_email: str
    frontend_origin: str
    workspace_path: str
    force: bool = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a VoiceOps production-trial env bundle without seeded demo users.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory for generated production-trial files.")
    parser.add_argument("--frontend-origin", required=True, help="Exact deployed frontend origin, e.g. https://voiceops.example.com.")
    parser.add_argument("--workspace", required=True, help="Absolute repository path VoiceOps may edit.")
    parser.add_argument("--admin-email", required=True, help="Initial real admin email address.")
    parser.add_argument("--admin-name", required=True, help="Initial real admin display name.")
    parser.add_argument("--agent-name", default="VoiceOps", help="AI teammate display name.")
    parser.add_argument(
        "--demo-evidence-source",
        help="Optional existing demo_evidence.json to copy into the generated bundle.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing generated bundle.")
    parser.add_argument(
        "--allow-non-git-workspace",
        action="store_true",
        help="Allow a workspace without .git. Intended only for dry infrastructure preparation.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable output.")
    return parser


def create_bundle(
    *,
    output_dir: Path,
    frontend_origin: str,
    workspace: Path,
    admin_email: str,
    admin_name: str,
    agent_name: str = "VoiceOps",
    force: bool = False,
    allow_non_git_workspace: bool = False,
    demo_evidence_source: Path | None = None,
) -> ProductionTrialBundle:
    _validate_frontend_origin(frontend_origin)
    _validate_admin_email(admin_email)
    _validate_workspace(workspace, allow_non_git=allow_non_git_workspace)
    _validate_runtime_location(output_dir, workspace)
    _ensure_safe_output_dir(output_dir, force=force)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    data_dir.mkdir(exist_ok=True)
    os.chmod(data_dir, 0o700)

    jwt_secret = secrets.token_hex(32)
    credential_secret = secrets.token_hex(32)
    admin_password = secrets.token_urlsafe(24)
    users_path = data_dir / "users.json"
    demo_evidence_path = data_dir / "demo_evidence.json"
    env_path = output_dir / ".env"
    bootstrap_path = output_dir / "bootstrap-admin.txt"
    next_steps_path = output_dir / "README_NEXT_STEPS.md"
    manifest_path = output_dir / "bundle-manifest.json"

    users = {
        "users": [
            {
                "id": "user-admin",
                "email": admin_email,
                "name": admin_name,
                "initials": _initials(admin_name),
                "role": Role.ADMIN.value,
                "password_hash": hash_password(admin_password),
            }
        ]
    }
    _write_private_json(users_path, users, force=force)
    _write_demo_evidence(demo_evidence_path, source=demo_evidence_source, force=force)
    _write_private_text(env_path, _env_text(
        frontend_origin=frontend_origin,
        workspace=workspace,
        users_path=users_path,
        data_dir=data_dir,
        jwt_secret=jwt_secret,
        credential_secret=credential_secret,
        agent_name=agent_name,
    ), force=force)
    _write_private_text(
        bootstrap_path,
        "\n".join([
            "VoiceOps production-trial bootstrap admin",
            f"email={admin_email}",
            f"temporary_password={admin_password}",
            "Rotate this password after the first login, then delete this file.",
            "",
        ]),
        force=force,
    )
    _write_private_text(
        next_steps_path,
        _next_steps_text(env_path, users_path, bootstrap_path, manifest_path),
        force=force,
        mode=0o644,
    )
    _write_private_json(
        manifest_path,
        _manifest(
            output_dir=output_dir,
            env_path=env_path,
            users_path=users_path,
            demo_evidence_path=demo_evidence_path,
            bootstrap_path=bootstrap_path,
            next_steps_path=next_steps_path,
            frontend_origin=frontend_origin,
            workspace=workspace,
            admin_email=admin_email,
            agent_name=agent_name,
        ),
        force=force,
        mode=0o644,
    )

    return ProductionTrialBundle(
        output_dir=str(output_dir),
        env_path=str(env_path),
        users_path=str(users_path),
        demo_evidence_path=str(demo_evidence_path),
        bootstrap_path=str(bootstrap_path),
        next_steps_path=str(next_steps_path),
        manifest_path=str(manifest_path),
        admin_email=admin_email,
        frontend_origin=frontend_origin,
        workspace_path=str(workspace),
        force=force,
    )


def _env_text(
    *,
    frontend_origin: str,
    workspace: Path,
    users_path: Path,
    data_dir: Path,
    jwt_secret: str,
    credential_secret: str,
    agent_name: str,
) -> str:
    initials = _initials(agent_name)
    return f"""# Generated by scripts/prepare_production_trial.py.
DEPLOYMENT_ENVIRONMENT=production
DEBUG=false
CORS_ALLOWED_ORIGINS={frontend_origin}
CORS_ALLOW_CREDENTIALS=true

JWT_SECRET={jwt_secret}
JWT_EXPIRE_MINUTES=720
USERS_STORE_PATH={users_path}
SEED_DEMO_USERS=false
PRODUCTION_STARTUP_SECURITY_GATE=true

COLLAB_STORE_BACKEND=sqlite
COLLAB_SQLITE_PATH={data_dir / 'collaboration.sqlite3'}
SPEAKER_STORE_BACKEND=sqlite
SPEAKER_SQLITE_PATH={data_dir / 'speakers.sqlite3'}
AGENT_RUNS_PATH={data_dir / 'agent_runs.json'}
AGENT_LLM_ROUTES_PATH={data_dir / 'agent_llm_routes.json'}
LONG_MEMORY_PATH={data_dir / 'long_memory.json'}
RAG_INDEX_PATH={data_dir / 'rag_index.json'}
LLM_CONNECTION_STORE_PATH={data_dir / 'llm_connections.json'}
EXTERNAL_AGENT_STORE_PATH={data_dir / 'external_agent_credentials.json'}
DEMO_EVIDENCE_PATH={data_dir / 'demo_evidence.json'}

VOICEOPS_WORKSPACE={workspace}

STT_PROVIDER=mock
SPEAKER_PROVIDER=whisperx
HF_TOKEN=replace-with-huggingface-token-with-pyannote-access
WHISPERX_MODEL=small
WHISPERX_DEVICE=cpu
WHISPERX_COMPUTE_TYPE=int8
WHISPERX_WORKER_MODE=persistent_subprocess

AGENT_DISPLAY_NAME={agent_name}
AGENT_INITIALS={initials}
AGENT_WAKE_WORDS={agent_name.lower()},assistant,agent
LLM_PROVIDER=mock
TTS_PROVIDER=mock
TTS_ON_VOICE=false

EXTERNAL_AGENT_ALLOWED_PROVIDERS=claude,codex,cursor
EXTERNAL_AGENT_CREDENTIAL_SECRET={credential_secret}
EXTERNAL_AGENT_OAUTH_REDIRECT_BASE_URL={frontend_origin}
EXTERNAL_AGENT_OAUTH_MOCK_ENABLED=false

GITHUB_PR_CREATION_ENABLED=false
GITHUB_PR_CLI_PATH=gh
GITHUB_PR_ALLOWED_BASE_BRANCHES=main
GITHUB_PR_COMMAND_TIMEOUT_SECONDS=60
GITHUB_PR_CREATE_DRAFT=true
"""


def _next_steps_text(env_path: Path, users_path: Path, bootstrap_path: Path, manifest_path: Path) -> str:
    return f"""# VoiceOps Production Trial Next Steps

1. Move `{env_path}` to the backend host as `backend/.env`.
2. Keep `{users_path}` private; it contains the real bootstrap admin account.
3. Confirm `HF_TOKEN` and any provider keys have been replaced with real secrets.
4. Start the backend with this env and log in with the temporary password in `{bootstrap_path}`.
5. Rotate the admin password, invite real users, then delete `{bootstrap_path}`.
6. Run `python scripts/production_cutover_check.py --env-file {env_path} --json --require-ready`.
7. Run `python scripts/production_trial_acceptance.py --env-file {env_path} --json --require-accepted`.
8. If either command fails, inspect the reported stage and run the printed lower-level command.
9. Keep `{manifest_path}` with the bundle; acceptance uses it to detect configuration drift.
"""


def _manifest(
    *,
    output_dir: Path,
    env_path: Path,
    users_path: Path,
    demo_evidence_path: Path,
    bootstrap_path: Path,
    next_steps_path: Path,
    frontend_origin: str,
    workspace: Path,
    admin_email: str,
    agent_name: str,
) -> dict:
    return {
        "schema_version": 1,
        "generated_by": "scripts/prepare_production_trial.py",
        "generated_at_epoch": int(time.time()),
        "output_dir": str(output_dir),
        "frontend_origin": frontend_origin,
        "workspace_path": str(workspace),
        "admin_email": admin_email,
        "agent_name": agent_name,
        "files": {
            "env": {"path": str(env_path), "mode": _mode(env_path)},
            "users": {"path": str(users_path), "mode": _mode(users_path)},
            "demo_evidence": {"path": str(demo_evidence_path), "mode": _mode(demo_evidence_path)},
            "bootstrap": {"path": str(bootstrap_path), "mode": _mode(bootstrap_path)},
            "next_steps": {"path": str(next_steps_path), "mode": _mode(next_steps_path)},
        },
        "security": {
            "jwt_secret_generated": True,
            "external_agent_credential_secret_generated": True,
            "seed_demo_users": False,
            "production_startup_security_gate": True,
            "demo_admin_email": False,
            "raw_secrets_in_manifest": False,
        },
        "stores": {
            "collab_store_backend": "sqlite",
            "speaker_store_backend": "sqlite",
            "relative_runtime_paths": False,
        },
        "required_replacements": [
            "HF_TOKEN",
        ],
        "cutover_command": f"python scripts/production_cutover_check.py --env-file {env_path} --json --require-ready",
        "acceptance_command": f"python scripts/production_trial_acceptance.py --env-file {env_path} --json --require-accepted",
    }


def _validate_frontend_origin(frontend_origin: str) -> None:
    parsed = urlparse(frontend_origin)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("frontend_origin must be an absolute http(s) origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("frontend_origin must be an origin only, without path, query, or fragment")
    if "*" in frontend_origin:
        raise ValueError("frontend_origin must not contain wildcards")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("frontend_origin must use https outside localhost")


def _validate_admin_email(admin_email: str) -> None:
    normalized = admin_email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise ValueError("admin_email must be a valid email address")
    if normalized in DEFAULT_USER_EMAILS or normalized.endswith("@voiceops.dev"):
        raise ValueError("admin_email must be a real team account, not a seeded demo address")


def _validate_workspace(workspace: Path, *, allow_non_git: bool) -> None:
    if not workspace.is_absolute():
        raise ValueError("workspace must be an absolute path")
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError("workspace must exist and be a directory")
    if not allow_non_git and not (workspace / ".git").exists():
        raise ValueError("workspace must be a local git repository; pass --allow-non-git-workspace only for dry infrastructure preparation")


def _validate_runtime_location(output_dir: Path, workspace: Path) -> None:
    output = output_dir.expanduser().resolve(strict=False)
    workspace_root = workspace.expanduser().resolve(strict=False)
    if output == workspace_root or workspace_root in output.parents:
        raise ValueError("output_dir must be outside VOICEOPS_WORKSPACE so runtime data is not editable repository state")


def _ensure_safe_output_dir(output_dir: Path, *, force: bool) -> None:
    if not output_dir.exists():
        return
    protected = [
        output_dir / ".env",
        output_dir / "bootstrap-admin.txt",
        output_dir / "README_NEXT_STEPS.md",
        output_dir / "bundle-manifest.json",
        output_dir / "data" / "users.json",
        output_dir / "data" / "demo_evidence.json",
    ]
    existing = [path for path in protected if path.exists()]
    if existing and not force:
        names = ", ".join(str(path) for path in existing)
        raise ValueError(f"output_dir already contains generated files; pass --force to overwrite: {names}")


def _initials(name: str) -> str:
    parts = [part[0] for part in name.replace("-", " ").split() if part]
    return "".join(parts[:2]).upper() or "VO"


def _write_private_text(path: Path, text: str, *, force: bool, mode: int = 0o600) -> None:
    if path.exists() and not force:
        raise ValueError(f"{path} already exists; pass --force to overwrite")
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def _write_private_json(path: Path, data: dict, *, force: bool, mode: int = 0o600) -> None:
    _write_private_text(path, json.dumps(data, indent=2) + "\n", force=force, mode=mode)


def _write_demo_evidence(path: Path, *, source: Path | None, force: bool) -> None:
    source = source or BACKEND_ROOT / "data" / "demo_evidence.json"
    if source.exists():
        data = json.loads(source.read_text(encoding="utf-8"))
    else:
        data = {"records": []}
    _write_private_json(path, data, force=force, mode=0o644)


def _mode(path: Path) -> str:
    return oct(path.stat().st_mode & 0o777)


def print_text_report(bundle: ProductionTrialBundle) -> None:
    print("VoiceOps production-trial bundle generated")
    print(f"- env: {bundle.env_path}")
    print(f"- users: {bundle.users_path}")
    print(f"- bootstrap admin: {bundle.bootstrap_path}")
    print(f"- next steps: {bundle.next_steps_path}")
    print(f"- manifest: {bundle.manifest_path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bundle = create_bundle(
        output_dir=Path(args.output_dir),
        frontend_origin=args.frontend_origin,
        workspace=Path(args.workspace),
        admin_email=args.admin_email,
        admin_name=args.admin_name,
        agent_name=args.agent_name,
        force=args.force,
        allow_non_git_workspace=args.allow_non_git_workspace,
        demo_evidence_source=Path(args.demo_evidence_source) if args.demo_evidence_source else None,
    )
    if args.json:
        print(json.dumps({"status": "ok", **asdict(bundle)}, indent=2))
    else:
        print_text_report(bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
