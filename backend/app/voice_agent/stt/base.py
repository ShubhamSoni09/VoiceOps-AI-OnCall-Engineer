from abc import ABC, abstractmethod

from app.voice_agent.models import TranscriptionResult


class SpeechToTextProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, *, filename: str = "audio.wav") -> TranscriptionResult:
        """Transcribe raw audio bytes to text."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...
