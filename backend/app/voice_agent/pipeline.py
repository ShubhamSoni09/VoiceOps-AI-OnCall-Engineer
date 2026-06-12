from __future__ import annotations

import asyncio
import json

from app.config import Settings, get_settings
from app.orchestrator import WorkspaceOrchestrator
from app.voice_agent.context.enricher import ContextEnricher, SessionMemory
from app.voice_agent.intent.extractor import get_intent_extractor
from app.voice_agent.models import SpeechResult, VoiceProcessResponse
from app.voice_agent.normalization.normalizer import CommandNormalizer
from app.voice_agent.stt import get_stt_provider
from app.voice_agent.tts import get_tts_provider
from app.voice_agent.tts.response_builder import build_response_text


class VoiceAgentPipeline:
    """
    Voice Processor pipeline:
      Audio/Text → STT → Intent Extraction → Normalization → Context Enrichment → Orchestrator
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._stt = get_stt_provider(self._settings)
        self._intent_extractor = get_intent_extractor(self._settings)
        self._normalizer = CommandNormalizer()
        self._memory = SessionMemory(self._settings)
        self._enricher = ContextEnricher(self._memory)
        self._tts = get_tts_provider(self._settings)
        self._orchestrator = WorkspaceOrchestrator(self._settings)

    async def process_audio(
        self,
        audio_bytes: bytes,
        *,
        session_id: str = "default",
        filename: str = "audio.wav",
        incident_context: dict | None = None,
        include_tts: bool | None = None,
        user_id: str | None = None,
    ) -> VoiceProcessResponse:
        transcription = await self._stt.transcribe(audio_bytes, filename=filename)
        return await self.process_text(
            transcription.text,
            session_id=session_id,
            incident_context=incident_context,
            include_tts=include_tts,
            user_id=user_id,
        )

    async def process_text(
        self,
        text: str,
        *,
        session_id: str = "default",
        incident_context: dict | None = None,
        include_tts: bool | None = None,
        progress: asyncio.Queue | None = None,
        user_id: str | None = None,
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

        orchestrator_result = await self._orchestrator.execute(
            command, transcript, progress=progress, user_id=user_id
        )

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

    async def process_text_stream(
        self,
        text: str,
        *,
        session_id: str = "default",
        incident_context: dict | None = None,
        user_id: str | None = None,
    ):
        """Async generator yielding SSE-formatted lines. Final event is {"type":"done","data":{...}}."""
        queue: asyncio.Queue = asyncio.Queue()

        async def _run() -> None:
            try:
                result = await self.process_text(
                    text,
                    session_id=session_id,
                    incident_context=incident_context,
                    include_tts=False,
                    progress=queue,
                    user_id=user_id,
                )
                await queue.put({"type": "done", "data": result.model_dump()})
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc)})

        task = asyncio.create_task(_run())

        # Emit the first step immediately so the UI isn't blank
        yield _sse({"type": "step", "label": "Understanding command…"})

        while True:
            event = await queue.get()
            yield _sse(event)
            if event["type"] in ("done", "error"):
                break

        await task

    def clear_session(self, session_id: str) -> None:
        self._memory.clear(session_id)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
