import pytest

from app.config import Settings
from app.llm import LLMRequest, LLMResponse
from app.planning.service import PlanningService


@pytest.mark.asyncio
async def test_deterministic_plan_marks_patch_closure_approval_required():
    service = PlanningService(Settings(llm_provider="mock"))

    plan = await service.build_plan(
        prompt="AI fix that",
        route="meeting_patch_closure",
        context={"source": "meeting"},
    )

    assert plan.provider == "deterministic"
    assert plan.model == "local-rules"
    assert plan.metadata["approval_required"] is True
    assert [step.role for step in plan.steps] == ["meeting", "memory", "code", "review", "test", "git"]
    assert [step.role for step in plan.steps if step.requires_approval] == ["code", "git"]
    assert {step.tool_policy for step in plan.steps if step.requires_approval} == {"approval_required"}


@pytest.mark.asyncio
async def test_llm_plan_uses_runtime_json_contract():
    runtime = _FakeRuntime(
        LLMResponse(
            content="""
            {
              "route": "meeting_memory",
              "summary": "Answer from memory only.",
              "steps": [
                {
                  "role": "memory",
                  "title": "Retrieve memory",
                  "detail": "Find cited meeting items.",
                  "tool_policy": "read_only",
                  "requires_approval": false
                }
              ],
              "metadata": {"approval_required": false}
            }
            """,
            provider="test-provider",
            model="planner-model",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
        )
    )
    service = PlanningService(Settings(llm_provider="openai", openai_api_key="test-key"), runtime=runtime)

    plan = await service.build_plan(prompt="what did we decide?", route="meeting_memory")

    assert plan.provider == "test-provider"
    assert plan.model == "planner-model"
    assert plan.summary == "Answer from memory only."
    assert plan.metadata["fallback"] is False
    assert plan.metadata["llm_usage"] == {"prompt_tokens": 10, "completion_tokens": 20}
    assert runtime.requests[0].purpose == "agent_planning"
    assert runtime.requests[0].response_format == "json"


@pytest.mark.asyncio
async def test_llm_plan_falls_back_to_deterministic_on_invalid_json():
    runtime = _FakeRuntime(LLMResponse(content="not json", provider="test-provider", model="planner-model"))
    service = PlanningService(Settings(llm_provider="openai", openai_api_key="test-key"), runtime=runtime)

    plan = await service.build_plan(prompt="AI fix that", route="meeting_patch_closure")

    assert plan.provider == "deterministic"
    assert plan.model == "local-rules"
    assert plan.metadata["fallback"] is True
    assert plan.metadata["approval_required"] is True


class _FakeRuntime:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return self.response
