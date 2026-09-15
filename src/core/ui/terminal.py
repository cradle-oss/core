"""Terminal UI for CORE - calm, evidence-based, no fake progress."""


class TerminalUI:
    """Renders agent activity honestly and concisely."""

    def __init__(self, verbose: bool = False, trace: bool = False):
        self.verbose = verbose
        self.trace = trace
        self._spinner_active = False

    def begin(self, what: str) -> None:
        """Announce a real operation that is about to be performed."""
        print(f"▸ {what}")

    def tool(self, name: str, args: dict) -> None:
        """Announce a tool invocation without pretending it's progressing."""
        if self.verbose:
            print(f"  └─ tool: {name}({self._brief(args)})")

    def tool_result(self, name: str, result) -> None:
        """Report tool result with evidence."""
        status = "ok" if result.success else "FAILED"
        detail = ""
        if result.truncated:
            detail = " (truncated)"
        print(f"  └─ {name}: {status}{detail}")

    def info(self, message: str) -> None:
        """Print informational output."""
        print(f"  {message}")

    def result(self, message: str) -> None:
        """Print the final result."""
        print(message)

    def error(self, message: str) -> None:
        """Print an error."""
        print(f"✗ {message}")

    def warning(self, message: str) -> None:
        """Print a warning."""
        print(f"! {message}")

    def _brief(self, args: dict) -> str:
        """Create a brief string representation of tool arguments."""
        parts = []
        for key, value in args.items():
            val = str(value)
            if len(val) > 60:
                val = val[:57] + "..."
            parts.append(f"{key}={val}")
        return ", ".join(parts)


def format_evidence(result) -> str:
    """Format evidence from a tool result for display."""
    lines = []
    if result.evidence:
        lines.append(result.evidence)
    if result.output:
        lines.append("")
        lines.append(result.output)
    if result.error:
        lines.append("")
        lines.append(f"Error: {result.error}")
    return "\n".join(lines)
