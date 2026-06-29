import re
from dataclasses import dataclass

from app.collab.events import RoomEventHub
from app.collab.models import CommandRouteResponse
from app.collab.service import CollaborationService, looks_like_audit_question, looks_like_memory_question
from app.config import Settings
from app.rag.providers import EmbeddingProviderError
from app.voice_agent.models import IncidentAction, VoiceProcessResponse
from app.workspace.service import WorkspaceCodeService
from app.workspace.tools import WorkspaceError


@dataclass(frozen=True)
class MeetingRouterAnswer:
    text: str
    metadata: dict


class MeetingCommandRouter:
    """Routes meeting questions before they become workspace-changing actions."""

    def __init__(
        self,
        *,
        collab: CollaborationService,
        settings: Settings,
        events: RoomEventHub | None = None,
    ) -> None:
        self._collab = collab
        self._settings = settings
        self._events = events

    def handle_processed_result(
        self,
        result: VoiceProcessResponse,
        room_id: str,
        *,
        memory_question_mode: str = "narrow",
    ) -> dict | None:
        route_trace = self.trace_prompt(
            room_id,
            result.transcript,
            memory_question_mode=memory_question_mode,
        ).model_dump(mode="json")
        demo_gate_metadata = self._demo_gate_metadata(result, room_id)
        if demo_gate_metadata:
            return {**demo_gate_metadata, "route_trace": route_trace}
        if result.command.action == IncidentAction.SUMMARIZE_CHANGES:
            answer = self._collab.summarize_changes(room_id, result.transcript)
            self._apply_answer(result, answer)
            return {"source": "change_summary", "question": result.transcript, "route_trace": route_trace}
        read_only = self.answer_read_only_prompt(
            room_id,
            result.transcript,
            memory_question_mode=memory_question_mode,
        )
        if read_only:
            self._apply_answer(result, read_only.text)
            return {**read_only.metadata, "route_trace": route_trace}
        code_metadata = self._maybe_enrich_code_query(result)
        if code_metadata:
            return {**code_metadata, "route_trace": route_trace}
        if result.orchestrator_result:
            result.orchestrator_result.approval = {
                **(result.orchestrator_result.approval or {}),
                "route_trace": route_trace,
            }
        return {"route_trace": route_trace}

    def answer_read_only_prompt(
        self,
        room_id: str,
        prompt: str,
        *,
        memory_question_mode: str = "broad",
    ) -> MeetingRouterAnswer | None:
        if looks_like_change_summary(prompt):
            return MeetingRouterAnswer(
                text=self._collab.summarize_changes(room_id, prompt),
                metadata={"source": "change_summary", "question": prompt},
            )
        if looks_like_audit_question(prompt):
            answer = self._collab.query_audit(room_id, prompt)
            return MeetingRouterAnswer(
                text=answer.answer,
                metadata={
                    "source": "audit_query",
                    "question": prompt,
                    "matched_items": [item.id for item in answer.items],
                    "mode": answer.mode,
                },
            )
        if self._looks_like_memory_question(prompt, mode=memory_question_mode):
            try:
                answer = self._collab.query_rag(room_id, prompt, self._settings)
            except EmbeddingProviderError as exc:
                return MeetingRouterAnswer(
                    text=f"RAG memory is unavailable: {exc}",
                    metadata={
                        "source": "rag_query",
                        "question": prompt,
                        "matched_items": [],
                        "citations": [],
                        "mode": "degraded",
                        "error": str(exc),
                    },
                )
            return MeetingRouterAnswer(
                text=answer.answer,
                metadata={
                    "source": "rag_query",
                    "question": prompt,
                    "matched_items": [item.source_id for item in answer.citations],
                    "citations": [item.model_dump(mode="json") for item in answer.citations],
                    "mode": answer.mode,
                    "retrieval": answer.retrieval,
                },
            )
        return None

    def trace_prompt(
        self,
        room_id: str,
        prompt: str,
        *,
        memory_question_mode: str = "broad",
    ) -> CommandRouteResponse:
        text = prompt.strip()
        if looks_like_change_summary(text):
            return CommandRouteResponse(
                route="change_summary",
                source="change_summary",
                reason="The prompt asks for a recap of teammate changes.",
                read_only=True,
                action_policy="no_workspace_change",
                metadata={"question": text},
            )
        if looks_like_audit_question(text):
            answer = self._collab.query_audit(room_id, text)
            return CommandRouteResponse(
                route="audit_query",
                source="audit_query",
                reason="The prompt asks about approvals, branches, gates, speaker mapping, or audit history.",
                read_only=True,
                action_policy="no_workspace_change",
                matched_item_ids=[item.id for item in answer.items],
                metadata={"mode": answer.mode, "answer": answer.answer},
            )
        if self._looks_like_memory_question(text, mode=memory_question_mode):
            try:
                answer = self._collab.query_rag(room_id, text, self._settings)
            except EmbeddingProviderError as exc:
                return CommandRouteResponse(
                    route="rag_query",
                    source="rag_query",
                    reason="The prompt asks about meeting/project memory, but RAG retrieval is unavailable.",
                    read_only=True,
                    action_policy="no_workspace_change",
                    matched_item_ids=[],
                    metadata={
                        "mode": "degraded",
                        "answer": f"RAG memory is unavailable: {exc}",
                        "error": str(exc),
                    },
                )
            return CommandRouteResponse(
                route="rag_query",
                source="rag_query",
                reason="The prompt asks about meeting/project memory and should retrieve cited context before answering.",
                read_only=True,
                action_policy="no_workspace_change",
                matched_item_ids=[item.source_id for item in answer.citations],
                metadata={"mode": answer.mode, "answer": answer.answer, "retrieval": answer.retrieval},
            )
        if looks_like_code_change_request(text):
            return CommandRouteResponse(
                route="agent_pipeline",
                source="agent_pipeline",
                reason="The prompt appears to request a code change and must go through preview-first approval.",
                read_only=False,
                action_policy="approval_required_before_workspace_write",
                confidence=0.75,
            )
        if looks_like_code_query(text):
            return CommandRouteResponse(
                route="code_query",
                source="code_query",
                reason="The prompt asks about code, files, functions, routes, or endpoints.",
                read_only=True,
                action_policy="no_workspace_change",
                metadata={"workspace_configured": bool(self._settings.voiceops_workspace)},
            )
        return CommandRouteResponse(
            route="agent_pipeline",
            source="agent_pipeline",
            reason="No deterministic read-only meeting route matched; the agent pipeline should interpret it.",
            read_only=False,
            action_policy="approval_required_if_code_changing",
            confidence=0.55,
        )

    def _demo_gate_metadata(self, result: VoiceProcessResponse, room_id: str) -> dict | None:
        approval = result.orchestrator_result.approval if result.orchestrator_result else {}
        if approval.get("source") != "demo_gate_run":
            return None
        try:
            from app.system.router import register_demo_gate_completion

            register_demo_gate_completion(
                approval.get("job_id"),
                room_id=room_id,
                collab=self._collab,
                events=self._events,
            )
        except Exception:
            pass
        return {
            "source": "demo_gate_run",
            "gate_id": approval.get("gate_id"),
            "gate_label": approval.get("gate_label"),
            "gate_job_id": approval.get("job_id"),
            "gate_status": approval.get("status"),
            "poll_url": approval.get("poll_url"),
            "error": approval.get("error"),
        }

    def _maybe_enrich_code_query(self, result: VoiceProcessResponse) -> dict | None:
        if result.orchestrator_result is not None:
            return None
        if result.command.action.value != "unknown":
            return None
        if not looks_like_code_query(result.transcript):
            return None
        try:
            answer = WorkspaceCodeService(self._settings).query(result.transcript, limit=8)
        except WorkspaceError:
            return None
        if not answer.references:
            return None
        references = [item.model_dump(mode="json") for item in answer.references]
        self._apply_answer(result, answer.answer, clear_orchestrator=False)
        return {
            "source": "code_query",
            "references": references,
            "mode": answer.mode,
        }

    def _looks_like_memory_question(self, text: str, *, mode: str) -> bool:
        if mode == "broad":
            return looks_like_memory_question(text)
        return looks_like_room_memory_question(text)

    @staticmethod
    def _apply_answer(
        result: VoiceProcessResponse,
        answer: str,
        *,
        clear_orchestrator: bool = True,
    ) -> None:
        result.response_text = answer
        result.speech = result.speech.model_copy(update={"text": answer})
        if clear_orchestrator:
            result.orchestrator_result = None


