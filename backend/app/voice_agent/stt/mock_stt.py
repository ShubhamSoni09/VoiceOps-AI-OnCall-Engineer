from app.voice_agent.models import TranscriptionResult
from app.voice_agent.stt.base import SpeechToTextProvider


class MockSTT(SpeechToTextProvider):
    """Deterministic STT provider for tests and local text-only flows."""

    @property
    def name(self) -> str:
        return "mock"

    async def transcribe(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> TranscriptionResult:
        try:
            text = audio_bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = ""
        return TranscriptionResult(text=text or "mock transcription", language="en", provider=self.name)
