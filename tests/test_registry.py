from core.tools.registry import Tool, ToolRegistry, ToolResult


def test_registry_register_and_get():
    registry = ToolRegistry()

    def handler():
        return ToolResult(success=True, output="hello")

    registry.register(
        Tool(
            name="test_tool",
            description="Test tool",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )

    assert registry.get("test_tool") is not None
    assert registry.get("missing") is None
    assert "test_tool" not in [t.name for t in []]


def test_registry_execute_success():
    registry = ToolRegistry()

    def handler(msg: str):
        return ToolResult(success=True, output=msg, evidence=f"returned {msg}")

    registry.register(
        Tool(
            name="echo",
            description="Echo",
            parameters={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
            handler=handler,
        )
    )

    result = registry.execute("echo", {"msg": "hi"})
    assert result.success
    assert result.output == "hi"
    assert result.evidence == "returned hi"


def test_registry_execute_missing():
    registry = ToolRegistry()
    result = registry.execute("nonexistent", {})
    assert not result.success
    assert "not found" in result.error


def test_registry_execute_exception():
    registry = ToolRegistry()

    def handler():
        raise ValueError("boom")

    registry.register(
        Tool(
            name="fail",
            description="Fails",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )

    result = registry.execute("fail", {})
    assert not result.success
    assert "boom" in result.error


def test_registry_to_openai_tools():
    registry = ToolRegistry()

    registry.register(
        Tool(
            name="read_file",
            description="Read a file",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            handler=lambda path: ToolResult(success=True, output=""),
        )
    )

    tools = registry.to_openai_tools()
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read_file"
    assert "parameters" in tools[0]["function"]
