from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

from app.agents.models import AgentAssignment, AgentAssignmentStatus, AgentFinding, AgentRole, AgentRun, AgentRunStatus


class AgentRunStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._runs: dict[str, list[AgentRun]] = {}
        self._assignments: dict[str, list[AgentAssignment]] = {}
        if self._path.exists():
            os.chmod(self._path, 0o600)
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        self._runs = {
            room_id: [AgentRun.model_validate(item) for item in runs]
            for room_id, runs in raw.get("runs", {}).items()
        }
        self._assignments = {
            room_id: [AgentAssignment.model_validate(item) for item in assignments]
            for room_id, assignments in raw.get("assignments", {}).items()
        }

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "runs": {
                room_id: [run.model_dump(mode="json") for run in runs]
                for room_id, runs in self._runs.items()
            },
            "assignments": {
                room_id: [assignment.model_dump(mode="json") for assignment in assignments]
                for room_id, assignments in self._assignments.items()
            },
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.chmod(self._path, 0o600)

    def append(self, run: AgentRun) -> AgentRun:
        with self._lock:
            self._runs.setdefault(run.room_id, []).append(run)
            self._persist()
            return run

    def update(self, run: AgentRun) -> AgentRun:
        with self._lock:
            runs = self._runs.setdefault(run.room_id, [])
            for index, existing in enumerate(runs):
                if existing.id == run.id:
                    runs[index] = run
                    self._persist()
                    return run
            runs.append(run)
            self._persist()
            return run

    def get(self, room_id: str, run_id: str) -> AgentRun | None:
        for run in self._runs.get(room_id, []):
            if run.id == run_id:
                return run
        return None

    def list(self, room_id: str, *, limit: int = 20) -> list[AgentRun]:
        return self._runs.get(room_id, [])[-limit:]

    def append_assignment(self, assignment: AgentAssignment) -> AgentAssignment:
        with self._lock:
            self._assignments.setdefault(assignment.room_id, []).append(assignment)
            self._persist()
            return assignment

    def update_assignment(self, assignment: AgentAssignment) -> AgentAssignment:
        with self._lock:
            assignments = self._assignments.setdefault(assignment.room_id, [])
            for index, existing in enumerate(assignments):
                if existing.id == assignment.id:
                    assignments[index] = assignment
                    self._persist()
                    return assignment
            assignments.append(assignment)
            self._persist()
            return assignment

    def get_assignment(self, room_id: str, assignment_id: str) -> AgentAssignment | None:
        for assignment in self._assignments.get(room_id, []):
            if assignment.id == assignment_id:
                return assignment
        return None

    def list_assignments(self, room_id: str, *, limit: int = 20) -> list[AgentAssignment]:
        return self._assignments.get(room_id, [])[-limit:]

    def remove_assignments_by_status(
        self,
        room_id: str,
        statuses: set[AgentAssignmentStatus],
    ) -> list[AgentAssignment]:
        with self._lock:
            assignments = self._assignments.get(room_id, [])
            removed = [assignment for assignment in assignments if assignment.status in statuses]
            if not removed:
                return []
            self._assignments[room_id] = [
                assignment for assignment in assignments
                if assignment.status not in statuses
            ]
            self._persist()
            return removed

    def recover_running(self) -> list[AgentRun]:
        with self._lock:
            recovered: list[AgentRun] = []
            changed = False
            for room_id, runs in self._runs.items():
                next_runs = []
                for run in runs:
                    if run.status == AgentRunStatus.RUNNING:
                        now = run.updated_at
                        run = run.model_copy(
                            update={
                                "status": AgentRunStatus.RECOVERED,
                                "summary": "Agent run recovered after process restart before completion.",
                                "completed_at": now,
                                "metadata": {
                                    **run.metadata,
                                    "recovered": True,
                                    "recovery_reason": "process_restarted_with_running_run",
                                },
                                "findings": [
                                    *run.findings,
                                    AgentFinding(
                                        role=AgentRole.COORDINATOR,
                                        title="Run recovered",
                                        detail="This run was still marked running when the agent service started.",
                                        severity="warning",
                                        metadata={"reason": "process_restarted_with_running_run"},
                                    ),
                                ],
                            }
                        )
                        recovered.append(run)
                        changed = True
                    next_runs.append(run)
                self._runs[room_id] = next_runs
            if changed:
                self._persist()
            return recovered
