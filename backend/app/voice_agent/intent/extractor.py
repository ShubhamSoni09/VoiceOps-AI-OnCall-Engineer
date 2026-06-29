import json
import re
from abc import ABC, abstractmethod

from app.config import Settings
from app.llm import LLMMessage, LLMRequest
from app.llm.service import LLMRuntime, get_llm_runtime
from app.voice_agent.intent.prompts import INTENT_EXTRACTION_SYSTEM, INTENT_EXTRACTION_USER
from app.voice_agent.intent.spoken_fallback import infer_spoken_response
from app.voice_agent.models import ConversationTurn, ExtractedIntent, IncidentAction, VoiceIntent


class IntentExtractor(ABC):
    @abstractmethod
    async def extract(
        self,
        transcript: str,
        *,
        history: list[ConversationTurn] | None = None,
        incident_context: dict | None = None,
    ) -> ExtractedIntent:
        ...


def _parse_intent_payload(payload: dict, *, transcript: str = "") -> ExtractedIntent:
    intent_raw = payload.get("intent", "unknown")
    action_raw = payload.get("action", "unknown")

    try:
        intent = VoiceIntent(intent_raw)
    except ValueError:
        intent = VoiceIntent.UNKNOWN

    try:
        action = IncidentAction(action_raw)
    except ValueError:
        action = IncidentAction.UNKNOWN

    result = ExtractedIntent(
        intent=intent,
        action=action,
        entities=payload.get("entities") or {},
        raw_summary=payload.get("raw_summary", transcript_fallback(payload)),
        spoken_response=payload.get("spoken_response"),
        confidence=float(payload.get("confidence", 0.5)),
    )
    if not result.spoken_response and transcript:
        spoken = infer_spoken_response(transcript, result)
        if spoken:
            result = result.model_copy(update={"spoken_response": spoken})
    return result


