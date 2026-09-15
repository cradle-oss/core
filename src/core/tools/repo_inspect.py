"""Repository inspection tool for CORE."""

from core.context.repo import RepoInspector
from core.tools.registry import Tool, ToolResult


def inspect_repo_tool(workdir: str = ".") -> ToolResult:
    """Inspect a repository and summarize its structure."""
    try:
        inspector = RepoInspector(workdir)
        profile = inspector.inspect()
    except FileNotFoundError as e:
        return ToolResult(success=False, output="", error=str(e))

    lines = [
        f"Root: {profile.root}",
        f"Is Git repo: {profile.is_git_repo}",
    ]
    if profile.git_branch:
        lines.append(f"Branch: {profile.git_branch}")
    if profile.languages:
        lines.append(f"Languages: {', '.join(profile.languages)}")
    if profile.package_managers:
        lines.append(f"Package managers: {', '.join(profile.package_managers)}")
    lines.append(f"File count: {len(profile.files)}")
    lines.append(f"Has README: {profile.has_readme}")
    lines.append(f"Has AGENTS.md: {profile.has_agency}")
    lines.append(f"Has tests dir: {profile.test_dir}")
    lines.append(f"Has CI config: {profile.ci_config}")
    lines.append(f"Has docs dir: {profile.docs_dir}")
    if profile.top_level:
        lines.append("Top-level entries:")
        lines.extend(f"  {e}" for e in profile.top_level[:40])
        if len(profile.top_level) > 40:
            lines.append(f"  ... ({len(profile.top_level) - 40} more)")
    if profile.git_state:
        lines.append(f"Git state: {profile.git_state}")

    return ToolResult(
        success=True,
        output="\n".join(lines),
        evidence=(
            f"Inspected repository at {profile.root}: {len(profile.files)} files, "
            f"{len(profile.languages)} languages"
        ),
    )


def register_ctx_tool(registry) -> None:
    """Register repository inspection tools."""
    registry.register(
        Tool(
            name="inspect_repository",
            description=(
                "Inspect a repository: structure, languages, package managers, "
                "Git state."
            ),
            parameters={
                "type": "object",
                "properties": {"workdir": {"type": "string"}},
                "required": [],
            },
            handler=inspect_repo_tool,
        )
    )

    registry.register(
        Tool(
            name="relevant_files",
            description=(
                "Find files likely relevant to a task: manifests, README, config, "
                "plus keyword matches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "Keyword to filter by",
                    },
                    "limit": {"type": "integer", "description": "Max files"},
                    "workdir": {"type": "string"},
                },
                "required": [],
            },
            handler=lambda search_term=None, limit=30, workdir=".": relevant_files_tool(
                search_term, limit, workdir
            ),
        )
    )


def relevant_files_tool(
    search_term: str | None = None, limit: int = 30, workdir: str = "."
) -> ToolResult:
    """Find relevant files in the repository."""
    try:
        inspector = RepoInspector(workdir)
        files = inspector.relevant_files(search_term, limit)
    except FileNotFoundError as e:
        return ToolResult(success=False, output="", error=str(e))

    if not files:
        return ToolResult(
            success=True,
            output="",
            evidence="No relevant files found",
        )
    return ToolResult(
        success=True,
        output="\n".join(files),
        evidence=f"Found {len(files)} relevant files",
    )
