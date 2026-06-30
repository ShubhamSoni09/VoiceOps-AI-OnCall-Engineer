from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


@dataclass(frozen=True)
class OperatorCommand:
    id: str
    label: str
    purpose: str
    command: list[str]
    cwd: str
    required: bool = True
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 300

    @property
    def shell_hint(self) -> str:
        prefix = " ".join(f"{key}={value}" for key, value in sorted(self.env.items()))
        body = " ".join(self.command)
        command = f"{prefix} {body}".strip()
        return f"cd {self.cwd} && {command}"


@dataclass
class OperatorResult:
    id: str
    label: str
    status: str
    required: bool
    duration_ms: int
    command: list[str]
    cwd: str
    exit_code: int | None = None
    detail: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    data: dict | None = None


@dataclass(frozen=True)
class PreflightResult:
    id: str
    label: str
    status: str
    required: bool
    detail: str
    next_action: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print or run the VoiceOps operator demo-readiness command set.",
    )
    parser.add_argument(
        "--profile",
        choices=["quick", "local", "production-trial", "real-mac"],
        default="local",
        help="Command profile to print or run.",
    )
    parser.add_argument("--run", action="store_true", help="Execute the selected profile.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    parser.add_argument("--timeout", type=int, default=600, help="Default per-command timeout in seconds.")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after a required command fails and report all failures.",
    )
    return parser


def command_plan(profile: str, *, timeout: int = 300) -> list[OperatorCommand]:
    python = sys.executable
    base_env = {
        "LLM_PROVIDER": "mock",
        "STT_PROVIDER": "mock",
        "TTS_PROVIDER": "mock",
    }
    if profile == "quick":
        return [
            OperatorCommand(
                id="target_readiness",
                label="Target readiness diagnostic",
                purpose="Show strict project milestones without running browser or regression gates.",
                command=[python, "scripts/target_readiness.py", "--json"],
                cwd=str(BACKEND_ROOT),
                env=base_env,
                timeout_seconds=60,
            ),
            OperatorCommand(
                id="demo_readiness_fast",
                label="Fast demo readiness diagnostic",
                purpose="Validate configuration and recorded evidence while skipping long test gates.",
                command=[python, "scripts/demo_readiness.py", "--skip-backend-tests", "--skip-frontend", "--json"],
                cwd=str(BACKEND_ROOT),
                required=False,
                env={**base_env, "SPEAKER_PROVIDER": "mock"},
                timeout_seconds=90,
            ),
        ]
    if profile == "real-mac":
        return [
            OperatorCommand(
                id="real_demo_readiness",
                label="Real WhisperX demo readiness",
                purpose="Generate a macOS two-speaker sample, require real diarization, run real live meeting smoke.",
                command=[
                    python,
                    "scripts/demo_readiness.py",
                    "--generate-macos-tts",
                    "--require-real-diarization",
                    "--require-real-live-meeting",
                    "--real-live-worker-mode",
                    "persistent_subprocess",
                    "--real-live-chunks",
                    "2",
                    "--timeout",
                    str(timeout),
                    "--json",
                ],
                cwd=str(BACKEND_ROOT),
                env={**base_env, "SPEAKER_PROVIDER": "whisperx", "WHISPERX_MODEL": "tiny"},
                timeout_seconds=max(timeout + 180, 480),
            ),
            OperatorCommand(
                id="target_readiness_required",
                label="Strict target readiness closure",
                purpose="Fail if any production-target milestone still lacks evidence.",
                command=[python, "scripts/target_readiness.py", "--require-ready", "--json"],
                cwd=str(BACKEND_ROOT),
                env={**base_env, "SPEAKER_PROVIDER": "whisperx"},
                timeout_seconds=60,
            ),
        ]
    if profile == "production-trial":
        return [
            OperatorCommand(
                id="sqlite_meeting_closure",
                label="SQLite meeting closure harness",
                purpose="Run the full two-person approval, branch, tests, handoff, and event-log closure on SQLite.",
                command=[
                    python,
                    "scripts/smoke_meeting_closure.py",
                    "--json",
                    "--collab-backend",
                    "sqlite",
                ],
                cwd=str(BACKEND_ROOT),
                env=base_env,
                timeout_seconds=max(timeout, 180),
            ),
            OperatorCommand(
                id="operator_acceptance",
                label="Operator acceptance diagnostic",
                purpose="Confirm production-facing checks still accept the current local build after SQLite closure.",
                command=[python, "scripts/operator_acceptance.py", "--json", "--require-accepted"],
                cwd=str(BACKEND_ROOT),
                env=base_env,
                timeout_seconds=max(timeout, 300),
            ),
        ]
    return [
        OperatorCommand(
            id="demo_readiness_full",
            label="Full local demo readiness",
            purpose="Run backend regression, frontend unit/build, and deterministic browser E2E gates.",
            command=[python, "scripts/demo_readiness.py", "--timeout", str(timeout), "--json"],
            cwd=str(BACKEND_ROOT),
            env={**base_env, "SPEAKER_PROVIDER": "mock"},
            # ponytail: demo_readiness applies timeout per inner gate; outer command just needs slack.
            timeout_seconds=max(timeout + 300, 900),
        ),
        OperatorCommand(
            id="target_readiness",
            label="Target readiness diagnostic",
            purpose="Print remaining strict target gaps after the full local gates complete.",
            command=[python, "scripts/target_readiness.py", "--json"],
            cwd=str(BACKEND_ROOT),
            env=base_env,
            timeout_seconds=60,
        ),
    ]


