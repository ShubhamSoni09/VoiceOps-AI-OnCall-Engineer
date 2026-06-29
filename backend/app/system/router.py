import asyncio
import importlib.util
import json
import os
import platform
import shutil
import tempfile
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, require_permission
from app.auth.models import UserPublic
from app.cache import JsonTTLCache
from app.collab.event_store_readiness import (
    EventStoreReadinessResponse,
    LOCAL_RUNTIME_BOOTSTRAP_COMMAND,
    build_event_store_readiness,
)
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.config import BACKEND_ROOT
from app.agents.store import AgentRunStore
from app.rag.service import RagIndexService
from app.rag.providers import EmbeddingProviderError
from app.speakers.service import SpeakerService, get_speaker_service
from app.speakers.warmup_worker import WARMUP_STAGE_PREFIX
from app.system.deployment import DeploymentHardeningResponse, build_deployment_hardening_report
from app.system.cutover import ProductionCutoverReport, build_production_cutover_report
from app.system.evidence import DemoEvidenceStatus, read_demo_evidence, write_demo_evidence_record
from app.system.security import ProductionSecurityReport, build_production_security_report
from app.workspace.git import WorkspaceGitService
from app.workspace.models import GitStatusResponse
from app.workspace.tools import WorkspaceError

router = APIRouter(prefix="/system", tags=["system"])

_speaker_warmup_job: "SpeakerWarmupResponse | None" = None
_speaker_warmup_task: asyncio.Task | None = None
_speaker_verification_job: "SpeakerVerificationJobResponse | None" = None
_speaker_verification_task: asyncio.Task | None = None
_demo_gate_run_job: "DemoGateRunResponse | None" = None
_demo_gate_run_task: asyncio.Task | None = None
_demo_gate_completion_targets: dict[str, "DemoGateCompletionTarget"] = {}

_AUDIO_SUFFIXES = {".wav", ".mp3", ".mpeg", ".webm", ".m4a", ".mp4", ".aiff", ".aif"}
FRONTEND_ROOT = BACKEND_ROOT.parent / "frontend"
DEMO_GATE_LABELS = {
    "mock_e2e": "Mock closure harness",
    "real_live_backend": "Real WhisperX backend live",
    "real_browser_live": "Real browser mic live",
}


class CacheStatusResponse(BaseModel):
    path: str
    exists: bool
    entries: int
    expired_entries: int
    rebuildable_entries: int = 0
    fingerprinted_entries: int = 0
    size_bytes: int
    git_ttl_seconds: float
    workspace_ttl_seconds: float


class CacheClearResponse(CacheStatusResponse):
    cleared_entries: int


class RuntimeStoreStatus(BaseModel):
    id: str
    label: str
    backend: str
    path: str
    exists: bool
    size_bytes: int
    migration_available: bool = False


class RuntimeProviderStatus(BaseModel):
    id: str
    label: str
    value: str
    ready: bool
    detail: str | None = None
    checks: list["RuntimeProviderCheck"] = Field(default_factory=list)


class RuntimeProviderCheck(BaseModel):
    id: str
    label: str
    ready: bool
    detail: str | None = None


class RuntimeWorkspaceStatus(BaseModel):
    configured: bool
    path: str | None = None
    exists: bool = False


class RuntimeStatusResponse(BaseModel):
    app_name: str
    stores: list[RuntimeStoreStatus]
    providers: list[RuntimeProviderStatus]
    workspace: RuntimeWorkspaceStatus
    cache: CacheStatusResponse
    warnings: list[str]


class ObservabilityMetric(BaseModel):
    id: str
    label: str
    value: int | float | str | bool | None
    status: str = "ok"
    detail: str | None = None


class ObservabilityComponent(BaseModel):
    id: str
    label: str
    status: str
    metrics: list[ObservabilityMetric] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ObservabilitySnapshot(BaseModel):
    status: str
    checked_at: str
    room_id: str
    components: list[ObservabilityComponent]
    warnings: list[str] = Field(default_factory=list)


class ReadinessCheck(BaseModel):
    id: str
    label: str
    ready: bool
    status: str
    detail: str | None = None


class SystemReadinessResponse(BaseModel):
    app_name: str
    status: str
    ready: bool
    checked_at: str
    demo_command: str = "cd backend && python scripts/demo_readiness.py"
    checks: list[ReadinessCheck]
    workspace: RuntimeWorkspaceStatus
    git: GitStatusResponse | None = None
    providers: list[RuntimeProviderStatus]
    cache: CacheStatusResponse
    warnings: list[str]
    speaker_verification: "SpeakerVerificationStatus"
    demo_evidence: DemoEvidenceStatus


class LocalDoctorCheck(BaseModel):
    id: str
    label: str
    ready: bool
    status: str
    detail: str
    action: str | None = None


class LocalDoctorResponse(BaseModel):
    status: str
    ready: bool
    checked_at: str
    platform: str
    python: str
    executable: str
    provider: str
    device: str
    worker_mode: str
    checks: list[LocalDoctorCheck]
    warnings: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    command: str


class TargetReadinessMilestone(BaseModel):
    id: str
    label: str
    ready: bool
    status: str
    detail: str
    evidence: str | None = None
    next_action: str | None = None
    command: str | None = None


class TargetReadinessResponse(BaseModel):
    status: str
    ready: bool
    checked_at: str
    score: int
    ready_count: int
    total_count: int
    milestones: list[TargetReadinessMilestone]
    next_steps: list[str] = Field(default_factory=list)


class OperatorAcceptanceStage(BaseModel):
    id: str
    label: str
    status: str
    ready: bool
    required: bool
    duration_ms: int
    summary: str
    next_steps: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class OperatorAcceptanceResponse(BaseModel):
    status: str
    accepted: bool
    checked_at: str
    env_file: str | None = None
    run_harnesses: bool = False
    run_profile: str | None = None
    duration_ms: int
    stages: list[OperatorAcceptanceStage]
    next_steps: list[str] = Field(default_factory=list)


class SpeakerVerificationStatus(BaseModel):
    path: str
    exists: bool
    verified: bool
    status: str
    provider: str
    checked_at: str | None = None
    distinct_speaker_count: int = 0
    speaker_labels: list[str] = Field(default_factory=list)
    elapsed_ms: int | None = None
    strict_multi_speaker: bool = False
    generated_audio: bool = False
    last_stage: str | None = None
    stages: list[dict] = Field(default_factory=list)
    quality: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    error: str | None = None
    detail: str


class SpeakerVerificationJobResponse(BaseModel):
    job_id: str | None = None
    state: str
    detail: str | None = None
    poll_url: str = "/system/speaker/verification"
    source: str = "upload"
    filename: str | None = None
    size_bytes: int = 0
    strict_multi_speaker: bool = False
    generated_audio: bool = False
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_ms: int | None = None
    timeout_seconds: float | None = None
    stages: list["SpeakerWarmupStage"] = Field(default_factory=list)
    exit_code: int | None = None
    error: str | None = None
    verification: SpeakerVerificationStatus | None = None


class SpeakerWarmupStage(BaseModel):
    stage: str
    message: str
    elapsed_ms: int = 0


class SpeakerWarmupResponse(BaseModel):
    job_id: str | None = None
    provider: str
    state: str
    detail: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_ms: int | None = None
    timeout_seconds: float | None = None
    stages: list[SpeakerWarmupStage] = Field(default_factory=list)
    error: str | None = None
    ready: bool = False


class DemoGateRunResponse(BaseModel):
    job_id: str | None = None
    gate_id: str | None = None
    label: str | None = None
    state: str
    detail: str | None = None
    poll_url: str = "/system/demo/gates/runs/current"
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_ms: int | None = None
    timeout_seconds: float | None = None
    command: list[str] = Field(default_factory=list)
    cwd: str | None = None
    stages: list[SpeakerWarmupStage] = Field(default_factory=list)
    exit_code: int | None = None
    error: str | None = None
    evidence: DemoEvidenceStatus | None = None
    completion_recorded: bool = False
    completion_error: str | None = None


@dataclass
class DemoGateCompletionTarget:
    room_id: str
    collab: Any
    events: Any | None = None


def _ensure_room_access(room_id: str, user: UserPublic, collab: CollaborationService) -> None:
    if collab.user_can_access_room(room_id, user):
        return
    raise HTTPException(status_code=403, detail="Join this room before accessing system observability.")


