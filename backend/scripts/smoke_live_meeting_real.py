from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(BACKEND_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT / "scripts"))

from app.auth.dependencies import get_user_store
from app.auth.users import UserStore
from app.collab.service import CollaborationService, get_collaboration_service
from app.collab.store import CollaborationStore
from app.config import Settings, get_settings
from app.main import app
from app.speakers.provider import get_speaker_provider
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.store import SpeakerStore
from app.system.evidence import write_demo_evidence_record
import app.voice_agent.router as voice_router
from smoke_whisperx_provider import (
    DEFAULT_ALICE_TEXT,
    DEFAULT_BOB_TEXT,
    ensure_pyannote_assets,
    generate_macos_tts_sample,
    in_process_deadline,
    mime_type_for_path,
    prepare_audio_sample,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real WhisperX live-meeting WebSocket smoke against a short audio file.",
    )
    parser.add_argument("--audio", type=Path, help="Path to a short wav, mp3, webm, m4a, or aiff audio sample.")
    parser.add_argument("--generate-macos-tts", action="store_true", help="Generate a two-speaker macOS sample.")
    parser.add_argument("--alice-text", default=DEFAULT_ALICE_TEXT)
    parser.add_argument("--bob-text", default=DEFAULT_BOB_TEXT)
    parser.add_argument("--timeout", type=float, default=300.0, help="WhisperX worker timeout in seconds.")
    parser.add_argument(
        "--receive-timeout",
        type=float,
        default=360.0,
        help="Overall smoke receive deadline in seconds.",
    )
    parser.add_argument("--model", default="tiny", help="WhisperX model for this live smoke.")
    parser.add_argument("--device", help="Override WHISPERX_DEVICE.")
    parser.add_argument("--compute-type", help="Override WHISPERX_COMPUTE_TYPE.")
    parser.add_argument(
        "--worker-mode",
        choices=["subprocess", "persistent_subprocess"],
        default="subprocess",
        help="Worker mode for the live WebSocket provider used by this smoke.",
    )
    parser.add_argument("--max-audio-seconds", type=float, default=12.0)
    parser.add_argument(
        "--chunks",
        type=int,
        default=1,
        help="Send the same prepared audio this many times on one live WebSocket session.",
    )
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int, default=2)
    parser.add_argument("--max-speakers", type=int, default=2)
    parser.add_argument(
        "--in-process-warmup",
        action="store_true",
        help="Warm the in-process WhisperX provider before sending live audio, then reuse it for the WebSocket run.",
    )
    parser.add_argument(
        "--allow-unsafe-in-process-warmup",
        action="store_true",
        help="Actually run real in-process WhisperX warmup. This can hang inside native model loading on some Mac/conda stacks.",
    )
    parser.add_argument(
        "--warmup-timeout",
        type=float,
        default=120.0,
        help="Maximum seconds to spend in in-process warmup before failing this smoke.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--keep-audio", action="store_true", help="Keep generated temporary audio files.")
    parser.add_argument(
        "--evidence-path",
        type=Path,
        help="Write a sanitized real-live demo evidence record for /system/readiness.",
    )
    return parser


