from __future__ import annotations

import re

from fastapi import HTTPException

from app.config import Settings
from app.voice_agent.models import OrchestratorResult

DEMO_GATE_LABELS = {
    "mock_e2e": "Mock closure harness",
    "real_live_backend": "Real WhisperX backend live",
    "real_browser_live": "Real browser mic live",
}


def demo_gate_id_from_text(text: str) -> str | None:
    lower = text.lower()
    if not _looks_like_gate_request(lower):
        return None
    if re.search(r"\b(real browser|browser mic|browser microphone|real mic|real microphone)\b", lower):
        return "real_browser_live"
    if re.search(r"\b(real live|whisperx backend|backend live|live backend)\b", lower):
        return "real_live_backend"
    return "mock_e2e"


def start_demo_gate_from_text(text: str, settings: Settings) -> OrchestratorResult | None:
    gate_id = demo_gate_id_from_text(text)
    if not gate_id:
        return None
    label = DEMO_GATE_LABELS[gate_id]
    try:
        from app.system.router import start_demo_gate_run_job

        job = start_demo_gate_run_job(gate_id, settings)
    except HTTPException as exc:
        detail = str(exc.detail)
        return OrchestratorResult(
            executed=False,
            action="verify",
            summary=f"I could not start {label}: {detail}",
            artifacts=[
                {
                    "type": "logs",
                    "title": label,
                    "subtitle": "not started",
                }
            ],
            approval={
                "source": "demo_gate_run",
                "gate_id": gate_id,
                "gate_label": label,
                "status": "not_started",
                "error": detail,
            },
        )
    return OrchestratorResult(
        executed=True,
        action="verify",
        summary=f"I started {label}. Track progress in Demo readiness; job {job.job_id}.",
        artifacts=[
            {
                "type": "logs",
                "title": label,
                "subtitle": "running",
            }
        ],
        approval={
            "source": "demo_gate_run",
            "gate_id": gate_id,
            "gate_label": label,
            "job_id": job.job_id,
            "status": job.state,
            "poll_url": job.poll_url,
        },
    )


def _looks_like_gate_request(lower: str) -> bool:
    if not re.search(r"\b(run|start|verify|check|prove)\b", lower):
        return False
    return any(
        marker in lower
        for marker in (
            "demo readiness",
            "readiness gate",
            "demo gate",
            "mock gate",
            "real live gate",
            "real browser gate",
            "browser mic gate",
            "browser microphone gate",
            "whisperx gate",
        )
    )
