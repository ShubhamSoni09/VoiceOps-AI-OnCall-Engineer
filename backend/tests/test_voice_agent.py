import pytest

from app.config import Settings
from app.voice_agent.models import IncidentAction, VoiceIntent
from app.voice_agent.normalization.normalizer import CommandNormalizer
from app.voice_agent.pipeline import VoiceAgentPipeline


@pytest.fixture
def mock_settings(tmp_path):
    return Settings(
        llm_provider="mock",
        stt_provider="whisper",
        tts_provider="mock",
        memory_store_path=str(tmp_path / "memory.json"),
        voiceops_workspace=None,
    )


@pytest.fixture
def pipeline(mock_settings):
    return VoiceAgentPipeline(settings=mock_settings)


@pytest.mark.asyncio
async def test_process_text_investigate(pipeline):
    result = await pipeline.process_text("Why is the API failing in production?")

    assert result.intent.intent == VoiceIntent.INVESTIGATE_INCIDENT
    assert result.intent.action == IncidentAction.INVESTIGATE
    assert result.command.requires_approval is False
    assert "api" in (result.command.target or "").lower() or "api" in result.command.parameters.get("service", "")
    assert result.context.suggested_next_steps
    assert result.response_text
    assert result.speech.text == result.response_text
    assert result.speech.provider in ("mock", "browser")
    assert result.speech.audio_base64 is None


@pytest.mark.asyncio
async def test_process_text_deploy_requires_approval(pipeline):
    result = await pipeline.process_text("Deploy the payment service to prod immediately")

    assert result.intent.intent == VoiceIntent.DEPLOY_SERVICE
    assert result.command.requires_approval is True
    assert result.command.urgency in ("high", "critical", "normal")


@pytest.mark.asyncio
async def test_process_text_rollback(pipeline):
    result = await pipeline.process_text("Rollback the last deployment")

    assert result.intent.intent == VoiceIntent.ROLLBACK_DEPLOYMENT
    assert result.command.requires_approval is True


@pytest.mark.asyncio
async def test_session_memory_persists(pipeline):
    await pipeline.process_text("Check API status", session_id="sess-1")
    await pipeline.process_text("Now investigate errors", session_id="sess-1")

    history = pipeline._memory.get_history("sess-1")
    assert len(history) >= 2
    assert any("Check API status" in t.content for t in history)


@pytest.mark.asyncio
async def test_casual_query_gets_friendly_response(pipeline):
    result = await pipeline.process_text("What are you doing?")

    assert result.intent.intent == VoiceIntent.GENERAL_QUERY
    assert "ready to help" in result.response_text.lower()
    assert "suggested next steps" not in result.response_text.lower()


@pytest.mark.asyncio
async def test_empty_transcript_raises(pipeline):
    with pytest.raises(ValueError, match="Empty transcript"):
        await pipeline.process_text("   ")


def test_normalizer_urgency():
    normalizer = CommandNormalizer()
    from app.voice_agent.models import ExtractedIntent

    intent = ExtractedIntent(
        intent=VoiceIntent.INVESTIGATE_INCIDENT,
        action=IncidentAction.INVESTIGATE,
        entities={"service": "api"},
        raw_summary="Critical outage on API",
        confidence=0.9,
    )
    cmd = normalizer.normalize("Critical P0 outage on the API", intent)
    assert cmd.urgency == "critical"
    assert cmd.target == "api"