def run_smoke(
    *,
    audio_path: Path | None,
    generate_macos_tts: bool,
    alice_text: str,
    bob_text: str,
    timeout: float,
    receive_timeout: float,
    model: str,
    device: str | None,
    compute_type: str | None,
    worker_mode: str,
    max_audio_seconds: float | None,
    chunks: int,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
    in_process_warmup: bool,
    allow_unsafe_in_process_warmup: bool,
    warmup_timeout: float,
    keep_audio: bool,
) -> tuple[int, dict]:
    started = time.perf_counter()
    temp_root = Path(tempfile.mkdtemp(prefix="voiceops-live-real-"))
    report: dict = {
        "status": "failed",
        "provider": "whisperx",
        "generated_audio": False,
        "audio_path": str(audio_path) if audio_path else None,
        "audio_seconds": None,
        "events": [],
        "stages": [],
        "partial_texts": [],
        "speaker_labels": [],
        "distinct_speaker_count": 0,
        "timeline_message_count": 0,
        "requested_chunks": max(1, int(chunks or 1)),
        "completed_chunks": 0,
        "chunk_results": [],
        "warmup_enabled": in_process_warmup,
        "warmup_timeout_seconds": warmup_timeout,
        "warmup_elapsed_ms": 0,
        "live_elapsed_ms": 0,
        "elapsed_ms": 0,
    }
    try:
        if audio_path is None:
            if not generate_macos_tts:
                report["error"] = "Provide --audio or pass --generate-macos-tts."
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

        settings = Settings(
            speaker_provider="whisperx",
            whisperx_worker_timeout_seconds=timeout,
            whisperx_model=model,
            whisperx_device=device or Settings().whisperx_device,
            whisperx_compute_type=compute_type or Settings().whisperx_compute_type,
            whisperx_num_speakers=num_speakers,
            whisperx_min_speakers=None if num_speakers else min_speakers,
            whisperx_max_speakers=None if num_speakers else max_speakers,
            whisperx_worker_mode="in_process" if in_process_warmup else worker_mode,
            users_store_path=temp_root / "users.json",
            jwt_secret="test-secret-key",
            memory_store_path=str(temp_root / "memory.json"),
            collab_store_path=temp_root / "collab-unused.json",
            speaker_store_path=temp_root / "speakers-unused.json",
            llm_provider="mock",
            tts_provider="mock",
            stt_provider="mock",
            live_chunk_seconds=2.0,
            live_window_seconds=12.0,
            live_processing_timeout_seconds=timeout + 10,
            voiceops_workspace=None,
        )
        ensure_pyannote_assets(settings, report)
        store = UserStore(settings.users_store_path)
        collab = CollaborationService(CollaborationStore(temp_root / "collab.json"))
        provider = get_speaker_provider("whisperx", settings)
        if (
            in_process_warmup
            and hasattr(provider, "_warmup_sync_locked")
            and not allow_unsafe_in_process_warmup
        ):
            report["error"] = (
                "Real in-process warmup is disabled by default because native WhisperX/pyannote loading "
                "cannot always be interrupted safely. Use the default subprocess smoke, or pass "
                "--allow-unsafe-in-process-warmup for manual diagnostics."
            )
            report["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            return 7, report
        speakers = SpeakerService(
            SpeakerStore(temp_root / "speakers.json"),
            collab,
            provider,
        )
        if in_process_warmup:
            warmup_started = time.perf_counter()

            def record_warmup_stage(stage: str, message: str | None = None) -> None:
                report["stages"].append(
                    {
                        "stage": f"warmup_{stage}",
                        "message": message or "",
                    }
                )

            try:
                _warm_provider_for_smoke(
                    provider,
                    speakers,
                    stage_callback=record_warmup_stage,
                    timeout=warmup_timeout,
                )
            except TimeoutError as exc:
                report["warmup_elapsed_ms"] = round((time.perf_counter() - warmup_started) * 1000)
                report["error"] = str(exc)
                return 6, report
            report["warmup_elapsed_ms"] = round((time.perf_counter() - warmup_started) * 1000)

        voice_router._pipeline = None
        app.dependency_overrides[get_user_store] = lambda: store
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_collaboration_service] = lambda: collab
        app.dependency_overrides[get_speaker_service] = lambda: speakers

        try:
            with TestClient(app) as client:
                token = _token(client)
                _join_room(client, token)
                result = _run_live_socket(
                    client,
                    token,
                    audio_bytes=audio_bytes,
                    mime_type=mime_type_for_path(audio_path),
                    receive_timeout=receive_timeout,
                    chunks=max(1, int(chunks or 1)),
                    report=report,
                )
                report["live_elapsed_ms"] = result.pop("live_elapsed_ms", 0)
                room = client.get("/collab/rooms/live-real", headers={"Authorization": f"Bearer {token}"})
                room.raise_for_status()
                messages = room.json().get("messages", [])
                report["timeline_message_count"] = len(messages)
                report["timeline_texts"] = [message.get("text") for message in messages]
                report.update(result)
        finally:
            voice_router._pipeline = None
            app.dependency_overrides.clear()

        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        if report["distinct_speaker_count"] < 2:
            report["error"] = f"Expected at least two speaker labels, got {report['speaker_labels'] or 'none'}."
            return 4, report
        if report["timeline_message_count"] < 1:
            report["error"] = "Live WebSocket returned speaker segments but did not write the collaboration timeline."
            return 5, report
        report["status"] = "passed"
        return 0, report
    except Exception as exc:
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        report["error"] = str(exc)
        return 1, report
    finally:
        if keep_audio and report.get("generated_audio"):
            report["kept_audio_dir"] = str(temp_root)
        else:
            shutil.rmtree(temp_root, ignore_errors=True)


