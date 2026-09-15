"""File modification tools for CORE."""

from pathlib import Path

from core.tools.registry import Tool, ToolResult


def write_file_tool(path: str, content: str, workdir: str = ".") -> ToolResult:
    """Write content to a file. Creates parent directories if needed."""
    file_path = (
        Path(workdir) / path if workdir and not Path(path).is_absolute() else Path(path)
    )
    try:
        if file_path.exists() and file_path.is_dir():
            return ToolResult(
                success=False,
                output="",
                error=f"Path is a directory: {path}",
            )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ToolResult(
            success=True,
            output=f"Wrote {len(content)} characters to {path}",
            evidence=f"Wrote {len(content)} chars to {file_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to write file: {type(e).__name__}: {e}",
        )


def edit_file_tool(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    workdir: str = ".",
) -> ToolResult:
    """Replace a string in a file. Requires old_string present in the file."""
    file_path = (
        Path(workdir) / path if workdir and not Path(path).is_absolute() else Path(path)
    )
    if not file_path.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"File does not exist: {path}",
        )
    try:
        content = file_path.read_text(encoding="utf-8")
        count = content.count(old_string)

        if count == 0:
            return ToolResult(
                success=False,
                output="",
                error=f"old_string not found in file: {path}",
            )

        if count > 1 and not replace_all:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Found {count} matches in {path}. "
                    "Pass replace_all=true or use more context."
                ),
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        file_path.write_text(new_content, encoding="utf-8")
        return ToolResult(
            success=True,
            output=f"Replaced {count} occurrence(s) in {path}",
            evidence=f"Edited {path}: {count} replacement(s)",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to edit file: {type(e).__name__}: {e}",
        )


def create_file_tool(path: str, content: str, workdir: str = ".") -> ToolResult:
    """Create a new file. Fails if the file already exists."""
    file_path = (
        Path(workdir) / path if workdir and not Path(path).is_absolute() else Path(path)
    )
    if file_path.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"File already exists: {path}. Use write_file to overwrite.",
        )
    return write_file_tool(path, content, workdir)


def append_file_tool(path: str, content: str, workdir: str = ".") -> ToolResult:
    """Append content to a file."""
    file_path = (
        Path(workdir) / path if workdir and not Path(path).is_absolute() else Path(path)
    )
    if not file_path.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"File does not exist: {path}",
        )
    try:
        with file_path.open("a", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(
            success=True,
            output=f"Appended {len(content)} characters to {path}",
            evidence=f"Appended {len(content)} chars to {file_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to append: {type(e).__name__}: {e}",
        )


def delete_file_tool(path: str, workdir: str = ".") -> ToolResult:
    """Delete a file from the working tree."""
    file_path = (
        Path(workdir) / path if workdir and not Path(path).is_absolute() else Path(path)
    )
    if not file_path.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"File does not exist: {path}",
        )
    if not file_path.is_file():
        return ToolResult(
            success=False,
            output="",
            error=f"Path is not a file: {path}",
        )
    try:
        file_path.unlink()
        return ToolResult(
            success=True,
            output=f"Deleted {path}",
            evidence=f"Deleted {file_path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to delete file: {type(e).__name__}: {e}",
        )


def register_file_tools(registry) -> None:
    """Register file modification tools."""
    registry.register(
        Tool(
            name="write_file",
            description=(
                "Write content to a file. Creates parent directories. "
                "Overwrites existing files."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "workdir": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=write_file_tool,
            safe=False,
        )
    )
    registry.register(
        Tool(
            name="create_file",
            description="Create a new file. Fails if the file already exists.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "workdir": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=create_file_tool,
            safe=False,
        )
    )
    registry.register(
        Tool(
            name="edit_file",
            description=(
                "Replace a string in a file. Requires old_string to exist in the file. "
                "If the string appears multiple times, pass replace_all=true "
                "or use more context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                    "workdir": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=edit_file_tool,
            safe=False,
        )
    )
    registry.register(
        Tool(
            name="append_file",
            description="Append content to the end of a file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "workdir": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=append_file_tool,
            safe=False,
        )
    )
    registry.register(
        Tool(
            name="delete_file",
            description="Delete a file from the working tree.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "workdir": {"type": "string"},
                },
                "required": ["path"],
            },
            handler=delete_file_tool,
            safe=False,
        )
    )
