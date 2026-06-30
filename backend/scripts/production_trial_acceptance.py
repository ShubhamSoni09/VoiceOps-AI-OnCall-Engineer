from __future__ import annotations

import argparse
import json
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
TESTS_ROOT = BACKEND_ROOT / "tests"
for path in (BACKEND_ROOT, TESTS_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.collab.event_store_readiness import build_event_store_readiness
from app.config import Settings, settings_from_env_file
from app.system.deployment import build_deployment_hardening_report
from app.system.security import StartupSecurityError, validate_startup_security
from scripts.final_acceptance_audit import run_final_acceptance_audit
from scripts.init_collab_event_store import init_event_store
from scripts.security_readiness import run_security_readiness


@dataclass
class TrialStage:
    id: str
    label: str
    status: str
    ready: bool
    required: bool
    duration_ms: int
    summary: str
    evidence: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    @property
    def failed_required(self) -> bool:
        return self.required and not self.ready


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full VoiceOps production-trial acceptance chain for a generated .env bundle.",
    )
    parser.add_argument("--env-file", required=True, help="Generated production-trial .env path.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--skip-harnesses", action="store_true", help="Use recorded evidence only; not sufficient for strict acceptance.")
    parser.add_argument("--replace-event-store", action="store_true", help="Replace the configured SQLite event store before initializing.")
    parser.add_argument("--require-accepted", action="store_true", help="Exit non-zero unless every required stage passes.")
    return parser


def run_production_trial_acceptance(
    *,
    env_file: str | Path,
    run_harnesses: bool = True,
    replace_event_store: bool = False,
    project_root: Path | None = None,
) -> dict:
    started_at = time.perf_counter()
    env_path = Path(env_file).expanduser()
    settings = _settings_from_env_file(env_path)
    root = project_root or PROJECT_ROOT
    stages = [
        _event_store_stage(settings, replace=replace_event_store),
        _bundle_manifest_stage(settings, env_path),
        _security_stage(settings, env_path),
        _startup_gate_stage(settings),
        _deployment_stage(settings),
        _final_acceptance_stage(settings, root, run_harnesses=run_harnesses),
    ]
    failed = [stage for stage in stages if stage.failed_required]
    return {
        "status": "accepted" if not failed else "needs_attention",
        "accepted": not failed,
        "env_file": str(env_path),
        "run_harnesses": run_harnesses,
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "stages": [asdict(stage) for stage in stages],
        "next_steps": _dedupe(step for stage in stages for step in stage.next_steps if stage.failed_required),
    }


def _settings_from_env_file(env_path: Path) -> Settings:
    settings = settings_from_env_file(env_path)
    base = env_path.resolve().parent
    updates: dict[str, Any] = {}
    path_fields = [
        "users_store_path",
        "collab_store_path",
        "collab_sqlite_path",
        "speaker_store_path",
        "speaker_sqlite_path",
        "speaker_verification_path",
        "agent_runs_path",
        "agent_llm_routes_path",
        "long_memory_path",
        "rag_index_path",
        "llm_connection_store_path",
        "external_agent_store_path",
        "demo_evidence_path",
        "voiceops_cache_path",
    ]
    for field in path_fields:
        value = getattr(settings, field)
        if isinstance(value, Path) and not value.is_absolute():
            updates[field] = base / value
    if settings.memory_store_path:
        memory_path = Path(settings.memory_store_path)
        if not memory_path.is_absolute():
            updates["memory_store_path"] = str(base / memory_path)
    return settings.model_copy(update=updates) if updates else settings


def _event_store_stage(settings: Settings, *, replace: bool) -> TrialStage:
    started_at = time.perf_counter()
    before = build_event_store_readiness(settings)
    initialized = False
    init_error = None
    if settings.collab_store_backend.strip().lower() == "sqlite" and (replace or not settings.collab_sqlite_path.exists()):
        try:
            init_event_store(settings.collab_sqlite_path, replace=replace)
            initialized = True
        except Exception as exc:  # pragma: no cover - exercised through report state in integration scripts.
            init_error = str(exc)
    after = build_event_store_readiness(settings)
    ready = after.ready and init_error is None
    next_steps = []
    if not ready:
        next_steps.extend(after.warnings)
        if after.migration_command:
            next_steps.append(after.migration_command)
        if init_error:
            next_steps.append(init_error)
    return TrialStage(
        id="event_store",
        label="SQLite event store initialization",
        status=after.status if init_error is None else "failed",
        ready=ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=(
            f"{after.backend}: {after.event_count} events, {after.stream_count} streams"
            if ready
            else init_error or (after.warnings[0] if after.warnings else "Event store is not ready.")
        ),
        evidence=[
            f"path={after.path}",
            f"initialized={initialized}",
            f"before={before.status}",
            *[check.id for check in after.checks if check.ready],
        ],
        next_steps=next_steps,
    )


