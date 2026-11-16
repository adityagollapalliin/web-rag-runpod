"""Chat models that stream an answer: a real HTTP client and a deterministic fake."""

import json
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from app.core.config import Settings

Messages = list[dict[str, str]]

_FIRST_SOURCE = re.compile(r"^\[1\][^\n]*\n(.+?)(?:\n\n|\Z)", re.MULTILINE | re.DOTALL)


class ChatModel(Protocol):
    def stream(self, messages: Messages) -> AsyncIterator[str]:
        """Yield the answer as text fragments."""
        ...

    async def aclose(self) -> None: ...


class FakeChatModel:
    """Quotes the opening of the first source, word by word. For tests and local runs."""

    async def stream(self, messages: Messages) -> AsyncIterator[str]:
        match = _FIRST_SOURCE.search(messages[-1]["content"])
        quote = match.group(1).split(". ")[0].strip().rstrip(".") if match else "nothing"
        for word in f"[fake model] The top source says: {quote}. [1]".split(" "):
            yield word + " "

    async def aclose(self) -> None:
        return None


class OpenAIChatModel:
    """Streams from an OpenAI-style `/v1/chat/completions` endpoint (vLLM on Runpod)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        max_tokens: int,
        extra_body: dict[str, Any] | None = None,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._extra_body = extra_body or {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def stream(self, messages: Messages) -> AsyncIterator[str]:
        body = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "max_tokens": self._max_tokens,
            "temperature": 0.2,
            **self._extra_body,
        }
        async with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                choices = json.loads(data).get("choices") or []
                if choices and (text := choices[0].get("delta", {}).get("content")):
                    yield text

    async def aclose(self) -> None:
        await self._client.aclose()


def build_chat_model(settings: Settings) -> ChatModel:
    if settings.chat_provider == "fake":
        return FakeChatModel()
    return OpenAIChatModel(
        settings.chat_url, settings.chat_model, settings.chat_max_tokens, settings.chat_extra_body
    )