def run_profile(profile: str, *, timeout: int, keep_going: bool) -> dict:
    started_at = time.perf_counter()
    commands = command_plan(profile, timeout=timeout)
    preflight = profile_preflight(profile)
    blocking_preflight = [item for item in preflight if item.required and item.status == "failed"]
    if blocking_preflight and not keep_going:
        return {
            "profile": profile,
            "status": "needs_attention",
            "ready": False,
            "duration_ms": round((time.perf_counter() - started_at) * 1000),
            "preflight": [asdict(item) for item in preflight],
            "commands": [asdict(command) | {"shell_hint": command.shell_hint} for command in commands],
            "results": [],
            "skipped_after_failure": [command.id for command in commands],
            "next_steps": preflight_next_steps(blocking_preflight),
        }
    results: list[OperatorResult] = []
    for command in commands:
        result = run_operator_command(command)
        results.append(result)
        if result.status == "failed" and result.required and not keep_going:
            break
    failed = [result for result in results if result.required and result.status == "failed"]
    skipped = [command for command in commands[len(results) :]]
    return {
        "profile": profile,
        "status": "ready" if not failed and not skipped else "needs_attention",
        "ready": not failed and not skipped,
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "preflight": [asdict(item) for item in preflight],
        "commands": [asdict(command) | {"shell_hint": command.shell_hint} for command in commands],
        "results": [asdict(result) for result in results],
        "skipped_after_failure": [command.id for command in skipped],
        "next_steps": operator_next_steps(failed, skipped, profile),
    }


