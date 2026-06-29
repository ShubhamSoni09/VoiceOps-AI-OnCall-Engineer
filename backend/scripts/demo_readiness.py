from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
FRONTEND_ROOT = PROJECT_ROOT / "frontend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.system.router import _speaker_verification_status


Status = str


@dataclass
class GateResult:
    id: str
    label: str
    status: Status
    required: bool
    detail: str
    command: list[str] = field(default_factory=list)
    cwd: str | None = None
    duration_ms: int = 0
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    data: dict | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def failed_required(self) -> bool:
        return self.required and self.status == "failed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the VoiceOps demo-readiness gates and print a concise closure report.",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")
    parser.add_argument("--skip-backend-tests", action="store_true", help="Skip pytest backend regression gates.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip frontend unit, build, and browser E2E gates.")
    parser.add_argument(
        "--require-real-diarization",
        action="store_true",
        help="Fail unless a recorded WhisperX multi-speaker verification report exists.",
    )
    parser.add_argument(
        "--require-real-live-meeting",
        action="store_true",
        help="Run and require a real WhisperX live-meeting WebSocket smoke.",
    )
    parser.add_argument(
        "--real-audio",
        type=Path,
        help="Run a real WhisperX smoke against this audio file and record verification status before checking readiness.",
    )
    parser.add_argument(
        "--generate-macos-tts",
        action="store_true",
        help="Generate a macOS two-voice sample for the real WhisperX smoke before checking readiness.",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-command timeout in seconds.")
    parser.add_argument(
        "--real-model",
        default="tiny",
        help="WhisperX model for generated/real smoke verification. Default is tiny for local Mac speed.",
    )
    parser.add_argument("--real-device", help="Override WHISPERX_DEVICE for real smoke verification.")
    parser.add_argument("--real-compute-type", help="Override WHISPERX_COMPUTE_TYPE for real smoke verification.")
    parser.add_argument(
        "--real-max-audio-seconds",
        type=float,
        default=12.0,
        help="Trim real/generated verification audio to this many seconds before diarization.",
    )
    parser.add_argument(
        "--real-diarization-load-timeout",
        type=float,
        default=120.0,
        help="Fail early if pyannote model loading does not progress within this many seconds.",
    )
    parser.add_argument(
        "--real-in-process",
        action="store_true",
        help="Run real smoke paths in process where supported for manual diagnostics and model reuse checks.",
    )
    parser.add_argument(
        "--real-live-worker-mode",
        choices=["subprocess", "persistent_subprocess"],
        default="subprocess",
        help="Worker mode used by the real live WebSocket smoke gate.",
    )
    parser.add_argument(
        "--real-live-chunks",
        type=int,
        default=1,
        help="Number of sequential audio chunks for the real live WebSocket smoke gate.",
    )
    parser.add_argument("--keep-audio", action="store_true", help="Keep generated audio from the WhisperX smoke.")
    return parser


def run_demo_readiness(
    *,
    skip_backend_tests: bool = False,
    skip_frontend: bool = False,
    require_real_diarization: bool = False,
    real_audio: Path | None = None,
    generate_macos_tts: bool = False,
    timeout: float = 180.0,
    real_model: str | None = "tiny",
    real_device: str | None = None,
    real_compute_type: str | None = None,
    real_max_audio_seconds: float | None = 12.0,
    real_diarization_load_timeout: float | None = 120.0,
    real_in_process: bool = False,
    real_live_worker_mode: str = "subprocess",
    real_live_chunks: int = 1,
    keep_audio: bool = False,
    require_real_live_meeting: bool = False,
) -> dict:
    started_at = time.perf_counter()
    settings = Settings()
    gates: list[GateResult] = []

    if real_audio or generate_macos_tts:
        gates.append(
            run_command_gate(
                gate_id="real_diarization_smoke",
                label="Real WhisperX diarization smoke",
                command=real_diarization_command(
                    real_audio=real_audio,
                    generate_macos_tts=generate_macos_tts,
                    timeout=timeout,
                    model=real_model,
                    device=real_device,
                    compute_type=real_compute_type,
                    max_audio_seconds=real_max_audio_seconds,
                    diarization_load_timeout=real_diarization_load_timeout,
                    in_process=real_in_process,
                    keep_audio=keep_audio,
                    verification_path=settings.speaker_verification_path,
                ),
                cwd=BACKEND_ROOT,
                required=require_real_diarization,
                timeout=timeout + 45,
                json_stdout=True,
            )
        )

    if skip_backend_tests:
        gates.append(skipped_gate("backend_tests", "Backend regression tests", required=True))
    else:
        gates.append(
            run_command_gate(
                gate_id="backend_tests",
                label="Backend regression tests",
                command=[sys.executable, "-m", "pytest", "tests", "-q"],
                cwd=BACKEND_ROOT,
                required=True,
                timeout=max(timeout, 240),
            )
        )

    if skip_frontend:
        gates.extend(
            [
                skipped_gate("frontend_unit", "Frontend unit tests", required=True),
                skipped_gate("frontend_build", "Frontend production build", required=True),
                skipped_gate("frontend_e2e", "Browser demo readiness E2E", required=True),
            ]
        )
    else:
        gates.append(
            run_command_gate(
                gate_id="frontend_unit",
                label="Frontend unit tests",
                command=["npm", "run", "test:unit"],
                cwd=FRONTEND_ROOT,
                required=True,
                timeout=timeout,
            )
        )
        gates.append(
            run_command_gate(
                gate_id="frontend_build",
                label="Frontend production build",
                command=["npm", "run", "build"],
                cwd=FRONTEND_ROOT,
                required=True,
                timeout=timeout,
            )
        )
        gates.append(
            run_command_gate(
                gate_id="frontend_e2e",
                label="Browser demo readiness E2E",
                command=["npm", "run", "e2e:all", "--", "--json"],
                cwd=FRONTEND_ROOT,
                required=True,
                timeout=max(timeout, 240),
                json_stdout=True,
            )
        )

    if require_real_live_meeting:
        gates.append(
            run_command_gate(
                gate_id="real_live_meeting_smoke",
                label="Real live meeting WebSocket smoke",
                command=real_live_meeting_command(
                    real_audio=real_audio,
                    generate_macos_tts=generate_macos_tts,
                    timeout=timeout,
                    model=real_model,
                    device=real_device,
                    compute_type=real_compute_type,
                    worker_mode=real_live_worker_mode,
                    max_audio_seconds=real_max_audio_seconds,
                    chunks=real_live_chunks,
                    keep_audio=keep_audio,
                ),
                cwd=BACKEND_ROOT,
                required=True,
                timeout=timeout + 90,
                json_stdout=True,
            )
        )

    gates.append(real_diarization_status_gate(settings, required=require_real_diarization))
    failed = [gate for gate in gates if gate.failed_required]
    skipped_required = [gate for gate in gates if gate.required and gate.status == "skipped"]
    status = "ready" if not failed and not skipped_required else "needs_attention"

    return {
        "status": status,
        "ready": status == "ready",
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "required_failed": [gate.id for gate in failed],
        "required_skipped": [gate.id for gate in skipped_required],
        "gates": [asdict(gate) for gate in gates],
        "next_steps": next_steps(gates, require_real_diarization=require_real_diarization),
    }


def real_diarization_command(
    *,
    real_audio: Path | None,
    generate_macos_tts: bool,
    timeout: float,
    model: str | None,
    device: str | None,
    compute_type: str | None,
    max_audio_seconds: float | None,
    diarization_load_timeout: float | None,
    in_process: bool,
    keep_audio: bool,
    verification_path: Path,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/smoke_whisperx_provider.py",
        "--timeout",
        str(timeout),
        "--record-status",
        "--verification-path",
        str(verification_path),
        "--require-multiple-speakers",
        "--json",
    ]
    if model:
        command.extend(["--model", model])
    if device:
        command.extend(["--device", device])
    if compute_type:
        command.extend(["--compute-type", compute_type])
    if max_audio_seconds and max_audio_seconds > 0:
        command.extend(["--max-audio-seconds", str(max_audio_seconds)])
    if diarization_load_timeout and diarization_load_timeout > 0:
        command.extend(["--diarization-load-timeout", str(diarization_load_timeout)])
    if in_process:
        command.append("--in-process")
    if real_audio:
        command.extend(["--audio", str(real_audio)])
    if generate_macos_tts:
        command.append("--generate-macos-tts")
    if keep_audio:
        command.append("--keep-audio")
    return command


def real_live_meeting_command(
    *,
    real_audio: Path | None,
    generate_macos_tts: bool,
    timeout: float,
    model: str | None,
    device: str | None,
    compute_type: str | None,
    worker_mode: str,
    max_audio_seconds: float | None,
    chunks: int,
    keep_audio: bool,
) -> list[str]:
    command = [
        sys.executable,
        "scripts/smoke_live_meeting_real.py",
        "--timeout",
        str(timeout),
        "--receive-timeout",
        str(timeout + 45),
        "--json",
    ]
    if model:
        command.extend(["--model", model])
    if device:
        command.extend(["--device", device])
    if compute_type:
        command.extend(["--compute-type", compute_type])
    if worker_mode:
        command.extend(["--worker-mode", worker_mode])
    if max_audio_seconds and max_audio_seconds > 0:
        command.extend(["--max-audio-seconds", str(max_audio_seconds)])
    if chunks and chunks > 1:
        command.extend(["--chunks", str(chunks)])
    if real_audio:
        command.extend(["--audio", str(real_audio)])
    if generate_macos_tts or not real_audio:
        command.append("--generate-macos-tts")
    if keep_audio:
        command.append("--keep-audio")
    return command


def run_command_gate(
    *,
    gate_id: str,
    label: str,
    command: Sequence[str],
    cwd: Path,
    required: bool,
    timeout: float,
    json_stdout: bool = False,
) -> GateResult:
    started_at = time.perf_counter()
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return GateResult(
            id=gate_id,
            label=label,
            status="failed",
            required=required,
            detail=f"Timed out after {timeout:.0f}s",
            command=list(command),
            cwd=str(cwd),
            duration_ms=round((time.perf_counter() - started_at) * 1000),
            exit_code=124,
            stdout_tail=tail(exc.stdout or ""),
            stderr_tail=tail(exc.stderr or ""),
        )

    data = parse_json_stdout(result.stdout) if json_stdout and result.stdout.strip() else None
    return GateResult(
        id=gate_id,
        label=label,
        status="passed" if result.returncode == 0 else "failed",
        required=required,
        detail=gate_detail(result.returncode, result.stdout, result.stderr, data),
        command=list(command),
        cwd=str(cwd),
        duration_ms=round((time.perf_counter() - started_at) * 1000),
        exit_code=result.returncode,
        stdout_tail=tail(result.stdout),
        stderr_tail=tail(result.stderr),
        data=data,
    )


def real_diarization_status_gate(settings: Settings, *, required: bool) -> GateResult:
    status = _speaker_verification_status(settings)
    verified = status.verified and status.distinct_speaker_count >= 2
    gate_status = "passed" if verified else ("failed" if required else "skipped")
    detail = status.detail
    if not verified and not required:
        detail = f"{status.detail}. Pass --require-real-diarization to make this gate mandatory."
    return GateResult(
        id="real_diarization_status",
        label="Recorded real multi-speaker diarization",
        status=gate_status,
        required=required,
        detail=detail,
        data=status.model_dump(mode="json"),
    )


def skipped_gate(gate_id: str, label: str, *, required: bool) -> GateResult:
    return GateResult(
        id=gate_id,
        label=label,
        status="skipped",
        required=required,
        detail="Skipped by command-line option",
    )


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


def gate_detail(exit_code: int, stdout: str, stderr: str, data: dict | None) -> str:
    if exit_code == 0:
        if data and data.get("status"):
            return str(data["status"])
        return "passed"
    if data and data.get("error"):
        return str(data["error"])
    for source in (stderr, stdout):
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        if lines:
            return lines[-1][:300]
    return f"Command exited with {exit_code}"


def next_steps(gates: list[GateResult], *, require_real_diarization: bool) -> list[str]:
    steps: list[str] = []
    for gate in gates:
        if gate.status == "failed":
            steps.append(f"Fix {gate.label}: {gate.detail}")
    real_gate = next((gate for gate in gates if gate.id == "real_diarization_status"), None)
    if real_gate and real_gate.status == "skipped":
        if real_gate.data and real_gate.data.get("last_stage"):
            steps.append(f"Last real diarization stage: {real_gate.data['last_stage']}.")
        for warning in (real_gate.data or {}).get("warnings") or []:
            steps.append(str(warning))
        steps.append(
            "For reusable isolated live models, set WHISPERX_WORKER_MODE=persistent_subprocess after validating memory use."
        )
        steps.append(
            "Run real audio verification when ready: python scripts/demo_readiness.py "
            "--real-audio <meeting.wav> --require-real-diarization --require-real-live-meeting"
        )
    if require_real_diarization and real_gate and real_gate.status == "failed":
        steps.append("Verify SPEAKER_PROVIDER=whisperx, HF_TOKEN, dependencies, and a clear two-speaker audio sample.")
    return dedupe(steps)


def tail(text: str, *, lines: int = 24) -> str:
    if not text:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


def dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def print_text_report(report: dict) -> None:
    print(f"VoiceOps demo readiness: {report['status']}")
    print(f"Duration: {report['duration_ms']}ms")
    for gate in report["gates"]:
        required = "required" if gate["required"] else "optional"
        print(f"- {gate['label']}: {gate['status']} ({required})")
        if gate.get("detail"):
            print(f"  {gate['detail']}")
    if report["next_steps"]:
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_demo_readiness(
        skip_backend_tests=args.skip_backend_tests,
        skip_frontend=args.skip_frontend,
        require_real_diarization=args.require_real_diarization,
        real_audio=args.real_audio,
        generate_macos_tts=args.generate_macos_tts,
        timeout=args.timeout,
        real_model=args.real_model,
        real_device=args.real_device,
        real_compute_type=args.real_compute_type,
        real_max_audio_seconds=args.real_max_audio_seconds,
        real_diarization_load_timeout=args.real_diarization_load_timeout,
        real_in_process=args.real_in_process,
        real_live_worker_mode=args.real_live_worker_mode,
        real_live_chunks=args.real_live_chunks,
        keep_audio=args.keep_audio,
        require_real_live_meeting=args.require_real_live_meeting,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
