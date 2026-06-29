from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.system.cutover import build_production_cutover_report
from scripts.production_trial_acceptance import _settings_from_env_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether a generated VoiceOps production-trial bundle is ready for real team cutover.",
    )
    parser.add_argument("--env-file", required=True, help="Generated production-trial .env path.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--require-ready", action="store_true", help="Exit non-zero unless cutover checks pass.")
    return parser


def run_production_cutover_check(*, env_file: str | Path) -> dict[str, Any]:
    env_path = Path(env_file).expanduser()
    settings = _settings_from_env_file(env_path)
    return build_production_cutover_report(settings, env_file=env_path).model_dump(mode="json")


def print_text_report(report: dict[str, Any]) -> None:
    print(f"VoiceOps production cutover: {report['status']}")
    for check in report["checks"]:
        marker = "ok" if check["ready"] else "blocked"
        print(f"- {check['label']}: {marker} ({check['severity']})")
        print(f"  {check['summary']}")
        if check.get("next_step") and not check["ready"]:
            print(f"  next: {check['next_step']}")
    print(f"Acceptance: {report['acceptance_command']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_production_cutover_check(env_file=args.env_file)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    return 0 if report["ready"] or not args.require_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
