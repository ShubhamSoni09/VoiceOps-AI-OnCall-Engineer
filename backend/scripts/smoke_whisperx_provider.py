from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager, suppress
import json
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings
from app.speakers.provider import WhisperXSpeakerProvider
from app.system.router import _speaker_provider_status


DEFAULT_ALICE_TEXT = "Alice says the login route needs a small patch in app.py."
DEFAULT_BOB_TEXT = "Bob says approve it after the tests pass."
PYANNOTE_MODEL_ID = "pyannote/speaker-diarization-community-1"
PYANNOTE_REQUIRED_ASSETS = (
    "config.yaml",
    "segmentation/pytorch_model.bin",
    "embedding/pytorch_model.bin",
    "plda/plda.npz",
    "plda/xvec_transform.npz",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real WhisperX speaker-provider smoke test against a short audio file.",
    )
    parser.add_argument("--audio", type=Path, help="Path to a short wav, mp3, webm, m4a, or aiff audio sample.")
    parser.add_argument(
        "--generate-macos-tts",
        action="store_true",
        help="Generate a short two-voice sample with macOS say and ffmpeg when --audio is omitted.",
    )
    parser.add_argument("--alice-text", default=DEFAULT_ALICE_TEXT)
    parser.add_argument("--bob-text", default=DEFAULT_BOB_TEXT)
    parser.add_argument("--timeout", type=float, default=180.0, help="Worker timeout in seconds.")
    parser.add_argument(
        "--diarization-load-timeout",
        type=float,
        default=120.0,
        help="Fail early if pyannote diarization model loading stays stuck for this many seconds.",
    )
    parser.add_argument(
        "--max-audio-seconds",
        type=float,
        help="Trim the smoke input to this many seconds before WhisperX processing.",
    )
    parser.add_argument("--model", help="Override WHISPERX_MODEL for this smoke run.")
    parser.add_argument("--device", help="Override WHISPERX_DEVICE for this smoke run.")
    parser.add_argument("--compute-type", help="Override WHISPERX_COMPUTE_TYPE for this smoke run.")
    parser.add_argument("--num-speakers", type=int, help="Tell pyannote the exact expected speaker count.")
    parser.add_argument("--min-speakers", type=int, help="Tell pyannote the minimum expected speaker count.")
    parser.add_argument("--max-speakers", type=int, help="Tell pyannote the maximum expected speaker count.")
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Run WhisperX in this process for clearer stage diagnostics and model reuse checks.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--keep-audio", action="store_true", help="Keep generated temporary audio files.")
    parser.add_argument(
        "--record-status",
        action="store_true",
        help="Write a sanitized verification report for /system/readiness.",
    )
    parser.add_argument(
        "--verification-path",
        type=Path,
        help="Override where --record-status writes the verification report.",
    )
    parser.add_argument(
        "--require-multiple-speakers",
        action="store_true",
        help="Exit non-zero unless diarization returns at least two distinct speaker labels.",
    )
    return parser


