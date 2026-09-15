"""Tool registry for the CORE agent."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    success: bool
    output: str
    evidence: str = ""  # What was actually observed
    truncated: bool = False
    error: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for parameters
    handler: Callable[..., ToolResult]
    safe: bool = True  # Whether this tool modifies state


class ToolRegistry:
    """Registry of available tools for the agent."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """Convert tools to OpenAI function calling format."""
        tools = []
        for tool in self._tools.values():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return tools

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute a tool with given arguments."""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' not found",
            )

        try:
            return tool.handler(**arguments)
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{name}' failed: {type(e).__name__}: {e}",
            )
