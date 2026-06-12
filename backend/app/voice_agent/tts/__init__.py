from app.config import Settings, get_settings
from app.voice_agent.tts.base import TextToSpeechProvider
from app.voice_agent.tts.mock_tts import MockTTS
from app.voice_agent.tts.openai_tts import OpenAITTS


def get_tts_provider(settings: Settings | None = None) -> TextToSpeechProvider:
    settings = settings or get_settings()
    if settings.tts_provider == "openai" and settings.openai_api_key:
        return OpenAITTS(settings)
    return MockTTS()
