from app.voice_agent.models import ExtractedIntent, IncidentAction, NormalizedCommand, VoiceIntent

# Actions that mutate production state require explicit approval
APPROVAL_REQUIRED_ACTIONS = {
    IncidentAction.PATCH,
    IncidentAction.DEPLOY,
    IncidentAction.ROLLBACK,
    IncidentAction.CREATE_PR,
}

URGENCY_KEYWORDS = {
    "critical": ("critical", "p0", "sev0", "emergency", "outage", "down"),
    "high": ("urgent", "p1", "sev1", "asap", "immediately"),
    "low": ("when you can", "low priority", "whenever"),
}


class CommandNormalizer:
    """Standardize extracted intents into orchestrator-ready commands."""

    def normalize(self, transcript: str, intent: ExtractedIntent) -> NormalizedCommand:
        target = self._resolve_target(intent)
        parameters = self._build_parameters(intent)
        urgency = self._detect_urgency(transcript, intent)
        requires_approval = intent.action in APPROVAL_REQUIRED_ACTIONS

        normalized_text = self._to_normalized_text(intent, target, parameters)

        return NormalizedCommand(
            intent=intent.intent,
            action=intent.action,
            target=target,
            parameters=parameters,
            urgency=urgency,
            requires_approval=requires_approval,
            original_transcript=transcript,
            normalized_text=normalized_text,
        )

    def _resolve_target(self, intent: ExtractedIntent) -> str | None:
        entities = intent.entities
        for key in ("service", "endpoint", "repo", "deployment", "ticket_id"):
            if entities.get(key):
                return str(entities[key])
        return None

    def _build_parameters(self, intent: ExtractedIntent) -> dict:
        params = dict(intent.entities)
        params["summary"] = intent.raw_summary
        if intent.intent == VoiceIntent.INVESTIGATE_INCIDENT:
            params.setdefault("steps", ["fetch_logs", "check_metrics", "diagnose"])
        elif intent.intent == VoiceIntent.FIX_ISSUE:
            params.setdefault("steps", ["diagnose", "patch", "test", "create_pr"])
        elif intent.intent == VoiceIntent.EXPLAIN_CODE:
            params.setdefault("steps", ["search_code", "summarize_references"])
        elif intent.intent == VoiceIntent.FIND_BUG:
            params.setdefault("steps", ["run_tests", "inspect_failures", "report_findings"])
        elif intent.intent == VoiceIntent.SUMMARIZE_CHANGES:
            params.setdefault("steps", ["read_timeline", "summarize_actions"])
        elif intent.intent == VoiceIntent.GIT_STATUS:
            params.setdefault("steps", ["read_git_status", "read_git_diff"])
        elif intent.intent == VoiceIntent.DEPLOY_SERVICE:
            params.setdefault("steps", ["deploy_preview", "health_check", "verify"])
        return params

    def _detect_urgency(self, transcript: str, intent: ExtractedIntent) -> str:
        text = transcript.lower()
        entities_urgency = str(intent.entities.get("urgency", "")).lower()
        if entities_urgency in ("critical", "high", "low", "normal"):
            return entities_urgency

        for level, keywords in URGENCY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return level
        return "normal"

    def _to_normalized_text(
        self, intent: ExtractedIntent, target: str | None, parameters: dict
    ) -> str:
        action = intent.action.value.replace("_", " ")
        target_part = f" on {target}" if target else ""
        env = parameters.get("environment")
        env_part = f" ({env})" if env else ""
        return f"{action}{target_part}{env_part}: {intent.raw_summary}"
