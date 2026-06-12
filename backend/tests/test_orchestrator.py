import textwrap

import pytest

from app.config import Settings
from app.orchestrator.workspace_orchestrator import WorkspaceOrchestrator
from app.voice_agent.models import ExtractedIntent, IncidentAction, NormalizedCommand, VoiceIntent
from app.voice_agent.normalization.normalizer import CommandNormalizer
from app.voice_agent.pipeline import VoiceAgentPipeline


def _write_sandbox(workspace) -> None:
    workspace.mkdir()
    (workspace / "requirements.txt").write_text("fastapi>=0.115.0\npytest>=8.3.0\n", encoding="utf-8")
    (workspace / "app.py").write_text(
        textwrap.dedent(
            """
            from fastapi import FastAPI

            app = FastAPI(title="Test Sandbox")

            @app.get("/")
            def root() -> dict[str, str]:
                return {"service": "sandbox-api", "status": "ok"}

            # Intentionally missing /health
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        textwrap.dedent(
            """
            from fastapi.testclient import TestClient
            from app import app

            client = TestClient(app)

            def test_health() -> None:
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json()["status"] == "ok"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def sandbox_workspace(tmp_path):
    ws = tmp_path / "sandbox"
    _write_sandbox(ws)
    return ws


@pytest.fixture
def workspace_settings(tmp_path, sandbox_workspace):
    return Settings(
        llm_provider="mock",
        stt_provider="whisper",
        tts_provider="mock",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_workspace=str(sandbox_workspace),
    )


@pytest.mark.asyncio
async def test_orchestrator_investigate_runs_pytest(workspace_settings, sandbox_workspace):
    orchestrator = WorkspaceOrchestrator(workspace_settings)
    command = NormalizedCommand(
        intent=VoiceIntent.INVESTIGATE_INCIDENT,
        action=IncidentAction.INVESTIGATE,
        target="sandbox-api",
        parameters={},
        urgency="normal",
        requires_approval=False,
        original_transcript="investigate the sandbox",
        normalized_text="Investigate sandbox-api",
    )

    result = await orchestrator.execute(command, "investigate the sandbox")

    assert result is not None
    assert result.executed is True
    assert "health" in result.summary.lower() or "404" in result.summary
    assert "pytest failed:" not in result.summary.lower()


@pytest.mark.asyncio
async def test_orchestrator_patch_adds_health_endpoint(workspace_settings):
    orchestrator = WorkspaceOrchestrator(workspace_settings)
    command = NormalizedCommand(
        intent=VoiceIntent.FIX_ISSUE,
        action=IncidentAction.PATCH,
        target="sandbox-api",
        parameters={},
        urgency="normal",
        requires_approval=True,
        original_transcript="fix the health endpoint",
        normalized_text="Patch sandbox-api",
    )

    result = await orchestrator.execute(command, "fix the health endpoint")

    assert result is not None
    assert result.executed is True
    assert "app.py" in result.files_changed
    assert "pass" in result.summary.lower() or "tests now pass" in result.summary.lower()


@pytest.mark.asyncio
async def test_pipeline_executes_workspace_actions(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    result = await pipeline.process_text("run tests in sandbox", session_id="orch-test")

    assert result.orchestrator_result is not None
    assert result.orchestrator_result.executed is True
    assert result.orchestrator_result.action == IncidentAction.TEST.value


@pytest.mark.asyncio
async def test_pipeline_fix_via_voice(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    result = await pipeline.process_text("fix the failing health check", session_id="orch-fix")

    assert result.orchestrator_result is not None
    assert result.orchestrator_result.executed is True
    assert "app.py" in result.orchestrator_result.files_changed