def run_operator_command(command: OperatorCommand) -> OperatorResult:
    started_at = time.perf_counter()
    env = {**os.environ, **command.env}
    try:
        result = subprocess.run(
            command.command,
            cwd=command.cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=command.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return OperatorResult(
            id=command.id,
            label=command.label,
            status="failed",
            required=command.required,
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            command=command.command,
            cwd=command.cwd,
            exit_code=124,
            detail=f"Timed out after {command.timeout_seconds}s",
            stdout_tail=tail(exc.stdout or ""),
            stderr_tail=tail(exc.stderr or ""),
        )
    data = parse_json_stdout(result.stdout)
    status = "passed" if result.returncode == 0 else ("diagnostic" if not command.required else "failed")
    return OperatorResult(
        id=command.id,
        label=command.label,
        status=status,
        required=command.required,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        command=command.command,
        cwd=command.cwd,
        exit_code=result.returncode,
        detail=operator_detail(result.returncode, result.stdout, result.stderr, data),
        stdout_tail=tail(result.stdout),
        stderr_tail=tail(result.stderr),
        data=data,
    )


def profile_preflight(profile: str) -> list[PreflightResult]:
    if profile != "real-mac":
        return []
    checks = [
        env_check("HF_TOKEN", "Hugging Face token", "Create a Hugging Face token with pyannote model access and export HF_TOKEN."),
        binary_check("ffmpeg", "ffmpeg", "Install ffmpeg, for example: brew install ffmpeg."),
        binary_check("say", "macOS say", "Run this profile on macOS or provide real audio to lower-level smoke scripts."),
    ]
    return checks


def env_check(name: str, label: str, next_action: str) -> PreflightResult:
    value = os.environ.get(name, "").strip()
    return PreflightResult(
        id=f"env_{name.lower()}",
        label=label,
        status="passed" if value else "failed",
        required=True,
        detail=f"{name} is set" if value else f"{name} is missing",
        next_action=None if value else next_action,
    )


def binary_check(name: str, label: str, next_action: str) -> PreflightResult:
    path = shutil.which(name)
    return PreflightResult(
        id=f"binary_{name}",
        label=label,
        status="passed" if path else "failed",
        required=True,
        detail=path or f"{name} was not found on PATH",
        next_action=None if path else next_action,
    )


def plan_report(profile: str, *, timeout: int) -> dict:
    commands = command_plan(profile, timeout=timeout)
    preflight = profile_preflight(profile)
    return {
        "profile": profile,
        "status": "plan",
        "ready": False,
        "preflight": [asdict(item) for item in preflight],
        "commands": [asdict(command) | {"shell_hint": command.shell_hint} for command in commands],
        "next_steps": [
            "Run again with --run to execute this profile.",
            "Use --profile quick for a fast diagnostic, --profile local for deterministic full local gates, --profile production-trial for SQLite closure, or --profile real-mac for WhisperX/macOS TTS gates.",
        ],
    }


def parse_json_stdout(stdout: str) -> dict | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None


def operator_detail(exit_code: int, stdout: str, stderr: str, data: dict | None) -> str:
    if exit_code == 0:
        if data and data.get("status"):
            return str(data["status"])
        return "passed"
    if data and data.get("next_steps"):
        return str(data["next_steps"][0])
    if data and data.get("status"):
        return str(data["status"])
    for source in (stderr, stdout):
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if lines:
            return lines[-1][:300]
    return f"Command exited with {exit_code}"


def operator_next_steps(failed: Sequence[OperatorResult], skipped: Sequence[OperatorCommand], profile: str) -> list[str]:
    steps = [f"Fix {result.label}: {result.detail}" for result in failed]
    if skipped:
        steps.append(f"Skipped after failure: {', '.join(command.id for command in skipped)}")
    if profile == "real-mac":
        steps.append("For real-mac, verify HF_TOKEN, pyannote model access, ffmpeg, and macOS say are available.")
    return dedupe(steps)


def preflight_next_steps(failed: Sequence[PreflightResult]) -> list[str]:
    return dedupe([item.next_action or item.detail for item in failed])


def print_text_report(report: dict) -> None:
    print(f"VoiceOps operator profile: {report['profile']} ({report['status']})")
    for item in report.get("preflight", []):
        marker = "ok" if item["status"] == "passed" else "blocked"
        print(f"- Preflight {item['label']}: {marker}")
        print(f"  {item['detail']}")
        if item.get("next_action"):
            print(f"  next: {item['next_action']}")
    for command in report["commands"]:
        print(f"- {command['label']}")
        print(f"  {command['purpose']}")
        print(f"  {command['shell_hint']}")
    for result in report.get("results", []):
        print(f"* {result['label']}: {result['status']} ({result['duration_ms']}ms)")
        if result.get("detail"):
            print(f"  {result['detail']}")
    if report.get("next_steps"):
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"- {step}")


def tail(text: str, *, lines: int = 18) -> str:
    if not text:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_profile(args.profile, timeout=args.timeout, keep_going=args.keep_going) if args.run else plan_report(args.profile, timeout=args.timeout)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    if args.run and not report["ready"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
