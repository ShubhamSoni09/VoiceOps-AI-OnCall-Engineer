from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.system.router import _speaker_provider_status


def build_report(settings: Settings | None = None) -> dict:
    settings = settings or Settings()
    status = _speaker_provider_status(settings)
    checks = [check.model_dump() for check in status.checks]
    return {
        "provider": status.value,
        "ready": status.ready,
        "detail": status.detail,
        "checks": checks,
        "config": {
            "hf_token_configured": bool(settings.hf_token),
            "whisperx_model": settings.whisperx_model,
            "whisperx_device": settings.whisperx_device,
            "whisperx_compute_type": settings.whisperx_compute_type,
            "live_chunk_seconds": settings.live_chunk_seconds,
            "live_window_seconds": settings.live_window_seconds,
            "worker_timeout_seconds": settings.whisperx_worker_timeout_seconds,
            "worker_mode": settings.whisperx_worker_mode,
        },
        "next_steps": next_steps(status.ready, status.value, checks, settings),
    }


def next_steps(ready: bool, provider: str, checks: list[dict], settings: Settings) -> list[str]:
    if provider == "mock":
        return [
            "Set SPEAKER_PROVIDER=whisperx to test real local diarization.",
            "Keep SPEAKER_PROVIDER=mock for deterministic demos and CI.",
        ]
    missing = [check["label"] for check in checks if not check["ready"]]
    steps: list[str] = []
    if "HF_TOKEN" in missing:
        steps.append("Create a Hugging Face token and export HF_TOKEN before starting the backend.")
    if any(label in missing for label in ("WhisperX package", "Torch runtime", "pyannote.audio")):
        steps.append("Install backend dependencies with python -m pip install -r requirements.txt.")
    if ready and settings.whisperx_device == "cpu":
        steps.append("CPU mode is supported but may lag; use WHISPERX_DEVICE=cuda or mps only if the installed stack supports it.")
    if ready and settings.whisperx_worker_mode == "subprocess":
        steps.append(
            "For lower live cold-start latency, set WHISPERX_WORKER_MODE=persistent_subprocess after validating memory use."
        )
    if ready and settings.whisperx_worker_mode == "in_process":
        steps.append("Use WHISPERX_WORKER_MODE=in_process only for manual diagnostics; native model loading can hang.")
    if ready:
        steps.append("Run python scripts/smoke_whisperx_provider.py --audio <sample.wav> --record-status to verify real diarization.")
        steps.append("Start the backend and use Live meeting mode; the UI should show provider whisperx in Live diagnostics.")
    return steps


def print_text(report: dict) -> None:
    state = "ready" if report["ready"] else "needs attention"
    print(f"Speaker provider: {report['provider']} ({state})")
    if report["detail"]:
        print(f"Detail: {report['detail']}")
    for check in report["checks"]:
        mark = "ok" if check["ready"] else "missing"
        print(f"- {check['label']}: {mark}" + (f" - {check['detail']}" if check.get("detail") else ""))
    config = report["config"]
    print(
        "Config: "
        f"model={config['whisperx_model']} "
        f"device={config['whisperx_device']} "
        f"compute={config['whisperx_compute_type']} "
        f"chunk={config['live_chunk_seconds']}s "
        f"window={config['live_window_seconds']}s"
        f" worker={config['worker_mode']}"
    )
    if report["next_steps"]:
        print("Next steps:")
        for step in report["next_steps"]:
            print(f"- {step}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local speaker provider readiness.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")
    parser.add_argument("--no-fail", action="store_true", help="Exit 0 even when the provider is not ready.")
    args = parser.parse_args(argv)

    report = build_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report)
    return 0 if args.no_fail or report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
