from __future__ import annotations

import argparse
import json
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

from app.config import Settings
from app.collab.event_store_readiness import build_event_store_readiness
from scripts.demo_operator import run_profile
from scripts.final_acceptance_audit import run_final_acceptance_audit
from scripts.security_readiness import run_security_readiness
from scripts.team_onboarding_check import run_team_onboarding_check


@dataclass
class AcceptanceStage:
    id: str
    label: str
    status: str
    ready: bool
    required: bool
    duration_ms: int
    summary: str
    next_steps: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def failed_required(self) -> bool:
        return self.required and not self.ready


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VoiceOps operator acceptance checklist.")
    parser.add_argument("--env-file", help="Validate a generated production .env file.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--run-harnesses", action="store_true", help="Run deterministic backend harnesses inside final acceptance. This is the default.")
    parser.add_argument("--skip-harnesses", action="store_true", help="Use recorded evidence only; faster but not sufficient for strict acceptance.")
    parser.add_argument(
        "--run-profile",
        choices=["quick", "local", "real-mac"],
        help="Also execute a demo_operator profile. Omit for in-process acceptance only.",
    )
    parser.add_argument("--timeout", type=int, default=300, help="Timeout passed to demo_operator profile runs.")
    parser.add_argument("--keep-going", action="store_true", help="Continue demo profile commands after a failure.")
    parser.add_argument(
        "--require-production-security",
        action="store_true",
        help="Fail if production security readiness is not ready even without --env-file.",
    )
    parser.add_argument(
        "--require-accepted",
        action="store_true",
        help="Exit non-zero unless every required acceptance stage is ready.",
    )
    return parser


def run_operator_acceptance(
    *,
    env_file: str | Path | None = None,
    run_harnesses: bool = True,
    run_profile_name: str | None = None,
    profile_timeout: int = 300,
    keep_going: bool = False,
    require_production_security: bool = False,
    settings: Settings | None = None,
    project_root: Path | None = None,
) -> dict:
    started_at = time.perf_counter()
    root = project_root or PROJECT_ROOT
    resolved_settings = _settings(settings=settings, env_file=env_file, project_root=root)
    production_security_required = _production_security_required(
        resolved_settings,
        require_production_security=require_production_security,
    )
    stages = [
        _onboarding_stage(settings=resolved_settings, project_root=root),
        _security_stage(
            settings=resolved_settings,
            env_file=env_file,
            required=production_security_required,
        ),
        _event_store_stage(
            settings=resolved_settings,
            required=bool(env_file) or require_production_security,
        ),
        _final_acceptance_stage(
            settings=resolved_settings,
            project_root=root,
            run_harnesses=run_harnesses,
        ),
    ]
    if run_profile_name:
        stages.append(
            _operator_profile_stage(
                profile=run_profile_name,
                timeout=profile_timeout,
                keep_going=keep_going,
            )
        )
    failed = [stage for stage in stages if stage.failed_required]
    return {
        "status": "accepted" if not failed else "needs_attention",
        "accepted": not failed,
        "env_file": str(env_file) if env_file else None,
        "run_harnesses": run_harnesses,
        "run_profile": run_profile_name,
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "stages": [asdict(stage) for stage in stages],
        "next_steps": _dedupe(step for stage in stages for step in stage.next_steps if stage.failed_required or not stage.required),
    }


def _settings(
    *,
    settings: Settings | None,
    env_file: str | Path | None,
    project_root: Path,
) -> Settings:
    if settings is not None:
        resolved = settings
    elif env_file:
        resolved = Settings(_env_file=env_file)
    else:
        resolved = Settings()
    if env_file or resolved.voiceops_workspace:
        return resolved
    return resolved.model_copy(update={"voiceops_workspace": str(project_root)})


def _production_security_required(settings: Settings, *, require_production_security: bool) -> bool:
    environment = settings.deployment_environment.strip().lower() or "local"
    return require_production_security or environment == "production"


