import pytest

from app.agents.reasoning import AgentReasoningService
from app.config import Settings
from app.llm import LLMRequest, LLMResponse


@pytest.mark.asyncio
async def test_read_only_reasoning_reads_explicit_file_without_patch(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
    service = AgentReasoningService(
        Settings(llm_provider="mock", voiceops_workspace=str(workspace))
    )

    answer = await service.answer_read_only_code(prompt="explain app.py")

    assert "app.py: read 1 line" in answer.text
    assert answer.metadata["reasoning_kind"] == "read_only_code"
    assert answer.metadata["files_read"] == ["app.py"]
    assert answer.metadata["provider"] == "deterministic"


@pytest.mark.asyncio
async def test_reasoning_uses_user_configured_llm_runtime():
    runtime = _FakeRuntime(LLMResponse(content="LLM teammate answer", provider="test", model="model-x"))
    service = AgentReasoningService(
        Settings(llm_provider="openai", openai_api_key="test-key"),
        runtime=runtime,
    )

    answer = await service.answer_read_only_code(prompt="explain app.py")

    assert answer.text == "LLM teammate answer"
    assert answer.metadata["provider"] == "test"
    assert answer.metadata["model"] == "model-x"
    assert answer.metadata["fallback"] is False
    assert runtime.requests[0].purpose == "agent_read_only_code"
    assert runtime.requests[0].response_format == "text"


class _FakeRuntime:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response
