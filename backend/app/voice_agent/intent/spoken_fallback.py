import re

from app.voice_agent.models import ExtractedIntent, VoiceIntent

_CASUAL_REPLIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(how are you|how'?s it going|how you doing)\b", re.I), "I'm doing great, thanks for asking! How can I help?"),
    (re.compile(r"\b(what are you doing|what you doing|what'?s up)\b", re.I), "I'm standing by, ready to help with incidents and deploys. What do you need?"),
    (re.compile(r"\b(hello|hi|hey|good morning|good evening)\b", re.I), "Hey! I'm here and ready to help. What's going on?"),
    (re.compile(r"\b(thank you|thanks)\b", re.I), "You're welcome! Let me know if you need anything else."),
]


def infer_spoken_response(transcript: str, intent: ExtractedIntent) -> str | None:
    for pattern, reply in _CASUAL_REPLIES:
        if pattern.search(transcript):
            return reply

    if intent.intent in (VoiceIntent.GENERAL_QUERY, VoiceIntent.UNKNOWN):
        return "I'm here and ready to help. What can I do for you?"

    if intent.intent not in (VoiceIntent.GENERAL_QUERY, VoiceIntent.UNKNOWN):
        action = intent.action.value.replace("_", " ")
        return f"Got it — I'll {action} that for you."

    return None
