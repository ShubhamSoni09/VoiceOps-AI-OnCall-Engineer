import pytest

from app.config import Settings
from app.llm import LLMMessage, LLMRequest, LLMResponse
from app.llm.providers import LLMProviderError, get_llm_provider
from app.llm.service import LLMRuntime
from app.voice_agent.intent.extractor import LLMIntentExtractor, get_intent_extractor
from app.voice_agent.models import IncidentAction, VoiceIntent


@pytest.mark.asyncio
async def test_mock_llm_runtime_generates_deterministic_json():
    runtime = LLMRuntime(get_llm_provider(Settings(llm_provider="mock")))

    response = await runtime.generate(
        LLMRequest(
            purpose="intent_extraction",
            response_format="json",
            messages=[LLMMessage(role="user", content="What did Alice decide?")],
        )
    )

    assert response.provider == "mock"
    assert response.model == "mock-deterministic"
    assert '"intent": "general_query"' in response.content
    assert response.metadata["deterministic"] is True


@pytest.mark.asyncio
async def test_llm_intent_extractor_uses_runtime_contract():
    runtime = _FakeRuntime(
        LLMResponse(
            content='{"intent":"fix_issue","action":"patch","entities":{"file":"app.py"},"raw_summary":"fix app.py","confidence":0.91}',
            provider="test",
            model="test-model",
        )
    )
    extractor = LLMIntentExtractor(Settings(llm_provider="openai", openai_api_key="test-key"), runtime=runtime)

    result = await extractor.extract("fix app.py", history=[], incident_context={"service": "api"})

    assert result.intent == VoiceIntent.FIX_ISSUE
    assert result.action == IncidentAction.PATCH
    assert result.entities["file"] == "app.py"
    assert runtime.requests[0].purpose == "intent_extraction"
    assert runtime.requests[0].response_format == "json"
    assert runtime.requests[0].messages[0].role == "system"


def test_get_intent_extractor_keeps_mock_rule_based_path():
    extractor = get_intent_extractor(Settings(llm_provider="mock"))

    assert extractor.__class__.__name__ == "MockIntentExtractor"


def test_get_intent_extractor_accepts_anthropic_runtime_path():
    extractor = get_intent_extractor(Settings(llm_provider="anthropic", anthropic_api_key="sk-ant-test"))

    assert extractor.__class__.__name__ == "OpenAIIntentExtractor"


def test_get_llm_provider_requires_anthropic_key():
    with pytest.raises(LLMProviderError, match="ANTHROPIC_API_KEY is required"):
        get_llm_provider(Settings(llm_provider="anthropic", anthropic_api_key=None))


def test_get_llm_provider_rejects_unknown_provider():
    with pytest.raises(LLMProviderError, match="Unsupported LLM provider"):
        get_llm_provider(Settings(llm_provider="unknown"))


class _FakeRuntime:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response
