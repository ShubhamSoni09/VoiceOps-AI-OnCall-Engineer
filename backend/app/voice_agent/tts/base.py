from abc import ABC, abstractmethod

from app.voice_agent.models import SpeechResult


class TextToSpeechProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> SpeechResult:
        """Convert text to speech."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...
