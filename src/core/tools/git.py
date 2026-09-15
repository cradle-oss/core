"""Git tool implementations for CORE."""

from core.git.operations import GitError, GitRepository
from core.tools.registry import Tool, ToolResult


def _get_repo(workdir: str = ".") -> GitRepository | None:
    """Get a GitRepository instance or None."""
    try:
        return GitRepository(workdir)
    except GitError:
        return None


def git_status_tool(workdir: str = ".") -> ToolResult:
    """Inspect Git status including staged, unstaged, and untracked files."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    status = repo.status()
    output = []
    if status["staged"]:
        output.append("## Staged changes")
        output.extend(f"  {f}" for f in status["staged"])
    if status["unstaged"]:
        output.append("## Unstaged changes")
        output.extend(f"  {f}" for f in status["unstaged"])
    if status["untracked"]:
        output.append("## Untracked files")
        output.extend(f"  {f}" for f in status["untracked"])
    if not any(status.values()):
        output.append("Working tree clean")

    return ToolResult(
        success=True,
        output="\n".join(output),
        evidence=(
            f"Status: {len(status['staged'])} staged, "
            f"{len(status['unstaged'])} unstaged, "
            f"{len(status['untracked'])} untracked"
        ),
    )


def git_diff_tool(
    cached: bool = False,
    stat: bool = False,
    path: str | None = None,
    workdir: str = ".",
) -> ToolResult:
    """Inspect the Git diff (working tree or staged)."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    try:
        if path:
            diff = repo.diff_of(path)
        else:
            diff = repo.diff(cached=cached, stat=stat)
        if not diff:
            area = "staged changes" if cached else "working tree changes"
            return ToolResult(
                success=True,
                output="",
                evidence=f"No {area}",
            )
        return ToolResult(
            success=True,
            output=diff,
            evidence=(
                f"Diff of {path or ('staged area' if cached else 'working tree')}, "
                f"{len(diff)} characters"
            ),
            truncated=len(diff) > 12000,
        )
    except GitError as e:
        return ToolResult(success=False, output="", error=str(e))


def git_log_tool(num: int = 10, workdir: str = ".") -> ToolResult:
    """Inspect commit history."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    try:
        log = repo.log(num=num)
        if not log:
            return ToolResult(success=True, output="", evidence="No commits yet")
        return ToolResult(
            success=True,
            output=log,
            evidence=f"Recent {num} commits",
        )
    except GitError as e:
        return ToolResult(success=False, output="", error=str(e))


def git_show_tool(ref: str, workdir: str = ".") -> ToolResult:
    """Show a specific commit."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    try:
        output = repo.show(ref)
        return ToolResult(
            success=True,
            output=output,
            evidence=f"Showed commit {ref}",
            truncated=len(output) > 12000,
        )
    except GitError as e:
        return ToolResult(success=False, output="", error=str(e))


def git_branch_tool(workdir: str = ".") -> ToolResult:
    """List branches."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    try:
        output = repo.branch(all=True)
        return ToolResult(
            success=True,
            output=output,
            evidence=f"Branches: {len(output.splitlines())}",
        )
    except GitError as e:
        return ToolResult(success=False, output="", error=str(e))


def git_summary_tool(workdir: str = ".") -> ToolResult:
    """Summarize repository Git state: branch, status, remotes, recent history."""
    repo = _get_repo(workdir)
    if not repo:
        return ToolResult(
            success=False,
            output="",
            error=f"Not a Git repository at {workdir}",
        )
    try:
        lines = [
            f"Root: {repo.root}",
            f"Branch: {repo.current_branch()}",
            f"Remotes: {', '.join(repo.remotes()) or 'none'}",
        ]
        status = repo.status()
        counts = (
            f"Staged: {len(status['staged'])}, "
            f"Unstaged: {len(status['unstaged'])}, "
            f"Untracked: {len(status['untracked'])}"
        )
        lines.append(f"Working tree: {counts}")
        log = repo.log(num=5)
        if log:
            lines.append("Recent commits:")
            lines.append(log)
        return ToolResult(
            success=True,
            output="\n".join(lines),
            evidence=f"Git summary for {repo.root}",
        )
    except GitError as e:
        return ToolResult(success=False, output="", error=str(e))


def register_git_tools(registry) -> None:
    """Register Git tools with the registry."""
    for tool in [
        Tool(
            name="git_status",
            description="Inspect Git status: staged, unstaged, untracked changes.",
            parameters={
                "type": "object",
                "properties": {"workdir": {"type": "string"}},
                "required": [],
            },
            handler=git_status_tool,
        ),
        Tool(
            name="git_diff",
            description=(
                "Inspect the Git diff (working tree or staged). "
                "Use cached=true for staged changes."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cached": {"type": "boolean", "description": "Show staged changes"},
                    "stat": {"type": "boolean", "description": "Show only a summary"},
                    "path": {"type": "string", "description": "Limit to a file"},
                    "workdir": {"type": "string"},
                },
                "required": [],
            },
            handler=git_diff_tool,
        ),
        Tool(
            name="git_log",
            description="Inspect commit history (oneline format).",
            parameters={
                "type": "object",
                "properties": {
                    "num": {"type": "integer", "description": "Number of commits"},
                    "workdir": {"type": "string"},
                },
                "required": [],
            },
            handler=git_log_tool,
        ),
        Tool(
            name="git_show",
            description="Show a specific commit: message, author, diff.",
            parameters={
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Commit reference"},
                    "workdir": {"type": "string"},
                },
                "required": ["ref"],
            },
            handler=git_show_tool,
        ),
        Tool(
            name="git_branch",
            description="List local and remote branches.",
            parameters={
                "type": "object",
                "properties": {"workdir": {"type": "string"}},
                "required": [],
            },
            handler=git_branch_tool,
        ),
        Tool(
            name="git_summary",
            description=(
                "Summarize repository Git state: branch, remotes, working tree, "
                "recent history."
            ),
            parameters={
                "type": "object",
                "properties": {"workdir": {"type": "string"}},
                "required": [],
            },
            handler=git_summary_tool,
        ),
    ]:
        registry.register(tool)
