"""One provider client for Ollama, OpenAI and OpenRouter.

All three speak the same chat-completions wire format including tool calls, so
switching between a local model and a hosted one is base_url + model. Do not
write a second client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from presence.config import settings


@dataclass
class Delta:
    text: str | None = None
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    done: bool = False
    finish_reason: str | None = None


@dataclass
class _PartialCall:
    id: str = ""
    name: str = ""
    args: str = ""


@dataclass
class OpenAICompatProvider:
    name: str = settings.provider
    base_url: str = settings.base_url
    api_key: str = settings.api_key
    _client: AsyncOpenAI = field(init=False)

    def __post_init__(self) -> None:
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "none",
            timeout=180.0,
            max_retries=1,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        **opts: Any,
    ) -> AsyncIterator[Delta]:
        """Stream one assistant turn.

        Yields text deltas as they arrive and one final Delta carrying the
        complete tool_calls list, because a tool call is only actionable once
        its arguments have finished arriving.
        """
        kwargs: dict[str, Any] = {
            "model": model or settings.model_main,
            "messages": messages,
            "stream": True,
            "temperature": opts.pop("temperature", settings.temperature),
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.name == "ollama":
            # Ollama-specific knobs ride in extra_body; other providers ignore them.
            kwargs["extra_body"] = {
                "options": {"num_ctx": settings.num_ctx},
                "keep_alive": settings.keep_alive,
            }
        kwargs.update(opts)

        partials: dict[int, _PartialCall] = {}
        finish: str | None = None

        stream = await self._client.chat.completions.create(**kwargs)
        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                d = choice.delta
                if choice.finish_reason:
                    finish = choice.finish_reason

                thinking = getattr(d, "reasoning_content", None) or getattr(d, "reasoning", None)
                if thinking:
                    yield Delta(thinking=thinking)
                if d.content:
                    yield Delta(text=d.content)

                for tc in d.tool_calls or []:
                    slot = partials.setdefault(tc.index, _PartialCall())
                    if tc.id:
                        slot.id = tc.id
                    if tc.function and tc.function.name:
                        slot.name = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot.args += tc.function.arguments
        finally:
            await stream.close()

        calls = [
            {
                "id": p.id or f"call_{i}",
                "type": "function",
                "function": {"name": p.name, "arguments": p.args or "{}"},
            }
            for i, p in sorted(partials.items())
            if p.name
        ]
        yield Delta(done=True, tool_calls=calls or None, finish_reason=finish)


class ModelRouter:
    """Two tiers. Cheap model for mechanical work, main model for the conversation."""

    FAST_TASKS = {"summarize", "classify", "title"}

    @staticmethod
    def pick(task: str = "chat") -> str:
        return settings.model_fast if task in ModelRouter.FAST_TASKS else settings.model_main
