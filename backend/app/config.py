from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "VoiceOps AI On-Call Engineer"
    debug: bool = False

    # Speech-to-text
    stt_provider: str = "whisper"  # whisper | aws
    whisper_model: str = "tiny"
    aws_region: str = "us-east-1"
    aws_transcribe_language: str = "en-US"
    aws_s3_bucket: str | None = None

    # LLM (intent extraction)
    llm_provider: str = "mock"  # openai | bedrock | mock
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    tts_provider: str = "openai"  # openai | mock
    tts_on_voice: bool = False
    openai_tts_model: str = "tts-1-hd"
    openai_tts_voice: str = "shimmer"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_region: str = "us-east-1"

    # Context / memory
    context_window_turns: int = 10
    memory_store_path: str = ".voiceops_memory.json"

    # Auth
    jwt_secret: str = "change-me-in-production-voiceops"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 12

    # Workspace — path to the folder VoiceOps investigates / patches
    voiceops_workspace: str | None = None

    # Persistent data directory (use /data on Render with a disk attached)
    data_dir: Path = BACKEND_ROOT / "data"

    # CORS — comma-separated frontend origins, e.g. https://voiceops-ui.onrender.com
    # Leave empty to allow all ("*") — fine for local dev.
    allowed_origins: str | None = None

    @property
    def users_store_path(self) -> Path:
        return self.data_dir / "users.json"

    @property
    def memory_store_path_resolved(self) -> Path:
        p = Path(self.memory_store_path)
        return p if p.is_absolute() else self.data_dir / p.name


@lru_cache
def get_settings() -> Settings:
    return Settings()
