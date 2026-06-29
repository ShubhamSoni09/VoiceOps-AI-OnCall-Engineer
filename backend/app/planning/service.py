from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.config import Settings, get_settings
from app.llm import LLMMessage, LLMRequest
from app.llm.service import LLMRuntime, get_llm_runtime
from app.planning.models import AgentPlan, PlanningStep


PLANNER_SYSTEM_PROMPT = """You are the VoiceOps planning layer.
Return JSON only. Plan roles and tool policy, but never execute tools.
VoiceOps owns memory, tool access, approval policy, and audit logging.
Code-changing steps must use approval_required.
"""


class PlanningService:
    def __init__(self, settings: Settings | None = None, runtime: LLMRuntime | None = None) -> None:
        self._settings = settings or get_settings()
        self._runtime = runtime or get_llm_runtime(self._settings)

    async def build_plan(
        self,
        *,
        prompt: str,
        route: str,
        context: dict[str, Any] | None = None,
    ) -> AgentPlan:
        if self._settings.llm_provider == "mock":
            return deterministic_plan(prompt=prompt, route=route, context=context)
        try:
            response = await self._runtime.generate(
                LLMRequest(
                    purpose="agent_planning",
                    response_format="json",
                    temperature=0.1,
                    max_tokens=1200,
                    metadata={"route": route},
                    messages=[
                        LLMMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=_planner_user_prompt(prompt, route, context or {})),
                    ],
                )
            )
            payload = _extract_json(response.content)
            plan = AgentPlan.model_validate(payload)
            return plan.model_copy(
                update={
                    "provider": response.provider,
                    "model": response.model,
                    "metadata": {
                        **plan.metadata,
                        "llm_usage": response.usage,
                        "fallback": False,
                    },
                }
            )
        except (json.JSONDecodeError, ValidationError, ValueError):
            plan = deterministic_plan(prompt=prompt, route=route, context=context)
            return plan.model_copy(update={"metadata": {**plan.metadata, "fallback": True}})


def deterministic_plan(
    *,
    prompt: str,
    route: str,
    context: dict[str, Any] | None = None,
) -> AgentPlan:
    if route == "meeting_patch_closure":
        steps = [
            PlanningStep(role="meeting", title="Resolve referenced meeting task", detail="Find the task implied by the latest request.", tool_policy="read_only"),
            PlanningStep(role="memory", title="Attach project memory", detail="Use room memory and recent decisions as context.", tool_policy="read_only"),
            PlanningStep(role="code", title="Prepare patch proposal", detail="Generate proposed file content and unified diff without writing workspace files.", tool_policy="approval_required", requires_approval=True),
            PlanningStep(role="review", title="Review proposal", detail="Check the diff, files, and handoff context before human approval.", tool_policy="read_only"),
            PlanningStep(role="test", title="Validate proposal", detail="Run pre-approval validation in an isolated temporary workspace.", tool_policy="read_only"),
            PlanningStep(role="git", title="Defer branch work", detail="Create or switch branch only after a teammate approves the patch.", tool_policy="approval_required", requires_approval=True),
        ]
        summary = "Plan a meeting task closure with approval-first patching."
    elif route == "meeting_memory":
        steps = [
            PlanningStep(role="memory", title="Retrieve cited memory", detail="Query short memory, long memory, and local RAG citations.", tool_policy="read_only"),
            PlanningStep(role="coordinator", title="Answer without workspace mutation", detail="Return a concise answer with uncertainty and citations.", tool_policy="none"),
        ]
        summary = "Plan a read-only meeting memory answer."
    elif route == "read_only_code":
        steps = [
            PlanningStep(role="code", title="Inspect code context", detail="Read relevant files and explain or diagnose without writing.", tool_policy="read_only"),
            PlanningStep(role="review", title="Confirm no approval needed", detail="Keep the route read-only unless the user explicitly asks for a fix.", tool_policy="none"),
        ]
        summary = "Plan a read-only code response."
    else:
        steps = [
            PlanningStep(role="coordinator", title="Classify teammate request", detail="No specialist action is required unless the user asks for memory, code, tests, or patch work.", tool_policy="none")
        ]
        summary = "Plan a general teammate response."
    return AgentPlan(
        route=route,
        summary=summary,
        steps=steps,
        provider="deterministic",
        model="local-rules",
        metadata={
            "prompt_preview": prompt[:160],
            "context_keys": sorted((context or {}).keys()),
            "approval_required": any(step.requires_approval for step in steps),
        },
    )


def _planner_user_prompt(prompt: str, route: str, context: dict[str, Any]) -> str:
    return json.dumps(
        {
            "prompt": prompt,
            "route": route,
            "context": context,
            "required_schema": {
                "route": "string",
                "summary": "string",
                "steps": [
                    {
                        "role": "coordinator|meeting|memory|code|review|test|git",
                        "title": "string",
                        "detail": "string",
                        "tool_policy": "none|read_only|approval_required",
                        "requires_approval": "boolean",
                    }
                ],
                "metadata": {"approval_required": "boolean"},
            },
        },
        indent=2,
    )


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)
