from app.voice_agent.models import EnrichedContext, ExtractedIntent, NormalizedCommand, OrchestratorResult, VoiceIntent


def build_response_text(
    intent: ExtractedIntent,
    command: NormalizedCommand,
    context: EnrichedContext,
    orchestrator_result: OrchestratorResult | None = None,
) -> str:
    """Build the spoken reply — conversational for chat, concise for ops."""
    if orchestrator_result and (orchestrator_result.executed or orchestrator_result.pending_approval) and orchestrator_result.summary:
        return orchestrator_result.summary.replace("**", "")

    if intent.spoken_response:
        return intent.spoken_response.strip()

    if intent.intent in (VoiceIntent.GENERAL_QUERY, VoiceIntent.UNKNOWN):
        return "I'm here and ready to help. What can I do for you?"

    parts = [command.normalized_text.rstrip(".") + "."]

    if command.requires_approval:
        parts.append("I'll need your approval before I proceed.")

    return " ".join(parts)
