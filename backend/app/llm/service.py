from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.models import LLMRequest, LLMResponse
from app.llm.providers import LLMProvider, get_llm_provider


class LLMRuntime:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    @property
    def provider_id(self) -> str:
        return self._provider.id

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await self._provider.generate(request)


@lru_cache
def _runtime_for_provider(provider_name: str) -> LLMRuntime:
    settings = get_settings()
    if settings.llm_provider != provider_name:
        settings = settings.model_copy(update={"llm_provider": provider_name})
    return LLMRuntime(get_llm_provider(settings))


def get_llm_runtime(settings: Settings | None = None) -> LLMRuntime:
    active = settings or get_settings()
    if settings is None:
        return _runtime_for_provider(active.llm_provider)
    return LLMRuntime(get_llm_provider(active))
