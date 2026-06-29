import pytest

from app.agents.llm_routing import AgentLLMRouteRequest, AgentLLMRouteStore, AgentLLMRoutingService
from app.agents.models import AgentRole
from app.auth.models import Role, UserPublic
from app.config import Settings
from app.llm import LLMRequest, LLMResponse
from app.llm.connections_models import LLMApiKeyConnectRequest, LLMConnectionProvider
from app.llm.connections_service import LLMConnectionService
from app.llm.connections_store import LLMConnectionStore


def test_agent_llm_routing_selects_openai_compatible_for_code_agent(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_alice")
    connections.connect_api_key(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        user,
        LLMApiKeyConnectRequest(
            api_key="",
            model="glm-5.2",
            base_url="http://127.0.0.1:8000/v1",
            account_label="Local GLM",
        ),
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    route = routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(role=AgentRole.CODE, provider="openai_compatible", model="glm-5.2"),
    )
    selected, metadata = routing.settings_for_role("main", user, AgentRole.CODE, settings)

    assert route.provider == "openai_compatible"
    assert selected.llm_provider == "openai_compatible"
    assert selected.openai_compatible_base_url == "http://127.0.0.1:8000/v1"
    assert selected.openai_compatible_model == "glm-5.2"
    assert metadata["role"] == "code"
    assert metadata["route_provider"] == "openai_compatible"
    assert metadata["model"] == "glm-5.2"


def test_agent_llm_routing_snapshot_includes_defaults(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-secret",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    snapshot = routing.snapshot("main")

    assert {route.role for route in snapshot.routes} == set(AgentRole)
    assert all(route.provider == "default" for route in snapshot.routes)


def test_agent_llm_route_store_writes_private_routing_file(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-secret",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    routing.update_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(role=AgentRole.CODE, provider="mock", model="mock-deterministic"),
    )

    assert settings.agent_llm_routes_path.stat().st_mode & 0o777 == 0o600


def test_agent_llm_route_store_tightens_existing_routing_file(tmp_path):
    path = tmp_path / "agent-llm-routes.json"
    path.write_text("[]", encoding="utf-8")
    path.chmod(0o644)

    AgentLLMRouteStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


def test_agent_llm_routing_applies_bedrock_model_for_claude_route(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-bedrock-secret",
        llm_provider="mock",
        bedrock_model_id="anthropic.default",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)
    user = _user("usr_alice")

    routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(
            role=AgentRole.REVIEW,
            provider="bedrock",
            model="anthropic.claude-sonnet-custom",
        ),
    )
    selected, metadata = routing.settings_for_role("main", user, AgentRole.REVIEW, settings)

    assert selected.llm_provider == "bedrock"
    assert selected.bedrock_model_id == "anthropic.claude-sonnet-custom"
    assert metadata["provider"] == "bedrock"
    assert metadata["model"] == "anthropic.claude-sonnet-custom"


def test_agent_llm_routing_selects_anthropic_for_review_agent(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-anthropic-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_alice")
    connections.connect_api_key(
        LLMConnectionProvider.ANTHROPIC,
        user,
        LLMApiKeyConnectRequest(
            api_key="sk-ant-test-1234567890",
            model="claude-sonnet-4-5",
            account_label="Alice Claude",
        ),
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    route = routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(role=AgentRole.REVIEW, provider="anthropic", model="claude-opus-4-1"),
    )
    selected, metadata = routing.settings_for_role("main", user, AgentRole.REVIEW, settings)
    runtime = {row.role: row for row in routing.snapshot("main", user).runtime}

    assert route.provider == "anthropic"
    assert selected.llm_provider == "anthropic"
    assert selected.anthropic_api_key == "sk-ant-test-1234567890"
    assert selected.anthropic_model == "claude-opus-4-1"
    assert metadata["route_provider"] == "anthropic"
    assert metadata["model"] == "claude-opus-4-1"
    assert runtime[AgentRole.REVIEW].ready is True
    assert runtime[AgentRole.REVIEW].effective_provider == "anthropic"
    assert runtime[AgentRole.REVIEW].credential_source == "user_connection"


def test_agent_llm_routing_snapshot_reports_runtime_matrix(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-runtime-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_alice")
    connections.connect_api_key(
        LLMConnectionProvider.OPENAI_COMPATIBLE,
        user,
        LLMApiKeyConnectRequest(
            api_key="",
            model="glm-5.2",
            base_url="http://127.0.0.1:8000/v1",
            account_label="Local GLM",
        ),
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)
    routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(role=AgentRole.CODE, provider="openai_compatible", model="glm-5.2"),
    )

    snapshot = routing.snapshot("main", user)
    runtime = {row.role: row for row in snapshot.runtime}

    assert len(snapshot.runtime) == len(AgentRole)
    assert runtime[AgentRole.CODE].ready is True
    assert runtime[AgentRole.CODE].effective_provider == "openai_compatible"
    assert runtime[AgentRole.CODE].effective_model == "glm-5.2"
    assert runtime[AgentRole.CODE].credential_source == "user_connection"
    assert runtime[AgentRole.COORDINATOR].effective_provider == "openai_compatible"
    assert runtime[AgentRole.COORDINATOR].effective_model == "glm-5.2"
    assert runtime[AgentRole.COORDINATOR].credential_source == "user_connection"
    assert runtime[AgentRole.COORDINATOR].ready is True


def test_agent_llm_default_route_uses_latest_user_connection(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-default-user-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    user = _user("usr_alice")
    connections.connect_api_key(
        LLMConnectionProvider.ANTHROPIC,
        user,
        LLMApiKeyConnectRequest(
            api_key="sk-ant-test-1234567890",
            model="claude-sonnet-4-5",
            account_label="Alice Claude",
        ),
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    selected, metadata = routing.settings_for_role("main", user, AgentRole.COORDINATOR, settings)
    runtime = {row.role: row for row in routing.snapshot("main", user).runtime}

    assert selected.llm_provider == "anthropic"
    assert metadata["route_provider"] == "default"
    assert metadata["provider"] == "anthropic"
    assert metadata["model"] == "claude-sonnet-4-5"
    assert runtime[AgentRole.COORDINATOR].effective_provider == "anthropic"
    assert runtime[AgentRole.COORDINATOR].credential_source == "user_connection"
    assert runtime[AgentRole.COORDINATOR].ready is True


def test_agent_llm_routing_runtime_marks_missing_openai_key(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-missing-secret",
        llm_provider="mock",
        openai_api_key=None,
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)
    user = _user("usr_bob")
    routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(role=AgentRole.REVIEW, provider="openai", model="gpt-5.4"),
    )

    snapshot = routing.snapshot("main", user)
    review = next(row for row in snapshot.runtime if row.role == AgentRole.REVIEW)

    assert review.ready is False
    assert review.status == "missing_credential"
    assert review.effective_provider == "openai"
    assert review.effective_model == "gpt-5.4"
    assert review.credential_source == "missing"
    assert review.warnings == ["Connect OpenAI or configure OPENAI_API_KEY."]


def test_agent_llm_routing_runtime_marks_missing_anthropic_key(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-missing-anthropic-secret",
        llm_provider="mock",
        anthropic_api_key=None,
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)
    user = _user("usr_bob")
    routing.update_route(
        "main",
        user,
        AgentLLMRouteRequest(role=AgentRole.CODE, provider="anthropic", model="claude-sonnet-4-5"),
    )

    snapshot = routing.snapshot("main", user)
    code = next(row for row in snapshot.runtime if row.role == AgentRole.CODE)

    assert code.ready is False
    assert code.status == "missing_credential"
    assert code.effective_provider == "anthropic"
    assert code.effective_model == "claude-sonnet-4-5"
    assert code.credential_source == "missing"
    assert code.warnings == ["Connect Anthropic or configure ANTHROPIC_API_KEY."]


@pytest.mark.asyncio
async def test_agent_llm_route_preflight_accepts_mock_route(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-preflight-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    result = await routing.preflight_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(role=AgentRole.MEMORY, provider="mock"),
    )

    assert result.ready is True
    assert result.effective_provider == "mock"
    assert result.effective_model == "mock-deterministic"
    assert result.text_ok is True
    assert result.json_ok is True
    assert result.blockers == []


@pytest.mark.asyncio
async def test_agent_llm_route_preflight_runs_openai_compatible_contract(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-glm-preflight-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    runtime = _FakeRuntime()
    connections = LLMConnectionService(
        LLMConnectionStore(settings.llm_connection_store_path),
        settings,
        runtime_factory=lambda _settings: runtime,
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    result = await routing.preflight_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(
            role=AgentRole.CODE,
            provider="openai_compatible",
            model="glm-5.2",
            base_url="http://127.0.0.1:8000/v1",
        ),
    )

    assert result.ready is True
    assert result.effective_provider == "openai_compatible"
    assert result.effective_model == "glm-5.2"
    assert result.text_ok is True
    assert result.json_ok is True
    assert [request.response_format for request in runtime.requests] == ["text", "json"]


@pytest.mark.asyncio
async def test_agent_llm_route_preflight_runs_anthropic_contract(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-anthropic-preflight-secret",
        llm_provider="mock",
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    runtime = _FakeRuntime(provider="anthropic", model="claude-sonnet-4-5")
    connections = LLMConnectionService(
        LLMConnectionStore(settings.llm_connection_store_path),
        settings,
        runtime_factory=lambda _settings: runtime,
    )
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    result = await routing.preflight_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(
            role=AgentRole.REVIEW,
            provider="anthropic",
            model="claude-sonnet-4-5",
        ),
    )

    assert result.ready is False
    assert result.status == "missing_credential"
    assert result.blockers == ["Connect Anthropic or configure ANTHROPIC_API_KEY."]

    connected = connections.connect_api_key(
        LLMConnectionProvider.ANTHROPIC,
        _user("usr_alice"),
        LLMApiKeyConnectRequest(api_key="sk-ant-test-1234567890", model="claude-sonnet-4-5"),
    )
    assert connected.provider == LLMConnectionProvider.ANTHROPIC
    result = await routing.preflight_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(
            role=AgentRole.REVIEW,
            provider="anthropic",
            model="claude-sonnet-4-5",
        ),
    )

    assert result.ready is True
    assert result.effective_provider == "anthropic"
    assert result.effective_model == "claude-sonnet-4-5"
    assert result.text_ok is True
    assert result.json_ok is True
    assert [request.response_format for request in runtime.requests] == ["text", "json"]


@pytest.mark.asyncio
async def test_agent_llm_route_preflight_reports_missing_openai_key(tmp_path):
    settings = Settings(
        jwt_secret="agent-routing-openai-preflight-secret",
        llm_provider="mock",
        openai_api_key=None,
        agent_llm_routes_path=tmp_path / "agent-llm-routes.json",
        llm_connection_store_path=tmp_path / "llm-connections.json",
    )
    connections = LLMConnectionService(LLMConnectionStore(settings.llm_connection_store_path), settings)
    routing = AgentLLMRoutingService(AgentLLMRouteStore(settings.agent_llm_routes_path), settings, connections)

    result = await routing.preflight_route(
        "main",
        _user("usr_alice"),
        AgentLLMRouteRequest(role=AgentRole.REVIEW, provider="openai", model="gpt-5.4"),
    )

    assert result.ready is False
    assert result.status == "missing_credential"
    assert result.effective_provider == "openai"
    assert result.effective_model == "gpt-5.4"
    assert result.blockers == ["Connect OpenAI or configure OPENAI_API_KEY."]


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