def looks_like_change_summary(text: str) -> bool:
    lower = text.lower()
    return (
        ("summarize" in lower or "recap" in lower or "总结" in text)
        and ("change" in lower or "diff" in lower or "work" in lower or "改" in text)
    ) or "what changes" in lower or bool(re.search(r"\bwhat did .+ (?:change|do|work on)\b", lower))


def looks_like_code_query(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            "code",
            "file",
            "function",
            "class",
            "component",
            "module",
            "middleware",
            "route",
            "endpoint",
        )
    )


def looks_like_code_change_request(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "fix",
            "patch",
            "change",
            "update",
            "edit",
            "modify",
            "implement",
            "add endpoint",
            "add route",
            "write code",
            "修",
            "改",
        )
    )


def looks_like_room_memory_question(text: str) -> bool:
    lower = text.lower()
    english_room_memory_markers = (
        "decide",
        "decision",
        "open task",
        "open tasks",
        "open question",
        "open questions",
        "still open",
        "remaining",
        "todo",
        "to do",
        "risk",
        "blocker",
        "blockers",
        "mentioned",
        "meeting memory",
        "what files",
        "which files",
    )
    chinese_room_memory_markers = (
        "刚刚决定",
        "刚才决定",
        "决定了什么",
        "做了什么决定",
        "有什么决定",
        "哪些决定",
        "会议决定",
        "还有哪些任务",
        "还有什么任务",
        "哪些任务",
        "待办",
        "未完成",
        "待处理",
        "还有哪些问题",
        "没解决的问题",
        "哪些风险",
        "有什么风险",
        "阻塞",
        "提到哪些文件",
        "提到了哪些文件",
        "哪些文件",
        "提到哪些代码",
        "提到了哪些代码",
        "哪些代码",
    )
    return any(
        marker in lower
        for marker in english_room_memory_markers
    ) or any(marker in text for marker in chinese_room_memory_markers)
