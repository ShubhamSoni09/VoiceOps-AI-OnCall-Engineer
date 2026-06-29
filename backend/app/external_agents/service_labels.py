from app.external_agents.models import ExternalAgentProvider


PROVIDER_LABELS = {
    ExternalAgentProvider.CLAUDE: "Claude Code",
    ExternalAgentProvider.CODEX: "OpenAI Codex",
    ExternalAgentProvider.CURSOR: "Cursor Agent",
    ExternalAgentProvider.LOCAL: "Local Open Agent",
}