@router.get("/cache/status", response_model=CacheStatusResponse)
async def cache_status(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> CacheStatusResponse:
    return _cache_status(settings)


@router.post("/cache/clear", response_model=CacheClearResponse)
async def clear_cache(
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> CacheClearResponse:
    cache = JsonTTLCache(settings.voiceops_cache_path)
    cleared = cache.clear()
    status = _cache_status(settings)
    return CacheClearResponse(**status.model_dump(), cleared_entries=cleared)


@router.get("/runtime/status", response_model=RuntimeStatusResponse)
async def runtime_status(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> RuntimeStatusResponse:
    return _runtime_status(settings)


@router.get("/observability", response_model=ObservabilitySnapshot)
async def observability_snapshot(
    room_id: str = "main",
    user: UserPublic = Depends(require_permission("dashboard:view")),
    settings: Settings = Depends(get_settings),
    collab: CollaborationService = Depends(get_collaboration_service),
) -> ObservabilitySnapshot:
    _ensure_room_access(room_id, user, collab)
    return _observability_snapshot(settings, collab, room_id)


@router.get("/readiness", response_model=SystemReadinessResponse)
async def system_readiness(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> SystemReadinessResponse:
    runtime = _runtime_status(settings)
    git_status = _git_status(settings, runtime.workspace)
    speaker_verification = _speaker_verification_status(settings)
    demo_evidence = read_demo_evidence(settings.demo_evidence_path)
    checks = _readiness_checks(runtime, git_status, speaker_verification)
    ready = all(check.ready for check in checks if check.id in {"backend", "workspace", "git", "speaker", "memory", "live"})
    return SystemReadinessResponse(
        app_name=settings.app_name,
        status="ready" if ready else "needs_attention",
        ready=ready,
        checked_at=datetime.now(UTC).isoformat(),
        checks=checks,
        workspace=runtime.workspace,
        git=git_status,
        providers=runtime.providers,
        cache=runtime.cache,
        warnings=runtime.warnings,
        speaker_verification=speaker_verification,
        demo_evidence=demo_evidence,
    )


@router.get("/local-doctor", response_model=LocalDoctorResponse)
async def local_doctor(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> LocalDoctorResponse:
    return _local_doctor(settings)


@router.get("/target-readiness", response_model=TargetReadinessResponse)
async def target_readiness(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TargetReadinessResponse:
    return _target_readiness(settings)


@router.get("/event-store/readiness", response_model=EventStoreReadinessResponse)
async def event_store_readiness(
    _user: UserPublic = Depends(require_permission("dashboard:view")),
    settings: Settings = Depends(get_settings),
) -> EventStoreReadinessResponse:
    return build_event_store_readiness(settings)


@router.get("/operator-acceptance", response_model=OperatorAcceptanceResponse)
async def operator_acceptance(
    run_harnesses: bool = False,
    user: UserPublic = Depends(require_permission("dashboard:view")),
    settings: Settings = Depends(get_settings),
) -> OperatorAcceptanceResponse:
    if run_harnesses and "admin:manage" not in user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role.value}' cannot perform this action",
        )
    try:
        report = await asyncio.to_thread(
            _run_operator_acceptance_report,
            settings,
            run_harnesses,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Operator acceptance unavailable: {exc}",
        ) from exc
    return OperatorAcceptanceResponse(
        **report,
        checked_at=datetime.now(UTC).isoformat(),
    )


@router.get("/security/readiness", response_model=ProductionSecurityReport)
async def production_security_readiness(
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> ProductionSecurityReport:
    return build_production_security_report(settings)


@router.get("/deployment/hardening", response_model=DeploymentHardeningResponse)
async def deployment_hardening(
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> DeploymentHardeningResponse:
    return build_deployment_hardening_report(settings)


@router.get("/deployment/cutover", response_model=ProductionCutoverReport)
async def production_cutover_readiness(
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> ProductionCutoverReport:
    return build_production_cutover_report(settings)


@router.get("/demo/gates/runs/current", response_model=DemoGateRunResponse)
async def current_demo_gate_run(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> DemoGateRunResponse:
    if _demo_gate_run_job is None:
        return DemoGateRunResponse(
            state="idle",
            detail="No demo gate run is active",
            evidence=read_demo_evidence(settings.demo_evidence_path),
        )
    return _demo_gate_run_job.model_copy(
        update={"evidence": read_demo_evidence(settings.demo_evidence_path)}
    )


@router.post("/demo/gates/{gate_id}/runs", response_model=DemoGateRunResponse)
async def start_demo_gate_run(
    gate_id: str,
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> DemoGateRunResponse:
    return start_demo_gate_run_job(gate_id, settings)


def start_demo_gate_run_job(gate_id: str, settings: Settings) -> DemoGateRunResponse:
    global _demo_gate_run_job, _demo_gate_run_task
    if gate_id not in DEMO_GATE_LABELS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown demo gate: {gate_id}")
    if _demo_gate_run_job is not None and _demo_gate_run_job.state == "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{_demo_gate_run_job.label or 'A demo gate'} is already running.",
        )

    command, cwd, timeout_seconds = _demo_gate_command(gate_id, settings)
    job = DemoGateRunResponse(
        job_id=f"gate-{uuid4().hex[:12]}",
        gate_id=gate_id,
        label=DEMO_GATE_LABELS[gate_id],
        state="running",
        detail=f"Starting {DEMO_GATE_LABELS[gate_id]}",
        started_at=datetime.now(UTC).isoformat(),
        timeout_seconds=timeout_seconds,
        command=command,
        cwd=str(cwd),
        evidence=read_demo_evidence(settings.demo_evidence_path),
    )
    _append_demo_gate_stage(job, "queued", f"Queued {DEMO_GATE_LABELS[gate_id]}", started_at=job.started_at)
    _demo_gate_run_job = job
    _demo_gate_run_task = asyncio.create_task(_run_demo_gate_job(job.job_id or "", settings))
    return job


def register_demo_gate_completion(
    job_id: str | None,
    *,
    room_id: str | None,
    collab: Any,
    events: Any | None = None,
) -> None:
    if not job_id or not room_id:
        return
    target = DemoGateCompletionTarget(room_id=room_id, collab=collab, events=events)
    _demo_gate_completion_targets[job_id] = target
    job = _demo_gate_run_job
    if job is not None and job.job_id == job_id and job.state in {"succeeded", "failed"}:
        _record_demo_gate_completion(job, target)


def _run_operator_acceptance_report(settings: Settings, run_harnesses: bool) -> dict[str, Any]:
    from scripts.operator_acceptance import run_operator_acceptance

    return run_operator_acceptance(settings=settings, run_harnesses=run_harnesses)


@router.get("/speaker/warmup", response_model=SpeakerWarmupResponse)
async def speaker_warmup_status(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> SpeakerWarmupResponse:
    if _speaker_warmup_job is None:
        return _idle_speaker_warmup(settings)
    return _speaker_warmup_job


@router.post("/speaker/warmup", response_model=SpeakerWarmupResponse)
async def start_speaker_warmup(
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
    speaker_service: SpeakerService = Depends(get_speaker_service),
) -> SpeakerWarmupResponse:
    global _speaker_warmup_job, _speaker_warmup_task
    if _speaker_warmup_job is not None and _speaker_warmup_job.state == "running":
        return _speaker_warmup_job

    provider_status = _speaker_provider_status(settings)
    job = SpeakerWarmupResponse(
        job_id=f"warmup-{uuid4().hex[:12]}",
        provider=settings.speaker_provider,
        state="running",
        detail="Starting speaker provider warmup",
        started_at=datetime.now(UTC).isoformat(),
        timeout_seconds=settings.whisperx_worker_timeout_seconds if settings.speaker_provider == "whisperx" else None,
    )
    _speaker_warmup_job = job

    if settings.speaker_provider != "whisperx":
        _complete_warmup(job, state="skipped", detail="Mock provider does not require warmup", ready=True)
        return job
    if not provider_status.ready:
        _complete_warmup(job, state="failed", detail=provider_status.detail, error=provider_status.detail, ready=False)
        return job

    if _use_in_process_warmup(settings):
        _speaker_warmup_task = asyncio.create_task(
            _run_in_process_speaker_warmup(job.job_id or "", speaker_service)
        )
    else:
        _speaker_warmup_task = asyncio.create_task(_run_speaker_warmup(job.job_id or "", settings))
    return job


@router.get("/speaker/verification", response_model=SpeakerVerificationJobResponse)
async def speaker_verification_status(
    _user: UserPublic = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> SpeakerVerificationJobResponse:
    verification = _speaker_verification_status(settings)
    if _speaker_verification_job is None:
        return SpeakerVerificationJobResponse(
            state="idle",
            detail="No verification job is running",
            verification=verification,
        )
    return _speaker_verification_job.model_copy(update={"verification": verification})


@router.post("/speaker/verification", response_model=SpeakerVerificationJobResponse)
async def start_speaker_verification(
    audio: UploadFile = File(...),
    require_multiple_speakers: bool = Form(False),
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> SpeakerVerificationJobResponse:
    global _speaker_verification_job, _speaker_verification_task
    if _speaker_verification_job is not None and _speaker_verification_job.state == "running":
        return _speaker_verification_job
    _ensure_speaker_verification_can_start(settings)
    temp_path, size_bytes = await _write_verification_upload(audio, settings)
    job = _new_speaker_verification_job(
        settings,
        source="upload",
        detail="Uploaded real audio; starting verification",
        filename=Path(audio.filename or temp_path.name).name,
        size_bytes=size_bytes,
        require_multiple_speakers=require_multiple_speakers,
        generated_audio=False,
    )
    _append_verification_stage(job, "accepted_upload", "Accepted real audio sample", started_at=job.started_at)
    _speaker_verification_job = job
    _speaker_verification_task = asyncio.create_task(
        _run_speaker_verification(job.job_id or "", temp_path, settings, require_multiple_speakers)
    )
    return job


@router.post("/speaker/verification/generated", response_model=SpeakerVerificationJobResponse)
async def start_generated_speaker_verification(
    require_multiple_speakers: bool = Form(True),
    _user: UserPublic = Depends(require_permission("admin:manage")),
    settings: Settings = Depends(get_settings),
) -> SpeakerVerificationJobResponse:
    global _speaker_verification_job, _speaker_verification_task
    if _speaker_verification_job is not None and _speaker_verification_job.state == "running":
        return _speaker_verification_job
    _ensure_speaker_verification_can_start(settings)
    job = _new_speaker_verification_job(
        settings,
        source="generated_macos_tts",
        detail="Generating macOS two-speaker sample; starting verification",
        filename="generated-macos-two-speaker.wav",
        size_bytes=0,
        require_multiple_speakers=require_multiple_speakers,
        generated_audio=True,
    )
    _append_verification_stage(job, "generating_audio", "Generating macOS two-speaker sample", started_at=job.started_at)
    _speaker_verification_job = job
    _speaker_verification_task = asyncio.create_task(
        _run_speaker_verification(job.job_id or "", None, settings, require_multiple_speakers)
    )
    return job


def _ensure_speaker_verification_can_start(settings: Settings) -> None:
    if settings.speaker_provider != "whisperx":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Set SPEAKER_PROVIDER=whisperx before running real diarization verification.",
        )
    provider_status = _speaker_provider_status(settings)
    if not provider_status.ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=provider_status.detail or "WhisperX provider is not ready.",
        )


def _new_speaker_verification_job(
    settings: Settings,
    *,
    source: str,
    detail: str,
    filename: str,
    size_bytes: int,
    require_multiple_speakers: bool,
    generated_audio: bool,
) -> SpeakerVerificationJobResponse:
    return SpeakerVerificationJobResponse(
        job_id=f"verify-{uuid4().hex[:12]}",
        state="running",
        detail=detail,
        source=source,
        filename=filename,
        size_bytes=size_bytes,
        strict_multi_speaker=require_multiple_speakers,
        generated_audio=generated_audio,
        started_at=datetime.now(UTC).isoformat(),
        timeout_seconds=settings.speaker_verification_timeout_seconds + 30,
        verification=_speaker_verification_status(settings),
    )


def _demo_gate_command(gate_id: str, settings: Settings) -> tuple[list[str], Path, float]:
    evidence_path = str(settings.demo_evidence_path)
    if gate_id == "mock_e2e":
        return (
            ["npm", "run", "e2e:all", "--", "--json", "--evidence-path", evidence_path],
            FRONTEND_ROOT,
            300.0,
        )
    if gate_id == "real_live_backend":
        timeout = 300.0
        return (
            [
                sys.executable,
                "scripts/smoke_live_meeting_real.py",
                "--timeout",
                str(timeout),
                "--receive-timeout",
                str(timeout + 60),
                "--model",
                "tiny",
                "--worker-mode",
                "persistent_subprocess",
                "--chunks",
                "2",
                "--generate-macos-tts",
                "--json",
                "--evidence-path",
                evidence_path,
            ],
            BACKEND_ROOT,
            timeout + 120,
        )
    if gate_id == "real_browser_live":
        timeout = 180.0
        return (
            [
                "npm",
                "run",
                "e2e:real-mic",
                "--",
                "--json",
                "--timeout",
                str(int(timeout)),
                "--worker-mode",
                "persistent_subprocess",
                "--evidence-path",
                evidence_path,
            ],
            FRONTEND_ROOT,
            timeout + 120,
        )
    raise ValueError(f"Unknown demo gate: {gate_id}")


async def _run_demo_gate_job(job_id: str, settings: Settings) -> None:
    job = _demo_gate_run_job
    if job is None or job.job_id != job_id or not job.command or not job.cwd:
        return
    started = datetime.now(UTC)
    env = os.environ.copy()
    env["DEMO_EVIDENCE_PATH"] = str(settings.demo_evidence_path)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        _append_demo_gate_stage(job, "starting_process", f"Starting {job.label or 'demo gate'}", started_at=job.started_at)
        process = await asyncio.create_subprocess_exec(
            *job.command,
            cwd=job.cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _append_demo_gate_stage(job, "running_process", "Gate process is running", started_at=job.started_at)
        stdout_task = asyncio.create_task(_collect_demo_gate_stream(process.stdout, job, stdout_lines, "stdout"))
        stderr_task = asyncio.create_task(_collect_demo_gate_stream(process.stderr, job, stderr_lines, "stderr"))
        await asyncio.wait_for(process.wait(), timeout=job.timeout_seconds or 300.0)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        output = "\n".join(stdout_lines)
        error_output = "\n".join(stderr_lines)
        exit_code = int(process.returncode or 0)
        if exit_code == 0:
            _append_demo_gate_stage(job, "succeeded", f"{job.label or 'Demo gate'} passed", started_at=job.started_at)
            _complete_demo_gate_run(
                job,
                state="succeeded",
                detail=f"{job.label or 'Demo gate'} completed",
                exit_code=exit_code,
                settings=settings,
            )
            return
        detail = _demo_gate_failure_detail(output, error_output)
        _append_demo_gate_stage(job, "failed", detail, started_at=job.started_at)
        _write_demo_gate_failure(job, settings, detail)
        _complete_demo_gate_run(
            job,
            state="failed",
            detail=detail,
            exit_code=exit_code,
            error=detail,
            settings=settings,
        )
    except asyncio.TimeoutError:
        if "process" in locals():
            process.kill()
            await process.wait()
            if "stdout_task" in locals() and "stderr_task" in locals():
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        detail = f"{job.label or 'Demo gate'} timed out after {job.timeout_seconds or 300:.0f}s"
        _append_demo_gate_stage(job, "timeout", detail, started_at=job.started_at)
        _write_demo_gate_failure(job, settings, detail)
        _complete_demo_gate_run(
            job,
            state="failed",
            detail=detail,
            exit_code=124,
            error=detail,
            settings=settings,
        )
    except Exception as exc:
        detail = str(exc)
        _append_demo_gate_stage(job, "failed", detail, started_at=job.started_at)
        _write_demo_gate_failure(job, settings, detail)
        _complete_demo_gate_run(
            job,
            state="failed",
            detail=detail,
            exit_code=1,
            error=detail,
            settings=settings,
        )
    finally:
        if job.started_at:
            try:
                started_at = datetime.fromisoformat(job.started_at)
                job.elapsed_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            except ValueError:
                job.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        job.evidence = read_demo_evidence(settings.demo_evidence_path)


def _complete_demo_gate_run(
    job: DemoGateRunResponse,
    *,
    state: str,
    detail: str,
    exit_code: int,
    settings: Settings,
    error: str | None = None,
) -> None:
    job.state = state
    job.detail = detail
    job.exit_code = exit_code
    job.error = error
    job.completed_at = datetime.now(UTC).isoformat()
    if job.started_at:
        started = datetime.fromisoformat(job.started_at)
        job.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    job.evidence = read_demo_evidence(settings.demo_evidence_path)
    _record_demo_gate_completion_if_registered(job)


def _record_demo_gate_completion_if_registered(job: DemoGateRunResponse) -> None:
    if not job.job_id:
        return
    target = _demo_gate_completion_targets.get(job.job_id)
    if target is None:
        return
    _record_demo_gate_completion(job, target)


def _record_demo_gate_completion(
    job: DemoGateRunResponse,
    target: DemoGateCompletionTarget,
) -> None:
    if job.completion_recorded:
        return
    record = _demo_gate_evidence_record(job)
    metadata = _demo_gate_completion_metadata(job, record)
    text = _demo_gate_completion_text(job, record)
    try:
        message = target.collab.add_agent_message(target.room_id, text, metadata=metadata)
        job.completion_recorded = True
        job.completion_error = None
        _publish_demo_gate_completion(target, job, message.id)
    except Exception as exc:
        job.completion_error = str(exc)


def _publish_demo_gate_completion(
    target: DemoGateCompletionTarget,
    job: DemoGateRunResponse,
    message_id: str,
) -> None:
    if target.events is None:
        return
    payload = {
        "message_id": message_id,
        "gate_id": job.gate_id,
        "gate_label": job.label,
        "gate_job_id": job.job_id,
        "gate_status": job.state,
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        target.events.publish(
            target.room_id,
            "demo_gate_result_recorded",
            actor_id="agent-voiceops",
            payload=payload,
        )
    )


def _demo_gate_evidence_record(job: DemoGateRunResponse) -> dict:
    if not job.evidence or not job.gate_id:
        return {}
    for record in job.evidence.records:
        data = record.model_dump(mode="json")
        if data.get("id") == job.gate_id:
            return data
    return {}


def _demo_gate_completion_metadata(job: DemoGateRunResponse, record: dict) -> dict:
    return {
        "source": "demo_gate_result",
        "gate_id": job.gate_id,
        "gate_label": job.label,
        "gate_job_id": job.job_id,
        "gate_status": job.state,
        "detail": job.detail,
        "error": job.error,
        "exit_code": job.exit_code,
        "duration_ms": job.elapsed_ms,
        "evidence_status": record.get("status"),
        "provider": record.get("provider"),
        "speaker_labels": record.get("speaker_labels") or [],
        "distinct_speaker_count": record.get("distinct_speaker_count", 0),
        "requested_chunks": record.get("requested_chunks"),
        "completed_chunks": record.get("completed_chunks"),
        "timeline_message_count": record.get("timeline_message_count"),
        "command": job.command,
    }


def _demo_gate_completion_text(job: DemoGateRunResponse, record: dict) -> str:
    label = job.label or "Demo gate"
    elapsed = _format_elapsed_ms(job.elapsed_ms)
    if job.state == "succeeded":
        labels = record.get("speaker_labels") or []
        speaker_part = f" Speakers: {', '.join(labels)}." if labels else ""
        chunks = _format_chunks(record)
        return f"{label} finished: passed in {elapsed}.{speaker_part}{chunks}"
    reason = job.error or record.get("error") or job.detail or "Unknown error"
    return f"{label} finished: failed in {elapsed}. {reason}"


def _format_elapsed_ms(elapsed_ms: int | None) -> str:
    if elapsed_ms is None:
        return "unknown time"
    if elapsed_ms < 1000:
        return f"{elapsed_ms}ms"
    return f"{elapsed_ms / 1000:.1f}s"


def _format_chunks(record: dict) -> str:
    completed = record.get("completed_chunks")
    requested = record.get("requested_chunks")
    if completed is None and requested is None:
        return ""
    if requested is None:
        return f" Chunks completed: {completed}."
    return f" Chunks completed: {completed}/{requested}."


async def _collect_demo_gate_stream(
    stream: asyncio.StreamReader | None,
    job: DemoGateRunResponse,
    lines: list[str],
    stream_name: str,
) -> None:
    if stream is None:
        return
    while True:
        raw = await stream.readline()
        if not raw:
            return
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        lines.append(text)
        if len(lines) > 80:
            del lines[: len(lines) - 80]
        stage = _demo_gate_stage_from_line(text, stream_name)
        if stage:
            _append_demo_gate_stage(job, stage[0], stage[1], started_at=job.started_at)


def _demo_gate_stage_from_line(text: str, stream_name: str) -> tuple[str, str] | None:
    lower = text.lower()
    if "real mic progress:" in lower:
        return "browser_progress", text.removeprefix("real mic progress:").strip() or text
    if "passed" in lower and "e2e" in lower:
        return "passed_output", text
    if "failed" in lower:
        return "failed_output", text
    if stream_name == "stderr" and ("progress" in lower or "whisperx" in lower):
        return "process_progress", text
    return None


def _append_demo_gate_stage(
    job: DemoGateRunResponse,
    stage: str,
    message: str,
    *,
    started_at: str | None,
) -> None:
    elapsed_ms = 0
    if started_at:
        try:
            started = datetime.fromisoformat(started_at)
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        except ValueError:
            elapsed_ms = 0
    if job.stages and job.stages[-1].stage == stage and job.stages[-1].message == message:
        job.stages[-1].elapsed_ms = elapsed_ms
    else:
        job.stages.append(SpeakerWarmupStage(stage=stage, message=message, elapsed_ms=elapsed_ms))
        job.stages = job.stages[-10:]
    job.detail = message


def _demo_gate_failure_detail(stdout: str, stderr: str) -> str:
    data = _json_from_stdout(stdout)
    if data and data.get("error"):
        return str(data["error"])
    if data and data.get("status") and data.get("status") != "ready":
        return str(data.get("status"))
    lines = [line for line in (stderr or stdout).splitlines() if line.strip()]
    return "\n".join(lines[-6:]) if lines else "Demo gate failed"


def _json_from_stdout(stdout: str) -> dict | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


def _write_demo_gate_failure(job: DemoGateRunResponse, settings: Settings, detail: str) -> None:
    if not job.gate_id:
        return
    write_demo_evidence_record(
        settings.demo_evidence_path,
        {
            "id": job.gate_id,
            "label": job.label or DEMO_GATE_LABELS.get(job.gate_id, job.gate_id),
            "status": "failed",
            "source": "backend_demo_gate_runner",
            "provider": "whisperx" if job.gate_id != "mock_e2e" else "mock",
            "detail": detail,
            "error": detail,
            "duration_ms": job.elapsed_ms or 0,
            "command": job.command,
        },
    )


def _runtime_status(settings: Settings) -> RuntimeStatusResponse:
    stores = [
        _store_status(
            "collab",
            "Collaboration memory",
            settings.collab_store_backend,
            settings.collab_sqlite_path if settings.collab_store_backend == "sqlite" else settings.collab_store_path,
            migration_available=True,
        ),
        _store_status(
            "speakers",
            "Speaker identity",
            settings.speaker_store_backend,
            settings.speaker_sqlite_path if settings.speaker_store_backend == "sqlite" else settings.speaker_store_path,
            migration_available=True,
        ),
        _store_status("users", "Users", "json", settings.users_store_path),
        _store_status("cache", "Runtime cache", "json_ttl", settings.voiceops_cache_path),
    ]
    workspace_path = Path(settings.voiceops_workspace).expanduser() if settings.voiceops_workspace else None
    speaker_status = _speaker_provider_status(settings)
    providers = [
        RuntimeProviderStatus(
            id="stt",
            label="Speech to text",
            value=settings.stt_provider,
            ready=settings.stt_provider in {"mock", "whisper", "aws"},
        ),
        RuntimeProviderStatus(
            id="llm",
            label="Intent router",
            value=settings.llm_provider,
            ready=settings.llm_provider == "mock" or bool(settings.openai_api_key),
            detail="OpenAI key configured" if settings.openai_api_key else None,
        ),
        RuntimeProviderStatus(
            id="tts",
            label="Text to speech",
            value=settings.tts_provider,
            ready=settings.tts_provider == "mock" or bool(settings.openai_api_key),
            detail="Browser speech fallback available" if not settings.openai_api_key else "OpenAI key configured",
        ),
        speaker_status,
    ]
    warnings = _runtime_warnings(settings, workspace_path, speaker_status)
    return RuntimeStatusResponse(
        app_name=settings.app_name,
        stores=stores,
        providers=providers,
        workspace=RuntimeWorkspaceStatus(
            configured=workspace_path is not None,
            path=str(workspace_path) if workspace_path else None,
            exists=workspace_path.exists() if workspace_path else False,
        ),
        cache=_cache_status(settings),
        warnings=warnings,
    )


def _observability_snapshot(settings: Settings, collab: CollaborationService, room_id: str) -> ObservabilitySnapshot:
    components = [
        _collaboration_observability(collab, room_id),
        _agent_observability(settings, room_id),
        _rag_observability(settings),
        _provider_observability(settings),
    ]
    warnings = [warning for component in components for warning in component.warnings]
    if any(component.status == "blocked" for component in components):
        status_value = "blocked"
    elif any(component.status == "needs_attention" for component in components):
        status_value = "needs_attention"
    else:
        status_value = "healthy"
    return ObservabilitySnapshot(
        status=status_value,
        checked_at=datetime.now(UTC).isoformat(),
        room_id=room_id,
        components=components,
        warnings=warnings,
    )


def _collaboration_observability(collab: CollaborationService, room_id: str) -> ObservabilityComponent:
    snapshot = collab.snapshot(room_id)
    memory_health = collab.memory_health(room_id)
    pending = [action for action in snapshot.actions if action.status == "pending_approval"]
    failed = [action for action in snapshot.actions if action.status == "failed"]
    status_value = "blocked" if pending else "needs_attention" if failed or memory_health.status != "healthy" else "healthy"
    warnings = []
    if pending:
        warnings.append(f"{len(pending)} pending approval(s) require review.")
    warnings.extend(memory_health.blockers)
    warnings.extend(memory_health.warnings)
    return ObservabilityComponent(
        id="collaboration",
        label="Collaboration state",
        status=status_value,
        metrics=[
            ObservabilityMetric(id="messages", label="Messages", value=len(snapshot.messages)),
            ObservabilityMetric(id="actions", label="Actions", value=len(snapshot.actions)),
            ObservabilityMetric(id="pending_approvals", label="Pending approvals", value=len(pending), status="blocked" if pending else "ok"),
            ObservabilityMetric(id="failed_actions", label="Failed actions", value=len(failed), status="warning" if failed else "ok"),
            ObservabilityMetric(id="memory_items", label="Memory items", value=memory_health.total_items),
            ObservabilityMetric(id="open_memory", label="Open memory", value=memory_health.status_counts.get("open", 0), status="warning" if memory_health.status_counts.get("open", 0) else "ok"),
        ],
        warnings=warnings,
    )


def _agent_observability(settings: Settings, room_id: str) -> ObservabilityComponent:
    store = AgentRunStore(settings.agent_runs_path)
    runs = store.list(room_id, limit=200)
    assignments = store.list_assignments(room_id, limit=200)
    failed_runs = [run for run in runs if run.status.value in {"failed", "timed_out", "budget_exhausted"}]
    open_assignments = [assignment for assignment in assignments if assignment.status.value in {"queued", "running"}]
    warnings = []
    if failed_runs:
        warnings.append(f"{len(failed_runs)} agent run(s) failed or timed out.")
    if open_assignments:
        warnings.append(f"{len(open_assignments)} agent assignment(s) still open.")
    return ObservabilityComponent(
        id="agents",
        label="Agent execution",
        status="needs_attention" if warnings else "healthy",
        metrics=[
            ObservabilityMetric(id="runs", label="Runs", value=len(runs)),
            ObservabilityMetric(id="failed_runs", label="Failed runs", value=len(failed_runs), status="warning" if failed_runs else "ok"),
            ObservabilityMetric(id="assignments", label="Assignments", value=len(assignments)),
            ObservabilityMetric(id="open_assignments", label="Open assignments", value=len(open_assignments), status="warning" if open_assignments else "ok"),
        ],
        warnings=warnings,
    )


def _rag_observability(settings: Settings) -> ObservabilityComponent:
    try:
        status_value = RagIndexService(settings).status()
        warnings = [] if status_value.document_count else ["RAG index has no documents yet."]
        return ObservabilityComponent(
            id="rag",
            label="RAG memory index",
            status="healthy" if status_value.document_count else "needs_attention",
            metrics=[
                ObservabilityMetric(id="documents", label="Documents", value=status_value.document_count),
                ObservabilityMetric(id="rooms", label="Rooms", value=status_value.room_count),
                ObservabilityMetric(id="provider", label="Provider", value=status_value.provider),
            ],
            warnings=warnings,
        )
    except EmbeddingProviderError as exc:
        return ObservabilityComponent(
            id="rag",
            label="RAG memory index",
            status="blocked",
            metrics=[ObservabilityMetric(id="provider", label="Provider", value=settings.rag_embedding_provider, status="blocked")],
            warnings=[str(exc)],
        )


def _provider_observability(settings: Settings) -> ObservabilityComponent:
    external_count = _json_record_count(settings.external_agent_store_path, "credentials")
    llm_count = _json_record_count(settings.llm_connection_store_path, "connections")
    warnings = []
    if not external_count:
        warnings.append("No external agent credentials are connected.")
    if not llm_count and settings.llm_provider != "mock":
        warnings.append("No user LLM connection is available for non-mock routing.")
    if settings.external_agent_api_execution_enabled and not external_count:
        warnings.append("External provider API execution is enabled but no provider credentials exist.")
    return ObservabilityComponent(
        id="providers",
        label="Provider connections",
        status="needs_attention" if warnings else "healthy",
        metrics=[
            ObservabilityMetric(id="external_credentials", label="External credentials", value=external_count),
            ObservabilityMetric(id="llm_connections", label="LLM connections", value=llm_count),
            ObservabilityMetric(id="external_api_enabled", label="External API execution", value=settings.external_agent_api_execution_enabled),
            ObservabilityMetric(id="external_cli_enabled", label="External CLI execution", value=settings.external_agent_cli_execution_enabled),
        ],
        warnings=warnings,
    )


def _json_record_count(path: Path, key: str) -> int:
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict) and isinstance(raw.get(key), list):
        return len(raw[key])
    return 0


def _idle_speaker_warmup(settings: Settings) -> SpeakerWarmupResponse:
    return SpeakerWarmupResponse(
        provider=settings.speaker_provider,
        state="idle",
        detail="Warmup has not been started",
        timeout_seconds=settings.whisperx_worker_timeout_seconds if settings.speaker_provider == "whisperx" else None,
    )


async def _write_verification_upload(audio: UploadFile, settings: Settings) -> tuple[Path, int]:
    filename = Path(audio.filename or "sample.wav").name
    suffix = Path(filename).suffix.lower()
    if suffix not in _AUDIO_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported audio file type: {suffix or '(none)'}",
        )
    upload_dir = settings.speaker_verification_path.expanduser().parent / "verification_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix="speaker-verification-", suffix=suffix, dir=upload_dir, delete=False)
    temp_path = Path(handle.name)
    size_bytes = 0
    try:
        with handle:
            while True:
                chunk = await audio.read(1024 * 1024)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > settings.speaker_verification_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"Audio sample is larger than {settings.speaker_verification_max_bytes} bytes.",
                    )
                handle.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await audio.close()
    if size_bytes == 0:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Audio sample is empty.")
    return temp_path, size_bytes


async def _run_speaker_verification(
    job_id: str,
    audio_path: Path | None,
    settings: Settings,
    require_multiple_speakers: bool,
) -> None:
    job = _speaker_verification_job
    if job is None or job.job_id != job_id:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
        return
    started = datetime.now(UTC)
    try:
        _append_verification_stage(job, "starting_smoke", "Starting real diarization smoke", started_at=job.started_at)
        args = [
            sys.executable,
            "scripts/smoke_whisperx_provider.py",
            "--timeout",
            str(settings.speaker_verification_timeout_seconds),
            "--record-status",
            "--verification-path",
            str(settings.speaker_verification_path),
            "--json",
        ]
        if audio_path is None:
            args.append("--generate-macos-tts")
        else:
            args.extend(["--audio", str(audio_path)])
        if require_multiple_speakers:
            args.append("--require-multiple-speakers")
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(BACKEND_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _append_verification_stage(job, "running_whisperx", "WhisperX verification is running", started_at=job.started_at)
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.speaker_verification_timeout_seconds + 30,
        )
        output = stdout.decode("utf-8", errors="replace")
        error_output = stderr.decode("utf-8", errors="replace")
        exit_code = int(process.returncode or 0)
        if exit_code == 0:
            _append_verification_stage(job, "succeeded", "Real diarization verification passed", started_at=job.started_at)
            _complete_speaker_verification(job, state="succeeded", detail="Real diarization verification completed", exit_code=exit_code)
            return
        detail = _verification_failure_detail(output, error_output)
        _append_verification_stage(job, "failed", detail, started_at=job.started_at)
        _complete_speaker_verification(job, state="failed", detail=detail, error=detail, exit_code=exit_code)
    except asyncio.TimeoutError:
        if "process" in locals():
            process.kill()
            await process.wait()
        detail = f"Real diarization verification timed out after {settings.speaker_verification_timeout_seconds + 30:.0f}s"
        _write_verification_failure(settings, detail, require_multiple_speakers)
        _append_verification_stage(job, "timeout", detail, started_at=job.started_at)
        _complete_speaker_verification(job, state="failed", detail=detail, error=detail, exit_code=5)
    except Exception as exc:
        detail = str(exc)
        _write_verification_failure(settings, detail, require_multiple_speakers)
        _append_verification_stage(job, "failed", detail, started_at=job.started_at)
        _complete_speaker_verification(job, state="failed", detail=detail, error=detail, exit_code=1)
    finally:
        if audio_path is not None:
            audio_path.unlink(missing_ok=True)
        if job.started_at:
            try:
                started_at = datetime.fromisoformat(job.started_at)
                job.elapsed_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            except ValueError:
                job.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        job.verification = _speaker_verification_status(settings)


def _complete_speaker_verification(
    job: SpeakerVerificationJobResponse,
    *,
    state: str,
    detail: str | None,
    exit_code: int,
    error: str | None = None,
) -> None:
    job.state = state
    job.detail = detail
    job.exit_code = exit_code
    job.error = error
    job.completed_at = datetime.now(UTC).isoformat()
    if job.started_at:
        started = datetime.fromisoformat(job.started_at)
        job.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)


def _append_verification_stage(
    job: SpeakerVerificationJobResponse,
    stage: str,
    message: str,
    *,
    started_at: str | None,
) -> None:
    elapsed_ms = 0
    if started_at:
        try:
            started = datetime.fromisoformat(started_at)
            elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        except ValueError:
            elapsed_ms = 0
    job.stages.append(SpeakerWarmupStage(stage=stage, message=message, elapsed_ms=elapsed_ms))
    job.stages = job.stages[-8:]
    job.detail = message


def _verification_failure_detail(stdout: str, stderr: str) -> str:
    try:
        report = json.loads(stdout.strip())
        if report.get("error"):
            return str(report["error"])
    except json.JSONDecodeError:
        pass
    lines = [line for line in (stderr or stdout).splitlines() if line.strip()]
    return "\n".join(lines[-6:]) if lines else "Real diarization verification failed"


def _write_verification_failure(settings: Settings, detail: str, require_multiple_speakers: bool) -> None:
    path = settings.speaker_verification_path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": "whisperx",
        "verified": False,
        "strict_multi_speaker": require_multiple_speakers,
        "generated_audio": False,
        "distinct_speaker_count": 0,
        "speaker_labels": [],
        "elapsed_ms": 0,
        "execution_mode": "server_upload",
        "error": detail,
        "detail": detail,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


async def _run_speaker_warmup(job_id: str, settings: Settings) -> None:
    job = _speaker_warmup_job
    if job is None or job.job_id != job_id:
        return
    started = datetime.now(UTC)
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.speakers.warmup_worker",
            cwd=str(BACKEND_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        _complete_warmup(job, state="failed", detail=str(exc), error=str(exc), ready=False)
        return
    stderr_lines: list[str] = []
    stdout_task = asyncio.create_task(_collect_warmup_stdout(process, job, started))
    stderr_task = asyncio.create_task(_collect_warmup_stderr(process, stderr_lines))
    try:
        await asyncio.wait_for(process.wait(), timeout=settings.whisperx_worker_timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        detail = _tail(stderr_lines) or _latest_warmup_stage(job) or "Warmup timed out"
        _complete_warmup(
            job,
            state="failed",
            detail=detail,
            error=f"WhisperX warmup timed out after {settings.whisperx_worker_timeout_seconds:.0f}s",
            ready=False,
        )
        return

    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
    if process.returncode == 0:
        _complete_warmup(job, state="succeeded", detail="WhisperX warmup completed", ready=True)
        return
    detail = _tail(stderr_lines) or _latest_warmup_stage(job) or "WhisperX warmup failed"
    _complete_warmup(job, state="failed", detail=detail, error=detail, ready=False)


async def _run_in_process_speaker_warmup(job_id: str, speaker_service: SpeakerService) -> None:
    job = _speaker_warmup_job
    if job is None or job.job_id != job_id:
        return
    started = datetime.now(UTC)

    def emit(stage: str, message: str | None = None) -> None:
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        job.stages.append(
            SpeakerWarmupStage(
                stage=stage,
                message=message or stage.replace("_", " "),
                elapsed_ms=elapsed_ms,
            )
        )
        job.detail = job.stages[-1].message

    try:
        await asyncio.wait_for(
            speaker_service.warmup_provider(stage_callback=emit),
            timeout=(job.timeout_seconds or 90.0),
        )
        _complete_warmup(
            job,
            state="succeeded",
            detail="WhisperX in-process warmup completed and will be reused by live meetings",
            ready=True,
        )
    except asyncio.TimeoutError:
        detail = _latest_warmup_stage(job) or "Warmup timed out"
        _complete_warmup(
            job,
            state="failed",
            detail=detail,
            error=f"WhisperX in-process warmup timed out after {job.timeout_seconds or 90:.0f}s",
            ready=False,
        )
    except Exception as exc:
        detail = str(exc)
        _complete_warmup(job, state="failed", detail=detail, error=detail, ready=False)


async def _collect_warmup_stdout(
    process: asyncio.subprocess.Process,
    job: SpeakerWarmupResponse,
    started: datetime,
) -> None:
    if process.stdout is None:
        return
    while True:
        line = await process.stdout.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").strip()
        if not text.startswith(WARMUP_STAGE_PREFIX):
            continue
        try:
            payload = json.loads(text.removeprefix(WARMUP_STAGE_PREFIX))
        except json.JSONDecodeError:
            continue
        elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        job.stages.append(
            SpeakerWarmupStage(
                stage=str(payload.get("stage") or "warmup"),
                message=str(payload.get("message") or ""),
                elapsed_ms=elapsed_ms,
            )
        )
        job.detail = job.stages[-1].message


async def _collect_warmup_stderr(
    process: asyncio.subprocess.Process,
    stderr_lines: list[str],
) -> None:
    if process.stderr is None:
        return
    while True:
        line = await process.stderr.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            stderr_lines.append(text)


def _complete_warmup(
    job: SpeakerWarmupResponse,
    *,
    state: str,
    detail: str | None,
    ready: bool,
    error: str | None = None,
) -> None:
    job.state = state
    job.detail = detail
    job.ready = ready
    job.error = error
    job.completed_at = datetime.now(UTC).isoformat()
    if job.started_at:
        started = datetime.fromisoformat(job.started_at)
        job.elapsed_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)


def _latest_warmup_stage(job: SpeakerWarmupResponse) -> str | None:
    if not job.stages:
        return None
    stage = job.stages[-1]
    return f"{stage.stage}: {stage.message}"


def _tail(lines: list[str], *, limit: int = 6) -> str:
    return "\n".join(lines[-limit:])


def _use_in_process_warmup(settings: Settings) -> bool:
    return settings.speaker_provider == "whisperx" and settings.whisperx_worker_mode.strip().lower() == "in_process"


def _git_status(settings: Settings, workspace: RuntimeWorkspaceStatus) -> GitStatusResponse | None:
    if not workspace.configured or not workspace.exists:
        return None
    try:
        return WorkspaceGitService(settings).status(use_cache=False)
    except WorkspaceError as exc:
        return GitStatusResponse(is_git_repo=False, warning=str(exc))


def _readiness_checks(
    runtime: RuntimeStatusResponse,
    git_status: GitStatusResponse | None,
    speaker_verification: SpeakerVerificationStatus,
) -> list[ReadinessCheck]:
    providers = {provider.id: provider for provider in runtime.providers}
    stores = {store.id: store for store in runtime.stores}
    speaker = providers.get("speaker")
    collab_store = stores.get("collab")
    speaker_store = stores.get("speakers")
    memory_ready = all(
        _store_parent_available(store)
        for store in (collab_store, speaker_store)
        if store is not None
    )
    live_ready = bool(speaker and speaker.ready)
    checks = [
        ReadinessCheck(
            id="backend",
            label="Backend",
            ready=True,
            status="ready",
            detail="FastAPI health endpoint is available",
        ),
        ReadinessCheck(
            id="workspace",
            label="Workspace",
            ready=runtime.workspace.configured and runtime.workspace.exists,
            status="ready" if runtime.workspace.configured and runtime.workspace.exists else "needs_attention",
            detail=runtime.workspace.path if runtime.workspace.path else "VOICEOPS_WORKSPACE is not configured",
        ),
        ReadinessCheck(
            id="git",
            label="Git workflow",
            ready=bool(git_status and git_status.is_git_repo),
            status=_git_readiness_status(git_status),
            detail=_git_readiness_detail(git_status),
        ),
        ReadinessCheck(
            id="speaker",
            label="Speaker identity",
            ready=bool(speaker and speaker.ready),
            status="ready" if speaker and speaker.ready else "needs_attention",
            detail=speaker.detail if speaker else "Speaker provider is not configured",
        ),
        ReadinessCheck(
            id="memory",
            label="Meeting memory",
            ready=memory_ready,
            status="ready" if memory_ready else "needs_attention",
            detail=_memory_store_detail(collab_store, speaker_store),
        ),
        ReadinessCheck(
            id="live",
            label="Live meeting",
            ready=live_ready,
            status="ready" if live_ready else "needs_attention",
            detail="Mock live path ready" if speaker and speaker.value == "mock" else (speaker.detail if speaker else None),
        ),
        ReadinessCheck(
            id="diarization",
            label="Real diarization",
            ready=_diarization_multi_speaker_ready(speaker_verification),
            status=_diarization_readiness_status(speaker_verification),
            detail=_diarization_readiness_detail(speaker_verification),
        ),
        ReadinessCheck(
            id="cache",
            label="Runtime cache",
            ready=True,
            status="ready",
            detail=f"{runtime.cache.entries} entries, {runtime.cache.expired_entries} expired",
        ),
    ]
    return checks


def _target_readiness(settings: Settings) -> TargetReadinessResponse:
    runtime = _runtime_status(settings)
    git_status = _git_status(settings, runtime.workspace)
    speaker_verification = _speaker_verification_status(settings)
    event_store = build_event_store_readiness(settings)
    demo_evidence = read_demo_evidence(settings.demo_evidence_path)
    readiness_checks = _readiness_checks(runtime, git_status, speaker_verification)
    checks = {check.id: check for check in readiness_checks}
    providers = {provider.id: provider for provider in runtime.providers}
    mock_e2e = _demo_evidence_record(demo_evidence, "mock_e2e")
    real_live = _demo_evidence_record(demo_evidence, "real_live_backend")
    browser_live = _demo_evidence_record(demo_evidence, "real_browser_live")

    workspace_ready = bool(checks.get("workspace") and checks["workspace"].ready)
    git_ready = bool(checks.get("git") and checks["git"].ready)
    memory_ready = bool(checks.get("memory") and checks["memory"].ready)
    live_ready = bool(checks.get("live") and checks["live"].ready)
    llm_ready = bool(providers.get("llm") and providers["llm"].ready)
    mock_e2e_ready = _evidence_passed(mock_e2e)
    real_diarization_ready = _diarization_multi_speaker_ready(speaker_verification)
    real_live_ready = _strict_live_evidence_passed(real_live, require_timeline=True)
    browser_live_ready = _strict_live_evidence_passed(browser_live, require_timeline=False)

    milestones = [
        TargetReadinessMilestone(
            id="workspace_git",
            label="Workspace + git workflow",
            ready=workspace_ready and git_ready,
            status="ready" if workspace_ready and git_ready else "needs_attention",
            detail=_target_join_details(
                checks.get("workspace"),
                checks.get("git"),
                fallback="Workspace must be connected to a local git repository.",
            ),
            evidence=git_status.branch if git_status and git_status.is_git_repo else None,
            next_action=None if workspace_ready and git_ready else "Run the local runtime bootstrap with a git workspace.",
            command=None if workspace_ready and git_ready else _local_runtime_bootstrap_command(),
        ),
        TargetReadinessMilestone(
            id="ai_memory_loop",
            label="AI teammate + meeting memory",
            ready=llm_ready and memory_ready and mock_e2e_ready,
            status="ready" if llm_ready and memory_ready and mock_e2e_ready else "unproven",
            detail=(
                "Mock closure harness proves wake/query, memory Q&A, and AI timeline replies."
                if mock_e2e_ready
                else "Run the mock closure harness to prove AI teammate memory behavior without real audio."
            ),
            evidence=_evidence_label(mock_e2e),
            next_action=None if mock_e2e_ready else "Run the mock closure gate.",
            command="cd frontend && npm run e2e:all -- --json",
        ),
        TargetReadinessMilestone(
            id="approval_git_tests",
            label="Approval + branch + tests",
            ready=git_ready and mock_e2e_ready,
            status="ready" if git_ready and mock_e2e_ready else "unproven",
            detail=(
                "Mock closure harness proves pending patch, approval, branch creation, test run, and handoff audit."
                if mock_e2e_ready
                else "Run the closure harness before treating code-changing AI actions as demo-ready."
            ),
            evidence=_evidence_label(mock_e2e),
            next_action=None if git_ready and mock_e2e_ready else "Run the mock closure gate after git is connected.",
            command="cd frontend && npm run e2e:all -- --json",
        ),
        TargetReadinessMilestone(
            id="durable_event_store",
            label="Durable event store",
            ready=event_store.ready,
            status=event_store.status,
            detail=_event_store_readiness_detail(event_store),
            evidence=_event_store_readiness_evidence(event_store),
            next_action=None if event_store.ready else _event_store_next_action(event_store),
            command=None if event_store.ready else _event_store_next_command(event_store),
        ),
        TargetReadinessMilestone(
            id="real_multi_speaker",
            label="Real 2+ speaker diarization",
            ready=real_diarization_ready,
            status="ready" if real_diarization_ready else _diarization_readiness_status(speaker_verification),
            detail=_diarization_readiness_detail(speaker_verification),
            evidence=_speaker_verification_evidence(speaker_verification),
            next_action=None if real_diarization_ready else "Upload or generate a strict two-speaker verification sample.",
            command="cd backend && python scripts/demo_readiness.py --real-audio <sample.wav> --require-real-diarization",
        ),
        TargetReadinessMilestone(
            id="real_live_backend",
            label="Real WhisperX live backend",
            ready=real_live_ready and real_diarization_ready,
            status="ready" if real_live_ready and real_diarization_ready else "unproven",
            detail=(
                _evidence_detail(real_live)
                if real_live_ready
                else "Run the real backend live gate to prove streaming chunks produce speaker-attributed segments."
            ),
            evidence=_evidence_label(real_live),
            next_action=None if real_live_ready and real_diarization_ready else "Run the real WhisperX backend live gate.",
            command=(
                "cd frontend && npm run e2e:all -- --json --real-live "
                "--real-live-worker-mode persistent_subprocess --real-live-chunks 2 --real-live-timeout 300"
            ),
        ),
        TargetReadinessMilestone(
            id="real_browser_live",
            label="Browser mic to speaker labels",
            ready=browser_live_ready and real_diarization_ready and live_ready,
            status="ready" if browser_live_ready and real_diarization_ready and live_ready else "unproven",
            detail=(
                _evidence_detail(browser_live)
                if browser_live_ready
                else "Run the browser live gate to prove MediaRecorder audio reaches backend diarization and updates the UI."
            ),
            evidence=_evidence_label(browser_live),
            next_action=None if browser_live_ready and real_diarization_ready and live_ready else "Run the real browser microphone live gate.",
            command=(
                "cd frontend && npm run e2e:all -- --json --real-browser-live "
                "--real-browser-live-worker-mode persistent_subprocess --real-browser-live-timeout 180"
            ),
        ),
    ]
    ready_count = sum(1 for milestone in milestones if milestone.ready)
    total_count = len(milestones)
    ready = ready_count == total_count
    next_steps = [milestone.next_action for milestone in milestones if not milestone.ready and milestone.next_action]
    return TargetReadinessResponse(
        status="ready" if ready else "needs_attention",
        ready=ready,
        checked_at=datetime.now(UTC).isoformat(),
        score=round((ready_count / total_count) * 100),
        ready_count=ready_count,
        total_count=total_count,
        milestones=milestones,
        next_steps=list(dict.fromkeys(next_steps))[:4],
    )


def _event_store_readiness_detail(event_store: EventStoreReadinessResponse) -> str:
    if event_store.ready:
        return (
            f"{event_store.backend} event store has {event_store.event_count} events "
            f"across {event_store.stream_count} streams."
        )
    failed = next((check for check in event_store.checks if not check.ready), None)
    if failed:
        return failed.detail
    return event_store.warnings[0] if event_store.warnings else "Event store readiness needs attention."


def _event_store_readiness_evidence(event_store: EventStoreReadinessResponse) -> str | None:
    if event_store.ready:
        return f"{event_store.backend} | {event_store.event_count} events | {event_store.stream_count} streams"
    if event_store.migration_available:
        return f"{event_store.runtime_mode} | migration available"
    return event_store.backend


def _event_store_next_action(event_store: EventStoreReadinessResponse) -> str:
    if event_store.runtime_mode == "development_json":
        return "Use SQLite event-store mode before production; bootstrap a local runtime or migrate existing JSON state."
    if event_store.init_command:
        return "Initialize the configured SQLite collaboration event store."
    return "Fix durable event-store readiness before treating collaboration history as production audit."


def _event_store_next_command(event_store: EventStoreReadinessResponse) -> str | None:
    if event_store.runtime_mode == "development_json":
        return event_store.bootstrap_command or event_store.migration_command
    return event_store.init_command or event_store.migration_command or event_store.bootstrap_command


def _local_runtime_bootstrap_command() -> str:
    return LOCAL_RUNTIME_BOOTSTRAP_COMMAND


def _demo_evidence_record(demo_evidence: DemoEvidenceStatus, record_id: str):
    for record in demo_evidence.records:
        if record.id == record_id:
            return record
    return None


def _evidence_passed(record) -> bool:
    return bool(record and record.status == "passed")


def _strict_live_evidence_passed(record, *, require_timeline: bool) -> bool:
    if not _evidence_passed(record):
        return False
    if int(record.distinct_speaker_count or 0) < 2:
        return False
    labels = [label for label in (record.speaker_labels or []) if str(label).strip()]
    if len(set(labels)) < 2:
        return False
    completed = int(record.completed_chunks or 0)
    requested = int(record.requested_chunks or 0)
    if completed < 1:
        return False
    if requested and completed < requested:
        return False
    if require_timeline and int(record.timeline_message_count or 0) < 1:
        return False
    return True


def _evidence_label(record) -> str | None:
    if not record:
        return None
    parts = [record.status]
    if record.distinct_speaker_count:
        parts.append(f"{record.distinct_speaker_count} speakers")
    if record.completed_chunks:
        chunks = f"{record.completed_chunks}"
        if record.requested_chunks:
            chunks += f"/{record.requested_chunks}"
        parts.append(f"{chunks} chunks")
    return " | ".join(parts)


def _evidence_detail(record) -> str:
    if not record:
        return "No evidence has been recorded."
    return record.detail or record.error or f"{record.label} {record.status}"


def _speaker_verification_evidence(verification: SpeakerVerificationStatus) -> str | None:
    if not verification.exists:
        return None
    parts = [verification.status, f"{verification.distinct_speaker_count} speakers"]
    if verification.strict_multi_speaker:
        parts.append("strict")
    if verification.generated_audio:
        parts.append("generated")
    return " | ".join(parts)


def _target_join_details(*checks: ReadinessCheck | None, fallback: str) -> str:
    details = [f"{check.label}: {check.detail or check.status}" for check in checks if check and not check.ready]
    if details:
        return " ".join(details)
    ready_details = [check.detail for check in checks if check and check.detail]
    return " ".join(ready_details) if ready_details else fallback


def _local_doctor(settings: Settings) -> LocalDoctorResponse:
    checks: list[LocalDoctorCheck] = []
    next_steps: list[str] = []

    def add_check(
        check_id: str,
        label: str,
        ready: bool,
        detail: str,
        *,
        action: str | None = None,
        status_value: str | None = None,
    ) -> None:
        checks.append(
            LocalDoctorCheck(
                id=check_id,
                label=label,
                ready=ready,
                status=status_value or ("ready" if ready else "missing"),
                detail=detail,
                action=action,
            )
        )
        if not ready and action:
            next_steps.append(action)

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_ready = sys.version_info >= (3, 10)
    add_check(
        "python",
        "Python",
        python_ready,
        f"Python {python_version} at {sys.executable}",
        action="Use Python 3.10+ for WhisperX.",
    )

    provider_ready = settings.speaker_provider.strip().lower() == "whisperx"
    add_check(
        "speaker_provider",
        "SPEAKER_PROVIDER",
        provider_ready,
        f"Configured as {settings.speaker_provider}",
        action="Set SPEAKER_PROVIDER=whisperx in backend/.env.",
    )

    add_check(
        "hf_token",
        "HF_TOKEN",
        bool(settings.hf_token),
        "Configured for pyannote gated model access" if settings.hf_token else "Required for pyannote diarization model access",
        action="Create a Hugging Face token, accept the pyannote model terms, then set HF_TOKEN.",
    )

    ffmpeg_path = shutil.which("ffmpeg")
    add_check(
        "ffmpeg",
        "ffmpeg",
        bool(ffmpeg_path),
        ffmpeg_path or "Not found on PATH",
        action="Install ffmpeg: brew install ffmpeg.",
    )

    module_specs = [
        ("whisperx", "WhisperX package", "Install WhisperX in the backend environment."),
        ("torch", "Torch runtime", "Install torch for the selected device."),
        ("pyannote.audio", "pyannote.audio", "Install pyannote.audio for diarization."),
    ]
    for module_name, label, action in module_specs:
        ready = _module_available(module_name)
        add_check(
            module_name.replace(".", "_"),
            label,
            ready,
            "Python import available" if ready else "Python import not available",
            action=action,
        )

    device_detail = _torch_device_detail(settings.whisperx_device)
    add_check(
        "device",
        "WhisperX device",
        device_detail["ready"],
        device_detail["detail"],
        action=device_detail.get("action"),
        status_value=device_detail.get("status"),
    )

    hints_ready = _speaker_hint_ready(settings)
    checks.append(
        LocalDoctorCheck(
            id="speaker_hints",
            label="Speaker hints",
            ready=True,
            status="ready" if hints_ready else "warning",
            detail=_speaker_hint_detail(settings) if hints_ready else "No 2+ speaker hint configured for live diarization windows",
            action=None if hints_ready else "For demos, set WHISPERX_MIN_SPEAKERS=2 and WHISPERX_MAX_SPEAKERS=2.",
        )
    )

    warnings = _local_doctor_warnings(settings, checks)
    for check in checks:
        if check.ready is False and check.action and check.action not in next_steps:
            next_steps.append(check.action)
        if check.status == "warning" and check.action:
            next_steps.append(check.action)
    next_steps.append("Run strict verification with a two-person sample before calling real multi-speaker ready.")
    deduped_steps = list(dict.fromkeys(step for step in next_steps if step))
    blocking_ready = all(check.ready for check in checks)
    return LocalDoctorResponse(
        status="ready" if blocking_ready else "needs_attention",
        ready=blocking_ready,
        checked_at=datetime.now(UTC).isoformat(),
        platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python=python_version,
        executable=sys.executable,
        provider=settings.speaker_provider,
        device=settings.whisperx_device,
        worker_mode=settings.whisperx_worker_mode,
        checks=checks,
        warnings=warnings,
        next_steps=deduped_steps,
        command="cd backend && python scripts/demo_readiness.py --real-audio <sample.wav> --require-real-diarization",
    )


def _torch_device_detail(device: str) -> dict[str, str | bool]:
    normalized = (device or "cpu").strip().lower()
    if normalized == "cpu":
        return {
            "ready": True,
            "status": "warning",
            "detail": "CPU mode is available but may lag on live diarization.",
        }
    if not _module_available("torch"):
        return {
            "ready": False,
            "status": "missing",
            "detail": f"Torch is required before checking {normalized}.",
            "action": "Install torch for the selected device or switch WHISPERX_DEVICE=cpu.",
        }
    try:
        import torch  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive import guard
        return {
            "ready": False,
            "status": "missing",
            "detail": f"Torch import failed: {exc}",
            "action": "Fix the torch installation or switch WHISPERX_DEVICE=cpu.",
        }
    if normalized == "mps":
        mps_ready = bool(getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available())
        return {
            "ready": mps_ready,
            "status": "ready" if mps_ready else "missing",
            "detail": "Apple Silicon MPS available" if mps_ready else "MPS is not available in this torch runtime.",
            "action": None if mps_ready else "Switch WHISPERX_DEVICE=cpu or install a torch build with MPS support.",
        }
    if normalized == "cuda":
        cuda_ready = bool(getattr(torch, "cuda", None) and torch.cuda.is_available())
        return {
            "ready": cuda_ready,
            "status": "ready" if cuda_ready else "missing",
            "detail": "CUDA available" if cuda_ready else "CUDA is not available in this torch runtime.",
            "action": None if cuda_ready else "On Mac, use WHISPERX_DEVICE=cpu or mps.",
        }
    return {
        "ready": False,
        "status": "missing",
        "detail": f"Unsupported WHISPERX_DEVICE={device}",
        "action": "Set WHISPERX_DEVICE to cpu, mps, or cuda.",
    }


def _speaker_hint_ready(settings: Settings) -> bool:
    values = [
        settings.whisperx_num_speakers,
        settings.whisperx_min_speakers,
        settings.whisperx_max_speakers,
    ]
    return any(isinstance(value, int) and value >= 2 for value in values)


def _speaker_hint_detail(settings: Settings) -> str:
    if settings.whisperx_num_speakers:
        return f"Exact speaker hint: {settings.whisperx_num_speakers}"
    parts = []
    if settings.whisperx_min_speakers:
        parts.append(f"min {settings.whisperx_min_speakers}")
    if settings.whisperx_max_speakers:
        parts.append(f"max {settings.whisperx_max_speakers}")
    return ", ".join(parts) if parts else "No speaker hints configured"


def _local_doctor_warnings(settings: Settings, checks: list[LocalDoctorCheck]) -> list[str]:
    warnings = []
    if settings.whisperx_device == "cpu":
        warnings.append("CPU mode can work locally but may lag; use generated verification before live demos.")
    if settings.whisperx_worker_mode == "in_process":
        warnings.append("In-process WhisperX worker is for diagnostics; prefer subprocess modes for demos.")
    if any(check.status == "warning" for check in checks if check.id == "speaker_hints"):
        warnings.append("No 2+ speaker hint is configured; diarization may collapse speakers in short demo audio.")
    return warnings


def _speaker_verification_status(settings: Settings) -> SpeakerVerificationStatus:
    path = settings.speaker_verification_path.expanduser()
    if not path.exists():
        return SpeakerVerificationStatus(
            path=str(path),
            exists=False,
            verified=False,
            status="not_verified",
            provider=settings.speaker_provider,
            detail="No real diarization smoke report has been recorded",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SpeakerVerificationStatus(
            path=str(path),
            exists=True,
            verified=False,
            status="invalid",
            provider=settings.speaker_provider,
            error=str(exc),
            detail="Speaker verification report is unreadable",
        )

    verified = bool(raw.get("verified"))
    error = raw.get("error")
    labels = raw.get("speaker_labels") or []
    if not isinstance(labels, list):
        labels = []
    stages = raw.get("stages") or []
    if not isinstance(stages, list):
        stages = []
    warnings = raw.get("warnings") or []
    if not isinstance(warnings, list):
        warnings = []
    config = raw.get("config") or {}
    if not isinstance(config, dict):
        config = {}
    quality = raw.get("quality") or {}
    if not isinstance(quality, dict):
        quality = {}
    status = "verified" if verified else "failed" if error else "not_verified"
    detail = str(raw.get("detail") or "")
    if not detail:
        if verified:
            detail = f"Verified with {int(raw.get('distinct_speaker_count') or 0)} speaker label(s)"
        elif error:
            detail = str(error)
        else:
            detail = "Real diarization smoke has not passed yet"
    last_stage = str(raw.get("last_stage") or "") or _last_verification_stage(stages)
    return SpeakerVerificationStatus(
        path=str(path),
        exists=True,
        verified=verified,
        status=status,
        provider=str(raw.get("provider") or settings.speaker_provider),
        checked_at=raw.get("checked_at"),
        distinct_speaker_count=int(raw.get("distinct_speaker_count") or 0),
        speaker_labels=[str(label) for label in labels],
        elapsed_ms=raw.get("elapsed_ms"),
        strict_multi_speaker=bool(raw.get("strict_multi_speaker") or raw.get("require_multiple_speakers")),
        generated_audio=bool(raw.get("generated_audio")),
        last_stage=last_stage,
        stages=[stage for stage in stages if isinstance(stage, dict)][-8:],
        quality={str(key): value for key, value in quality.items()},
        warnings=[str(warning) for warning in warnings if str(warning).strip()],
        config={str(key): value for key, value in config.items()},
        error=str(error) if error else None,
        detail=detail,
    )


def _diarization_multi_speaker_ready(verification: SpeakerVerificationStatus) -> bool:
    return bool(verification.verified and verification.distinct_speaker_count >= 2)


def _diarization_readiness_status(verification: SpeakerVerificationStatus) -> str:
    if _diarization_multi_speaker_ready(verification):
        return verification.status
    if verification.verified:
        return "weak"
    return verification.status


def _diarization_readiness_detail(verification: SpeakerVerificationStatus) -> str:
    if _diarization_multi_speaker_ready(verification):
        return verification.detail
    if verification.verified:
        count = verification.distinct_speaker_count
        label = "speaker label" if count == 1 else "speaker labels"
        verb = "was" if count == 1 else "were"
        return (
            f"{verification.detail} Only {count} {label} {verb} verified; "
            "run strict 2+ speaker verification before treating this as multi-speaker ready."
        )
    return verification.detail


def _last_verification_stage(stages: list) -> str | None:
    for stage in reversed(stages):
        if isinstance(stage, dict) and stage.get("stage"):
            return str(stage["stage"])
    return None


def _store_parent_available(store: RuntimeStoreStatus | None) -> bool:
    if store is None:
        return False
    return Path(store.path).expanduser().parent.exists()


def _memory_store_detail(
    collab_store: RuntimeStoreStatus | None,
    speaker_store: RuntimeStoreStatus | None,
) -> str:
    collab = collab_store.backend if collab_store else "missing"
    speaker = speaker_store.backend if speaker_store else "missing"
    return f"collab {collab}, speakers {speaker}"


def _git_readiness_status(git_status: GitStatusResponse | None) -> str:
    if not git_status or not git_status.is_git_repo:
        return "needs_attention"
    return "dirty" if git_status.dirty else "ready"


def _git_readiness_detail(git_status: GitStatusResponse | None) -> str:
    if git_status is None:
        return "Workspace is not connected"
    if not git_status.is_git_repo:
        return git_status.warning or "Connected workspace is not a git repository"
    changed = len(git_status.files)
    suffix = f", {changed} changed files" if changed else ", clean"
    return f"{git_status.branch or 'detached'}{suffix}"


def _cache_status(settings: Settings) -> CacheStatusResponse:
    status = JsonTTLCache(settings.voiceops_cache_path).status()
    return CacheStatusResponse(
        **status,
        git_ttl_seconds=settings.git_cache_ttl_seconds,
        workspace_ttl_seconds=settings.workspace_cache_ttl_seconds,
    )


def _store_status(
    store_id: str,
    label: str,
    backend: str,
    path: Path,
    *,
    migration_available: bool = False,
) -> RuntimeStoreStatus:
    exists = path.exists()
    return RuntimeStoreStatus(
        id=store_id,
        label=label,
        backend=backend,
        path=str(path),
        exists=exists,
        size_bytes=path.stat().st_size if exists and path.is_file() else 0,
        migration_available=migration_available,
    )


def _speaker_provider_status(settings: Settings) -> RuntimeProviderStatus:
    provider = settings.speaker_provider.strip().lower()
    if provider == "mock":
        return RuntimeProviderStatus(
            id="speaker",
            label="Speaker identity",
            value=settings.speaker_provider,
            ready=True,
            detail="Mock diarization for local demos",
        )
    if provider != "whisperx":
        return RuntimeProviderStatus(
            id="speaker",
            label="Speaker identity",
            value=settings.speaker_provider,
            ready=False,
            detail=f"Unsupported speaker provider: {settings.speaker_provider}",
        )

    checks = [
        RuntimeProviderCheck(
            id="hf_token",
            label="HF_TOKEN",
            ready=bool(settings.hf_token),
            detail="Configured; model access is verified on first run" if settings.hf_token else "Required for pyannote diarization",
        ),
        RuntimeProviderCheck(
            id="whisperx",
            label="WhisperX package",
            ready=_module_available("whisperx"),
            detail="Python import available",
        ),
        RuntimeProviderCheck(
            id="torch",
            label="Torch runtime",
            ready=_module_available("torch"),
            detail=f"Device: {settings.whisperx_device}",
        ),
        RuntimeProviderCheck(
            id="pyannote",
            label="pyannote.audio",
            ready=_module_available("pyannote.audio"),
            detail="Diarization backend package",
        ),
    ]
    missing = [check.label for check in checks if not check.ready]
    ready = not missing
    detail = (
        "WhisperX ready; CPU mode may lag" if ready and settings.whisperx_device == "cpu"
        else "WhisperX ready for local live diarization" if ready
        else f"Missing: {', '.join(missing)}"
    )
    if ready:
        detail = f"{detail}; worker mode {settings.whisperx_worker_mode}"
    return RuntimeProviderStatus(
        id="speaker",
        label="Speaker identity",
        value=settings.speaker_provider,
        ready=ready,
        detail=detail,
        checks=checks,
    )


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _runtime_warnings(
    settings: Settings,
    workspace_path: Path | None,
    speaker_status: RuntimeProviderStatus,
) -> list[str]:
    warnings: list[str] = []
    if settings.jwt_secret == "change-me-in-production-voiceops":
        warnings.append("JWT secret is using the development default.")
    if settings.collab_store_backend == "json":
        warnings.append("Collaboration store is JSON; use SQLite for longer sessions.")
    if settings.speaker_store_backend == "json":
        warnings.append("Speaker store is JSON; use SQLite for persistent room identity.")
    if settings.speaker_provider == "whisperx":
        failed_checks = [check for check in speaker_status.checks if not check.ready]
        if failed_checks:
            labels = ", ".join(check.label for check in failed_checks)
            warnings.append(f"WhisperX speaker provider is not ready: {labels}.")
        elif settings.whisperx_device == "cpu":
            warnings.append("WhisperX is configured for CPU mode; live diarization may lag.")
        if settings.whisperx_worker_mode == "persistent_subprocess":
            warnings.append("WhisperX persistent subprocess can reduce cold-start latency but keeps a model worker resident.")
        if settings.whisperx_worker_mode == "in_process":
            warnings.append(
                "WhisperX in-process worker is for diagnostics; native model loading can hang the backend process."
            )
    if settings.tts_provider == "openai" and not settings.openai_api_key:
        warnings.append("OpenAI TTS is selected but OPENAI_API_KEY is not configured.")
    if workspace_path is None:
        warnings.append("VOICEOPS_WORKSPACE is not configured.")
    elif not workspace_path.exists():
        warnings.append("VOICEOPS_WORKSPACE path does not exist.")
    return warnings
