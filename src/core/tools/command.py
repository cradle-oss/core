"""Shell command execution for CORE."""

import subprocess

from core.tools.registry import Tool, ToolResult

MAX_COMMAND_OUTPUT = 20000


def execute_command_tool(
    command: str, timeout: int = 60, workdir: str | None = None
) -> ToolResult:
    """Execute a shell command safely."""
    if not command or not command.strip():
        return ToolResult(
            success=False,
            output="",
            error="Empty command",
        )

    # Basic safety: refuse interactive commands without explicit approval
    dangerous_prefixes = [
        "rm -rf /",
        "sudo ",
        "curl http://",
        "wget http://",
    ]
    for prefix in dangerous_prefixes:
        if command.lstrip().startswith(prefix):
            return ToolResult(
                success=False,
                output="",
                error=f"Command blocked by safety policy: starts with '{prefix}'",
            )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workdir,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr and result.returncode != 0:
            output += f"\n[stderr]\n{result.stderr}"

        truncated = len(output) > MAX_COMMAND_OUTPUT
        if truncated:
            output = output[:MAX_COMMAND_OUTPUT] + "\n... (output truncated)"

        return ToolResult(
            success=result.returncode == 0,
            output=output,
            evidence=f"Command '{command}' exited with code {result.returncode}",
            truncated=truncated,
            error=None if result.returncode == 0 else f"exit code {result.returncode}",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            success=False,
            output="",
            error=f"Command timed out after {timeout}s: '{command}'",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to execute command: {type(e).__name__}: {e}",
        )


def register_command_tool(registry) -> None:
    """Register shell command execution tool."""
    registry.register(
        Tool(
            name="execute_command",
            description=(
                "Execute a shell command in the repository. Returns output "
                "and exit code. Use for running tests, build tools, or "
                "inspecting the environment."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds"},
                    "workdir": {"type": "string", "description": "Working directory"},
                },
                "required": ["command"],
            },
            handler=execute_command_tool,
            safe=False,  # Commands can be destructive; requires approval
        )
    )
