from app.config import Settings, get_settings
from app.voice_agent.stt.aws_transcribe import AWSTranscribeSTT
from app.voice_agent.stt.base import SpeechToTextProvider
from app.voice_agent.stt.whisper import WhisperSTT


def get_stt_provider(settings: Settings | None = None) -> SpeechToTextProvider:
    settings = settings or get_settings()
    if settings.stt_provider == "aws":
        return AWSTranscribeSTT(settings)
    return WhisperSTT(settings)