def _onboarding_stage(*, settings: Settings, project_root: Path) -> AcceptanceStage:
    started_at = time.perf_counter()
    report = run_team_onboarding_check(settings=settings, project_root=project_root)
    failed = [check for check in report.checks if check.required and not check.ready]
    return AcceptanceStage(
        id="team_onboarding",
        label="Team onboarding package",
        status=report.status,
        ready=report.ready,
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report.checks) - len(failed)}/{len(report.checks)} onboarding checks ready",
        next_steps=report.next_steps,
        evidence=[check.id for check in report.checks if check.ready],
    )


def _security_stage(
    *,
    settings: Settings,
    env_file: str | Path | None,
    required: bool,
) -> AcceptanceStage:
    started_at = time.perf_counter()
    report = run_security_readiness(settings=settings)
    failed = [check for check in report.checks if not check.ready]
    evidence = [check.id for check in report.checks if check.ready]
    if env_file:
        evidence.append(f"env_file={env_file}")
    return AcceptanceStage(
        id="production_security",
        label="Production security readiness",
        status=report.status if required else ("diagnostic" if not report.ready else "ready"),
        ready=report.ready or not required,
        required=required,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report.checks) - len(failed)}/{len(report.checks)} security checks ready",
        next_steps=report.next_steps,
        evidence=evidence,
    )


def _event_store_stage(*, settings: Settings, required: bool) -> AcceptanceStage:
    started_at = time.perf_counter()
    report = build_event_store_readiness(settings)
    failed = [check for check in report.checks if not check.ready]
    next_steps = list(report.warnings)
    if report.migration_command:
        next_steps.append(report.migration_command)
    return AcceptanceStage(
        id="event_store_readiness",
        label="Durable event-store readiness",
        status=report.status if required else ("diagnostic" if not report.ready else "ready"),
        ready=report.ready or not required,
        required=required,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=(
            f"{report.backend}: {report.event_count} events, {report.stream_count} streams"
            if report.ready
            else f"{report.backend}: {len(report.checks) - len(failed)}/{len(report.checks)} event-store checks ready"
        ),
        next_steps=next_steps,
        evidence=[check.id for check in report.checks if check.ready],
    )


def _final_acceptance_stage(
    *,
    settings: Settings,
    project_root: Path,
    run_harnesses: bool,
) -> AcceptanceStage:
    started_at = time.perf_counter()
    report = run_final_acceptance_audit(
        run_harnesses=run_harnesses,
        settings=settings,
        project_root=project_root,
    )
    failed = [item for item in report["items"] if item["required"] and item["status"] != "passed"]
    return AcceptanceStage(
        id="final_acceptance",
        label="Final multi-person AI teammate acceptance",
        status=report["status"],
        ready=bool(report["accepted"]),
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report['items']) - len(failed)}/{len(report['items'])} final acceptance items passed",
        next_steps=report["next_steps"],
        evidence=[item["id"] for item in report["items"] if item["status"] == "passed"],
    )


def _operator_profile_stage(
    *,
    profile: str,
    timeout: int,
    keep_going: bool,
) -> AcceptanceStage:
    started_at = time.perf_counter()
    report = run_profile(profile, timeout=timeout, keep_going=keep_going)
    failed = [result for result in report.get("results", []) if result["required"] and result["status"] == "failed"]
    return AcceptanceStage(
        id=f"operator_profile_{profile}",
        label=f"Operator profile: {profile}",
        status=report["status"],
        ready=bool(report["ready"]),
        required=True,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        summary=f"{len(report.get('results', []))} command(s) executed; {len(failed)} required failure(s)",
        next_steps=report.get("next_steps") or [],
        evidence=[result["id"] for result in report.get("results", []) if result["status"] in {"passed", "diagnostic"}],
    )


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def print_text_report(report: dict) -> None:
    print(f"VoiceOps operator acceptance: {report['status']}")
    for stage in report["stages"]:
        marker = "ok" if stage["ready"] else ("blocked" if stage["required"] else "note")
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
    report = run_operator_acceptance(
        env_file=args.env_file,
        run_harnesses=not args.skip_harnesses,
        run_profile_name=args.run_profile,
        profile_timeout=args.timeout,
        keep_going=args.keep_going,
        require_production_security=args.require_production_security,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    if args.require_accepted and not report["accepted"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
