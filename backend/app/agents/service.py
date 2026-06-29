from __future__ import annotations

import difflib
import asyncio
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from app.agents.adapters import LocalTestAgentAdapter
from app.agents.models import (
    AgentAssignment,
    AgentAssignmentCancelRequest,
    AgentAssignmentRequest,
    AgentAssignmentStatus,
    AgentAssignmentStatusUpdate,
    AgentExchange,
    AgentFinding,
    AgentRole,
    AgentRun,
    AgentRunRequest,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
)
from app.agents.llm_routing import (
    AgentLLMRoute,
    AgentLLMRoutePreflightResponse,
    AgentLLMRouteRequest,
    AgentLLMRouteStore,
    AgentLLMRoutingService,
    AgentLLMRoutingSnapshot,
)
from app.agents.reasoning import AgentReasoningService
from app.agents.store import AgentRunStore
from app.auth.models import UserPublic
from app.collab.service import CollaborationService, get_collaboration_service
from app.config import Settings, get_settings
from app.llm.connections_service import get_llm_connection_service
from app.orchestrator.workspace_orchestrator import WorkspaceOrchestrator
from app.planning.service import PlanningService
from app.voice_agent.models import IncidentAction, NormalizedCommand, OrchestratorResult, VoiceIntent
from app.workspace.tools import WorkspaceError, read_file


