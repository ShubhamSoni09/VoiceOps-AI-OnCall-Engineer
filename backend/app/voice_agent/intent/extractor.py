import json
import re
from abc import ABC, abstractmethod

import boto3

from app.config import Settings
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


class ClaudeIntentExtractor(IntentExtractor):
    """Extract intent using Anthropic Claude (direct API)."""

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=claude")
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.claude_model

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

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=INTENT_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.2,
        )
        content = message.content[0].text if message.content else "{}"
        return _parse_intent_payload(_extract_json(content), transcript=transcript)


class OpenAIIntentExtractor(IntentExtractor):
    """Extract intent using OpenAI Chat Completions."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

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

        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": INTENT_EXTRACTION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        return _parse_intent_payload(_extract_json(content), transcript=transcript)


class BedrockIntentExtractor(IntentExtractor):
    """Extract intent using AWS Bedrock (Claude)."""

    def __init__(self, settings: Settings) -> None:
        self._client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
        self._model_id = settings.bedrock_model_id

    async def extract(
        self,
        transcript: str,
        *,
        history: list[ConversationTurn] | None = None,
        incident_context: dict | None = None,
    ) -> ExtractedIntent:
        import asyncio

        history_text = "\n".join(f"{t.role}: {t.content}" for t in (history or [])) or "(none)"
        incident_text = json.dumps(incident_context or {}, indent=2)

        user_prompt = INTENT_EXTRACTION_USER.format(
            history=history_text,
            transcript=transcript,
            incident_context=incident_text,
        )

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": INTENT_EXTRACTION_SYSTEM,
            "messages": [{"role": "user", "content": user_prompt}],
        }

        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self._model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        content = result["content"][0]["text"]
        return _parse_intent_payload(_extract_json(content), transcript=transcript)


class MockIntentExtractor(IntentExtractor):
    """Rule-based intent extractor for local dev without Bedrock."""

    RULES: list[tuple[re.Pattern[str], VoiceIntent, IncidentAction]] = [
        (re.compile(r"\b(fix|patch|resolve|repair)\b", re.I), VoiceIntent.FIX_ISSUE, IncidentAction.PATCH),
        (
            re.compile(
                r"\b(why|investigate|what'?s wrong|what is the (issue|problem)|tell me about|explain the|describe the|"
                r"failing|down|error|incident|issue|problem|bug|root cause)\b",
                re.I,
            ),
            VoiceIntent.INVESTIGATE_INCIDENT,
            IncidentAction.INVESTIGATE,
        ),
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
        if intent == VoiceIntent.GENERAL_QUERY and incident_context:
            lower = transcript.lower()
            has_incident = bool(incident_context.get("incident_id") or incident_context.get("title"))
            asks_about_issue = bool(
                re.search(r"\b(issue|incident|problem|what|tell|explain|describe|wrong|broken)\b", lower)
            )
            if has_incident and asks_about_issue:
                intent = VoiceIntent.INVESTIGATE_INCIDENT
                action = IncidentAction.INVESTIGATE
                confidence = 0.8

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
    if settings.llm_provider == "claude" and settings.anthropic_api_key:
        return ClaudeIntentExtractor(settings)
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIIntentExtractor(settings)
    if settings.llm_provider == "bedrock":
        return BedrockIntentExtractor(settings)
    return MockIntentExtractor()
