"""Core agent loop for CORE."""

from dataclasses import dataclass
from pathlib import Path

from core.agent.state import Session
from core.context.repo import RepoInspector
from core.integrations.discovery import (
    build_integration_briefing,
    discover_integrations,
)
from core.model.provider import Message, ModelProvider
from core.safety.approval import (
    ApprovalPolicy,
    InteractiveApprover,
    NonInteractiveApprover,
)
from core.tools.command import register_command_tool
from core.tools.files import register_file_tools
from core.tools.filesystem import register_filesystem_tools
from core.tools.git import register_git_tools
from core.tools.registry import ToolRegistry
from core.tools.repo_inspect import register_ctx_tool
from core.tools.search import register_search_tools
from core.ui.terminal import TerminalUI
from core.web import WebContext, register_web_tools

MODE_ITERATIONS = {
    "quick": 15,
    "investigate": 30,
    "deep": 80,
}

MODE_PREFIX = {
    "quick": (
        "QUICK MODE: Answer efficiently with minimal tool calls. "
        "Do not investigate beyond what is needed for a direct answer.\n\n"
    ),
    "investigate": "",
    "deep": (
        "DEEP INVESTIGATION MODE: Gather exhaustive evidence before answering. "
        "Use available tools systematically: read files, search code, trace "
        "callers, inspect tests, inspect CI checks, inspect review comments. "
        "Report evidence for every claim. Do not stop at the first match; "
        "follow the full trace. Show meaningful progress at each step.\n\n"
    ),
}

SYSTEM_PROMPT = (
    "You are CORE, a terminal-native developer agent for understanding, "
    "inspecting, and safely modifying software repositories.\n\n"
    "PHILOSOPHY: Observe → Inspect → Understand → Explore → Plan → Act → Verify.\n"
    "Never rush directly from a user request to editing files. First establish "
    "what exists, what is relevant, what you know, what you do not know, and "
    "what you intend to change.\n\n"
    "GROUNDING RULES:\n"
    "- Prefer evidence over confidence.\n"
    "- Never claim a test passed unless it actually ran.\n"
    "- Never claim a file was inspected unless it was actually read.\n"
    "- Never claim a dependency exists unless evidence supports it.\n"
    "- Never claim a change works unless it was verified.\n"
    "- Never claim a command succeeded if it failed.\n"
    "- Say \"I don't know\" when you don't know.\n"
    "- Do not invent files, commands, APIs, tests, or results.\n\n"
    "GIT: Git is a first-class system. Use the Git tools to inspect status, "
    "diffs, history, branches, and state before making changes. Understand the "
    "difference between the working tree, the index, local/remote branches, and "
    "commits. Before any destructive operation, inspect state and explain what "
    "will happen. Never run destructive commands without clear justification. "
    "Do not commit or push unless explicitly asked.\n\n"
    "PLANNING: For non-trivial changes, follow this loop:\n"
    "1. inspect\n"
    "2. identify relevant files\n"
    "3. understand dependencies\n"
    "4. formulate a concise plan\n"
    "5. make the changes\n"
    "6. inspect the diff\n"
    "7. run appropriate verification\n"
    "8. report the result\n\n"
    "Plans should reference real paths. Do not invent paths.\n\n"
    "WORKING: Make changes only through the provided tools. Use write_file / "
    "create_file / edit_file / append_file / delete_file to modify files. Use "
    "execute_command to run tests and build commands. Verify your work.\n\n"
    "When finished, summarize what you did with evidence: what was inspected, "
    "what was changed, what was verified, and any uncertainty remaining."
)