async def run_smoke(
    *,
    audio_path: Path | None,
    generate_macos_tts: bool,
    alice_text: str,
    bob_text: str,
    timeout: float,
    keep_audio: bool,
    require_multiple_speakers: bool,
    in_process: bool = False,
    model: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    max_audio_seconds: float | None = None,
    num_speakers: int | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    diarization_load_timeout: float | None = 120.0,
) -> tuple[int, dict]:
    setting_overrides = {
        "speaker_provider": "whisperx",
        "whisperx_worker_timeout_seconds": timeout,
    }
    if model:
        setting_overrides["whisperx_model"] = model
    if device:
        setting_overrides["whisperx_device"] = device
    if compute_type:
        setting_overrides["whisperx_compute_type"] = compute_type
    speaker_hints = default_speaker_hints(
        require_multiple_speakers=require_multiple_speakers,
        num_speakers=num_speakers,
        min_speakers=min_speakers,
        max_speakers=max_speakers,
    )
    setting_overrides.update(speaker_hints)
    settings = Settings(**setting_overrides)
    provider_status = _speaker_provider_status(settings)
    report: dict = {
        "provider": "whisperx",
        "ready": provider_status.ready,
        "readiness_detail": provider_status.detail,
        "checks": [check.model_dump() for check in provider_status.checks],
        "generated_audio": False,
        "input_audio_path": str(audio_path) if audio_path else None,
        "audio_path": str(audio_path) if audio_path else None,
        "audio_seconds": None,
        "max_audio_seconds": max_audio_seconds,
        "audio_trimmed": False,
        "stages": [],
        "segments": [],
        "speaker_labels": [],
        "distinct_speaker_count": 0,
        "partial_text": "",
        "elapsed_ms": 0,
        "execution_mode": "in_process" if in_process else "subprocess",
        "warnings": [],
        "config": {
            "whisperx_model": settings.whisperx_model,
            "whisperx_device": settings.whisperx_device,
            "whisperx_compute_type": settings.whisperx_compute_type,
            "worker_timeout_seconds": settings.whisperx_worker_timeout_seconds,
            "worker_mode": "in_process" if in_process else settings.whisperx_worker_mode,
            "max_audio_seconds": max_audio_seconds,
            "whisperx_num_speakers": settings.whisperx_num_speakers,
            "whisperx_min_speakers": settings.whisperx_min_speakers,
            "whisperx_max_speakers": settings.whisperx_max_speakers,
            "diarization_load_timeout_seconds": diarization_load_timeout,
        },
    }
    if in_process:
        report["warnings"].append(
            "--in-process is for manual diagnostics; use default subprocess mode for isolated automated gates."
        )
    if not provider_status.ready:
        report["error"] = provider_status.detail or "WhisperX provider is not ready"
        return 2, report
    try:
        ensure_pyannote_assets(settings, report)
    except RuntimeError as exc:
        report["error"] = str(exc)
        return 2, report

    temp_root = Path(tempfile.mkdtemp(prefix="voiceops-whisperx-smoke-"))
    try:
        if audio_path is None:
            if not generate_macos_tts:
                report["error"] = "Provide --audio or pass --generate-macos-tts on macOS."
                return 2, report
            audio_path = generate_macos_tts_sample(temp_root, alice_text, bob_text)
            report["generated_audio"] = True
            report["audio_path"] = str(audio_path)

        if not audio_path.exists():
            report["error"] = f"Audio file does not exist: {audio_path}"
            return 2, report

        audio_path = prepare_audio_sample(audio_path, temp_root, max_audio_seconds, report)
        audio_bytes = audio_path.read_bytes()
        if not audio_bytes:
            report["error"] = f"Audio file is empty: {audio_path}"
            return 2, report

        started = time.perf_counter()
        provider = WhisperXSpeakerProvider(settings, use_subprocess=not in_process)
        last_stage: dict = {"stage": None, "message": "", "seen_at": None}

        def record_stage(stage: str, message: str | None) -> None:
            last_stage.update({"stage": stage, "message": message or "", "seen_at": time.perf_counter()})
            report["stages"].append(
                {
                    "stage": stage,
                    "message": message or "",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                }
            )

        if in_process and hasattr(provider, "_process_live_chunk_sync"):
            with in_process_deadline(timeout + 10):
                result = provider._process_live_chunk_sync(
                    audio_bytes,
                    session_id="manual-whisperx-smoke",
                    sequence=1,
                    mime_type=mime_type_for_path(audio_path),
                    stage_callback=record_stage,
                )
        else:
            result = await await_with_stage_deadline(
                provider.process_live_chunk(
                    audio_bytes,
                    session_id="manual-whisperx-smoke",
                    sequence=1,
                    mime_type=mime_type_for_path(audio_path),
                    stage_callback=record_stage,
                ),
                total_timeout=timeout + 10,
                diarization_load_timeout=diarization_load_timeout,
                last_stage=last_stage,
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        segments = [segment.model_dump(mode="json") for segment in result.segments]
        labels = sorted({segment["speaker_label"] for segment in segments if segment.get("speaker_label")})
        report.update(
            {
                "temp_id": result.temp_id,
                "partial_text": result.partial_text or "",
                "segments": segments,
                "speaker_labels": labels,
                "distinct_speaker_count": len(labels),
                "elapsed_ms": elapsed_ms,
            }
        )
        if not segments:
            report["error"] = "WhisperX completed but returned no speaker segments."
            return 3, report
        if require_multiple_speakers and len(labels) < 2:
            report["error"] = f"Expected at least two speaker labels, got {labels or 'none'}."
            return 4, report
        if len(labels) < 2:
            report["warnings"].append(
                "Only one speaker label returned. Use a clearer two-person audio sample for strict diarization validation."
            )
        return 0, report
    except DiarizationLoadTimeout as exc:
        report["elapsed_ms"] = _elapsed_ms(started)
        report["error"] = str(exc)
        _append_timeout_hint(report)
        report["warnings"].append(
            "pyannote model loading did not finish. Check Hugging Face model access, local cache, and Python/ffmpeg library conflicts."
        )
        return 6, report
    except asyncio.TimeoutError:
        report["elapsed_ms"] = _elapsed_ms(started)
        report["error"] = f"WhisperX smoke timed out after {timeout + 10:.0f}s."
        _append_timeout_hint(report)
        return 5, report
    except Exception as exc:
        report["elapsed_ms"] = _elapsed_ms(started) if "started" in locals() else 0
        report["error"] = str(exc)
        if "timed out" in report["error"].lower():
            _append_timeout_hint(report)
        return 1, report
    finally:
        if keep_audio and report.get("generated_audio"):
            report["kept_audio_dir"] = str(temp_root)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


class DiarizationLoadTimeout(TimeoutError):
    pass


def ensure_pyannote_assets(settings: Settings, report: dict) -> None:
    started = time.perf_counter()
    report["stages"].append(
        {
            "stage": "preloading_pyannote_assets",
            "message": "Checking pyannote diarization model assets",
            "elapsed_ms": 0,
        }
    )
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Install huggingface_hub to preload pyannote diarization assets") from exc

    missing: list[str] = []
    for filename in PYANNOTE_REQUIRED_ASSETS:
        try:
            hf_hub_download(
                PYANNOTE_MODEL_ID,
                filename,
                token=settings.hf_token,
                etag_timeout=20,
            )
        except Exception as exc:
            missing.append(f"{filename}: {type(exc).__name__}: {str(exc).splitlines()[0][:180]}")
    elapsed_ms = _elapsed_ms(started)
    report.setdefault("config", {})["pyannote_model_id"] = PYANNOTE_MODEL_ID
    report["config"]["pyannote_required_assets"] = list(PYANNOTE_REQUIRED_ASSETS)
    if missing:
        report["stages"].append(
            {
                "stage": "pyannote_assets_failed",
                "message": "pyannote asset preload failed",
                "elapsed_ms": elapsed_ms,
            }
        )
        raise RuntimeError("pyannote model asset preload failed: " + "; ".join(missing[:3]))
    report["stages"].append(
        {
            "stage": "pyannote_assets_ready",
            "message": "pyannote diarization assets are cached",
            "elapsed_ms": elapsed_ms,
        }
    )


async def await_with_stage_deadline(
    awaitable,
    *,
    total_timeout: float,
    diarization_load_timeout: float | None,
    last_stage: dict,
):
    task = asyncio.create_task(awaitable)
    started = time.perf_counter()
    try:
        while not task.done():
            if time.perf_counter() - started >= total_timeout:
                raise asyncio.TimeoutError
            stage = last_stage.get("stage")
            message = str(last_stage.get("message") or "").lower()
            seen_at = last_stage.get("seen_at")
            if (
                diarization_load_timeout
                and stage == "diarizing"
                and "loading pyannote" in message
                and seen_at
                and time.perf_counter() - float(seen_at) >= diarization_load_timeout
            ):
                raise DiarizationLoadTimeout(
                    f"pyannote diarization model load timed out after {diarization_load_timeout:.0f}s"
                )
            await asyncio.sleep(0.25)
        return await task
    except BaseException:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        raise


def generate_macos_tts_sample(temp_root: Path, alice_text: str, bob_text: str) -> Path:
    say = shutil.which("say")
    ffmpeg = shutil.which("ffmpeg")
    if not say:
        raise RuntimeError("macOS say command is required for --generate-macos-tts")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to concatenate generated macOS TTS samples")

    alice = temp_root / "alice.aiff"
    bob = temp_root / "bob.aiff"
    output = temp_root / "meeting-smoke.wav"
    run_checked([say, "-v", "Samantha", "-o", str(alice), alice_text])
    run_checked([say, "-v", "Daniel", "-o", str(bob), bob_text])
    run_checked(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(alice),
            "-i",
            str(bob),
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(output),
        ]
    )
    return output


