import base64

from app.config import Settings
from app.voice_agent.models import SpeechResult
from app.voice_agent.tts.base import TextToSpeechProvider


class OpenAITTS(TextToSpeechProvider):
    """Synthesize speech using the OpenAI Audio API."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when TTS_PROVIDER=openai")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_tts_model
        self._voice = settings.openai_tts_voice

    @property
    def name(self) -> str:
        return "openai"

    async def synthesize(self, text: str) -> SpeechResult:
        response = await self._client.audio.speech.create(
            model=self._model,
            voice=self._voice,
            input=text,
            response_format="mp3",
        )
        audio_bytes = response.content
        return SpeechResult(
            text=text,
            audio_base64=base64.b64encode(audio_bytes).decode("ascii"),
            mime_type="audio/mpeg",
            provider=self.name,
        )