WEB_CAPABILITY_PREFIX = (
    "\n\nWEB RESEARCH / SCRAPING IS A FIRST-CLASS CAPABILITY.\n"
    "CORE works with the public web as an engineering tool. Do not reduce "
    "web access to a single 'fetch URL' tool.\n"
    "Know the difference between capabilities and choose deliberately:\n"
    "- fetch: retrieve raw HTML/text (web_fetch).\n"
    "- extract: turn a page into readable content (web_extract).\n"
    "- inspect: structure, links, metadata, scripts, styles (web_inspect, "
    "web_links, web_metadata).\n"
    "- search: find relevant pages (web_search), then verify at the source.\n"
    "- crawl: follow links within an explicit host/path scope, always bounded "
    "(web_crawl). Never crawl without a defined scope.\n"
    "- render: a JavaScript page needs the renderer, not plain fetch "
    "(web_render). Use it only when fetch/extract is insufficient.\n"
    "- compare: compare pages or versions (web_compare).\n\n"
    "Respect robots.txt, site terms, authentication boundaries, rate limits, "
    "and access restrictions. Never attempt to bypass authentication, "
    "CAPTCHAs, paywalls, or technical access controls.\n\n"
    "EVIDENCE PRINCIPLES FOR THE WEB:\n"
    "- URL reached does NOT mean page understood.\n"
    "- HTML received does NOT mean the rendered application is understood.\n"
    "- A search result found does NOT mean the claim is verified. Open and "
    "inspect the source when the claim matters.\n"
    "- If only partial content was obtained (truncation, error, blocked "
    "result), say so explicitly.\n"
    "Distinct evidence levels: fetched HTML → extracted text → rendered DOM → "
    "screenshot. Never claim rendered-page understanding when only HTML was "
    "fetched. Screenshots are captured as evidence, not visually interpreted "
    "unless a vision-capable model consumes them.\n\n"
    "WEB + CODE + GITHUB SHOULD CONNECT: Web research feeds repository work "
    "in the same context. E.g. 'check latest upstream docs and update this "
    "implementation': identify the authoritative documentation, inspect the "
    "relevant section, inspect the repository implementation, compare the "
    "two, decide whether a change is needed, make it, verify. Web research "
    "is not a disconnected chat feature."
)


@dataclass
class TurnResult:
    """Result of a single agent turn."""

    response: str
    tool_calls_made: int = 0
    session: Session | None = None