def _run_live_socket(
    client: TestClient,
    token: str,
    *,
    audio_bytes: bytes,
    mime_type: str,
    receive_timeout: float,
    chunks: int,
    report: dict,
) -> dict:
    started = time.perf_counter()
    all_segments: list[dict] = []
    chunk_results: list[dict] = []
    with client.websocket_connect(f"/speakers/rooms/live-real/live?token={token}") as ws:
        _record_event(ws.receive_json(), report)
        ws.send_json(
            {
                "type": "start",
                "session_id": "real-live-smoke",
                "mime_type": mime_type,
            }
        )
        _record_event(ws.receive_json(), report)
        deadline = started + receive_timeout
        for sequence in range(1, chunks + 1):
            chunk_started = time.perf_counter()
            segments: list[dict] = []
            ws.send_json(
                {
                    "type": "audio_chunk",
                    "sequence": sequence,
                    "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                    "mime_type": mime_type,
                }
            )
            while time.perf_counter() < deadline:
                event = ws.receive_json()
                _record_event(event, report)
                if event.get("type") == "error":
                    raise RuntimeError(event.get("message") or event.get("code") or "live socket error")
                if event.get("type") == "speaker_segments":
                    segments = list(event.get("segments") or [])
                    all_segments.extend(segments)
                    labels = sorted(
                        {str(segment.get("speaker_label")) for segment in segments if segment.get("speaker_label")}
                    )
                    chunk_results.append(
                        {
                            "sequence": sequence,
                            "elapsed_ms": round((time.perf_counter() - chunk_started) * 1000),
                            "segment_count": len(segments),
                            "speaker_labels": labels,
                        }
                    )
                    break
            else:
                raise TimeoutError(f"Live WebSocket smoke timed out after {receive_timeout:.0f}s")
            if not segments:
                raise RuntimeError(f"Live WebSocket returned no speaker segments for chunk {sequence}")
        ws.send_json({"type": "stop"})

    labels = sorted({str(segment.get("speaker_label")) for segment in all_segments if segment.get("speaker_label")})
    return {
        "segments": all_segments,
        "speaker_labels": labels,
        "distinct_speaker_count": len(labels),
        "completed_chunks": len(chunk_results),
        "chunk_results": chunk_results,
        "live_elapsed_ms": round((time.perf_counter() - started) * 1000),
    }


def _warm_provider_for_smoke(provider, speakers: SpeakerService, *, stage_callback, timeout: float) -> None:
    with in_process_deadline(timeout):
        if hasattr(provider, "_warmup_sync_locked"):
            provider._warmup_sync_locked(stage_callback=stage_callback)
            return

        import asyncio

        asyncio.run(speakers.warmup_provider(stage_callback=stage_callback))


def _record_event(event: dict, report: dict) -> None:
    compact = {
        "type": event.get("type"),
        "state": event.get("state"),
        "stage": event.get("stage"),
        "message": event.get("message"),
        "sequence": event.get("sequence"),
        "elapsed_ms": event.get("elapsed_ms"),
    }
    report["events"].append({key: value for key, value in compact.items() if value is not None})
    if event.get("stage"):
        report["stages"].append(
            {
                "stage": event.get("stage"),
                "message": event.get("message") or "",
            }
        )
    if event.get("type") == "partial_transcript" and event.get("text"):
        report["partial_texts"].append(event["text"])


