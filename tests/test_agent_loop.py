from core.agent.loop import AgentLoop
from core.model.provider import ModelProvider, ModelResponse, ToolCall


class FakeProvider(ModelProvider):
    """A provider that returns canned responses for testing."""

    def __init__(self, calls):
        self._calls = calls
        self.chat_count = 0

    def chat(self, messages, tools=None):
        self.chat_count += 1
        idx = min(self.chat_count - 1, len(self._calls) - 1)
        return self._calls[idx]

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


def test_agent_loop_single_tool_call(tmp_path):
    provider = FakeProvider(
        [
            ModelResponse(
                content=None,
                tool_calls=[ToolCall(id="tc1", name="list_directory", arguments={})],
                model="fake",
            ),
            ModelResponse(
                content="Done inspecting.",
                tool_calls=[],
                model="fake",
            ),
        ]
    )

    # Create a directory with known contents
    (tmp_path / "file_a.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme\n", encoding="utf-8")

    agent = AgentLoop(
        provider=provider,
        root=str(tmp_path),
        interactive=False,
        verbose=False,
    )
    result = agent.chat("inspect the directory")

    assert result.response == "Done inspecting."
    assert result.tool_calls_made == 1
    assert len(result.session.tool_calls) == 1
    call = result.session.tool_calls[0]
    assert call.tool == "list_directory"
    assert call.success
    assert "file_a.py" in call.output


def test_agent_loop_rejected_tool_call(tmp_path):
    provider = FakeProvider(
        [
            ModelResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="write_file",
                        arguments={"path": "x.py", "content": "print(1)"},
                    )
                ],
                model="fake",
            ),
            ModelResponse(
                content="Write was rejected.",
                tool_calls=[],
                model="fake",
            ),
        ]
    )

    agent = AgentLoop(
        provider=provider,
        root=str(tmp_path),
        interactive=False,  # Non-interactive: no approval prompt, tool blocked
        verbose=False,
    )
    result = agent.chat("write a file")

    assert result.response == "Write was rejected."
    # The tool call is recorded and failed (blocked by safety)
    assert len(result.session.tool_calls) == 1
    assert not result.session.tool_calls[0].success


def test_agent_loop_model_error(tmp_path):
    class BrokenProvider(FakeProvider):
        def chat(self, messages, tools=None):
            raise ConnectionError("network down")

    agent = AgentLoop(
        provider=BrokenProvider([]),
        root=str(tmp_path),
        interactive=False,
        verbose=False,
    )
    result = agent.chat("do something")
    assert "network down" in result.response


def test_agent_loop_modes(tmp_path):
    for mode, expected in (("quick", 15), ("investigate", 30), ("deep", 80)):
        agent = AgentLoop(
            provider=FakeProvider([]),
            root=str(tmp_path),
            interactive=False,
            mode=mode,
            integrations=[],  # hermetic: no real gh/vercel probing
        )
        assert agent.max_iterations == expected

    # invalid mode falls back to investigate
    agent = AgentLoop(
        provider=FakeProvider([]),
        root=str(tmp_path),
        interactive=False,
        mode="bogus",
        integrations=[],
    )
    assert agent.mode == "investigate"
    assert agent.max_iterations == 30


def test_agent_loop_deep_mode_adds_prefix(tmp_path):
    from core.agent.loop import MODE_PREFIX, AgentLoop
    from core.model.provider import ModelResponse

    seen_messages = []

    class CapturingProvider(FakeProvider):
        def chat(self, messages, tools=None):
            seen_messages.append(messages)
            return ModelResponse(content="done", tool_calls=[], model="fake")

    provider = CapturingProvider([])
    agent = AgentLoop(
        provider=provider,
        root=str(tmp_path),
        interactive=False,
        mode="deep",
        integrations=[],
    )
    agent.chat("inspect")
    system_text = seen_messages[0][0].content
    assert MODE_PREFIX["deep"] in system_text
    assert "REPOSITORY:" in seen_messages[0][1].content
