from app.config import Settings, get_settings
from app.voice_agent.stt.base import SpeechToTextProvider
from app.voice_agent.stt.mock_stt import MockSTT


def get_stt_provider(settings: Settings | None = None) -> SpeechToTextProvider:
    settings = settings or get_settings()
    if settings.stt_provider == "mock":
        return MockSTT()
    if settings.stt_provider == "aws":
        from app.voice_agent.stt.aws_transcribe import AWSTranscribeSTT

        return AWSTranscribeSTT(settings)
    if settings.stt_provider != "whisper":
        raise ValueError(f"Unsupported STT provider: {settings.stt_provider}")
    from app.voice_agent.stt.whisper import WhisperSTT

    return WhisperSTT(settings)
