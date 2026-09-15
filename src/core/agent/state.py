"""Session and state management for CORE."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core import __version__


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict[str, Any]
    success: bool
    output: str
    evidence: str
    truncated: bool
    timestamp: str = ""


@dataclass
class Session:
    """A session captures the state of agent activity."""

    root: str
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def record_tool_call(
        self,
        tool: str,
        arguments: dict[str, Any],
        success: bool,
        output: str,
        evidence: str,
        truncated: bool,
    ) -> None:
        """Record a tool call for observability."""
        self.tool_calls.append(
            ToolCallRecord(
                tool=tool,
                arguments=arguments,
                success=success,
                output=output,
                evidence=evidence,
                truncated=truncated,
                timestamp=datetime.now().isoformat(),
            )
        )

    def summary(self) -> dict[str, Any]:
        """Produce a session summary."""
        succeeded = sum(1 for t in self.tool_calls if t.success)
        failed = len(self.tool_calls) - succeeded
        return {
            "root": self.root,
            "version": __version__,
            "started_at": self.started_at,
            "tool_calls": len(self.tool_calls),
            "succeeded": succeeded,
            "failed": failed,
            "truncated": sum(1 for t in self.tool_calls if t.truncated),
        }


def get_session_dir() -> Path:
    """Get the session directory for this project."""
    home = Path.home()
    session_dir = home / ".core" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session(session: Session) -> Path:
    """Persist a session to disk."""
    session_dir = get_session_dir()
    filename = datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
    path = session_dir / filename

    data = {
        "root": session.root,
        "started_at": session.started_at,
        "tool_calls": [
            {
                "tool": c.tool,
                "arguments": c.arguments,
                "success": c.success,
                "output": c.output,
                "evidence": c.evidence,
                "truncated": c.truncated,
                "timestamp": c.timestamp,
            }
            for c in session.tool_calls
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def load_recent_session() -> Session | None:
    """Load the most recent session if it exists."""
    session_dir = get_session_dir()
    if not session_dir.exists():
        return None
    sessions = sorted(session_dir.glob("*.json"))
    if not sessions:
        return None
    try:
        data = json.loads(sessions[-1].read_text(encoding="utf-8"))
        session = Session(root=data.get("root", os.getcwd()))
        for call in data.get("tool_calls", []):
            session.record_tool_call(
                tool=call.get("tool", "?"),
                arguments=call.get("arguments", {}),
                success=call.get("success", False),
                output=call.get("output", ""),
                evidence=call.get("evidence", ""),
                truncated=call.get("truncated", False),
            )
        return session
    except (json.JSONDecodeError, OSError):
        return None