def prepare_audio_sample(
    audio_path: Path,
    temp_root: Path,
    max_audio_seconds: float | None,
    report: dict,
) -> Path:
    duration = audio_duration_seconds(audio_path)
    if duration is not None:
        report["audio_seconds"] = round(duration, 3)
    if not max_audio_seconds or max_audio_seconds <= 0:
        return audio_path
    if duration is not None and duration <= max_audio_seconds:
        return audio_path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        report.setdefault("warnings", []).append(
            "ffmpeg is not available; using the full audio sample instead of trimming it."
        )
        return audio_path
    trimmed = temp_root / "meeting-smoke-trimmed.wav"
    run_checked(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio_path),
            "-t",
            str(max_audio_seconds),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(trimmed),
        ]
    )
    report["audio_path"] = str(trimmed)
    report["audio_trimmed"] = True
    trimmed_duration = audio_duration_seconds(trimmed)
    if trimmed_duration is not None:
        report["audio_seconds"] = round(trimmed_duration, 3)
    return trimmed


def audio_duration_seconds(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        import wave

        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
                return frames / float(rate) if rate else None
        except (wave.Error, OSError, EOFError):
            return None
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def run_checked(command: list[str]) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Command failed: {' '.join(command[:2])}: {detail}")


@contextmanager
def in_process_deadline(seconds: float):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)

    def handle_timeout(_signum, _frame):
        raise TimeoutError(f"In-process WhisperX smoke timed out after {seconds:.0f}s.")

    signal.signal(signal.SIGALRM, handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, max(float(seconds), 0.001))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return "audio/wav"
    if suffix in {".mp3", ".mpeg"}:
        return "audio/mpeg"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    if suffix in {".aif", ".aiff"}:
        return "audio/aiff"
    if suffix == ".webm":
        return "audio/webm"
    return "application/octet-stream"


