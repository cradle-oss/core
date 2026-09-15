"""Integration boundary for CORE.

Developer-workflow CLIs (gh, vercel, ...) are first-class integrations, not
arbitrary shell commands. Each integration:

1. Probes availability (binary present + authenticated) before registering tools.
2. Wraps the native CLI, preferring structured JSON output over text scraping.
3. Returns bounded, structured evidence back into the agent context.

The generic shell remains the escape hatch, not the primary abstraction.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from core.tools.registry import Tool


@dataclass
class CommandResult:
    """Result of running an external command."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Runner = Callable[..., "CommandResult"]


def run_command(
    cmd: list[str],
    timeout: int = 60,
    cwd: str | None = None,
) -> CommandResult:
    """Run an external command in exec-form (no shell interpolation)."""
    import subprocess

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return CommandResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=124, stdout="", stderr="timed out")
    except FileNotFoundError:
        return CommandResult(returncode=127, stdout="", stderr="command not found")


@dataclass
class Availability:
    """Result of probing whether an integration is usable."""

    name: str
    available: bool
    version: str | None = None
    authenticated: bool = False
    identity: str | None = None
    detail: str = ""

    @property
    def status(self) -> str:
        if not self.available:
            return f"{self.name}: unavailable"
        if not self.authenticated:
            return (
                f"{self.name}: installed ({self.version or '?'}) but not authenticated"
            )
        return f"{self.name}: available ({self.version or '?'})" + (
            f" as {self.identity}" if self.identity else ""
        )


class Integration(ABC):
    """Base class for developer-workflow integrations."""

    name: str = ""

    def __init__(self, runner: Runner | None = None):
        self._runner = runner or run_command
        self._availability: Availability | None = None

    def availability(self) -> Availability:
        """Probe and cache availability."""
        if self._availability is None:
            self._availability = self._probe_availability()
        return self._availability

    @abstractmethod
    def _base_command(self) -> list[str]:
        """The argv prefix for this integration's CLI (e.g. ['gh'])."""
        ...

    @abstractmethod
    def _probe_availability(self) -> Availability:
        """Determine whether this integration is usable right now."""
        ...

    @abstractmethod
    def tools(self) -> list[Tool]:
        """Return the tools this integration exposes when available."""
        ...

    def register_tools(self, registry) -> list[Tool]:
        """Register tools when available and authenticated. Returns registered."""
        if not self.availability().available or not self.availability().authenticated:
            return []
        tools = self.tools()
        for tool in tools:
            registry.register(tool)
        return tools

    def run(
        self,
        args: list[str],
        timeout: int = 60,
        cwd: str | None = None,
    ) -> CommandResult:
        """Run the underlying CLI: base command + args, exec-form."""
        return self._runner(self._base_command() + args, timeout=timeout, cwd=cwd)

    def require_available(self) -> None:
        """Raise if this integration is not available/authenticated."""
        av = self.availability()
        if not av.available:
            raise IntegrationError(f"{self.name} is not installed")
        if not av.authenticated:
            raise IntegrationError(f"{self.name} is not authenticated")


class IntegrationError(Exception):
    """Raised when an integration cannot be used."""

    pass


def truncate_output(text: str, limit: int = 20000) -> tuple[str, bool]:
    """Bound output for agent context, returning (text, was_truncated)."""
    if len(text) <= limit:
        return text, False
    return (
        text[:limit] + f"\n... [truncated: {len(text) - limit} more chars]",
        True,
    )
