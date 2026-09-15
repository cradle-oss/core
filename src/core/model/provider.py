"""Model provider abstraction for CORE."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class OpenAIProvider(ModelProvider):
    """OpenAI-compatible model provider."""

    def __init__(self, model: str = "gpt-4", api_key: str | None = None):
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=api_key)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> ModelResponse:
        """Send messages to OpenAI-compatible API."""
        formatted_messages = [
            {"role": m.role, "content": m.content} for m in messages if m.content
        ]

        kwargs: dict[str, Any] = {"model": self.model, "messages": formatted_messages}
        if tools:
            kwargs["tools"] = tools

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                import json

                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

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