def build_verification_status(report: dict, *, exit_code: int, require_multiple_speakers: bool) -> dict:
    labels = sorted(str(label) for label in (report.get("speaker_labels") or []))
    error = report.get("error")
    error_text = compact_error(error)
    verified = exit_code == 0 and not error and bool(report.get("segments"))
    stages = sanitize_stages(report.get("stages") or [])
    last_stage = stages[-1]["stage"] if stages else None
    quality = build_quality_summary(
        report,
        verified=verified,
        require_multiple_speakers=require_multiple_speakers,
        labels=labels,
    )
    detail = (
        f"Verified {len(labels)} speaker label(s) with WhisperX"
        if verified
        else error_text or "WhisperX smoke did not complete successfully"
    )
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": "whisperx",
        "verified": verified,
        "strict_multi_speaker": bool(require_multiple_speakers),
        "generated_audio": bool(report.get("generated_audio")),
        "distinct_speaker_count": len(labels),
        "speaker_labels": labels,
        "elapsed_ms": int(report.get("elapsed_ms") or 0),
        "execution_mode": report.get("execution_mode") or "subprocess",
        "last_stage": last_stage,
        "stages": stages,
        "quality": quality,
        "warnings": [str(warning) for warning in (report.get("warnings") or []) if str(warning).strip()],
        "config": sanitize_config(report.get("config") or {}),
        "error": error_text,
        "detail": detail,
    }


def compact_error(error: object, *, limit: int = 300) -> str | None:
    if not error:
        return None
    for line in str(error).splitlines():
        clean = line.strip()
        if clean:
            if clean.startswith("WhisperX worker timed out"):
                return "WhisperX worker timed out"
            return clean[:limit]
    return str(error).strip()[:limit] or None


def sanitize_stages(stages: list) -> list[dict]:
    sanitized = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        sanitized.append(
            {
                "stage": str(stage.get("stage") or "unknown"),
                "message": str(stage.get("message") or ""),
                "elapsed_ms": int(stage.get("elapsed_ms") or 0),
            }
        )
    return sanitized[-12:]


def sanitize_config(config: dict) -> dict:
    allowed = {
        "whisperx_model",
        "whisperx_device",
        "whisperx_compute_type",
        "whisperx_num_speakers",
        "whisperx_min_speakers",
        "whisperx_max_speakers",
        "worker_timeout_seconds",
        "worker_mode",
        "max_audio_seconds",
        "diarization_load_timeout_seconds",
        "pyannote_model_id",
        "pyannote_required_assets",
    }
    return {key: config[key] for key in allowed if key in config}


