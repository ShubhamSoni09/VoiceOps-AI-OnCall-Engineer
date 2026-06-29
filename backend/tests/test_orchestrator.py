import textwrap
from pathlib import Path

import pytest

from app.config import Settings
from app.llm import LLMRequest, LLMResponse
import app.orchestrator.workspace_orchestrator as orchestrator_module
from app.orchestrator.workspace_orchestrator import WorkspaceOrchestrator
from app.voice_agent.models import ExtractedIntent, IncidentAction, NormalizedCommand, VoiceIntent
from app.voice_agent.normalization.normalizer import CommandNormalizer
from app.voice_agent.pipeline import VoiceAgentPipeline


def _write_sandbox(workspace) -> None:
    workspace.mkdir()
    (workspace / "requirements.txt").write_text("fastapi>=0.115.0\npytest>=8.3.0\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Sandbox API\n\nRoot endpoint is available.\n", encoding="utf-8")
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
        voiceops_cache_path=tmp_path / "cache.json",
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
    app_path = Path(workspace_settings.voiceops_workspace) / "app.py"
    original = app_path.read_text(encoding="utf-8")
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
    assert result.executed is False
    assert result.pending_approval is True
    assert "app.py" in result.files_changed
    assert "@@ " in result.approval["diff"]
    assert result.approval_payload["files"]["app.py"] != original
    assert app_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_pipeline_executes_workspace_actions(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    result = await pipeline.process_text("run tests in sandbox", session_id="orch-test")

    assert result.orchestrator_result is not None
    assert result.orchestrator_result.executed is True
    assert result.orchestrator_result.action == IncidentAction.TEST.value


@pytest.mark.asyncio
async def test_pipeline_find_bug_reports_without_patch(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    app_path = Path(workspace_settings.voiceops_workspace) / "app.py"
    original = app_path.read_text(encoding="utf-8")

    result = await pipeline.process_text("find bug in the sandbox", session_id="bug-scan")

    assert result.command.action == IncidentAction.FIND_BUG
    assert result.orchestrator_result is not None
    assert result.orchestrator_result.action == IncidentAction.FIND_BUG.value
    assert result.orchestrator_result.pending_approval is False
    assert "No patch was proposed" in result.orchestrator_result.summary or "say fix or patch" in result.orchestrator_result.summary
    assert app_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_pipeline_explain_code_uses_read_only_query(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    result = await pipeline.process_text("explain what app.py does", session_id="explain-code")

    assert result.command.action == IncidentAction.EXPLAIN_CODE
    assert result.orchestrator_result is not None
    assert result.orchestrator_result.action == IncidentAction.EXPLAIN_CODE.value
    assert result.orchestrator_result.pending_approval is False
    assert "app.py" in result.orchestrator_result.summary


@pytest.mark.asyncio
async def test_pipeline_fix_via_voice(workspace_settings):
    pipeline = VoiceAgentPipeline(settings=workspace_settings)
    result = await pipeline.process_text("fix the failing health check", session_id="orch-fix")

    assert result.orchestrator_result is not None
    assert result.orchestrator_result.executed is False
    assert result.orchestrator_result.pending_approval is True
    assert "app.py" in result.orchestrator_result.files_changed
    assert "approve" in result.response_text.lower()


@pytest.mark.asyncio
async def test_orchestrator_patch_can_propose_multiple_files(workspace_settings):
    orchestrator = WorkspaceOrchestrator(workspace_settings)
    app_path = Path(workspace_settings.voiceops_workspace) / "app.py"
    readme_path = Path(workspace_settings.voiceops_workspace) / "README.md"
    original_app = app_path.read_text(encoding="utf-8")
    original_readme = readme_path.read_text(encoding="utf-8")
    command = NormalizedCommand(
        intent=VoiceIntent.FIX_ISSUE,
        action=IncidentAction.PATCH,
        target="sandbox-api",
        parameters={},
        urgency="normal",
        requires_approval=True,
        original_transcript="fix the health endpoint and update README docs",
        normalized_text="Patch sandbox-api",
    )

    result = await orchestrator.execute(command, "fix the health endpoint and update README docs")

    assert result is not None
    assert result.pending_approval is True
    assert result.files_changed == ["app.py", "README.md"]
    assert "--- a/app.py" in result.approval["diff"]
    assert "--- a/README.md" in result.approval["diff"]
    assert app_path.read_text(encoding="utf-8") == original_app
    assert readme_path.read_text(encoding="utf-8") == original_readme


@pytest.mark.asyncio
async def test_orchestrator_patch_generation_uses_llm_runtime(monkeypatch, workspace_settings):
    app_path = Path(workspace_settings.voiceops_workspace) / "app.py"
    generated_app = app_path.read_text(encoding="utf-8") + "\n\n@app.get(\"/health\")\ndef health() -> dict[str, str]:\n    return {\"status\": \"ok\"}\n"
    runtime = _FakeRuntime(LLMResponse(content=generated_app, provider="test-provider", model="test-model"))
    monkeypatch.setattr(orchestrator_module, "get_llm_runtime", lambda _settings: runtime)
    settings = workspace_settings.model_copy(update={"llm_provider": "openai", "openai_api_key": "test-key"})
    orchestrator = WorkspaceOrchestrator(settings)
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
    assert result.pending_approval is True
    assert result.approval["llm"]["provider"] == "test-provider"
    assert result.approval["llm"]["model"] == "test-model"
    assert result.approval["llm"]["fallback"] is False
    assert runtime.requests[0].purpose == "patch_generation"
    assert runtime.requests[0].metadata["tool_policy"] == "approval_required"


class _FakeRuntime:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response