def _bundle_manifest_stage(settings: Settings, env_path: Path) -> TrialStage:
    started_at = time.perf_counter()
    manifest_path = env_path.resolve().parent / "bundle-manifest.json"
    next_steps: list[str] = []
    evidence = [f"path={manifest_path}"]
    if not manifest_path.exists():
        return TrialStage(
            id="bundle_manifest",
            label="Generated bundle manifest",
            status="missing",
            ready=False,
            required=True,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            summary="bundle-manifest.json is missing beside the production-trial .env file.",
            evidence=evidence,
            next_steps=[
                "Regenerate the production-trial bundle with scripts/prepare_production_trial.py so acceptance can detect config drift."
            ],
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return TrialStage(
            id="bundle_manifest",
            label="Generated bundle manifest",
            status="invalid",
            ready=False,
            required=True,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            summary=f"bundle-manifest.json could not be parsed: {exc}",
            evidence=evidence,
            next_steps=["Regenerate the production-trial bundle and rerun acceptance."],
        )

    checks = {
        "schema_version": manifest.get("schema_version") == 1,
        "generated_by": manifest.get("generated_by") == "scripts/prepare_production_trial.py",
        "frontend_origin": manifest.get("frontend_origin") in settings.cors_allowed_origins,
        "workspace_path": manifest.get("workspace_path") == str(Path(settings.voiceops_workspace or "")),
        "seed_demo_users": manifest.get("security", {}).get("seed_demo_users") is False and not settings.seed_demo_users,
        "startup_security_gate": (
            manifest.get("security", {}).get("production_startup_security_gate") is True
            and settings.production_startup_security_gate
        ),
        "collab_sqlite": settings.collab_store_backend.strip().lower() == "sqlite",
        "speaker_sqlite": settings.speaker_store_backend.strip().lower() == "sqlite",
        "raw_secrets_redacted": manifest.get("security", {}).get("raw_secrets_in_manifest") is False,
    }
    file_checks = _manifest_file_checks(manifest)
    checks.update(file_checks)
    failed = [name for name, ready in checks.items() if not ready]
    evidence.extend(f"{name}={ready}" for name, ready in checks.items())
    if failed:
        next_steps.append(
            "Regenerate the bundle or update the production-trial .env and bundle-manifest.json together; drift detected in "
            + ", ".join(failed)
            + "."
        )

    ready = not failed
    return TrialStage(
        id="bundle_manifest",
        label="Generated bundle manifest",
        status="ready" if ready else "needs_attention",
        ready=ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary="Production-trial bundle manifest matches env and file permissions." if ready else f"Bundle manifest drift: {', '.join(failed)}",
        evidence=evidence,
        next_steps=next_steps,
    )


def _manifest_file_checks(manifest: dict[str, Any]) -> dict[str, bool]:
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    expected_modes = {
        "env": "0o600",
        "users": "0o600",
        "demo_evidence": "0o644",
        "next_steps": "0o644",
    }
    checks: dict[str, bool] = {}
    for key, expected_mode in expected_modes.items():
        file_info = files.get(key) if isinstance(files.get(key), dict) else {}
        raw_path = file_info.get("path")
        if not raw_path:
            checks[f"{key}_file"] = False
            continue
        path = Path(raw_path)
        actual_mode = _file_mode(path)
        checks[f"{key}_file"] = path.exists()
        checks[f"{key}_mode"] = actual_mode == expected_mode and file_info.get("mode") == expected_mode
    bootstrap_info = files.get("bootstrap") if isinstance(files.get("bootstrap"), dict) else {}
    bootstrap_path = Path(bootstrap_info.get("path") or "")
    bootstrap_mode = _file_mode(bootstrap_path)
    checks["bootstrap_file_recorded"] = bool(bootstrap_info.get("path"))
    checks["bootstrap_deleted_or_private"] = not bootstrap_path.exists() or (
        bootstrap_mode == "0o600" and bootstrap_info.get("mode") == "0o600"
    )
    checks["bootstrap_deleted"] = bool(bootstrap_info.get("path")) and not bootstrap_path.exists()
    return checks


def _security_stage(settings: Settings, env_path: Path) -> TrialStage:
    started_at = time.perf_counter()
    report = run_security_readiness(settings=settings)
    failed = [check for check in report.checks if not check.ready]
    return TrialStage(
        id="security_readiness",
        label="Production security readiness",
        status=report.status,
        ready=report.ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report.checks) - len(failed)}/{len(report.checks)} security checks ready",
        evidence=[f"env_file={env_path}", *[check.id for check in report.checks if check.ready]],
        next_steps=report.next_steps,
    )