def build_quality_summary(
    report: dict,
    *,
    verified: bool,
    require_multiple_speakers: bool,
    labels: list[str],
) -> dict:
    segments = report.get("segments") or []
    if not isinstance(segments, list):
        segments = []
    audio_seconds = _optional_float(report.get("audio_seconds"))
    partial_text = str(report.get("partial_text") or "").strip()
    notes: list[str] = []
    if audio_seconds is not None and audio_seconds < 3:
        notes.append("Audio sample is very short; use 10-30 seconds for stronger validation.")
    if require_multiple_speakers and len(labels) < 2:
        notes.append("Strict multi-speaker validation needs at least two detected speaker labels.")
    if not partial_text and verified:
        notes.append("No transcript preview was captured; inspect the audio quality before trusting diarization.")
    if not segments:
        notes.append("No final speaker-attributed segments were produced.")
    level = "verified" if verified and len(labels) >= (2 if require_multiple_speakers else 1) else "weak" if verified else "failed"
    return {
        "level": level,
        "audio_seconds": audio_seconds,
        "segment_count": len(segments),
        "has_transcript": bool(partial_text),
        "multi_speaker": len(labels) >= 2,
        "strict_passed": (len(labels) >= 2) if require_multiple_speakers else verified,
        "notes": notes,
    }


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 3)


def default_speaker_hints(
    *,
    require_multiple_speakers: bool,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> dict[str, int]:
    if num_speakers and num_speakers > 0:
        return {"whisperx_num_speakers": num_speakers}

    hints: dict[str, int] = {}
    if min_speakers and min_speakers > 0:
        hints["whisperx_min_speakers"] = min_speakers
    if max_speakers and max_speakers > 0:
        hints["whisperx_max_speakers"] = max_speakers
    if not hints and require_multiple_speakers:
        hints["whisperx_min_speakers"] = 2
        hints["whisperx_max_speakers"] = 2
    return hints


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _append_timeout_hint(report: dict) -> None:
    config = report.get("config") or {}
    device = str(config.get("whisperx_device") or "").lower()
    if device == "cpu":
        report.setdefault("warnings", []).append(
            "CPU diarization can exceed the smoke timeout. Try --timeout 600, a shorter sample, or a supported GPU/MPS setup."
        )


def write_verification_status(path: Path, status: dict) -> None:
    path.expanduser().parent.mkdir(parents=True, exist_ok=True)
    path.expanduser().write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")


def print_text(report: dict) -> None:
    state = "passed" if not report.get("error") else "failed"
    print(f"WhisperX audio smoke {state}")
    print(f"Ready: {report['ready']} - {report.get('readiness_detail') or ''}")
    print(f"Audio: {report.get('audio_path') or '(none)'}")
    print(f"Elapsed: {report.get('elapsed_ms', 0)}ms")
    print(f"Speakers: {', '.join(report.get('speaker_labels') or []) or '(none)'}")
    if report.get("partial_text"):
        print(f"Transcript: {report['partial_text']}")
    if report.get("segments"):
        print("Segments:")
        for segment in report["segments"]:
            print(
                f"- {segment.get('speaker_label')}: {segment.get('text')}"
                f" ({segment.get('start_ms')}ms-{segment.get('end_ms')}ms)"
            )
    if report.get("warnings"):
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    if report.get("error"):
        print(f"Error: {report['error']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code, report = asyncio.run(
        run_smoke(
            audio_path=args.audio,
            generate_macos_tts=args.generate_macos_tts,
            alice_text=args.alice_text,
            bob_text=args.bob_text,
            timeout=args.timeout,
            keep_audio=args.keep_audio,
            require_multiple_speakers=args.require_multiple_speakers,
            in_process=args.in_process,
            model=args.model,
            device=args.device,
            compute_type=args.compute_type,
            max_audio_seconds=args.max_audio_seconds,
            num_speakers=args.num_speakers,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
            diarization_load_timeout=args.diarization_load_timeout,
        )
    )
    if args.record_status:
        verification_path = args.verification_path or Settings().speaker_verification_path
        write_verification_status(
            verification_path,
            build_verification_status(
                report,
                exit_code=exit_code,
                require_multiple_speakers=args.require_multiple_speakers,
            ),
        )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
