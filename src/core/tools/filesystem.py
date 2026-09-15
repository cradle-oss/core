"""Filesystem tool implementations."""

from pathlib import Path

from core.tools.registry import Tool, ToolResult

MAX_FILE_LENGTH = 2000


def _resolve(base_workdir: str, path: str) -> Path:
    """Resolve a path relative to a working directory."""
    p = Path(path)
    if p.is_absolute() or not base_workdir:
        return p
    return Path(base_workdir) / p


def read_file_tool(
    path: str, offset: int = 1, limit: int = 2000, workdir: str = "."
) -> ToolResult:
    """Read a file from the repository."""
    file_path = _resolve(workdir, path)
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
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)

        start_idx = max(0, offset - 1)
        end_idx = min(total_lines, start_idx + limit)

        selected = lines[start_idx:end_idx]
        numbered = [f"{i + start_idx + 1}: {line}" for i, line in enumerate(selected)]
        output = "\n".join(numbered)

        truncated = end_idx < total_lines
        if truncated:
            output += f"\n... ({total_lines - end_idx} more lines truncated)"

        return ToolResult(
            success=True,
            output=output,
            evidence=f"Read {len(selected)} lines of {total_lines} from {path}",
            truncated=truncated,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to read file: {e}",
        )


def list_directory_tool(path: str = ".", workdir: str = ".") -> ToolResult:
    """List files in a directory."""
    dir_path = _resolve(workdir, path)
    if not dir_path.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"Directory does not exist: {path}",
        )
    if not dir_path.is_dir():
        return ToolResult(
            success=False,
            output="",
            error=f"Path is not a directory: {path}",
        )

    entries = []
    try:
        for entry in sorted(dir_path.iterdir()):
            name = entry.name
            if entry.is_dir():
                name += "/"
            entries.append(name)
        return ToolResult(
            success=True,
            output="\n".join(entries),
            evidence=f"Listed {len(entries)} entries in {path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to list directory: {e}",
        )


def search_files_tool(pattern: str, path: str = ".", workdir: str = ".") -> ToolResult:
    """Search for files matching a glob pattern."""
    base = _resolve(workdir, path)
    if not base.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"Path does not exist: {path}",
        )

    matches = []
    try:
        for item in base.glob(pattern):
            matches.append(str(item.relative_to(base)))
        if not matches:
            return ToolResult(
                success=True,
                output="",
                evidence=f"No files matched pattern '{pattern}' in {path}",
            )
        return ToolResult(
            success=True,
            output="\n".join(matches),
            evidence=f"Found {len(matches)} files matching '{pattern}' in {path}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to search files: {e}",
        )


def search_text_tool(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    max_results: int = 50,
    workdir: str = ".",
) -> ToolResult:
    """Search for text pattern in files."""
    import re

    base = _resolve(workdir, path)
    if not base.exists():
        return ToolResult(
            success=False,
            output="",
            error=f"Path does not exist: {path}",
        )

    try:
        regex = re.compile(pattern)
        results: list[str] = []

        if base.is_file():
            files = [base]
        else:
            files = [
                p
                for p in base.rglob("*")
                if p.is_file()
                and ".git" not in str(p)
                and "__pycache__" not in str(p)
                and "node_modules" not in str(p)
                and (
                    include is None
                    or p.suffix
                    in (
                        inc if include.startswith(".") else f".{include}"
                        for inc in [include]
                    )
                )
            ]

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(content.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{file_path}:{i}: {line.strip()}")
                        if len(results) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue
            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(
                success=True,
                output="",
                evidence=f"No matches found for '{pattern}'",
            )
        return ToolResult(
            success=True,
            output="\n".join(results),
            evidence=f"Found {len(results)} matches for '{pattern}'",
            truncated=len(results) >= max_results,
        )
    except re.error as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Invalid regex pattern: {e}",
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output="",
            error=f"Failed to search text: {e}",
        )


def register_filesystem_tools(registry) -> None:
    """Register filesystem tools with the registry."""
    workdir_prop = {
        "workdir": {"type": "string", "description": "Working directory root"},
    }
    registry.register(
        Tool(
            name="read_file",
            description=(
                "Read a file from the repository. Returns numbered lines "
                "with truncation after the limit."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Line offset (1-indexed)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read",
                    },
                    **workdir_prop,
                },
                "required": ["path"],
            },
            handler=read_file_tool,
        )
    )
    registry.register(
        Tool(
            name="list_directory",
            description=(
                "List files in a directory. Directories are suffixed with '/'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path",
                    },
                    **workdir_prop,
                },
                "required": [],
            },
            handler=list_directory_tool,
        )
    )
    registry.register(
        Tool(
            name="search_files",
            description="Search for files matching a glob pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base path",
                    },
                    **workdir_prop,
                },
                "required": ["pattern"],
            },
            handler=search_files_tool,
        )
    )
    registry.register(
        Tool(
            name="search_text",
            description="Search for a regex pattern in file contents.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path",
                    },
                    "include": {
                        "type": "string",
                        "description": "File extension filter",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max results",
                    },
                    **workdir_prop,
                },
                "required": ["pattern"],
            },
            handler=search_text_tool,
        )
    )
