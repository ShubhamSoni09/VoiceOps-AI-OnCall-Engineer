from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from app.config import Settings
from app.llm.models import LLMRequest, LLMResponse


class LLMProviderError(RuntimeError):
    pass


class LLMProvider(ABC):
    id: str

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        ...


class MockLLMProvider(LLMProvider):
    id = "mock"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        content = _mock_json_response(request) if request.response_format == "json" else _last_user_message(request)
        return LLMResponse(
            content=content,
            provider=self.id,
            model="mock-deterministic",
            usage={"prompt_messages": len(request.messages), "completion_tokens": len(content.split())},
            metadata={"purpose": request.purpose, "deterministic": True},
        )


class OpenAIChatProvider(LLMProvider):
    id = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMProviderError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        kwargs = {}
        if request.response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[message.model_dump() for message in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return LLMResponse(
            content=content,
            provider=self.id,
            model=self._model,
            usage=usage,
            metadata={"purpose": request.purpose},
        )


class OpenAICompatibleProvider(LLMProvider):
    id = "openai_compatible"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_compatible_base_url:
            raise LLMProviderError("OPENAI_COMPATIBLE_BASE_URL is required when LLM_PROVIDER=openai_compatible")
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=settings.openai_compatible_api_key or "local",
            base_url=settings.openai_compatible_base_url,
        )
        self._model = settings.openai_compatible_model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        kwargs = {}
        if request.response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[message.model_dump() for message in request.messages],
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        usage = response.usage.model_dump() if response.usage else {}
        return LLMResponse(
            content=content,
            provider=self.id,
            model=self._model,
            usage=usage,
            metadata={"purpose": request.purpose, "base_url": str(settings_base_url_safe(self._client))},
        )


class AnthropicMessagesProvider(LLMProvider):
    id = "anthropic"

    def __init__(self, settings: Settings) -> None:
        if not settings.anthropic_api_key:
            raise LLMProviderError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        self._api_key = settings.anthropic_api_key
        self._model = settings.anthropic_model
        self._base_url = settings.anthropic_api_base_url.rstrip("/")
        self._api_version = settings.anthropic_api_version

    async def generate(self, request: LLMRequest) -> LLMResponse:
        import httpx

        system_messages = [message.content for message in request.messages if message.role == "system"]
        chat_messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": chat_messages,
        }
        if system_messages:
            body["system"] = "\n\n".join(system_messages)
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(f"{self._base_url}/v1/messages", headers=headers, json=body)
            response.raise_for_status()
        data = response.json()
        content = _anthropic_text(data)
        return LLMResponse(
            content=content,
            provider=self.id,
            model=self._model,
            usage=data.get("usage", {}),
            metadata={"purpose": request.purpose, "base_url": self._base_url},
        )


class BedrockClaudeProvider(LLMProvider):
    id = "bedrock"

    def __init__(self, settings: Settings) -> None:
        import boto3

        self._client = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
        self._model = settings.bedrock_model_id

    async def generate(self, request: LLMRequest) -> LLMResponse:
        system_messages = [message.content for message in request.messages if message.role == "system"]
        chat_messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "system": "\n\n".join(system_messages),
            "messages": chat_messages,
        }
        response = await asyncio.to_thread(
            self._client.invoke_model,
            modelId=self._model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        content = result["content"][0]["text"]
        usage: dict[str, Any] = {
            "input_tokens": result.get("usage", {}).get("input_tokens"),
            "output_tokens": result.get("usage", {}).get("output_tokens"),
        }
        return LLMResponse(
            content=content,
            provider=self.id,
            model=self._model,
            usage={key: value for key, value in usage.items() if value is not None},
            metadata={"purpose": request.purpose},
        )


def get_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        return OpenAIChatProvider(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicMessagesProvider(settings)
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(settings)
    if settings.llm_provider == "bedrock":
        return BedrockClaudeProvider(settings)
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    raise LLMProviderError(f"Unsupported LLM provider: {settings.llm_provider}")


def settings_base_url_safe(client: Any) -> str:
    return str(getattr(client, "base_url", "") or "")


def _anthropic_text(data: dict[str, Any]) -> str:
    parts = []
    for item in data.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def _last_user_message(request: LLMRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user":
            return message.content
    return request.messages[-1].content


def _mock_json_response(request: LLMRequest) -> str:
    return json.dumps(
        {
            "intent": "general_query",
            "action": "unknown",
            "entities": {},
            "raw_summary": _last_user_message(request),
            "confidence": 0.5,
        }
    )