def _startup_gate_stage(settings: Settings) -> TrialStage:
    started_at = time.perf_counter()
    environment = settings.deployment_environment.strip().lower() or "local"
    next_steps: list[str] = []
    evidence = [
        f"environment={environment}",
        f"production_startup_security_gate={settings.production_startup_security_gate}",
    ]
    if environment != "production":
        next_steps.append("Set DEPLOYMENT_ENVIRONMENT=production in the production-trial env file.")
    if not settings.production_startup_security_gate:
        next_steps.append("Set PRODUCTION_STARTUP_SECURITY_GATE=true so unsafe production startup fails closed.")
    try:
        validate_startup_security(settings)
        gate_passed = True
        error = None
    except StartupSecurityError as exc:
        gate_passed = False
        error = str(exc)
        next_steps.append(error)
    ready = environment == "production" and settings.production_startup_security_gate and gate_passed
    if ready:
        status = "ready"
        summary = "Production startup security gate would allow this configuration."
        evidence.append("startup_gate=passed")
    else:
        status = "needs_attention"
        summary = error or "Production startup security gate is not enforcing this configuration."
        evidence.append("startup_gate=blocked" if error else "startup_gate=not_enforced")
    return TrialStage(
        id="startup_security_gate",
        label="Production startup fail-closed gate",
        status=status,
        ready=ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=summary,
        evidence=evidence,
        next_steps=_dedupe(next_steps),
    )


def _deployment_stage(settings: Settings) -> TrialStage:
    started_at = time.perf_counter()
    report = build_deployment_hardening_report(settings)
    failed = [check for check in report.checks if not check.ready]
    return TrialStage(
        id="deployment_hardening",
        label="Deployment hardening checklist",
        status=report.status,
        ready=report.ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{report.ready_count}/{report.total_count} deployment checks ready",
        evidence=[check.id for check in report.checks if check.ready],
        next_steps=report.next_steps,
    )


def _final_acceptance_stage(settings: Settings, project_root: Path, *, run_harnesses: bool) -> TrialStage:
    started_at = time.perf_counter()
    report = run_final_acceptance_audit(
        run_harnesses=run_harnesses,
        settings=settings,
        project_root=project_root,
    )
    failed = [item for item in report["items"] if item["required"] and item["status"] != "passed"]
    return TrialStage(
        id="final_acceptance",
        label="Final product acceptance",
        status=report["status"],
        ready=bool(report["accepted"]),
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report['items']) - len(failed)}/{len(report['items'])} acceptance items passed",
        evidence=[item["id"] for item in report["items"] if item["status"] == "passed"],
        next_steps=report["next_steps"],
    )


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _file_mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(stat.S_IMODE(path.stat().st_mode))


def print_text_report(report: dict[str, Any]) -> None:
    print(f"VoiceOps production-trial acceptance: {report['status']}")
    for stage in report["stages"]:
        marker = "ok" if stage["ready"] else "blocked"
        print(f"- {stage['label']}: {marker} ({stage['status']})")
        print(f"  {stage['summary']}")
        for step in stage.get("next_steps") or []:
            print(f"  next: {step}")
    if report["next_steps"]:
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_production_trial_acceptance(
        env_file=args.env_file,
        run_harnesses=not args.skip_harnesses,
        replace_event_store=args.replace_event_store,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    return 0 if report["accepted"] or not args.require_accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
