from app.voice_agent.models import SpeechResult
from app.voice_agent.tts.base import TextToSpeechProvider


class MockTTS(TextToSpeechProvider):
    """Text-only fallback — no server-side audio generation."""

    @property
    def name(self) -> str:
        return "mock"

    async def synthesize(self, text: str) -> SpeechResult:
        return SpeechResult(text=text, provider=self.name)
