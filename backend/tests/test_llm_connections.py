import pytest

from app.auth.models import Role, UserPublic
from app.config import Settings
from app.llm import LLMRequest, LLMResponse
from app.llm.connections_models import LLMApiKeyConnectRequest, LLMConnectionProvider, LLMProviderPreflightRequest
from app.llm.connections_service import LLMConnectionService
from app.llm.connections_store import LLMConnectionStore


def test_llm_connection_store_repairs_existing_file_permissions(tmp_path):
    path = tmp_path / "llm-connections.json"
    path.write_text("[]", encoding="utf-8")
    path.chmod(0o644)

    LLMConnectionStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


def test_llm_connection_service_stores_user_api_key_and_builds_settings(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        openai_model="gpt-4o-mini",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_alice")

    connected = service.connect_api_key(
        LLMConnectionProvider.OPENAI,
        user,
        LLMApiKeyConnectRequest(api_key="sk-test-1234567890", model="gpt-test", account_label="Alice OpenAI"),
    )

    assert connected.provider == LLMConnectionProvider.OPENAI
    assert connected.account_label == "Alice OpenAI"
    assert connected.model == "gpt-test"
    assert connected.token_preview == "sk-t...7890"
    assert settings.llm_connection_store_path.stat().st_mode & 0o777 == 0o600
    providers = service.list_providers(user)
    assert providers[0].connected is True
    assert providers[0].token_preview == "sk-t...7890"

    user_settings = service.settings_for_user(user, settings)
    assert user_settings.llm_provider == "openai"
    assert user_settings.openai_api_key == "sk-test-1234567890"
    assert user_settings.openai_model == "gpt-test"

    service.disconnect(LLMConnectionProvider.OPENAI, user)
    fallback_settings = service.settings_for_user(user, settings)
    assert fallback_settings.llm_provider == "mock"
    assert fallback_settings.openai_api_key is None


def test_llm_connection_service_supports_openai_compatible_glm_endpoint(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_glm")

    connected = service.connect_api_key(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        user,
        LLMApiKeyConnectRequest(
            api_key="",
            model="glm-5.2",
            base_url="http://127.0.0.1:8000/v1",
            account_label="Local GLM",
        ),
    )

    assert connected.provider == LLMConnectionProvider.OPENAI_COMPATIBLE
    assert connected.model == "glm-5.2"
    assert connected.base_url == "http://127.0.0.1:8000/v1"
    assert connected.token_preview is None
    user_settings = service.settings_for_user_provider(user, LLMConnectionProvider.OPENAI_COMPATIBLE, settings)
    assert user_settings.llm_provider == "openai_compatible"
    assert user_settings.openai_compatible_api_key == "local"
    assert user_settings.openai_compatible_base_url == "http://127.0.0.1:8000/v1"
    assert user_settings.openai_compatible_model == "glm-5.2"


def test_llm_connection_service_supports_anthropic_claude_endpoint(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_claude")

    connected = service.connect_api_key(
        LLMConnectionProvider.ANTHROPIC,
        user,
        LLMApiKeyConnectRequest(
            api_key="sk-ant-test-1234567890",
            model="claude-sonnet-4-5",
            account_label="Alice Anthropic",
        ),
    )

    assert connected.provider == LLMConnectionProvider.ANTHROPIC
    assert connected.model == "claude-sonnet-4-5"
    assert connected.token_preview == "sk-a...7890"
    user_settings = service.settings_for_user_provider(user, LLMConnectionProvider.ANTHROPIC, settings)
    assert user_settings.llm_provider == "anthropic"
    assert user_settings.anthropic_api_key == "sk-ant-test-1234567890"
    assert user_settings.anthropic_model == "claude-sonnet-4-5"


def test_llm_connection_default_uses_latest_valid_user_provider(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_default")

    service.connect_api_key(
        LLMConnectionProvider.ANTHROPIC,
        user,
        LLMApiKeyConnectRequest(api_key="sk-ant-test-1234567890", model="claude-sonnet-4-5"),
    )
    anthropic_settings = service.settings_for_user(user, settings)

    assert anthropic_settings.llm_provider == "anthropic"
    assert anthropic_settings.anthropic_model == "claude-sonnet-4-5"

    service.connect_api_key(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        user,
        LLMApiKeyConnectRequest(
            api_key="",
            model="glm-5.2",
            base_url="http://127.0.0.1:8000/v1",
        ),
    )
    glm_settings = service.settings_for_user(user, settings)

    assert glm_settings.llm_provider == "openai_compatible"
    assert glm_settings.openai_compatible_model == "glm-5.2"
    assert glm_settings.openai_compatible_base_url == "http://127.0.0.1:8000/v1"

    service.disconnect(LLMConnectionProvider.OPENAI_COMPATIBLE, user)
    fallback_settings = service.settings_for_user(user, settings)

    assert fallback_settings.llm_provider == "anthropic"
    assert fallback_settings.anthropic_model == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_llm_preflight_reports_missing_openai_compatible_base_url(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)

    result = await service.preflight_provider(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        _user("usr_glm"),
        LLMProviderPreflightRequest(model="glm-5.2"),
    )

    assert result.ready is False
    assert result.blockers == ["missing_openai_compatible_base_url"]


@pytest.mark.asyncio
async def test_llm_preflight_reports_missing_anthropic_key(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    service = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)

    result = await service.preflight_provider(
        LLMConnectionProvider.ANTHROPIC,
        _user("usr_claude"),
        LLMProviderPreflightRequest(model="claude-sonnet-4-5"),
    )

    assert result.ready is False
    assert result.model == "claude-sonnet-4-5"
    assert result.blockers == ["missing_anthropic_api_key"]


@pytest.mark.asyncio
async def test_llm_preflight_accepts_openai_compatible_runtime(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    runtime = _FakeRuntime()
    service = LLMConnectionService(
        LLMConnectionStore(settings.llm_connection_store_path),
        settings,
        runtime_factory=lambda _settings: runtime,
    )

    result = await service.preflight_provider(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        _user("usr_glm"),
        LLMProviderPreflightRequest(model="glm-5.2", base_url="http://127.0.0.1:8000/v1"),
    )

    assert result.ready is True
    assert result.model == "glm-5.2"
    assert result.base_url == "http://127.0.0.1:8000/v1"
    assert result.text_ok is True
    assert result.json_ok is True
    assert [request.response_format for request in runtime.requests] == ["text", "json"]


@pytest.mark.asyncio
async def test_llm_preflight_accepts_anthropic_runtime(tmp_path):
    settings = Settings(
        jwt_secret="llm-connection-secret",
        llm_provider="mock",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    runtime = _FakeRuntime(provider="anthropic", model="claude-sonnet-4-5")
    service = LLMConnectionService(
        LLMConnectionStore(settings.llm_connection_store_path),
        settings,
        runtime_factory=lambda _settings: runtime,
    )

    result = await service.preflight_provider(
        LLMConnectionProvider.ANTHROPIC,
        _user("usr_claude"),
        LLMProviderPreflightRequest(
            api_key="sk-ant-test-1234567890",
            model="claude-sonnet-4-5",
        ),
    )

    assert result.ready is True
    assert result.model == "claude-sonnet-4-5"
    assert result.base_url == "https://api.anthropic.com"
    assert result.text_ok is True
    assert result.json_ok is True
    assert [request.response_format for request in runtime.requests] == ["text", "json"]


class _FakeRuntime:
    def __init__(self, provider: str = "openai_compatible", model: str = "glm-5.2") -> None:
        self.requests: list[LLMRequest] = []
        self.provider = provider
        self.model = model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if request.response_format == "json":
            return LLMResponse(content='{"ready": true}', provider=self.provider, model=self.model)
        return LLMResponse(content="ready", provider=self.provider, model=self.model)


def _user(user_id: str) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"{user_id}@voiceops.dev",
        name="Alice",
        initials="AL",
        role=Role.ON_CALL,
        role_label="On-call engineer",
        permissions=["voice:use"],
    )
