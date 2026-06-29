from app.config import Settings
from app.voice_agent.stt import get_stt_provider
from app.voice_agent.pipeline import VoiceAgentPipeline


def test_pipeline_text_mode_does_not_load_stt_backend():
    pipeline = VoiceAgentPipeline(Settings(llm_provider="mock", tts_provider="mock"))

    assert pipeline._stt is None


def test_mock_stt_provider_does_not_import_optional_audio_backends():
    provider = get_stt_provider(Settings(stt_provider="mock", llm_provider="mock", tts_provider="mock"))

    assert provider.name == "mock"


def test_unknown_stt_provider_fails_fast():
    try:
        get_stt_provider(Settings(stt_provider="unknown", llm_provider="mock", tts_provider="mock"))
    except ValueError as exc:
        assert "Unsupported STT provider" in str(exc)
    else:
        raise AssertionError("Expected unsupported STT provider to raise ValueError")