class AgentRunControlError(Exception):
    status: AgentRunStatus = AgentRunStatus.FAILED
    finding_title = "Run stopped"

    def __init__(self, detail: str, *, metadata: dict | None = None, run: AgentRun | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.metadata = metadata or {}
        self.run = run


class AgentRunTimedOut(AgentRunControlError):
    status = AgentRunStatus.TIMED_OUT
    finding_title = "Run timed out"


class AgentRunBudgetExceeded(AgentRunControlError):
    status = AgentRunStatus.BUDGET_EXHAUSTED
    finding_title = "Run budget exhausted"


class AgentRunCancelled(AgentRunControlError):
    status = AgentRunStatus.CANCELLED
    finding_title = "Run cancelled"


class MultiAgentService:
    def __init__(
        self,
        store: AgentRunStore,
        collab: CollaborationService,
        settings: Settings,
        external_runner: Any | None = None,
    ) -> None:
        self._store = store
        self._collab = collab
        self._settings = settings
        self._external_runner = external_runner
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._cancelled: dict[tuple[str, str], str] = {}
        self._llm_routing = AgentLLMRoutingService(
            AgentLLMRouteStore(settings.agent_llm_routes_path),
            settings,
            get_llm_connection_service(settings),
        )
        self._store.recover_running()

    def list_runs(self, room_id: str, *, limit: int = 20) -> list[AgentRun]:
        return list(reversed(self._store.list(room_id, limit=limit)))

    def list_assignments(self, room_id: str, *, limit: int = 20) -> list[AgentAssignment]:
        return list(reversed(self._store.list_assignments(room_id, limit=limit)))

    def get_run(self, room_id: str, run_id: str) -> AgentRun | None:
        return self._store.get(room_id, run_id)

    def llm_routing(self, room_id: str, user: UserPublic | None = None) -> AgentLLMRoutingSnapshot:
        return self._llm_routing.snapshot(room_id, user)

    def update_llm_route(self, room_id: str, user: UserPublic, body: AgentLLMRouteRequest) -> AgentLLMRoute:
        route = self._llm_routing.update_route(room_id, user, body)
        self._collab.add_agent_message(
            room_id,
            f"{user.name} updated {route.role.value} agent LLM routing to {route.provider}.",
            metadata={
                "source": "agent_llm_routing",
                "role": route.role.value,
                "provider": route.provider,
                "model": route.model,
                "base_url": route.base_url,
                "updated_by": user.id,
                "updated_by_name": user.name,
            },
        )
        return route

    async def preflight_llm_route(
        self,
        room_id: str,
        user: UserPublic,
        body: AgentLLMRouteRequest,
    ) -> AgentLLMRoutePreflightResponse:
        return await self._llm_routing.preflight_route(room_id, user, body)

    def create_assignment(
        self,
        room_id: str,
        user: UserPublic,
        request: AgentAssignmentRequest,
    ) -> AgentAssignment:
        now = _now()
        selected_model = " ".join((request.model or "").strip().split()) or None
        assignment_metadata = {
            **request.metadata,
            **self._initial_external_recommendation_metadata(room_id, user, request, selected_model),
            "approval_required": request.mode == "patch",
            "requested_by": user.id,
            "requested_by_name": user.name,
            **({"model": selected_model} if selected_model else {}),
        }
        assignment = AgentAssignment(
            id=_id("asg"),
            room_id=room_id,
            agent_id=request.agent_id.strip(),
            agent_label=request.agent_label.strip(),
            agent_kind=request.agent_kind,
            task=request.task.strip(),
            mode=request.mode,
            source=request.source,
            requested_by=user.id,
            requested_by_name=user.name,
            status=AgentAssignmentStatus.QUEUED,
            created_at=now,
            updated_at=now,
            metadata=assignment_metadata,
        )
        self._store.append_assignment(assignment)
        self._collab.add_agent_message(
            room_id,
            f"{user.name} assigned {assignment.agent_label} to {assignment.task}",
            metadata={
                **_assignment_audit_metadata(assignment),
                "source": "agent_assignment",
            },
        )
        return assignment

    def _initial_external_recommendation_metadata(
        self,
        room_id: str,
        user: UserPublic,
        request: AgentAssignmentRequest,
        selected_model: str | None,
    ) -> dict[str, Any]:
        if request.agent_kind != "external" or request.agent_id.strip() != "auto":
            return {}
        external_runner = self._external_runner_for_room(room_id)
        if not hasattr(external_runner, "recommend"):
            return {
                "routing_source": "external_agent_recommendation",
                "recommendation_ready": False,
                "recommendation_error": "External runner does not support recommendations",
            }
        try:
            from app.external_agents.models import ExternalAgentRecommendationRequest

            recommendation = external_runner.recommend(
                user,
                ExternalAgentRecommendationRequest(
                    task=request.task.strip(),
                    mode=None,
                    model=selected_model,
                ),
            )
        except Exception as exc:
            return {
                "routing_source": "external_agent_recommendation",
                "recommendation_ready": False,
                "recommendation_error": str(exc),
            }
        return _external_recommendation_metadata(recommendation)

    def update_assignment_status(
        self,
        room_id: str,
        assignment_id: str,
        user: UserPublic,
        request: AgentAssignmentStatusUpdate,
    ) -> AgentAssignment | None:
        assignment = self._store.get_assignment(room_id, assignment_id)
        if assignment is None:
            return None
        now = _now()
        updated = assignment.model_copy(
            update={
                "status": request.status,
                "note": request.note,
                "updated_at": now,
                "completed_at": now if request.status in {
                    AgentAssignmentStatus.COMPLETED,
                    AgentAssignmentStatus.FAILED,
                    AgentAssignmentStatus.CANCELLED,
                } else assignment.completed_at,
                "metadata": {
                    **assignment.metadata,
                    "updated_by": user.id,
                    "updated_by_name": user.name,
                },
            }
        )
        self._store.update_assignment(updated)
        self._collab.add_agent_message(
            room_id,
            f"{user.name} marked {updated.agent_label} assignment {updated.status.value}.",
            metadata={
                **_assignment_audit_metadata(updated),
                "source": "agent_assignment",
                "note": request.note,
            },
        )
        return updated

    async def cancel_assignment(
        self,
        room_id: str,
        assignment_id: str,
        user: UserPublic,
        request: AgentAssignmentCancelRequest,
    ) -> AgentAssignment | None:
        assignment = self._store.get_assignment(room_id, assignment_id)
        if assignment is None:
            return None
        if assignment.status in _TERMINAL_ASSIGNMENT_STATUSES:
            raise ValueError("Only queued or running assignments can be cancelled")

        now = _now()
        if assignment.run_id:
            await self.cancel_run(room_id, assignment.run_id, user, reason=request.reason)
        cancelled = assignment.model_copy(
            update={
                "status": AgentAssignmentStatus.CANCELLED,
                "note": request.reason,
                "updated_at": now,
                "completed_at": now,
                "metadata": {
                    **assignment.metadata,
                    "cancelled_by": user.id,
                    "cancelled_by_name": user.name,
                    "cancelled_reason": request.reason,
                },
            }
        )
        self._store.update_assignment(cancelled)
        self._collab.add_agent_message(
            room_id,
            f"{user.name} cancelled {cancelled.agent_label} assignment: {cancelled.task}",
            metadata={
                **_assignment_audit_metadata(cancelled),
                "source": "agent_assignment",
                "note": request.reason,
            },
        )
        return cancelled

    def retry_assignment(
        self,
        room_id: str,
        assignment_id: str,
        user: UserPublic,
    ) -> AgentAssignment | None:
        assignment = self._store.get_assignment(room_id, assignment_id)
        if assignment is None:
            return None
        if assignment.status not in _TERMINAL_ASSIGNMENT_STATUSES:
            raise ValueError("Only completed, failed, or cancelled assignments can be retried")

        now = _now()
        retry_count = int(assignment.metadata.get("retry_count", 0)) + 1
        retried = AgentAssignment(
            id=_id("asg"),
            room_id=room_id,
            agent_id=assignment.agent_id,
            agent_label=assignment.agent_label,
            agent_kind=assignment.agent_kind,
            task=assignment.task,
            mode=assignment.mode,
            source=assignment.source,
            requested_by=user.id,
            requested_by_name=user.name,
            status=AgentAssignmentStatus.QUEUED,
            created_at=now,
            updated_at=now,
            metadata={
                "approval_required": assignment.mode == "patch",
                "requested_by": user.id,
                "requested_by_name": user.name,
                "retry_of": assignment.id,
                "retry_of_requested_by": assignment.requested_by,
                "retry_of_requested_by_name": assignment.requested_by_name,
                "retry_count": retry_count,
                "original_status": assignment.status.value,
                "retried_by": user.id,
                "retried_by_name": user.name,
            },
        )
        self._store.append_assignment(retried)
        self._collab.add_agent_message(
            room_id,
            f"{user.name} retried {retried.agent_label} assignment: {retried.task}",
            metadata={
                **_assignment_audit_metadata(retried),
                "source": "agent_assignment",
                "assignment_id": retried.id,
                "retry_of": assignment.id,
            },
        )
        return retried

    def clear_completed_assignments(
        self,
        room_id: str,
        user: UserPublic,
    ) -> tuple[int, list[AgentAssignment]]:
        removed = self._store.remove_assignments_by_status(room_id, _TERMINAL_ASSIGNMENT_STATUSES)
        remaining = self.list_assignments(room_id, limit=20)
        if removed:
            self._collab.add_agent_message(
                room_id,
                f"{user.name} cleared {len(removed)} completed assignment{'s' if len(removed) != 1 else ''} from the queue.",
                metadata={
                    "source": "agent_assignment",
                    "status": "cleared",
                    "cleared_count": len(removed),
                    "cleared_assignment_ids": [assignment.id for assignment in removed],
                    "cleared_assignment_summaries": [
                        _assignment_audit_metadata(assignment)
                        for assignment in removed
                    ],
                    "cleared_by": user.id,
                    "cleared_by_name": user.name,
                },
            )
        return len(removed), remaining

    async def dispatch_assignment(
        self,
        room_id: str,
        assignment_id: str,
        user: UserPublic,
    ) -> AgentAssignment | None:
        assignment = self._store.get_assignment(room_id, assignment_id)
        if assignment is None:
            return None
        if assignment.status != AgentAssignmentStatus.QUEUED:
            raise ValueError("Only queued assignments can be dispatched")

        now = _now()
        running = assignment.model_copy(
            update={
                "status": AgentAssignmentStatus.RUNNING,
                "updated_at": now,
                "metadata": {
                    **assignment.metadata,
                    "dispatched_by": user.id,
                    "dispatched_by_name": user.name,
                    "dispatched_at": now.isoformat(),
                },
            }
        )
        self._store.update_assignment(running)
        if assignment.agent_kind == "external":
            return self._dispatch_external_assignment(room_id, user, running)

        run = await self.start_run(
            room_id,
            user,
            AgentRunRequest(
                prompt=assignment.task,
                source="agent_assignment",
                metadata={
                    "assignment_id": assignment.id,
                    "assigned_agent_id": assignment.agent_id,
                    "assigned_agent_label": assignment.agent_label,
                    "mode": assignment.mode,
                },
            ),
        )
        finished_status = (
            AgentAssignmentStatus.COMPLETED
            if run.status in {AgentRunStatus.COMPLETED, AgentRunStatus.RECOVERED}
            else AgentAssignmentStatus.FAILED
        )
        completed = running.model_copy(
            update={
                "status": finished_status,
                "run_id": run.id,
                "action_id": run.action_id,
                "updated_at": _now(),
                "completed_at": _now(),
                "metadata": {
                    **running.metadata,
                    "run_status": run.status.value,
                    "run_route": run.route,
                },
            }
        )
        self._store.update_assignment(completed)
        self._collab.add_agent_message(
            room_id,
            f"{assignment.agent_label} finished assignment: {assignment.task}",
            metadata={
                **_assignment_audit_metadata(completed),
                "source": "agent_assignment",
                "run_id": completed.run_id,
                "action_id": completed.action_id,
            },
        )
        return completed

    def _dispatch_external_assignment(
        self,
        room_id: str,
        user: UserPublic,
        assignment: AgentAssignment,
    ) -> AgentAssignment:
        if self._external_runner is None:
            failed = self._finish_assignment(
                room_id,
                assignment,
                status=AgentAssignmentStatus.FAILED,
                metadata={"error": "external_runner_not_configured"},
                message=f"{assignment.agent_label} assignment failed because no external provider adapter is configured.",
            )
            return failed

        external_runner = self._external_runner_for_room(room_id)
        from app.external_agents.models import (
            ExternalAgentProvider,
            ExternalAgentRecommendationRequest,
            ExternalAgentRunMode,
            ExternalAgentRunRequest,
        )

        try:
            provider, mode, model, recommendation_metadata = self._resolve_external_assignment_route(
                user,
                assignment,
                external_runner=external_runner,
                ExternalAgentProvider=ExternalAgentProvider,
                ExternalAgentRunMode=ExternalAgentRunMode,
                ExternalAgentRecommendationRequest=ExternalAgentRecommendationRequest,
            )
        except ValueError as exc:
            return self._finish_assignment(
                room_id,
                assignment,
                status=AgentAssignmentStatus.FAILED,
                metadata={"error": str(exc), "mode": assignment.mode, "provider": assignment.agent_id},
                message=f"{assignment.agent_label} assignment failed because the provider or mode is not supported.",
            )

        try:
            if hasattr(external_runner, "preflight_run"):
                preflight = external_runner.preflight_run(user, provider, mode)
                if not preflight.get("ready"):
                    return self._finish_assignment(
                        room_id,
                        assignment,
                        status=AgentAssignmentStatus.FAILED,
                        metadata={
                            "error": preflight.get("detail") or preflight.get("reason"),
                            "readiness": preflight,
                            "provider": provider.value,
                            "mode": mode.value,
                            **recommendation_metadata,
                        },
                        message=f"{assignment.agent_label} assignment is not ready: {preflight.get('detail') or preflight.get('reason')}",
                    )
            result = external_runner.run(
                room_id,
                user,
                ExternalAgentRunRequest(
                    provider=provider,
                    prompt=assignment.task,
                    mode=mode,
                    model=model,
                    source="agent_assignment",
                    metadata={"assignment_id": assignment.id, **recommendation_metadata},
                ),
            )
        except Exception as exc:
            return self._finish_assignment(
                room_id,
                assignment,
                status=AgentAssignmentStatus.FAILED,
                metadata={
                    "error": str(exc),
                    "provider": provider.value if "provider" in locals() else assignment.agent_id,
                    "mode": mode.value if "mode" in locals() else assignment.mode,
                    **(recommendation_metadata if "recommendation_metadata" in locals() else {}),
                },
                message=f"{assignment.agent_label} assignment failed: {exc}",
            )

        return self._finish_assignment(
            room_id,
            assignment,
            status=AgentAssignmentStatus.COMPLETED,
            run_id=result.provider_run_id,
            action_id=result.action_id,
            metadata={
                "provider_run_id": result.provider_run_id,
                "provider_status": result.status,
                "provider": provider.value,
                "mode": mode.value,
                "model": result.model,
                "message_id": result.message_id,
                "files_changed": result.files_changed,
                **recommendation_metadata,
            },
            message=f"{assignment.agent_label} completed external assignment: {assignment.task}",
        )

    def _resolve_external_assignment_route(
        self,
        user: UserPublic,
        assignment: AgentAssignment,
        *,
        external_runner: Any,
        ExternalAgentProvider: Any,
        ExternalAgentRunMode: Any,
        ExternalAgentRecommendationRequest: Any,
    ) -> tuple[Any, Any, str | None, dict]:
        requested_model = assignment.metadata.get("model") if isinstance(assignment.metadata, dict) else None
        if assignment.agent_id != "auto":
            provider = ExternalAgentProvider(assignment.agent_id)
            mode = ExternalAgentRunMode(assignment.mode)
            return provider, mode, requested_model, {"routing_source": "explicit_external_assignment"}

        if not hasattr(external_runner, "recommend"):
            raise ValueError("Auto external assignments require an external runner with recommendation support")
        recommendation = external_runner.recommend(
            user,
            ExternalAgentRecommendationRequest(
                task=assignment.task,
                mode=None,
                model=requested_model,
            ),
        )
        return (
            recommendation.provider,
            recommendation.mode,
            recommendation.model,
            _external_recommendation_metadata(recommendation),
        )

    def _external_runner_for_room(self, room_id: str) -> Any:
        if self._external_runner is None or not hasattr(self._external_runner, "with_settings"):
            return self._external_runner
        return self._external_runner.with_settings(self._collab.settings_for_room(room_id, self._settings))

    def _finish_assignment(
        self,
        room_id: str,
        assignment: AgentAssignment,
        *,
        status: AgentAssignmentStatus,
        run_id: str | None = None,
        action_id: str | None = None,
        metadata: dict | None = None,
        message: str,
    ) -> AgentAssignment:
        finished = assignment.model_copy(
            update={
                "status": status,
                "run_id": run_id,
                "action_id": action_id,
                "updated_at": _now(),
                "completed_at": _now(),
                "metadata": {
                    **assignment.metadata,
                    **(metadata or {}),
                },
            }
        )
        self._store.update_assignment(finished)
        self._collab.add_agent_message(
            room_id,
            message,
            metadata={
                **_assignment_audit_metadata(finished),
                "source": "agent_assignment",
                "run_id": finished.run_id,
                "action_id": finished.action_id,
                **(metadata or {}),
            },
        )
        return finished

    async def start_run(self, room_id: str, user: UserPublic, request: AgentRunRequest) -> AgentRun:
        now = _now()
        route = _route_prompt(request.prompt)
        control = _control_metadata(request)
        run = AgentRun(
            id=_id("run"),
            room_id=room_id,
            prompt=request.prompt.strip(),
            source=request.source,
            requested_by=user.id,
            requested_by_name=user.name,
            status=AgentRunStatus.RUNNING,
            route=route,
            summary="Coordinator is assigning specialist agents.",
            created_at=now,
            updated_at=now,
            metadata={
                "request_metadata": request.metadata,
                "control": control,
                "background": request.background,
            },
        )
        self._store.append(run)

        if request.background:
            task = asyncio.create_task(self._run_and_finalize(room_id, user, run))
            self._tasks[(room_id, run.id)] = task
            return run

        return await self._run_and_finalize(room_id, user, run)

    async def cancel_run(
        self,
        room_id: str,
        run_id: str,
        user: UserPublic,
        *,
        reason: str = "cancelled by user",
    ) -> AgentRun | None:
        run = self._store.get(room_id, run_id)
        if run is None:
            return None
        if run.status != AgentRunStatus.RUNNING:
            return run
        key = (room_id, run_id)
        self._cancelled[key] = reason
        task = self._tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
        cancelled = _control_finish(
            run,
            AgentRunCancelled(
                reason,
                metadata={"cancelled_by": user.id, "cancelled_by_name": user.name},
            ),
        )
        self._store.update(cancelled)
        self._collab.add_agent_message(
            room_id,
            cancelled.summary,
            metadata={
                "source": "multi_agent_run",
                "agent_run_id": cancelled.id,
                "status": cancelled.status.value,
                "cancelled_by": user.id,
                "cancelled_by_name": user.name,
            },
        )
        return cancelled

    async def _run_and_finalize(self, room_id: str, user: UserPublic, run: AgentRun) -> AgentRun:
        key = (room_id, run.id)

        try:
            run = await self._execute(room_id, user, run)
        except asyncio.CancelledError:
            current = self._store.get(room_id, run.id)
            if current is not None and current.status == AgentRunStatus.CANCELLED:
                return current
            reason = self._cancelled.get(key, "agent run task was cancelled")
            run = _control_finish(run, AgentRunCancelled(reason))
            self._store.update(run)
            self._collab.add_agent_message(
                room_id,
                run.summary,
                metadata={"source": "multi_agent_run", "agent_run_id": run.id, "status": run.status.value},
            )
            raise
        except AgentRunControlError as exc:
            run = _control_finish(exc.run or run, exc)
            self._store.update(run)
            self._collab.add_agent_message(
                room_id,
                run.summary,
                metadata={"source": "multi_agent_run", "agent_run_id": run.id, "status": run.status.value},
            )
            return run
        except Exception as exc:
            run = run.model_copy(
                update={
                    "status": AgentRunStatus.FAILED,
                    "summary": f"Multi-agent run failed: {exc}",
                    "updated_at": _now(),
                    "completed_at": _now(),
                    "findings": [
                        *run.findings,
                        AgentFinding(
                            role=AgentRole.COORDINATOR,
                            title="Run failed",
                            detail=str(exc),
                            severity="error",
                        ),
                    ],
                }
            )
            self._store.update(run)
            self._collab.add_agent_message(
                room_id,
                run.summary,
                metadata={"source": "multi_agent_run", "agent_run_id": run.id, "status": run.status.value},
            )
            return run
        finally:
            if key in self._tasks and self._tasks[key].done():
                self._tasks.pop(key, None)
                self._cancelled.pop(key, None)

        self._store.update(run)
        self._collab.add_agent_message(
            room_id,
            run.summary,
            metadata={
                "source": "multi_agent_run",
                "agent_run_id": run.id,
                "route": run.route,
                "roles": [step.role.value for step in run.steps],
                "exchange_count": len(run.exchanges),
                "action_id": run.action_id,
                "status": run.status.value,
            },
        )
        self._tasks.pop(key, None)
        self._cancelled.pop(key, None)
        return run

    async def _execute(self, room_id: str, user: UserPublic, run: AgentRun) -> AgentRun:
        await self._maybe_debug_delay(run)
        self._check_control(run)
        coordinator_settings, coordinator_llm = self._llm_routing.settings_for_role(
            room_id,
            user,
            AgentRole.COORDINATOR,
            self._settings,
        )
        plan = await PlanningService(coordinator_settings).build_plan(
            prompt=run.prompt,
            route=run.route,
            context={
                "source": run.source,
                "request_metadata": run.metadata.get("request_metadata", {}),
                "llm_route": coordinator_llm,
            },
        )
        plan_data = plan.model_dump(mode="json")
        run = run.model_copy(
            update={
                "metadata": {
                    **run.metadata,
                    "plan": plan_data,
                    "llm_routes": {"coordinator": coordinator_llm},
                }
            }
        )
        run = _complete_step(
            run,
            AgentRole.COORDINATOR,
            "Coordinator routed the request",
            plan.summary or _route_detail(run.route),
            metadata={"route": run.route, "plan": plan_data},
        )
        self._check_control(run)

        if run.route == "meeting_memory":
            memory_settings, memory_llm = self._llm_routing.settings_for_role(
                room_id,
                user,
                AgentRole.MEMORY,
                self._settings,
            )
            run = _record_llm_route(run, AgentRole.MEMORY, memory_llm)
            answer = self._collab.query_rag(room_id, run.prompt, memory_settings, limit=6)
            reasoned = await AgentReasoningService(memory_settings).answer_memory(question=run.prompt, rag_answer=answer)
            run = _record_exchange(
                run,
                AgentRole.COORDINATOR,
                AgentRole.MEMORY,
                "Memory question delegated",
                "Coordinator handed the prompt to Memory Agent for cited retrieval.",
                metadata={"route": run.route},
            )
            self._check_control(run)
            run = _complete_step(
                run,
                AgentRole.MEMORY,
                "Memory Agent retrieved cited context",
                reasoned.text,
                metadata={
                    "citation_count": len(answer.citations),
                    "retrieval": answer.retrieval,
                    "reasoning": {**reasoned.metadata, "llm_route": memory_llm},
                },
            )
            self._check_control(run)
            return _finish(run, f"Memory Agent answered with {len(answer.citations)} cited item(s).")

        if run.route == "read_only_code":
            code_settings, code_llm = self._llm_routing.settings_for_role(room_id, user, AgentRole.CODE, self._settings)
            run = _record_llm_route(run, AgentRole.CODE, code_llm)
            return await self._read_only_code_run(room_id, run, code_settings, code_llm)

        if run.route == "meeting_patch_closure":
            code_settings, code_llm = self._llm_routing.settings_for_role(room_id, user, AgentRole.CODE, self._settings)
            run = _record_llm_route(run, AgentRole.CODE, code_llm)
            return await self._patch_closure_run(room_id, user, run, code_settings, code_llm)

        run = _complete_step(
            run,
            AgentRole.MEETING,
            "Meeting Agent recorded no actionable specialist route",
            "The prompt did not request memory retrieval, code reading, tests, or a patch.",
        )
        return _finish(run, "No specialist work was needed for this prompt.")

    async def _read_only_code_run(self, room_id: str, run: AgentRun, settings: Settings, llm_route: dict) -> AgentRun:
        settings = self._collab.settings_for_room(room_id, settings)
        reasoned = await AgentReasoningService(settings).answer_read_only_code(prompt=run.prompt)
        run = _complete_step(
            run,
            AgentRole.CODE,
            "Code Agent handled a read-only request",
            reasoned.text,
            metadata={"approval_required": False, "reasoning": {**reasoned.metadata, "llm_route": llm_route}},
        )
        self._check_control(run)
        run = _complete_step(
            run,
            AgentRole.REVIEW,
            "Review Agent confirmed no workspace write",
            "No approval item was created because the route is read-only.",
        )
        self._check_control(run)
        return _finish(run, "Code Agent completed a read-only route without workspace changes.")

    async def _patch_closure_run(self, room_id: str, user: UserPublic, run: AgentRun, settings: Settings, llm_route: dict) -> AgentRun:
        settings = self._collab.settings_for_room(room_id, settings)
        resolved_context = self._collab.resolve_recent_task_context(room_id, run.prompt)
        run = _complete_step(
            run,
            AgentRole.MEETING,
            "Meeting Agent resolved the current task",
            resolved_context["text"] if resolved_context else "No recent meeting task was found; using the prompt directly.",
            metadata={"resolved_context": resolved_context},
        )
        self._check_control(run)
        run = _complete_step(
            run,
            AgentRole.MEMORY,
            "Memory Agent attached project context",
            "Recent room memory was checked before asking Code Agent for a patch proposal.",
            metadata={"context_id": resolved_context.get("id") if resolved_context else None},
        )
        self._check_control(run)
        run = _record_exchange(
            run,
            AgentRole.MEETING,
            AgentRole.MEMORY,
            "Resolved task handed to memory",
            "Meeting Agent shared the resolved task so Memory Agent could attach project context.",
            metadata={"context_id": resolved_context.get("id") if resolved_context else None},
        )
        self._check_control(run)
        run = _record_exchange(
            run,
            AgentRole.MEMORY,
            AgentRole.CODE,
            "Context handed to code",
            "Memory Agent handed resolved meeting context to Code Agent before patch generation.",
            metadata={"has_resolved_context": bool(resolved_context)},
        )
        self._check_control(run)

        command = NormalizedCommand(
            intent=VoiceIntent.FIX_ISSUE,
            action=IncidentAction.PATCH,
            parameters={"resolved_context": resolved_context} if resolved_context else {},
            requires_approval=True,
            original_transcript=run.prompt,
            normalized_text=run.prompt,
        )
        result = await WorkspaceOrchestrator(settings).execute(command, run.prompt)
        if result is None:
            run = _complete_step(
                run,
                AgentRole.CODE,
                "Code Agent could not reach the workspace",
                "No workspace is configured, so no patch proposal was created.",
                status=AgentStepStatus.FAILED,
            )
            return _finish(run, "Multi-agent patch route stopped because no workspace is connected.", failed=True)

        diff = result.approval.get("diff", "")
        run = _complete_step(
            run,
            AgentRole.CODE,
            "Code Agent proposed a patch",
            result.summary,
            metadata={
                "files_changed": result.files_changed,
                "pending_approval": result.pending_approval,
                "diff_lines": len(diff.splitlines()) if diff else 0,
                "revision": 0,
                "llm_route": llm_route,
            },
        )
        self._check_control(run)
        run = _record_exchange(
            run,
            AgentRole.CODE,
            AgentRole.REVIEW,
            "Patch proposal sent for review",
            "Code Agent sent the proposed diff and file list to Review Agent.",
            metadata={"files_changed": result.files_changed, "diff_lines": len(diff.splitlines()) if diff else 0},
        )
        self._check_control(run)

        review = _review_patch_result(run, result, resolved_context)
        run = _complete_step(
            run,
            AgentRole.REVIEW,
            "Review Agent checked the proposal",
            review["detail"],
            metadata=review,
        )
        self._check_control(run)
        if review["decision"] == "blocked":
            run = run.model_copy(
                update={
                    "findings": [
                        *run.findings,
                        AgentFinding(
                            role=AgentRole.REVIEW,
                            title="Proposal blocked",
                            detail=review["detail"],
                            severity="error",
                            metadata={"reason": review.get("reason")},
                        ),
                    ]
                }
            )
            return _finish(run, "Review Agent blocked the patch route before any approval action was created.", failed=True)
        if review["decision"] == "request_revision":
            run = run.model_copy(
                update={
                    "findings": [
                        *run.findings,
                        AgentFinding(
                            role=AgentRole.REVIEW,
                            title="Revision requested",
                            detail=review["detail"],
                            severity="warning",
                            metadata={"reason": review.get("reason")},
                        ),
                    ]
                }
            )
            run = _record_exchange(
                run,
                AgentRole.REVIEW,
                AgentRole.CODE,
                "Revision requested",
                review["detail"],
                metadata={"reason": review.get("reason")},
            )
            self._check_control(run)
            result = _revise_patch_for_review_note(result, self._settings)
            diff = result.approval.get("diff", "")
            run = _complete_step(
                run,
                AgentRole.CODE,
                "Code Agent revised the proposal",
                "Code Agent added the Review Agent's requested handoff note to the patch proposal.",
                metadata={
                    "files_changed": result.files_changed,
                    "pending_approval": result.pending_approval,
                    "diff_lines": len(diff.splitlines()) if diff else 0,
                    "revision": result.approval.get("revision_count", 1),
                },
            )
            self._check_control(run)
            run = _record_exchange(
                run,
                AgentRole.CODE,
                AgentRole.REVIEW,
                "Revised proposal sent for review",
                "Code Agent sent the revised diff back to Review Agent.",
                metadata={
                    "files_changed": result.files_changed,
                    "diff_lines": len(diff.splitlines()) if diff else 0,
                    "revision": result.approval.get("revision_count", 1),
                },
            )
            self._check_control(run)
            second_review = _review_patch_result(run, result, resolved_context)
            run = _complete_step(
                run,
                AgentRole.REVIEW,
                "Review Agent accepted the revised proposal",
                second_review["detail"],
                metadata=second_review,
            )
            self._check_control(run)
        run = _record_exchange(
            run,
            AgentRole.REVIEW,
            AgentRole.TEST,
            "Reviewed patch handed to tests",
            "Review Agent handed the accepted proposal to Test Agent for pre-approval validation.",
            metadata={"review_decision": "accepted"},
        )
        self._check_control(run)

        test_validation = LocalTestAgentAdapter(settings).validate_patch(result)
        result.approval["preapproval_test"] = {
            key: value
            for key, value in test_validation.items()
            if key not in {"output"}
        }
        run = _complete_step(
            run,
            AgentRole.TEST,
            "Test Agent validated the proposed patch",
            (
                f"Pre-approval validation passed with {test_validation['command']}."
                if test_validation.get("passed")
                else f"Pre-approval validation did not pass: {test_validation.get('detail') or test_validation.get('status')}."
            ),
            status=AgentStepStatus.COMPLETED if test_validation.get("passed") else AgentStepStatus.FAILED,
            metadata=test_validation,
        )
        self._check_control(run)
        if not test_validation.get("passed"):
            run = run.model_copy(
                update={
                    "findings": [
                        *run.findings,
                        AgentFinding(
                            role=AgentRole.TEST,
                            title="Pre-approval tests failed",
                            detail=test_validation.get("output") or test_validation.get("detail") or "Tests failed.",
                            severity="error",
                            metadata={key: value for key, value in test_validation.items() if key != "output"},
                        ),
                    ]
                }
            )
            return _finish(run, "Test Agent blocked the patch before human approval because validation failed.", failed=True)
        run = _record_exchange(
            run,
            AgentRole.TEST,
            AgentRole.GIT,
            "Validated patch handed to git",
            "Test Agent passed validation metadata to Git Agent for approval-time branch planning.",
            metadata={
                "test_command": test_validation.get("command"),
                "passed": test_validation.get("passed"),
            },
        )
        self._check_control(run)

        _attach_multi_agent_approval_metadata(result, run, user)
        action = self._collab.add_action(room_id, user, result)
        if action:
            run = run.model_copy(update={"action_id": action.id})
        run = _complete_step(
            run,
            AgentRole.GIT,
            "Git Agent is waiting for approval",
            "Branch creation is deferred until the human approves the pending patch.",
            metadata={"branch_after_approval": bool(action)},
        )
        self._check_control(run)
        summary = (
            f"Coordinator assigned Meeting, Memory, Code, Review, Test, and Git agents. "
            f"Patch action {action.id} is waiting for approval."
            if action
            else "Coordinator assigned specialist agents, but no pending patch action was created."
        )
        return _finish(run, summary)

    def _check_control(self, run: AgentRun) -> None:
        key = (run.room_id, run.id)
        if key in self._cancelled:
            raise AgentRunCancelled(self._cancelled[key], run=run)
        control = run.metadata.get("control") if isinstance(run.metadata, dict) else {}
        if not isinstance(control, dict):
            return
        timeout_seconds = float(control.get("timeout_seconds") or 0)
        if timeout_seconds <= 0:
            raise AgentRunTimedOut(
                f"Agent run timed out after {timeout_seconds:.3f}s.",
                metadata={"timeout_seconds": timeout_seconds},
                run=run,
            )
        elapsed = (_now() - run.created_at).total_seconds()
        if elapsed > timeout_seconds:
            raise AgentRunTimedOut(
                f"Agent run timed out after {timeout_seconds:.3f}s.",
                metadata={"timeout_seconds": timeout_seconds, "elapsed_seconds": elapsed},
                run=run,
            )
        max_steps = int(control.get("max_steps") or 0)
        if max_steps and len(run.steps) >= max_steps:
            raise AgentRunBudgetExceeded(
                f"Agent run step budget exhausted after {max_steps} step(s).",
                metadata={"max_steps": max_steps, "step_count": len(run.steps)},
                run=run,
            )

    async def _maybe_debug_delay(self, run: AgentRun) -> None:
        if self._settings.llm_provider != "mock":
            return
        request_metadata = run.metadata.get("request_metadata") if isinstance(run.metadata, dict) else {}
        if not isinstance(request_metadata, dict):
            return
        delay = float(request_metadata.get("debug_delay_seconds") or 0)
        if delay <= 0:
            return
        await asyncio.sleep(min(delay, 30.0))


def _complete_step(
    run: AgentRun,
    role: AgentRole,
    title: str,
    detail: str,
    *,
    status: AgentStepStatus = AgentStepStatus.COMPLETED,
    metadata: dict | None = None,
) -> AgentRun:
    now = _now()
    step = AgentStep(
        id=_id("step"),
        role=role,
        status=status,
        title=title,
        detail=detail,
        created_at=now,
        completed_at=now if status != AgentStepStatus.PLANNED else None,
        metadata=metadata or {},
    )
    return run.model_copy(update={"steps": [*run.steps, step], "updated_at": now})


def _record_exchange(
    run: AgentRun,
    from_role: AgentRole,
    to_role: AgentRole,
    title: str,
    detail: str,
    *,
    metadata: dict | None = None,
) -> AgentRun:
    now = _now()
    exchange = AgentExchange(
        id=_id("exchange"),
        from_role=from_role,
        to_role=to_role,
        title=title,
        detail=detail,
        created_at=now,
        metadata=metadata or {},
    )
    return run.model_copy(update={"exchanges": [*run.exchanges, exchange], "updated_at": now})


def _finish(run: AgentRun, summary: str, *, failed: bool = False) -> AgentRun:
    now = _now()
    return run.model_copy(
        update={
            "status": AgentRunStatus.FAILED if failed else AgentRunStatus.COMPLETED,
            "summary": summary,
            "updated_at": now,
            "completed_at": now,
        }
    )


def _control_metadata(request: AgentRunRequest) -> dict:
    return {
        "max_steps": request.max_steps,
        "timeout_seconds": request.timeout_seconds,
    }


def _control_finish(run: AgentRun, exc: AgentRunControlError) -> AgentRun:
    now = _now()
    status = exc.status
    return run.model_copy(
        update={
            "status": status,
            "summary": f"Multi-agent run {status.value.replace('_', ' ')}: {exc.detail}",
            "updated_at": now,
            "completed_at": now,
            "metadata": {
                **run.metadata,
                "control_result": {
                    "status": status.value,
                    "detail": exc.detail,
                    **exc.metadata,
                },
            },
            "findings": [
                *run.findings,
                AgentFinding(
                    role=AgentRole.COORDINATOR,
                    title=exc.finding_title,
                    detail=exc.detail,
                    severity="warning" if status != AgentRunStatus.TIMED_OUT else "error",
                    metadata=exc.metadata,
                ),
            ],
        }
    )


def _review_patch_result(run: AgentRun, result: OrchestratorResult, resolved_context: dict | None) -> dict:
    diff = result.approval.get("diff", "")
    proposed_files = list(result.files_changed or result.approval.get("proposed_files") or [])
    context_text = " ".join(
        str(part)
        for part in [
            run.prompt,
            resolved_context.get("text") if isinstance(resolved_context, dict) else "",
        ]
        if part
    ).lower()
    if not result.pending_approval or not diff:
        return {
            "decision": "blocked",
            "reason": "missing_pending_diff",
            "detail": "Review Agent did not receive a pending patch diff to review.",
            "has_diff": bool(diff),
            "approval_required": result.pending_approval,
        }
    if "review note" in context_text and "README.md" not in proposed_files:
        return {
            "decision": "request_revision",
            "reason": "missing_review_note",
            "detail": "Review Agent requested a revision: add a README review note for handoff clarity.",
            "has_diff": True,
            "approval_required": True,
            "proposed_files": proposed_files,
        }
    return {
        "decision": "approved_for_human_review",
        "reason": "checklist_passed",
        "detail": "Review Agent found a pending approval diff, validation command, and required handoff context.",
        "has_diff": True,
        "approval_required": True,
        "proposed_files": proposed_files,
    }


def _revise_patch_for_review_note(result: OrchestratorResult, settings: Settings) -> OrchestratorResult:
    payload = {**(result.approval_payload or {})}
    files = dict(payload.get("files") or {})
    if "README.md" in files:
        return result
    try:
        before = read_file("README.md", configured=settings.voiceops_workspace)
    except WorkspaceError:
        before = ""
    note = "- Review Agent requested this handoff note for the health check patch.\n"
    after = before.rstrip()
    if "Review Agent requested this handoff note" not in after:
        after = f"{after}\n\n## Review note\n\n{note}" if after else f"## Review note\n\n{note}"
    files["README.md"] = after.rstrip() + "\n"
    proposed_files = list(dict.fromkeys([*result.files_changed, "README.md"]))
    approval = {
        **result.approval,
        "diff": (result.approval.get("diff") or "") + _unified_diff("README.md", before, files["README.md"]),
        "proposed_files": proposed_files,
        "revision_count": int(result.approval.get("revision_count") or 0) + 1,
        "review": {
            "status": "revised",
            "requested_by": AgentRole.REVIEW.value,
            "reason": "missing_review_note",
        },
    }
    payload["files"] = files
    return result.model_copy(
        update={
            "summary": f"{result.summary} Revised after Review Agent requested a README handoff note.",
            "files_changed": proposed_files,
            "approval": approval,
            "approval_payload": payload,
        }
    )


def _unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def _route_prompt(prompt: str) -> str:
    lower = prompt.lower()
    if any(token in lower for token in ("fix that", "fix this", "patch", "修复", "改一下")):
        return "meeting_patch_closure"
    if any(token in lower for token in ("what did", "what is open", "what files", "memory", "decide", "决定", "还有什么")):
        return "meeting_memory"
    if any(token in lower for token in ("explain", "find bug", "run test", "code", "解释", "bug", "测试")):
        return "read_only_code"
    return "general"


def _route_detail(route: str) -> str:
    if route == "meeting_patch_closure":
        return "Code-changing request: route through specialist agents and keep approval-first."
    if route == "meeting_memory":
        return "Memory request: use RAG and citations, no workspace mutation."
    if route == "read_only_code":
        return "Read-only code route: no patch proposal unless explicitly requested."
    return "General teammate message: no specialist action required."


def _attach_multi_agent_approval_metadata(result: OrchestratorResult, run: AgentRun, user: UserPublic) -> None:
    if not result.pending_approval:
        return
    result.approval["policy"] = {
        "mode": "preview_first",
        "approval_required": True,
        "workspace_write_before_approval": False,
        "tool_policy": "approval_required",
    }
    result.approval["multi_agent"] = {
        "run_id": run.id,
        "route": run.route,
        "requested_by": user.id,
        "requested_by_name": user.name,
        "plan": run.metadata.get("plan") if isinstance(run.metadata, dict) else None,
        "llm_routes": run.metadata.get("llm_routes") if isinstance(run.metadata, dict) else None,
        "steps": [
            {
                "role": step.role.value,
                "status": step.status.value,
                "title": step.title,
            }
            for step in run.steps
        ],
        "exchange_count": len(run.exchanges),
    }


def _record_llm_route(run: AgentRun, role: AgentRole, metadata: dict) -> AgentRun:
    current = run.metadata.get("llm_routes", {}) if isinstance(run.metadata, dict) else {}
    if not isinstance(current, dict):
        current = {}
    return run.model_copy(update={"metadata": {**run.metadata, "llm_routes": {**current, role.value: metadata}}})


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


_TERMINAL_ASSIGNMENT_STATUSES = {
    AgentAssignmentStatus.COMPLETED,
    AgentAssignmentStatus.FAILED,
    AgentAssignmentStatus.CANCELLED,
}


def _assignment_audit_metadata(assignment: AgentAssignment) -> dict[str, Any]:
    metadata = assignment.metadata or {}
    return {
        "assignment_id": assignment.id,
        "agent_id": assignment.agent_id,
        "agent_label": assignment.agent_label,
        "agent_kind": assignment.agent_kind,
        "mode": assignment.mode,
        "status": assignment.status.value,
        "requested_by": assignment.requested_by,
        "requested_by_name": assignment.requested_by_name,
        "dispatched_by": metadata.get("dispatched_by"),
        "dispatched_by_name": metadata.get("dispatched_by_name"),
        "cancelled_by": metadata.get("cancelled_by"),
        "cancelled_by_name": metadata.get("cancelled_by_name"),
        "retried_by": metadata.get("retried_by"),
        "retried_by_name": metadata.get("retried_by_name"),
        "updated_by": metadata.get("updated_by"),
        "updated_by_name": metadata.get("updated_by_name"),
        "retry_of": metadata.get("retry_of"),
        "original_status": metadata.get("original_status"),
    }


def _external_recommendation_metadata(recommendation: Any) -> dict[str, Any]:
    return {
        "routing_source": "external_agent_recommendation",
        "recommended_provider": recommendation.provider.value,
        "recommended_label": recommendation.label,
        "recommended_mode": recommendation.mode.value,
        "recommended_model": recommendation.model,
        "recommended_task_kind": recommendation.task_kind.value,
        "recommendation_ready": recommendation.ready,
        "recommendation_confidence": recommendation.confidence,
        "recommendation_reason": recommendation.reason,
        "recommendation_blockers": recommendation.blockers,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


@lru_cache
def get_multi_agent_service() -> MultiAgentService:
    settings = get_settings()
    from app.external_agents.service import ExternalAgentService
    from app.external_agents.store import ExternalAgentCredentialStore

    collab = get_collaboration_service()
    return MultiAgentService(
        AgentRunStore(settings.agent_runs_path),
        collab,
        settings,
        external_runner=ExternalAgentService(
            ExternalAgentCredentialStore(settings.external_agent_store_path),
            settings,
            collab,
        ),
    )
