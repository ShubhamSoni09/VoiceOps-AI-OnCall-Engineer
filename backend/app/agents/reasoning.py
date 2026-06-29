from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.llm import LLMMessage, LLMRequest
from app.llm.service import LLMRuntime, get_llm_runtime
from app.workspace.tools import WorkspaceError, read_file


REASONING_SYSTEM_PROMPT = """You are an AI teammate inside VoiceOps.
You can explain, summarize, and reason from provided context.
You cannot execute tools, mutate files, approve actions, create branches, or invent hidden evidence.
When context is thin, say what is missing.
Keep answers concise and operational.
"""


@dataclass(frozen=True)
class AgentReasoningAnswer:
    text: str
    metadata: dict[str, Any]


class AgentReasoningService:
    def __init__(self, settings: Settings | None = None, runtime: LLMRuntime | None = None) -> None:
        self._settings = settings or get_settings()
        self._runtime = runtime or get_llm_runtime(self._settings)

    async def answer_memory(self, *, question: str, rag_answer: Any) -> AgentReasoningAnswer:
        fallback = getattr(rag_answer, "answer", "") or "No matching memory was found."
        citations = [citation.model_dump(mode="json") for citation in getattr(rag_answer, "citations", [])[:6]]
        metadata = {
            "source": "agent_reasoning",
            "reasoning_kind": "memory",
            "citation_count": len(citations),
        }
        if self._settings.llm_provider == "mock":
            return AgentReasoningAnswer(
                text=fallback,
                metadata={**metadata, "provider": "deterministic", "model": "local-rules", "fallback": False},
            )
        return await self._generate(
            purpose="agent_memory_answer",
            fallback=fallback,
            metadata=metadata,
            payload={
                "question": question,
                "deterministic_answer": fallback,
                "citations": citations,
                "instruction": "Rewrite only if helpful. Preserve uncertainty and cite only provided evidence.",
            },
        )

    async def answer_read_only_code(self, *, prompt: str) -> AgentReasoningAnswer:
        code_context = _read_prompt_file_context(prompt, self._settings)
        fallback = _deterministic_code_answer(prompt, code_context)
        metadata = {
            "source": "agent_reasoning",
            "reasoning_kind": "read_only_code",
            "files_read": [item["path"] for item in code_context],
        }
        if self._settings.llm_provider == "mock":
            return AgentReasoningAnswer(
                text=fallback,
                metadata={**metadata, "provider": "deterministic", "model": "local-rules", "fallback": False},
            )
        return await self._generate(
            purpose="agent_read_only_code",
            fallback=fallback,
            metadata=metadata,
            payload={
                "prompt": prompt,
                "code_context": code_context,
                "instruction": "Answer as a read-only code teammate. Do not propose file writes unless explicitly requested.",
            },
        )

    async def _generate(
        self,
        *,
        purpose: str,
        fallback: str,
        metadata: dict[str, Any],
        payload: dict[str, Any],
    ) -> AgentReasoningAnswer:
        try:
            response = await self._runtime.generate(
                LLMRequest(
                    purpose=purpose,
                    response_format="text",
                    temperature=0.2,
                    max_tokens=900,
                    metadata=metadata,
                    messages=[
                        LLMMessage(role="system", content=REASONING_SYSTEM_PROMPT),
                        LLMMessage(role="user", content=json.dumps(payload, indent=2)),
                    ],
                )
            )
            text = response.content.strip() or fallback
            return AgentReasoningAnswer(
                text=text,
                metadata={
                    **metadata,
                    "provider": response.provider,
                    "model": response.model,
                    "usage": response.usage,
                    "fallback": False,
                },
            )
        except Exception as exc:
            return AgentReasoningAnswer(
                text=fallback,
                metadata={
                    **metadata,
                    "provider": self._settings.llm_provider,
                    "fallback": True,
                    "error": exc.__class__.__name__,
                },
            )


def _read_prompt_file_context(prompt: str, settings: Settings) -> list[dict[str, str]]:
    paths = _extract_file_paths(prompt)
    context: list[dict[str, str]] = []
    for path in paths[:4]:
        try:
            content = read_file(path, configured=settings.voiceops_workspace)
        except WorkspaceError as exc:
            context.append({"path": path, "error": str(exc)})
            continue
        context.append({"path": path, "content": _truncate(content, 6000)})
    return context


def _extract_file_paths(prompt: str) -> list[str]:
    candidates = re.findall(r"[\w./-]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|css|html)", prompt)
    seen: set[str] = set()
    paths: list[str] = []
    for candidate in candidates:
        cleaned = candidate.strip("./")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            paths.append(cleaned)
    return paths


def _deterministic_code_answer(prompt: str, code_context: list[dict[str, str]]) -> str:
    if not code_context:
        return (
            "Read-only code route completed. I did not find an explicit file path in the prompt, "
            "so no workspace files were read and no patch was proposed."
        )
    lines = []
    for item in code_context:
        if "error" in item:
            lines.append(f"{item['path']}: {item['error']}")
        else:
            line_count = len(item["content"].splitlines())
            lines.append(f"{item['path']}: read {line_count} line(s) for read-only analysis.")
    return "Read-only code context: " + " ".join(lines)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"
