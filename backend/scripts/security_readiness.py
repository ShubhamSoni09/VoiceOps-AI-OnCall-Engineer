from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings, settings_from_env_file
from app.system.security import ProductionSecurityReport, build_production_security_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print the VoiceOps production security readiness report.",
    )
    parser.add_argument("--env-file", help="Read settings from a generated .env file instead of the current process environment.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless production security readiness passes.",
    )
    return parser


def run_security_readiness(
    settings: Settings | None = None,
    *,
    env_file: str | Path | None = None,
) -> ProductionSecurityReport:
    if settings is not None:
        return build_production_security_report(settings)
    if env_file:
        return build_production_security_report(settings_from_env_file(env_file))
    return build_production_security_report(Settings())


def print_text_report(report: ProductionSecurityReport) -> None:
    print(f"VoiceOps production security readiness: {report.status}")
    print(f"Environment: {report.environment}")
    print(f"Checks: {sum(1 for check in report.checks if check.ready)}/{len(report.checks)} ready")
    for check in report.checks:
        marker = "ok" if check.ready else "blocked"
        print(f"- {check.label}: {marker} ({check.severity})")
        print(f"  {check.detail}")
        if check.mitigations and not check.ready:
            print(f"  next: {check.mitigations[0]}")
    if report.next_steps:
        print("Next steps:")
        for step in report.next_steps:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_security_readiness(env_file=args.env_file)
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2))
    else:
        print_text_report(report)
    if args.require_ready and not report.ready:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
