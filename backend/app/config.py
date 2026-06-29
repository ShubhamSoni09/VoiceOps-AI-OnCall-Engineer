from functools import lru_cache
import json
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "VoiceOps AI On-Call Engineer"
    debug: bool = False
    deployment_environment: str = "local"  # local | staging | production
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    cors_allow_credentials: bool = True

    # Speech-to-text
    stt_provider: str = "whisper"  # mock | whisper | aws
    whisper_model: str = "tiny"
    aws_region: str = "us-east-1"
    aws_transcribe_language: str = "en-US"
    aws_s3_bucket: str | None = None

    # LLM (intent extraction)
    llm_provider: str = "mock"  # openai | anthropic | openai_compatible | bedrock | mock
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    openai_compatible_api_key: str | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_model: str = "glm-5.2"
    tts_provider: str = "openai"  # openai | mock
    tts_on_voice: bool = False  # skip server TTS for faster voice replies (UI uses browser speech)
    openai_tts_model: str = "tts-1-hd"
    openai_tts_voice: str = "shimmer"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_region: str = "us-east-1"

    # Context / memory
    context_window_turns: int = 10
    memory_store_path: str = ".voiceops_memory.json"
    voiceops_cache_path: Path = BACKEND_ROOT / ".voiceops_cache" / "cache.json"
    git_cache_ttl_seconds: float = 2.0
    workspace_cache_ttl_seconds: float = 10.0
    rag_index_path: Path = BACKEND_ROOT / "data" / "rag_index.json"
    rag_embedding_provider: str = "local_sparse"  # local_sparse | future embedding provider
    agent_runs_path: Path = BACKEND_ROOT / "data" / "agent_runs.json"
    agent_llm_routes_path: Path = BACKEND_ROOT / "data" / "agent_llm_routes.json"
    long_memory_path: Path = BACKEND_ROOT / "data" / "long_memory.json"
    external_agent_store_path: Path = BACKEND_ROOT / "data" / "external_agent_credentials.json"
    external_agent_credential_secret: str | None = None
    llm_connection_store_path: Path = BACKEND_ROOT / "data" / "llm_connections.json"
    external_agent_oauth_redirect_base_url: str = "http://127.0.0.1:8001"
    external_agent_oauth_mock_enabled: bool = True
    external_agent_allowed_providers: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["claude", "codex", "cursor", "local"])
    claude_oauth_client_id: str | None = None
    claude_oauth_client_secret: str | None = None
    claude_oauth_authorize_url: str = "https://console.anthropic.com/oauth/authorize"
    claude_oauth_token_url: str | None = None
    codex_oauth_client_id: str | None = None
    codex_oauth_client_secret: str | None = None
    codex_oauth_authorize_url: str = "https://auth.openai.com/oauth/authorize"
    codex_oauth_token_url: str | None = None
    cursor_oauth_client_id: str | None = None
    cursor_oauth_client_secret: str | None = None
    cursor_oauth_authorize_url: str = "https://cursor.com/oauth/authorize"
    cursor_oauth_token_url: str | None = None
    external_agent_cli_execution_enabled: bool = False
    external_agent_cli_timeout_seconds: int = 120
    external_agent_api_execution_enabled: bool = False
    external_agent_api_timeout_seconds: float = 45.0
    anthropic_api_base_url: str = "https://api.anthropic.com"
    anthropic_api_version: str = "2023-06-01"
    openai_api_base_url: str = "https://api.openai.com/v1"

    # Auth
    jwt_secret: str = "change-me-in-production-voiceops"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12
    seed_demo_users: bool = True
    production_startup_security_gate: bool = True
    users_store_path: Path = BACKEND_ROOT / "data" / "users.json"
    collab_store_backend: str = "json"  # json | sqlite
    collab_store_path: Path = BACKEND_ROOT / "data" / "collaboration.json"
    collab_sqlite_path: Path = BACKEND_ROOT / "data" / "collaboration.sqlite3"
    speaker_provider: str = "mock"
    speaker_store_backend: str = "json"  # json | sqlite
    speaker_store_path: Path = BACKEND_ROOT / "data" / "speakers.json"
    speaker_sqlite_path: Path = BACKEND_ROOT / "data" / "speakers.sqlite3"
    speaker_verification_path: Path = BACKEND_ROOT / "data" / "speaker_verification.json"
    demo_evidence_path: Path = BACKEND_ROOT / "data" / "demo_evidence.json"
    speaker_verification_max_bytes: int = 50 * 1024 * 1024
    hf_token: str | None = None
    whisperx_model: str = "small"
    whisperx_device: str = "cpu"
    whisperx_compute_type: str = "int8"
    whisperx_num_speakers: int | None = None
    whisperx_min_speakers: int | None = None
    whisperx_max_speakers: int | None = None
    live_chunk_seconds: float = 2.0
    live_window_seconds: float = 8.0
    live_max_chunk_bytes: int = 5 * 1024 * 1024
    live_session_idle_ttl_seconds: float = 1800.0
    live_processing_timeout_seconds: float = 8.0
    whisperx_worker_timeout_seconds: float = 90.0
    speaker_verification_timeout_seconds: float = 300.0
    whisperx_worker_mode: str = "subprocess"  # subprocess | persistent_subprocess | in_process
    agent_display_name: str = "VoiceOps"
    agent_initials: str | None = None
    agent_wake_words: str = "voiceops,voice ops,assistant,agent"

    # MCP workspace (empty = dashboard shows disconnected state)
    voiceops_workspace: str | None = None
    voiceops_workspace_source: str = "none"
    workspace_selection_path: Path = BACKEND_ROOT / "data" / "workspace_selection.json"
    workspace_clone_root: Path = Field(default_factory=lambda: Path.home() / ".voiceops" / "workspaces")

    # External side effects
    github_pr_creation_enabled: bool = False
    github_pr_cli_path: str = "gh"
    github_pr_allowed_base_branches: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["main"])
    github_pr_command_timeout_seconds: float = 60.0
    github_pr_create_draft: bool = True

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("github_pr_allowed_base_branches", mode="before")
    @classmethod
    def parse_github_pr_allowed_base_branches(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("external_agent_allowed_providers", mode="before")
    @classmethod
    def parse_external_agent_allowed_providers(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
                return []
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.voiceops_workspace:
        settings.voiceops_workspace_source = "configured"
    apply_workspace_selection(settings)
    return settings


def apply_workspace_selection(settings: Settings) -> Settings:
    if settings.voiceops_workspace:
        if settings.voiceops_workspace_source == "none":
            settings.voiceops_workspace_source = "configured"
        return settings
    try:
        raw = json.loads(settings.workspace_selection_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings
    selected = str(raw.get("path") or "").strip()
    if selected:
        settings.voiceops_workspace = selected
        settings.voiceops_workspace_source = "saved"
    return settings


def persist_workspace_selection(settings: Settings, path: str) -> None:
    settings.workspace_selection_path.parent.mkdir(parents=True, exist_ok=True)
    settings.workspace_selection_path.write_text(
        json.dumps({"path": path}, indent=2),
        encoding="utf-8",
    )
