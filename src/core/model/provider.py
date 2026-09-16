"""Model provider abstraction for CORE.

Configuration precedence (deterministic):
1. An explicit model (the --model flag or CORE_MODEL) plus a configured
   provider wins. A model starting with "openrouter/" selects OpenRouter
   and requires OPENROUTER_API_KEY; any other model selects direct OpenAI
   and requires OPENAI_API_KEY.
2. Otherwise, when OPENROUTER_API_KEY is set, use OpenRouter with the
   default "openrouter/auto" route.
3. Otherwise, when OPENAI_API_KEY is set, use direct OpenAI with the
   current default model.
4. Otherwise, fail clearly with setup instructions.

No API key value is ever included in messages, logs, or evidence.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "openrouter/auto"
OPENAI_DEFAULT_MODEL = "gpt-6-astra"
ZEN_BASE_URL = "https://opencode.ai/zen/v1"
ZEN_DEFAULT_MODEL = "big-pickle"

SETUP_INSTRUCTIONS = (
    "CORE needs a model provider. Set OPENROUTER_API_KEY for the default "
    "openrouter/auto route, or set OPENAI_API_KEY for direct OpenAI access. "
    "Optional: set CORE_MODEL to request a specific model."
)


class ProviderConfigError(RuntimeError):
    """Raised when no usable provider/model combination is configured."""


@dataclass
class ProviderConfig:
    """Resolved provider selection. Never carries key material in repr."""

    provider: str  # "openrouter" or "openai"
    base_url: str | None
    api_key: str
    requested_model: str

    def __repr__(self) -> str:
        return (
            f"ProviderConfig(provider={self.provider!r}, "
            f"base_url={self.base_url!r}, "
            f"requested_model={self.requested_model!r}, api_key='...')"
        )


@dataclass
class Message:
    role: str  # "system", "user", "assistant", "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""


class ModelProvider(ABC):
    """Abstract model provider interface."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Send messages to the model and get a response."""
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        pass


def resolve_provider_config(model_arg: str | None = None) -> ProviderConfig:
    """Resolve which provider and model to use from flags and environment.

    An explicit model comes from the --model flag first, then CORE_MODEL.
    Returns a ProviderConfig, or raises ProviderConfigError with setup
    instructions when no usable combination exists.
    """
    explicit = (model_arg or "").strip() or os.environ.get("CORE_MODEL", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    zen_key = os.environ.get("CORE_ZEN_API_KEY", "")

    if explicit:
        if explicit.startswith("openrouter/"):
            if not openrouter_key:
                raise ProviderConfigError(
                    f"Model {explicit!r} selects OpenRouter, but "
                    "OPENROUTER_API_KEY is not set. " + SETUP_INSTRUCTIONS
                )
            return ProviderConfig(
                provider="openrouter",
                base_url=OPENROUTER_BASE_URL,
                api_key=openrouter_key,
                requested_model=explicit,
            )
        if explicit.startswith("zen/"):
            if not zen_key:
                raise ProviderConfigError(
                    f"Model {explicit!r} selects OpenCode Zen, but "
                    "CORE_ZEN_API_KEY is not set. " + SETUP_INSTRUCTIONS
                )
            return ProviderConfig(
                provider="zen",
                base_url=ZEN_BASE_URL,
                api_key=zen_key,
                requested_model=explicit,
            )
        if not openai_key:
            raise ProviderConfigError(
                f"Model {explicit!r} selects direct OpenAI, but "
                "OPENAI_API_KEY is not set. " + SETUP_INSTRUCTIONS
            )
        return ProviderConfig(
            provider="openai",
            base_url=None,
            api_key=openai_key,
            requested_model=explicit,
        )

    if openrouter_key:
        return ProviderConfig(
            provider="openrouter",
            base_url=OPENROUTER_BASE_URL,
            api_key=openrouter_key,
            requested_model=OPENROUTER_DEFAULT_MODEL,
        )
    if zen_key:
        return ProviderConfig(
            provider="zen",
            base_url=ZEN_BASE_URL,
            api_key=zen_key,
            requested_model=ZEN_DEFAULT_MODEL,
        )
    if openai_key:
        return ProviderConfig(
            provider="openai",
            base_url=None,
            api_key=openai_key,
            requested_model=OPENAI_DEFAULT_MODEL,
        )
    raise ProviderConfigError(SETUP_INSTRUCTIONS)


class OpenAIProvider(ModelProvider):
    """OpenAI-compatible model provider (direct OpenAI or OpenRouter)."""

    def __init__(
        self,
        model: str = OPENAI_DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str = "openai",
    ):
        from openai import OpenAI

        self.model = model
        self.provider = provider
        self.base_url = base_url
        self.last_actual_model: str | None = None
        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Send messages to OpenAI-compatible API."""
        formatted_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.content
        ]

        wire_model = self.model
        # The "openrouter/" and "zen/" prefixes are CORE's internal routing
        # hints that select the OpenRouter or Zen provider. They are not valid
        # model ids on the wire: only the literal "openrouter/auto" token is
        # accepted upstream as an auto-routing alias. Strip the prefix for any
        # other explicitly selected model before sending.
        if wire_model.startswith("openrouter/") and wire_model != "openrouter/auto":
            wire_model = wire_model[len("openrouter/") :]
        elif wire_model.startswith("zen/"):
            wire_model = wire_model[len("zen/") :]

        kwargs: dict[str, Any] = {"model": wire_model, "messages": formatted_messages}
        if tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                import json

                raw_arguments = tc.function.arguments or "{}"
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(raw_arguments),
                    )
                )

        self.last_actual_model = response.model or self.model

        return ModelResponse(
            content=choice.message.content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens
                if response.usage
                else 0,
            },
            model=response.model,
        )

    def count_tokens(self, text: str) -> int:
        """Rough token estimate (4 chars per token)."""
        return len(text) // 4