class AgentLoop:
    """The CORE agent loop: request → inspect → plan → act → verify."""

    def __init__(
        self,
        provider: ModelProvider,
        root: str | Path = ".",
        verbose: bool = False,
        trace: bool = False,
        interactive: bool = True,
        max_iterations: int | None = None,
        mode: str = "investigate",
        integrations=None,
        max_tokens_per_tool_call: int = 2000,
    ):
        self.provider = provider
        self.root = Path(root).resolve()
        self.verbose = verbose
        self.trace = trace
        self.mode = mode if mode in MODE_ITERATIONS else "investigate"
        self.max_iterations = max_iterations or MODE_ITERATIONS[self.mode]
        self.max_tokens_per_tool_call = max_tokens_per_tool_call

        self.ui = TerminalUI(verbose=verbose, trace=trace)
        self.registry = ToolRegistry()
        self.session = Session(root=str(self.root))

        # Safety layer
        self.policy = ApprovalPolicy()
        self.approver = (
            InteractiveApprover() if interactive else NonInteractiveApprover()
        )

        # Register core tools
        register_filesystem_tools(self.registry)
        register_file_tools(self.registry)
        register_command_tool(self.registry)
        register_git_tools(self.registry)
        register_ctx_tool(self.registry)
        register_search_tools(self.registry)

        # Web subsystem (first-class fetching/extraction/search/render)
        self.web = WebContext()
        register_web_tools(self.registry, self.web)
        self.web_briefing = self.web.briefing()

        # Discover and register first-class CLI integrations
        self.integrations = (
            integrations if integrations is not None else discover_integrations()
        )
        self.integrated_tools: list[str] = []
        for integration in self.integrations:
            registered = integration.register_tools(self.registry)
            self.integrated_tools.extend(t.name for t in registered)

        # Repository + integration briefing
        self.repo_briefing = self._build_repo_briefing()
        self.integration_briefing = build_integration_briefing(self.integrations)

    def _build_repo_briefing(self) -> str:
        """Build a brief repository overview for context."""
        try:
            inspector = RepoInspector(self.root)
            profile = inspector.inspect()
        except FileNotFoundError as e:
            return f"Working directory: {self.root}\n({e})"

        lines = [
            f"Working directory: {profile.root}",
            f"Is Git repo: {profile.is_git_repo}",
        ]
        if profile.git_branch:
            lines.append(f"Branch: {profile.git_branch}")
        if profile.languages:
            lines.append(f"Languages: {', '.join(profile.languages)}")
        if profile.package_managers:
            lines.append(f"Package managers: {', '.join(profile.package_managers)}")
        if profile.has_readme:
            lines.append("Has README")
        if profile.has_agency:
            lines.append("Has AGENTS.md")
        if profile.test_dir:
            lines.append("Has tests directory")
        if profile.ci_config:
            lines.append("Has CI configuration")
        return "\n".join(lines)

    def _inject_default_workdir(self, tool_call) -> None:
        """Default the workdir argument to the agent root when not supplied."""
        tool = self.registry.get(tool_call.name)
        if not tool:
            return
        properties = tool.parameters.get("properties", {})
        if "workdir" in properties and "workdir" not in tool_call.arguments:
            tool_call.arguments["workdir"] = str(self.root)

    def _web_block(self) -> str:
        return f"\n\nWEB:\n{self.web_briefing}"

    def chat(self, user_input: str) -> TurnResult:
        """Run the agent loop for a single user request."""
        mode_block = MODE_PREFIX.get(self.mode, "")
        integration_context = (
            f"\n\nINTEGRATIONS:\n{self.integration_briefing}\n"
            "Available integration tools: "
            f"{', '.join(self.integrated_tools) or '(none)'}"
            if self.integrated_tools
            else ""
        )
        messages = [
            Message(
                role="system",
                content=SYSTEM_PROMPT + mode_block + WEB_CAPABILITY_PREFIX,
            ),
            Message(
                role="system",
                content=(
                    f"REPOSITORY:\n{self.repo_briefing}"
                    f"{integration_context}"
                    f"{self._web_block()}"
                ),
            ),
            Message(role="user", content=user_input),
        ]

        tool_calls_made = 0
        self.ui.begin(user_input)

        for iteration in range(self.max_iterations):
            try:
                response = self.provider.chat(
                    messages, tools=self.registry.to_openai_tools()
                )
            except Exception as e:
                self.ui.error(f"Model call failed: {type(e).__name__}: {e}")
                return TurnResult(
                    response=f"I could not reach the model: {type(e).__name__}: {e}",
                    tool_calls_made=tool_calls_made,
                    session=self.session,
                )

            if not response.tool_calls:
                # Final response
                final = response.content or ""
                self.ui.result(final)
                return TurnResult(
                    response=final,
                    tool_calls_made=tool_calls_made,
                    session=self.session,
                )

            # Process tool calls
            for tool_call in response.tool_calls:
                tool_calls_made += 1
                self._inject_default_workdir(tool_call)
                self.ui.tool(tool_call.name, tool_call.arguments)

                # Approval assessment
                decision = self.policy.assess(tool_call.name, tool_call.arguments)
                if not decision.approved:
                    approved = self.approver.approve(decision)
                    if not approved:
                        self.session.record_tool_call(
                            tool=tool_call.name,
                            arguments=tool_call.arguments,
                            success=False,
                            output="",
                            evidence="Blocked by approval layer; user rejected",
                            truncated=False,
                        )
                        messages.append(
                            Message(
                                role="assistant",
                                content=f"Tool call rejected by user: {tool_call.name}",
                                tool_call_id=tool_call.id,
                            )
                        )
                        messages.append(
                            Message(
                                role="tool",
                                content="[USER REJECTED THIS TOOL CALL]",
                                tool_call_id=tool_call.id,
                            )
                        )
                        continue

                result = self.registry.execute(tool_call.name, tool_call.arguments)

                # Record for observability
                self.session.record_tool_call(
                    tool=tool_call.name,
                    arguments=tool_call.arguments,
                    success=result.success,
                    output=result.output,
                    evidence=result.evidence,
                    truncated=result.truncated,
                )

                self.ui.tool_result(tool_call.name, result)

                messages.append(
                    Message(
                        role="assistant",
                        content=None,
                        tool_call_id=tool_call.id,
                        tool_calls=[
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.name,
                                    "arguments": tool_call.arguments,
                                },
                            }
                        ],
                    )
                )

                truncated_output = result.output
                if (
                    result.output
                    and len(result.output) > self.max_tokens_per_tool_call * 4
                ):
                    truncated_output = result.output[
                        : self.max_tokens_per_tool_call * 4
                    ] + (
                        f"\n... [truncated for context, "
                        f"full {len(result.output)} chars available]"
                    )

                tool_result_text = ""
                if result.success:
                    tool_result_text = truncated_output
                elif result.output:
                    tool_result_text = (
                        f"{result.error}\n{truncated_output}"
                        if result.error
                        else truncated_output
                    )
                elif result.error:
                    tool_result_text = f"ERROR: {result.error}"
                else:
                    tool_result_text = "[no output]"

                messages.append(
                    Message(
                        role="tool",
                        content=tool_result_text,
                        tool_call_id=tool_call.id,
                    )
                )

        self.ui.warning(f"Reached max iterations ({self.max_iterations})")
        return TurnResult(
            response="Reached the maximum number of tool iterations. "
            "This may indicate the task is too complex or the loop is stuck.",
            tool_calls_made=tool_calls_made,
            session=self.session,
        )

    def plan(self, user_input: str) -> str:
        """Generate a plan without performing any actions (read-only)."""
        mode_block = MODE_PREFIX.get(self.mode, "")
        integration_context = (
            f"\n\nINTEGRATIONS:\n{self.integration_briefing}"
            if self.integrated_tools
            else ""
        )
        messages = [
            Message(
                role="system",
                content=SYSTEM_PROMPT + mode_block + WEB_CAPABILITY_PREFIX,
            ),
            Message(
                role="system",
                content=(
                    f"REPOSITORY:\n{self.repo_briefing}"
                    f"{integration_context}"
                    f"{self._web_block()}"
                ),
            ),
            Message(
                role="user",
                content=(
                    "Plan how to accomplish this task WITHOUT making any "
                    "changes:\n\n"
                    f"{user_input}\n\n"
                    "First inspect the repository, then produce a concise plan "
                    "referencing real paths. Explicitly state what you do NOT know."
                ),
            ),
        ]

        try:
            response = self.provider.chat(
                messages, tools=self.registry.to_openai_tools()
            )
        except Exception as e:
            self.ui.error(f"Model call failed: {type(e).__name__}: {e}")
            return f"Could not reach model: {e}"

        # Handle tool calls during planning
        for iteration in range(10):
            if not response.tool_calls:
                return response.content or ""

            for tool_call in response.tool_calls:
                self._inject_default_workdir(tool_call)
                result = self.registry.execute(tool_call.name, tool_call.arguments)
                self.session.record_tool_call(
                    tool=tool_call.name,
                    arguments=tool_call.arguments,
                    success=result.success,
                    output=result.output,
                    evidence=result.evidence,
                    truncated=result.truncated,
                )
                messages.append(
                    Message(
                        role="assistant",
                        content=None,
                        tool_call_id=tool_call.id,
                        tool_calls=[
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.name,
                                    "arguments": tool_call.arguments,
                                },
                            }
                        ],
                    )
                )
                messages.append(
                    Message(
                        role="tool",
                        content=result.output or result.error or "[no output]",
                        tool_call_id=tool_call.id,
                    )
                )

            try:
                response = self.provider.chat(
                    messages, tools=self.registry.to_openai_tools()
                )
            except Exception as e:
                self.ui.error(f"Model call failed: {type(e).__name__}: {e}")
                return f"Could not reach model: {e}"

        return "Planning exceeded iteration limit."

    def log_session(self) -> None:
        """Persist the session to disk for observability."""
        from core.agent.state import save_session

        try:
            path = save_session(self.session)
            if self.verbose:
                print(f"[CORE] Session saved: {path}")
        except OSError as e:
            self.ui.warning(f"Could not save session: {e}")
