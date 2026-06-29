from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.system.router import TargetReadinessResponse, _target_readiness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the strict VoiceOps target-readiness closure report.",
    )
    parser.add_argument("--env-file", help="Read settings from a generated .env file instead of the current process environment.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless every target-readiness milestone is ready.",
    )
    return parser


def run_target_readiness(
    settings: Settings | None = None,
    *,
    env_file: str | Path | None = None,
) -> TargetReadinessResponse:
    if settings is not None:
        return _target_readiness(settings)
    if env_file:
        return _target_readiness(Settings(_env_file=env_file))
    return _target_readiness(Settings())


def print_text_report(report: TargetReadinessResponse) -> None:
    print(f"VoiceOps target readiness: {report.status}")
    print(f"Score: {report.score}% ({report.ready_count}/{report.total_count})")
    for milestone in report.milestones:
        marker = "ok" if milestone.ready else "blocked"
        print(f"- {milestone.label}: {marker} ({milestone.status})")
        if milestone.evidence:
            print(f"  evidence: {milestone.evidence}")
        print(f"  {milestone.detail}")
        if milestone.next_action:
            print(f"  next: {milestone.next_action}")
        if milestone.command:
            print(f"  command: {milestone.command}")
    if report.next_steps:
        print("Next steps:")
        for step in report.next_steps:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_target_readiness(env_file=args.env_file) if args.env_file else run_target_readiness()
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print_text_report(report)
    if args.require_ready and not report.ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
