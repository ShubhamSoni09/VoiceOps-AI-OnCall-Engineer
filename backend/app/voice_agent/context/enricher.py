import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.voice_agent.models import ConversationTurn, ExtractedIntent, NormalizedCommand, VoiceIntent


class SessionMemory:
    """Short-term conversation memory per session."""

    def __init__(self, settings: Settings) -> None:
        self._max_turns = settings.context_window_turns
        self._store_path = Path(settings.memory_store_path)
        self._sessions: dict[str, list[ConversationTurn]] = {}
        self._load()

    def _load(self) -> None:
        if self._store_path.exists():
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            self._sessions = {
                sid: [ConversationTurn.model_validate(t) for t in turns]
                for sid, turns in raw.items()
            }

    def _persist(self) -> None:
        payload = {sid: [t.model_dump() for t in turns] for sid, turns in self._sessions.items()}
        self._store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_history(self, session_id: str) -> list[ConversationTurn]:
        return list(self._sessions.get(session_id, []))

    def append(self, session_id: str, role: str, content: str) -> None:
        turns = self._sessions.setdefault(session_id, [])
        turns.append(
            ConversationTurn(
                role=role,
                content=content,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        if len(turns) > self._max_turns:
            self._sessions[session_id] = turns[-self._max_turns :]
        self._persist()

    def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._persist()


NEXT_STEPS_BY_INTENT: dict[VoiceIntent, list[str]] = {
    VoiceIntent.INVESTIGATE_INCIDENT: [
        "Fetch recent logs and error traces",
        "Check service health and metrics",
        "Correlate with recent deployments",
    ],
    VoiceIntent.FIX_ISSUE: [
        "Diagnose root cause",
        "Generate and apply patch in sandbox",
        "Run tests and open PR for review",
    ],
    VoiceIntent.DEPLOY_SERVICE: [
        "Create preview deployment",
        "Run health checks",
        "Await approval before promote",
    ],
    VoiceIntent.CHECK_STATUS: [
        "Query Render service status",
        "Summarize recent incidents",
    ],
    VoiceIntent.ROLLBACK_DEPLOYMENT: [
        "Identify last known good deployment",
        "Request approval for rollback",
    ],
    VoiceIntent.RUN_TESTS: [
        "Run lint and unit tests in sandbox",
        "Report failures with suggested fixes",
    ],
    VoiceIntent.CREATE_PR: [
        "Ensure branch is pushed",
        "Open PR with incident summary",
    ],
}


class ContextEnricher:
    """Merge transcript, intent, history, and incident data."""

    def __init__(self, memory: SessionMemory) -> None:
        self._memory = memory

    def enrich(
        self,
        session_id: str,
        transcript: str,
        intent: ExtractedIntent,
        command: NormalizedCommand,
        incident_context: dict | None = None,
    ):
        from app.voice_agent.models import EnrichedContext

        history = self._memory.get_history(session_id)
        merged_incident = {**(incident_context or {}), **intent.entities}
        suggested = list(NEXT_STEPS_BY_INTENT.get(intent.intent, ["Clarify request with user"]))

        if command.urgency in ("critical", "high"):
            suggested.insert(0, f"Priority: {command.urgency} — escalate if blocked")

        ctx = EnrichedContext(
            transcript=transcript,
            intent=intent,
            command=command,
            conversation_history=history,
            incident_context=merged_incident,
            suggested_next_steps=suggested,
        )

        self._memory.append(session_id, "user", transcript)
        assistant_reply = intent.spoken_response or command.normalized_text
        self._memory.append(session_id, "assistant", assistant_reply)

        return ctx