def _token(client: TestClient) -> str:
    response = client.post("/auth/login", json={"email": "priya@voiceops.dev", "password": "oncall123"})
    response.raise_for_status()
    return response.json()["access_token"]


def _join_room(client: TestClient, token: str) -> None:
    response = client.post(
        "/collab/rooms/live-real/join",
        headers={"Authorization": f"Bearer {token}"},
        json={"room_name": "Live real smoke", "project": "workspace"},
    )
    response.raise_for_status()


def print_text_report(report: dict) -> None:
    print(f"Real live meeting smoke: {report['status']}")
    if report.get("error"):
        print(f"Error: {report['error']}")
    print(f"Speakers: {report.get('speaker_labels', [])}")
    print(f"Timeline messages: {report.get('timeline_message_count', 0)}")
    print(f"Elapsed: {report.get('elapsed_ms', 0)}ms")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    argv = argv if argv is not None else sys.argv[1:]
    exit_code, report = run_smoke(
        audio_path=args.audio,
        generate_macos_tts=args.generate_macos_tts,
        alice_text=args.alice_text,
        bob_text=args.bob_text,
        timeout=args.timeout,
        receive_timeout=args.receive_timeout,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        worker_mode=args.worker_mode,
        max_audio_seconds=args.max_audio_seconds,
        chunks=args.chunks,
        num_speakers=args.num_speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
        in_process_warmup=args.in_process_warmup,
        allow_unsafe_in_process_warmup=args.allow_unsafe_in_process_warmup,
        warmup_timeout=args.warmup_timeout,
        keep_audio=args.keep_audio,
    )
    evidence_path = args.evidence_path or Settings().demo_evidence_path
    write_demo_evidence_record(evidence_path, _demo_evidence_record(report, exit_code, argv))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text_report(report)
    return exit_code


def _demo_evidence_record(report: dict, exit_code: int, argv: list[str]) -> dict:
    chunk_results = report.get("chunk_results") or []
    latencies = [
        int(chunk.get("elapsed_ms") or 0)
        for chunk in chunk_results
        if isinstance(chunk, dict) and int(chunk.get("elapsed_ms") or 0) > 0
    ]
    return {
        "id": "real_live_backend",
        "label": "Real WhisperX backend live",
        "status": "passed" if exit_code == 0 and report.get("status") == "passed" else "failed",
        "source": "backend_smoke_live_meeting_real",
        "provider": str(report.get("provider") or "whisperx"),
        "checked_at": datetime.now(UTC).isoformat(),
        "detail": "Real live WebSocket smoke passed" if exit_code == 0 else str(report.get("error") or "Real live WebSocket smoke failed"),
        "error": None if exit_code == 0 else str(report.get("error") or "Real live WebSocket smoke failed"),
        "duration_ms": int(report.get("elapsed_ms") or 0),
        "command": [sys.executable, "scripts/smoke_live_meeting_real.py", *argv],
        "speaker_labels": report.get("speaker_labels") or [],
        "distinct_speaker_count": int(report.get("distinct_speaker_count") or 0),
        "requested_chunks": int(report.get("requested_chunks") or 0),
        "completed_chunks": int(report.get("completed_chunks") or 0),
        "timeline_message_count": int(report.get("timeline_message_count") or 0),
        "latency": {
            "cold_start_ms": latencies[0] if latencies else 0,
            "best_warm_ms": min(latencies[1:]) if len(latencies) > 1 else 0,
            "completed_latencies_ms": latencies,
        },
        "metrics": {
            "generated_audio": bool(report.get("generated_audio")),
            "audio_seconds": report.get("audio_seconds"),
            "live_elapsed_ms": report.get("live_elapsed_ms") or 0,
            "warmup_elapsed_ms": report.get("warmup_elapsed_ms") or 0,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
