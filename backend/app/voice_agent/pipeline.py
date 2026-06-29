from app.config import Settings, get_settings
from app.orchestrator import WorkspaceOrchestrator
from app.voice_agent.context.enricher import ContextEnricher, SessionMemory
from app.voice_agent.demo_gates import start_demo_gate_from_text
from app.voice_agent.intent.extractor import get_intent_extractor
from app.voice_agent.models import SpeechResult, TranscriptionResult, VoiceProcessResponse
from app.voice_agent.normalization.normalizer import CommandNormalizer
from app.voice_agent.stt import SpeechToTextProvider, get_stt_provider
from app.voice_agent.tts import get_tts_provider
from app.voice_agent.tts.response_builder import build_response_text


class VoiceAgentPipeline:
    """
    Voice Processor pipeline:
      Audio/Text → STT → Intent Extraction → Normalization → Context Enrichment → Orchestrator
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stt: SpeechToTextProvider | None = None
        self._intent_extractor = get_intent_extractor(self._settings)
        self._normalizer = CommandNormalizer()
        self._memory = SessionMemory(self._settings)
        self._enricher = ContextEnricher(self._memory)
        self._tts = get_tts_provider(self._settings)
        self._orchestrator = WorkspaceOrchestrator(self._settings)

    def _get_stt(self) -> SpeechToTextProvider:
        if self._stt is None:
            self._stt = get_stt_provider(self._settings)
        return self._stt

    async def process_audio(
        self,
        audio_bytes: bytes,
        *,
        session_id: str = "default",
        filename: str = "audio.wav",
        incident_context: dict | None = None,
        include_tts: bool | None = None,
    ) -> VoiceProcessResponse:
        transcription = await self.transcribe_audio(audio_bytes, filename=filename)
        return await self.process_text(
            transcription.text,
            session_id=session_id,
            incident_context=incident_context,
            include_tts=include_tts,
        )

    async def transcribe_audio(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.wav",
    ) -> TranscriptionResult:
        return await self._get_stt().transcribe(audio_bytes, filename=filename)

    async def process_text(
        self,
        text: str,
        *,
        session_id: str = "default",
        incident_context: dict | None = None,
        include_tts: bool | None = None,
    ) -> VoiceProcessResponse:
        transcript = text.strip()
        if not transcript:
            raise ValueError("Empty transcript")

        history = self._memory.get_history(session_id)
        intent = await self._intent_extractor.extract(
            transcript,
            history=history,
            incident_context=incident_context,
        )
        command = self._normalizer.normalize(transcript, intent)
        context = self._enricher.enrich(
            session_id,
            transcript,
            intent,
            command,
            incident_context=incident_context,
        )

        orchestrator_result = start_demo_gate_from_text(transcript, self._settings)
        if orchestrator_result is None:
            orchestrator_result = await self._orchestrator.execute(command, transcript)

        response_text = build_response_text(intent, command, context, orchestrator_result)
        use_tts = include_tts if include_tts is not None else self._settings.tts_on_voice
        if use_tts:
            speech = await self._tts.synthesize(response_text)
        else:
            speech = SpeechResult(text=response_text, provider="browser")

        return VoiceProcessResponse(
            session_id=session_id,
            transcript=transcript,
            intent=intent,
            command=command,
            context=context,
            response_text=response_text,
            speech=speech,
            orchestrator_result=orchestrator_result,
        )

    def clear_session(self, session_id: str) -> None:
        self._memory.clear(session_id)