def transcript_fallback(payload: dict) -> str:
    return str(payload.get("raw_summary", "User command received"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


class LLMIntentExtractor(IntentExtractor):
    """Extract intent through the shared VoiceOps LLM runtime."""

    def __init__(self, settings: Settings, runtime: LLMRuntime | None = None) -> None:
        self._settings = settings
        self._runtime = runtime or get_llm_runtime(settings)

    async def extract(
        self,
        transcript: str,
        *,
        history: list[ConversationTurn] | None = None,
        incident_context: dict | None = None,
    ) -> ExtractedIntent:
        history_text = "\n".join(f"{t.role}: {t.content}" for t in (history or [])) or "(none)"
        incident_text = json.dumps(incident_context or {}, indent=2)

        user_prompt = INTENT_EXTRACTION_USER.format(
            history=history_text,
            transcript=transcript,
            incident_context=incident_text,
        )

        response = await self._runtime.generate(
            LLMRequest(
                purpose="intent_extraction",
                response_format="json",
                temperature=0.2,
                max_tokens=1024,
                metadata={"llm_provider": self._settings.llm_provider},
                messages=[
                    LLMMessage(role="system", content=INTENT_EXTRACTION_SYSTEM),
                    LLMMessage(role="user", content=user_prompt),
                ],
            )
        )
        return _parse_intent_payload(_extract_json(response.content), transcript=transcript)


class OpenAIIntentExtractor(LLMIntentExtractor):
    """Backward-compatible alias for OpenAI intent extraction."""


class BedrockIntentExtractor(LLMIntentExtractor):
    """Backward-compatible alias for Bedrock intent extraction."""


class MockIntentExtractor(IntentExtractor):
    """Rule-based intent extractor for local dev without Bedrock."""

    RULES: list[tuple[re.Pattern[str], VoiceIntent, IncidentAction]] = [
        (re.compile(r"\b(verify|check|run|start).+\b(demo readiness|readiness gate|demo gate|real live gate|real browser gate|browser mic gate|whisperx gate)\b", re.I), VoiceIntent.CHECK_STATUS, IncidentAction.VERIFY),
        (re.compile(r"\b(explain|walk me through|what does .+ do)\b|解释|讲一下", re.I), VoiceIntent.EXPLAIN_CODE, IncidentAction.EXPLAIN_CODE),
        (re.compile(r"\b(find (?:a )?bug|look for bugs|bug hunt|debug)\b|找.*bug|查.*bug", re.I), VoiceIntent.FIND_BUG, IncidentAction.FIND_BUG),
        (re.compile(r"\b(summarize|recap).+\b(changes|adjustments|diffs|work)\b|\bwhat changes\b|\bwhat did .+ (?:change|do|work on)\b|总结.*(改|变化|工作)", re.I), VoiceIntent.SUMMARIZE_CHANGES, IncidentAction.SUMMARIZE_CHANGES),
        (re.compile(r"\b(git status|branch|working tree|workspace diff)\b|git状态|分支|工作区状态", re.I), VoiceIntent.GIT_STATUS, IncidentAction.GIT_STATUS),
        (re.compile(r"\b(fix|patch|resolve|repair)\b", re.I), VoiceIntent.FIX_ISSUE, IncidentAction.PATCH),
        (re.compile(r"\b(why|investigate|what'?s wrong|failing|down|error|incident)\b", re.I), VoiceIntent.INVESTIGATE_INCIDENT, IncidentAction.INVESTIGATE),
        (re.compile(r"\b(deploy|ship|release|push to prod)\b", re.I), VoiceIntent.DEPLOY_SERVICE, IncidentAction.DEPLOY),
        (re.compile(r"\b(status|health|how is|is .+ up)\b", re.I), VoiceIntent.CHECK_STATUS, IncidentAction.STATUS),
        (re.compile(r"\b(rollback|revert)\b", re.I), VoiceIntent.ROLLBACK_DEPLOYMENT, IncidentAction.ROLLBACK),
        (re.compile(r"\b(test|run tests|lint)\b", re.I), VoiceIntent.RUN_TESTS, IncidentAction.TEST),
        (re.compile(r"\b(pull request|pr|merge)\b", re.I), VoiceIntent.CREATE_PR, IncidentAction.CREATE_PR),
    ]

    async def extract(
        self,
        transcript: str,
        *,
        history: list[ConversationTurn] | None = None,
        incident_context: dict | None = None,
    ) -> ExtractedIntent:
        intent = VoiceIntent.GENERAL_QUERY
        action = IncidentAction.UNKNOWN
        confidence = 0.6
        for pattern, matched_intent, matched_action in self.RULES:
            if pattern.search(transcript):
                intent = matched_intent
                action = matched_action
                confidence = 0.85
                break

        entities = _extract_entities(transcript, incident_context or {})
        result = ExtractedIntent(
            intent=intent,
            action=action,
            entities=entities,
            raw_summary=transcript.strip(),
            confidence=confidence,
        )
        spoken = infer_spoken_response(transcript, result)
        if spoken:
            result = result.model_copy(update={"spoken_response": spoken})
        return result


def _extract_entities(transcript: str, incident_context: dict) -> dict:
    entities: dict = dict(incident_context)
    service_match = re.search(r"\b(api|auth|payment|checkout|webhook)\b", transcript, re.I)
    if service_match:
        entities.setdefault("service", service_match.group(1).lower())
    env_match = re.search(r"\b(prod|production|staging|dev)\b", transcript, re.I)
    if env_match:
        entities["environment"] = env_match.group(1).lower()
    return entities


def get_intent_extractor(settings: Settings) -> IntentExtractor:
    if settings.llm_provider in {"openai", "anthropic", "openai_compatible"}:
        return OpenAIIntentExtractor(settings)
    if settings.llm_provider == "bedrock":
        return BedrockIntentExtractor(settings)
    return MockIntentExtractor()
