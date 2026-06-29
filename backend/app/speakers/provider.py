import asyncio
import base64
import json
import sys
import tempfile
import threading
from pathlib import Path
from collections.abc import Callable
from contextlib import suppress

from app.config import BACKEND_ROOT, Settings
from app.speakers.models import LiveProviderResult, SpeakerSegment

LiveStageCallback = Callable[[str, str | None], None]
_WORKER_RESULT_PREFIX = "VOICEOPS_LIVE_RESULT="
_WORKER_STAGE_PREFIX = "VOICEOPS_LIVE_STAGE="
_WORKER_READY_PREFIX = "VOICEOPS_LIVE_READY="


class MockSpeakerProvider:
    """Deterministic local provider for tests and product flow wiring."""

    name = "mock"

    def normalize_segments(self, segments: list[SpeakerSegment]) -> list[SpeakerSegment]:
        normalized: list[SpeakerSegment] = []
        for index, segment in enumerate(segments):
            label = segment.speaker_label.strip() or f"SPEAKER_{index:02d}"
            normalized.append(
                segment.model_copy(
                    update={
                        "speaker_label": label,
                        "text": segment.text.strip(),
                        "confidence": segment.confidence if segment.confidence is not None else 0.72,
                    }
                )
            )
        return normalized

    def segment_transcript(self, text: str) -> list[SpeakerSegment]:
        return [
            SpeakerSegment(
                speaker_label="SPEAKER_SELF",
                text=text.strip(),
                confidence=1.0,
                identity_confidence=1.0,
            )
        ]

    async def warmup(self, stage_callback: LiveStageCallback | None = None) -> None:
        if stage_callback:
            stage_callback("completed", "Mock speaker provider is ready")

    async def process_live_chunk(
        self,
        audio_bytes: bytes,
        *,
        session_id: str,
        sequence: int,
        mime_type: str,
        debug_text: str | None = None,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        text = (debug_text or _decode_debug_audio(audio_bytes) or f"Live audio chunk {sequence}").strip()
        speaker_label = f"SPEAKER_{sequence % 2:02d}"
        start_ms = max(sequence - 1, 0) * 2000
        end_ms = start_ms + 1800
        segment = SpeakerSegment(
            speaker_label=speaker_label,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            confidence=0.72,
        )
        return LiveProviderResult(
            temp_id=f"{session_id}-{sequence}",
            partial_text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            segments=[segment],
        )


class WhisperXSpeakerProvider(MockSpeakerProvider):
    name = "whisperx"

    def __init__(
        self,
        settings: Settings,
        *,
        use_subprocess: bool | None = None,
        worker_mode: str | None = None,
    ) -> None:
        self._settings = settings
        if worker_mode is None:
            worker_mode = "subprocess" if use_subprocess is not False else "in_process"
        self._worker_mode = _normalize_worker_mode(worker_mode)
        self._use_subprocess = self._worker_mode == "subprocess"
        self._sync_lock = threading.Lock()
        self._model = None
        self._align_model = None
        self._align_metadata = None
        self._diarize_model = None
        self._persistent_worker: PersistentWhisperXWorker | None = None

    async def process_live_chunk(
        self,
        audio_bytes: bytes,
        *,
        session_id: str,
        sequence: int,
        mime_type: str,
        debug_text: str | None = None,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        if self._worker_mode == "persistent_subprocess":
            if self._persistent_worker is None:
                self._persistent_worker = PersistentWhisperXWorker(self._settings)
            return await self._persistent_worker.process_live_chunk(
                audio_bytes,
                session_id=session_id,
                sequence=sequence,
                mime_type=mime_type,
                stage_callback=stage_callback,
            )
        if self._worker_mode == "subprocess":
            return await self._process_live_chunk_subprocess(
                audio_bytes,
                session_id=session_id,
                sequence=sequence,
                mime_type=mime_type,
                stage_callback=stage_callback,
            )
        return await asyncio.to_thread(
            self._process_live_chunk_sync_locked,
            audio_bytes,
            session_id=session_id,
            sequence=sequence,
            mime_type=mime_type,
            stage_callback=stage_callback,
        )

    def _process_live_chunk_sync_locked(self, *args, **kwargs) -> LiveProviderResult:
        with self._sync_lock:
            return self._process_live_chunk_sync(*args, **kwargs)

    async def warmup(self, stage_callback: LiveStageCallback | None = None) -> None:
        if self._worker_mode == "persistent_subprocess":
            if self._persistent_worker is None:
                self._persistent_worker = PersistentWhisperXWorker(self._settings)
            await self._persistent_worker.warmup(stage_callback=stage_callback)
            return
        if self._worker_mode == "subprocess":
            if stage_callback:
                stage_callback("skipped", "Subprocess workers cannot reuse in-process warmup")
            return
        await asyncio.to_thread(self._warmup_sync_locked, stage_callback=stage_callback)

    def _warmup_sync_locked(self, *, stage_callback: LiveStageCallback | None = None) -> None:
        with self._sync_lock:
            self._warmup_sync(stage_callback=stage_callback)

    def _warmup_sync(self, *, stage_callback: LiveStageCallback | None = None) -> None:
        if not self._settings.hf_token:
            raise RuntimeError("HF_TOKEN is required for WhisperX speaker diarization")
        try:
            if stage_callback:
                stage_callback("importing", "Importing WhisperX and diarization modules")
            import whisperx
            from whisperx.diarize import DiarizationPipeline
        except ImportError as exc:
            raise RuntimeError("Install whisperx to use SPEAKER_PROVIDER=whisperx") from exc

        device = self._settings.whisperx_device
        if self._model is None:
            if stage_callback:
                stage_callback("loading_asr", f"Loading WhisperX {self._settings.whisperx_model}")
            self._model = whisperx.load_model(
                self._settings.whisperx_model,
                device,
                compute_type=self._settings.whisperx_compute_type,
            )
        else:
            if stage_callback:
                stage_callback("loading_asr", "WhisperX ASR model already loaded")

        self._ensure_diarization_model(
            DiarizationPipeline,
            device=device,
            loading_stage="loading_diarization",
            loading_message="Loading pyannote speaker diarization",
            ready_message="pyannote diarization already loaded",
            stage_callback=stage_callback,
        )

        if stage_callback:
            stage_callback("completed", "WhisperX in-process provider warmup completed")

    def _ensure_diarization_model(
        self,
        DiarizationPipeline,
        *,
        device: str,
        loading_stage: str,
        loading_message: str,
        ready_message: str,
        stage_callback: LiveStageCallback | None = None,
    ) -> None:
        if self._diarize_model is None:
            if stage_callback:
                stage_callback(loading_stage, loading_message)
            self._diarize_model = DiarizationPipeline(
                token=self._settings.hf_token,
                device=device,
            )
        else:
            if stage_callback:
                stage_callback(loading_stage, ready_message)

    async def _process_live_chunk_subprocess(
        self,
        audio_bytes: bytes,
        *,
        session_id: str,
        sequence: int,
        mime_type: str,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        if stage_callback:
            stage_callback("loading_model", "Starting isolated WhisperX worker")
        payload = {
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "session_id": session_id,
            "sequence": sequence,
            "mime_type": mime_type,
            "settings": _worker_settings_payload(self._settings),
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.speakers.provider",
            cwd=str(BACKEND_ROOT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        async def collect_stdout() -> None:
            if process.stdout is None:
                return
            while True:
                line = await process.stdout.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                stdout_lines.append(text)
                _replay_worker_stage_line(text, stage_callback)

        async def collect_stderr() -> None:
            if process.stderr is None:
                return
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                stderr_lines.append(line.decode("utf-8", errors="replace").rstrip("\n"))

        stdout_task = asyncio.create_task(collect_stdout())
        stderr_task = asyncio.create_task(collect_stderr())
        try:
            if process.stdin is None:
                raise RuntimeError("WhisperX worker stdin is unavailable")
            process.stdin.write(json.dumps(payload).encode("utf-8"))
            await process.stdin.drain()
            process.stdin.close()
            if hasattr(process.stdin, "wait_closed"):
                await process.stdin.wait_closed()
            await asyncio.wait_for(process.wait(), timeout=self._settings.whisperx_worker_timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except Exception:
                pass
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            detail = _worker_output_tail_from_lines(stdout_lines, stderr_lines)
            message = "WhisperX worker timed out"
            if detail:
                message = f"{message}: {detail}"
            raise RuntimeError(message) from exc
        except asyncio.CancelledError:
            process.kill()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except Exception:
                pass
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        finally:
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        output = "\n".join(stdout_lines)
        error_output = "\n".join(stderr_lines)
        if process.returncode != 0:
            detail = (error_output or output).strip().splitlines()[-8:]
            raise RuntimeError("WhisperX worker failed: " + "\n".join(detail))
        for line in reversed(output.splitlines()):
            if line.startswith(_WORKER_RESULT_PREFIX):
                if stage_callback:
                    stage_callback("completed", "WhisperX worker completed")
                return LiveProviderResult.model_validate_json(line.removeprefix(_WORKER_RESULT_PREFIX))
        detail = (error_output or output).strip().splitlines()[-8:]
        raise RuntimeError("WhisperX worker did not return a result: " + "\n".join(detail))

    def _process_live_chunk_sync(
        self,
        audio_bytes: bytes,
        *,
        session_id: str,
        sequence: int,
        mime_type: str,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        if not self._settings.hf_token:
            raise RuntimeError("HF_TOKEN is required for WhisperX speaker diarization")
        try:
            if stage_callback:
                stage_callback("loading_model", "Importing WhisperX and diarization modules")
            import whisperx
            from whisperx.diarize import DiarizationPipeline
        except ImportError as exc:
            raise RuntimeError("Install whisperx to use SPEAKER_PROVIDER=whisperx") from exc

        suffix = _suffix_from_mime(mime_type)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            device = self._settings.whisperx_device
            self._ensure_diarization_model(
                DiarizationPipeline,
                device=device,
                loading_stage="diarizing",
                loading_message="Loading pyannote diarization pipeline",
                ready_message="pyannote diarization pipeline already loaded",
                stage_callback=stage_callback,
            )
            audio = whisperx.load_audio(tmp_path)
            if self._model is None:
                if stage_callback:
                    stage_callback("loading_model", f"Loading WhisperX {self._settings.whisperx_model}")
                self._model = whisperx.load_model(
                    self._settings.whisperx_model,
                    device,
                    compute_type=self._settings.whisperx_compute_type,
                )
            if stage_callback:
                stage_callback("transcribing", "Transcribing live audio")
            result = self._model.transcribe(audio, batch_size=4)
            preview_text = transcript_preview_from_whisperx(result.get("segments", []))
            if stage_callback and preview_text:
                stage_callback("transcript_preview", preview_text)
            language = result.get("language") or "en"
            if self._align_model is None:
                if stage_callback:
                    stage_callback("aligning", f"Loading alignment model for {language}")
                self._align_model, self._align_metadata = whisperx.load_align_model(
                    language_code=language,
                    device=device,
                )
            if stage_callback:
                stage_callback("aligning", "Aligning words to timestamps")
            aligned = whisperx.align(
                result["segments"],
                self._align_model,
                self._align_metadata,
                audio,
                device,
                return_char_alignments=False,
            )
            diarization_kwargs = _diarization_kwargs(self._settings)
            if stage_callback:
                stage_callback("diarizing", _diarization_message(diarization_kwargs))
            diarized = self._diarize_model(audio, **diarization_kwargs)
            if stage_callback:
                stage_callback("assigning_speakers", "Assigning speakers to words")
            assigned = whisperx.assign_word_speakers(diarized, aligned)
            segments = segments_from_whisperx(assigned.get("segments", []))
            partial_text = " ".join(segment.text for segment in segments).strip() or None
            if stage_callback:
                stage_callback("completed", "WhisperX processing completed")
            return LiveProviderResult(
                temp_id=f"{session_id}-{sequence}",
                partial_text=partial_text,
                start_ms=segments[0].start_ms if segments else None,
                end_ms=segments[-1].end_ms if segments else None,
                segments=segments,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class PersistentWhisperXWorker:
    """Serialized JSONL adapter around a long-lived isolated WhisperX process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_task: asyncio.Task | None = None

    async def ensure_started(self, stage_callback: LiveStageCallback | None = None) -> None:
        if self._process is not None and self._process.returncode is None:
            if stage_callback:
                stage_callback("worker_ready", "Persistent WhisperX worker already running")
            return
        if stage_callback:
            stage_callback("starting_worker", "Starting persistent WhisperX worker")
        self._stderr_lines = []
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.speakers.provider",
            "--persistent",
            cwd=str(BACKEND_ROOT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._collect_stderr())
        if stage_callback:
            stage_callback("worker_ready", "Persistent WhisperX worker started")

    async def warmup(self, stage_callback: LiveStageCallback | None = None) -> None:
        async with self._lock:
            await self.ensure_started(stage_callback=stage_callback)
            if self._process is None or self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError("Persistent WhisperX worker is unavailable")
            payload = {
                "type": "warmup",
                "settings": _worker_settings_payload(self._settings),
            }
            try:
                self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                await self._process.stdin.drain()
                await asyncio.wait_for(
                    self._read_warmup_ready(stage_callback),
                    timeout=self._settings.whisperx_worker_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                detail = _worker_output_tail_from_lines([], self._stderr_lines)
                await self.aclose()
                message = "Persistent WhisperX warmup timed out"
                if detail:
                    message = f"{message}: {detail}"
                raise RuntimeError(message) from exc

    async def process_live_chunk(
        self,
        audio_bytes: bytes,
        *,
        session_id: str,
        sequence: int,
        mime_type: str,
        stage_callback: LiveStageCallback | None = None,
    ) -> LiveProviderResult:
        async with self._lock:
            await self.ensure_started(stage_callback=stage_callback)
            if self._process is None or self._process.stdin is None or self._process.stdout is None:
                raise RuntimeError("Persistent WhisperX worker is unavailable")
            payload = {
                "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                "session_id": session_id,
                "sequence": sequence,
                "mime_type": mime_type,
                "settings": _worker_settings_payload(self._settings),
            }
            try:
                self._process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
                await self._process.stdin.drain()
                return await asyncio.wait_for(
                    self._read_result(stage_callback),
                    timeout=self._settings.whisperx_worker_timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                detail = _worker_output_tail_from_lines([], self._stderr_lines)
                await self.aclose()
                message = "Persistent WhisperX worker timed out"
                if detail:
                    message = f"{message}: {detail}"
                raise RuntimeError(message) from exc
            except (BrokenPipeError, ConnectionResetError) as exc:
                detail = _worker_output_tail_from_lines([], self._stderr_lines)
                await self.aclose()
                raise RuntimeError(f"Persistent WhisperX worker stopped: {detail}") from exc
            except asyncio.CancelledError:
                await self.aclose()
                raise

    async def _read_result(self, stage_callback: LiveStageCallback | None) -> LiveProviderResult:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Persistent WhisperX worker stdout is unavailable")
        while True:
            line_bytes = await self._process.stdout.readline()
            if not line_bytes:
                detail = _worker_output_tail_from_lines([], self._stderr_lines)
                await self.aclose()
                raise RuntimeError(f"Persistent WhisperX worker exited before returning a result: {detail}")
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith(_WORKER_RESULT_PREFIX):
                if stage_callback:
                    stage_callback("completed", "Persistent WhisperX worker completed")
                return LiveProviderResult.model_validate_json(line.removeprefix(_WORKER_RESULT_PREFIX))
            if line.startswith(_WORKER_STAGE_PREFIX):
                _replay_worker_stage_line(line, stage_callback)

    async def _read_warmup_ready(self, stage_callback: LiveStageCallback | None) -> None:
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Persistent WhisperX worker stdout is unavailable")
        while True:
            line_bytes = await self._process.stdout.readline()
            if not line_bytes:
                detail = _worker_output_tail_from_lines([], self._stderr_lines)
                await self.aclose()
                raise RuntimeError(f"Persistent WhisperX worker exited before warmup completed: {detail}")
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith(_WORKER_READY_PREFIX):
                if stage_callback:
                    stage_callback("completed", "Persistent WhisperX warmup completed")
                return
            if line.startswith(_WORKER_STAGE_PREFIX):
                _replay_worker_stage_line(line, stage_callback)

    async def _collect_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                return
            self._stderr_lines.append(line.decode("utf-8", errors="replace").rstrip("\n"))
            self._stderr_lines = self._stderr_lines[-40:]

    async def aclose(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.kill()
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=5)
        if self._stderr_task is not None:
            self._stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stderr_task
            self._stderr_task = None


def get_speaker_provider(name: str, settings: Settings | None = None) -> MockSpeakerProvider:
    if name == "whisperx":
        if settings is None:
            raise ValueError("settings are required for whisperx speaker provider")
        return WhisperXSpeakerProvider(settings, worker_mode=_normalize_worker_mode(settings.whisperx_worker_mode))
    if name != "mock":
        raise ValueError(f"Unsupported speaker provider: {name}")
    return MockSpeakerProvider()


def _use_subprocess_worker(settings: Settings) -> bool:
    return _normalize_worker_mode(getattr(settings, "whisperx_worker_mode", "subprocess")) == "subprocess"


def _normalize_worker_mode(value: str | None) -> str:
    mode = str(value or "subprocess").strip().lower()
    if mode in {"subprocess", "in_process", "persistent_subprocess"}:
        return mode
    return "subprocess"


def _decode_debug_audio(audio_bytes: bytes) -> str | None:
    try:
        text = audio_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return text if text and all(ch.isprintable() or ch.isspace() for ch in text) else None


def _suffix_from_mime(mime_type: str) -> str:
    if "wav" in mime_type:
        return ".wav"
    if "mpeg" in mime_type or "mp3" in mime_type:
        return ".mp3"
    return ".webm"


def segments_from_whisperx(raw_segments: list[dict]) -> list[SpeakerSegment]:
    segments: list[SpeakerSegment] = []
    for index, item in enumerate(raw_segments):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker = str(item.get("speaker") or f"SPEAKER_{index:02d}")
        segments.append(
            SpeakerSegment(
                speaker_label=speaker,
                text=text,
                start_ms=_seconds_to_ms(item.get("start")),
                end_ms=_seconds_to_ms(item.get("end")),
                confidence=item.get("score"),
            )
        )
    return segments


def transcript_preview_from_whisperx(raw_segments: list[dict]) -> str:
    return " ".join(str(item.get("text") or "").strip() for item in raw_segments).strip()


def _seconds_to_ms(value) -> int | None:
    if value is None:
        return None
    return int(float(value) * 1000)


def _run_whisperx_worker() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        audio_bytes = base64.b64decode(payload["audio_base64"])
        provider = WhisperXSpeakerProvider(Settings(**(payload.get("settings") or {})), use_subprocess=False)
        result = provider._process_live_chunk_sync(
            audio_bytes,
            session_id=payload["session_id"],
            sequence=int(payload["sequence"]),
            mime_type=payload["mime_type"],
            stage_callback=_emit_worker_stage,
        )
        print(f"{_WORKER_RESULT_PREFIX}{result.model_dump_json()}", flush=True)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


def _run_persistent_whisperx_worker() -> int:
    provider: WhisperXSpeakerProvider | None = None
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if provider is None:
                provider = WhisperXSpeakerProvider(Settings(**(payload.get("settings") or {})), use_subprocess=False)
            if payload.get("type") == "warmup":
                provider._warmup_sync(stage_callback=_emit_worker_stage)
                print(f"{_WORKER_READY_PREFIX}{json.dumps({'ready': True, 'warmed': True})}", flush=True)
                continue
            audio_bytes = base64.b64decode(payload["audio_base64"])
            result = provider._process_live_chunk_sync(
                audio_bytes,
                session_id=payload["session_id"],
                sequence=int(payload["sequence"]),
                mime_type=payload["mime_type"],
                stage_callback=_emit_worker_stage,
            )
            print(f"{_WORKER_RESULT_PREFIX}{result.model_dump_json()}", flush=True)
        except Exception as exc:
            print(str(exc), file=sys.stderr, flush=True)
            return 1
    return 0


def _emit_worker_stage(stage: str, message: str | None) -> None:
    print(
        f"{_WORKER_STAGE_PREFIX}{json.dumps({'stage': stage, 'message': message or ''})}",
        flush=True,
    )


def _replay_worker_stages(output: str, stage_callback: LiveStageCallback | None) -> None:
    if stage_callback is None:
        return
    for line in output.splitlines():
        _replay_worker_stage_line(line, stage_callback)


def _replay_worker_stage_line(line: str, stage_callback: LiveStageCallback | None) -> None:
    if stage_callback is None or not line.startswith(_WORKER_STAGE_PREFIX):
        return
    try:
        payload = json.loads(line.removeprefix(_WORKER_STAGE_PREFIX))
    except json.JSONDecodeError:
        return
    stage_callback(str(payload.get("stage") or "worker"), str(payload.get("message") or ""))


def _worker_output_tail(stdout: bytes, stderr: bytes, *, max_lines: int = 8) -> str:
    output = stdout.decode("utf-8", errors="replace")
    error_output = stderr.decode("utf-8", errors="replace")
    output_lines = [line for line in output.strip().splitlines() if line.strip()]
    error_lines = [line for line in error_output.strip().splitlines() if line.strip()]
    chunks = []
    if output_lines:
        chunks.append("stdout:\n" + "\n".join(output_lines[-max_lines:]))
    if error_lines:
        chunks.append("stderr:\n" + "\n".join(error_lines[-max_lines:]))
    if not chunks:
        return ""
    return "\n".join(chunks)


def _worker_output_tail_from_lines(
    stdout_lines: list[str],
    stderr_lines: list[str],
    *,
    max_lines: int = 8,
) -> str:
    chunks = []
    output_lines = [line for line in stdout_lines if line.strip()]
    error_lines = [line for line in stderr_lines if line.strip()]
    if output_lines:
        chunks.append("stdout:\n" + "\n".join(output_lines[-max_lines:]))
    if error_lines:
        chunks.append("stderr:\n" + "\n".join(error_lines[-max_lines:]))
    return "\n".join(chunks)


def _worker_settings_payload(settings: Settings) -> dict:
    return {
        "hf_token": settings.hf_token,
        "whisperx_model": settings.whisperx_model,
        "whisperx_device": settings.whisperx_device,
        "whisperx_compute_type": settings.whisperx_compute_type,
        "whisperx_num_speakers": settings.whisperx_num_speakers,
        "whisperx_min_speakers": settings.whisperx_min_speakers,
        "whisperx_max_speakers": settings.whisperx_max_speakers,
        "whisperx_worker_timeout_seconds": settings.whisperx_worker_timeout_seconds,
        "whisperx_worker_mode": settings.whisperx_worker_mode,
    }


def _positive_int(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _diarization_kwargs(settings: Settings) -> dict[str, int]:
    num_speakers = _positive_int(settings.whisperx_num_speakers)
    if num_speakers is not None:
        return {"num_speakers": num_speakers}

    kwargs: dict[str, int] = {}
    min_speakers = _positive_int(settings.whisperx_min_speakers)
    max_speakers = _positive_int(settings.whisperx_max_speakers)
    if min_speakers is not None:
        kwargs["min_speakers"] = min_speakers
    if max_speakers is not None:
        kwargs["max_speakers"] = max_speakers
    return kwargs


def _diarization_message(kwargs: dict[str, int]) -> str:
    if "num_speakers" in kwargs:
        return f"Detecting speaker turns with {kwargs['num_speakers']} expected speaker(s)"
    if "min_speakers" in kwargs or "max_speakers" in kwargs:
        minimum = kwargs.get("min_speakers", "?")
        maximum = kwargs.get("max_speakers", "?")
        return f"Detecting speaker turns with {minimum}..{maximum} speaker hint"
    return "Detecting speaker turns"


if __name__ == "__main__":
    if "--persistent" in sys.argv:
        raise SystemExit(_run_persistent_whisperx_worker())
    raise SystemExit(_run_whisperx_worker())
