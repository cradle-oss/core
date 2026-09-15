"""Repository search: code search with explicit depth.

Depth capabilities:
- search_code: line search for a pattern.
- find_definitions: definition-looking declarations of a symbol.
- find_references: every occurrence of a symbol across the tree.

Search backend preference:
1. ripgrep (rg) when installed: fast path.
2. Python walker fallback: bounded, works everywhere, never claims more
   than it actually scanned.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.tools.registry import Tool, ToolResult

DEFAULT_MAX = 100
MAX_OUTPUT = 20000

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    "target",
}


@dataclass
class Match:
    path: str
    line: int
    text: str


def _should_exclude(relative: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in relative.parts)


def _find_files(root: Path) -> list[Path]:
    """Walk the repository, returning file paths (excluding VCS/deps)."""
    files: list[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, onerror=None):
        dirnames[:] = [
            d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")
        ]
        for name in filenames:
            files.append(Path(dirpath) / name)
    return files


def _rg_available() -> bool:
    return shutil.which("rg") is not None


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _comma_targets(pattern: str, globs: list[str]) -> list[str]:
    """Expand a comma-separated pattern list, keep globs separate."""
    return [p.strip() for p in pattern.split(",") if p.strip()]


class SearchEngine:
    """Runs searches against a repository root."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    def _rg_search(
        self,
        pattern: str,
        include: str | None,
        max_results: int,
        line_numbers: bool = True,
        whole_word: bool = False,
    ) -> tuple[list[Match], bool]:
        cmd = ["rg", "--no-heading", "--color", "never", "-n", "-m", str(max_results)]
        if include:
            cmd += ["-g", include]
        if whole_word:
            pattern = rf"\b{re.escape(pattern)}\b"
        cmd += [pattern, str(self.root)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.SubprocessError, OSError):
            return [], False
        trimmed = _strip_ansi(result.stdout)
        matches: list[Match] = []
        for line in trimmed.splitlines()[:max_results]:
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                matches.append(Match(path=parts[0], line=int(parts[1]), text=parts[2]))
        return matches, len(matches) >= max_results

    def _walk_search(
        self,
        pattern: str,
        include: str | None,
        max_results: int,
        regex: bool = True,
        whole_word: bool = False,
    ) -> tuple[list[Match], bool]:
        """Python fallback search over the tree."""
        if regex:
            try:
                compiled = re.compile(pattern)
            except re.error:
                return [], False
        else:
            compiled = None

        matches: list[Match] = []
        for file_path in _find_files(self.root):
            if include:
                suffix = file_path.suffix
                wanted = include.lstrip("*.")
                if suffix != f".{wanted}":
                    continue
            try:
                rel = file_path.relative_to(self.root)
                with file_path.open("r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        line_clean = line.rstrip("\n")
                        if compiled:
                            found = compiled.search(line_clean)
                        elif whole_word:
                            found = re.search(rf"\b{re.escape(pattern)}\b", line_clean)
                        else:
                            found = pattern in line_clean
                        if found:
                            matches.append(
                                Match(path=str(rel), line=lineno, text=line_clean)
                            )
                            if len(matches) >= max_results:
                                return matches, True
            except OSError:
                continue
        return matches, len(matches) >= max_results

    def search_lines(
        self,
        pattern: str,
        include: str | None = None,
        max_results: int = DEFAULT_MAX,
        whole_word: bool = False,
    ) -> ToolResult:
        """Search repository for a pattern, returning matching lines."""
        if _rg_available():
            matches, truncated = self._rg_search(
                pattern, include, max_results, whole_word=whole_word
            )
            backend = "ripgrep"
        else:
            matches, truncated = self._walk_search(
                pattern,
                include,
                max_results,
                regex=False if whole_word else True,
                whole_word=whole_word,
            )
            backend = "python walker (ripgrep not installed)"

        return self._render_matches(matches, truncated, pattern, backend, max_results)

    def find_definitions(
        self, symbol: str, include: str | None = None, max_results: int = 60
    ) -> ToolResult:
        """Find definition-looking declarations for a symbol.

        Uses a heuristic per the searched source; read the file to confirm.
        """
        definition_hint = (
            r"(def |class |func |function |fn |interface |struct |impl "
            r"|public |private |protected |static |const |let |var |new |=>)"
        )
        pattern = rf"^\s*(?:{definition_hint}).*\b{re.escape(symbol)}\b"
        results = self.search_lines(pattern, include, max_results, whole_word=False)
        if results.success:
            results.evidence = results.evidence.replace(
                "Search", "Definition lookups"
            ) + (
                "  [heuristic: definition-like declarations only; "
                "confirm by reading the file]"
            )
        return results

    def find_references(
        self, symbol: str, include: str | None = None, max_results: int = 60
    ) -> ToolResult:
        """Find every occurrence of a symbol across the tree."""
        return self.search_lines(symbol, include, max_results, whole_word=True)

    def _render_matches(
        self,
        matches: list[Match],
        truncated: bool,
        pattern: str,
        backend: str,
        max_results: int,
    ) -> ToolResult:
        if not matches:
            return ToolResult(
                success=True,
                output="",
                evidence=f"No matches for '{pattern}' in {self.root} ({backend})",
            )
        lines = [f"{m.path}:{m.line}: {m.text.strip()}" for m in matches]
        output = "\n".join(lines)
        output, out_trunc = _truncate(output, MAX_OUTPUT)
        return ToolResult(
            success=True,
            output=output,
            evidence=(
                f"Found {len(matches)} matches for '{pattern}' in {self.root} "
                f"via {backend}"
            ),
            truncated=truncated or out_trunc,
        )


def _truncate(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated: {len(text) - limit} more chars]", True


def _make_tool(
    name: str,
    description: str,
    handler,
    extra_props: dict | None = None,
    required: list[str] | None = None,
    safe: bool = True,
) -> Tool:
    props = {
        "pattern": {
            "type": "string",
            "description": "Search pattern or symbol name",
        },
        "include": {
            "type": "string",
            "description": "File glob filter, e.g. '*.py'",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum results",
        },
        "workdir": {
            "type": "string",
            "description": "Repository root",
        },
    }
    if extra_props:
        props.update(extra_props)
    return Tool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": props,
            "required": required or ["pattern"],
        },
        handler=handler,
        safe=safe,
    )


def register_search_tools(registry) -> None:
    """Register search tools with the registry."""

    def search_code(pattern, include=None, max_results=100, workdir="."):
        engine = SearchEngine(workdir)
        return engine.search_lines(pattern, include, max_results)

    def find_definitions(pattern, include=None, max_results=60, workdir="."):
        engine = SearchEngine(workdir)
        return engine.find_definitions(pattern, include, max_results)

    def find_references(pattern, include=None, max_results=60, workdir="."):
        engine = SearchEngine(workdir)
        return engine.find_references(pattern, include, max_results)

    registry.register(
        _make_tool(
            "search_code",
            "Search repository code for a pattern, returning file:line:content. "
            "Faster and more thorough than search_text across large repos.",
            search_code,
        )
    )
    registry.register(
        _make_tool(
            "find_definitions",
            "Find definition-looking declarations of a symbol "
            "(def, class, function, interface, etc.). Heuristic: "
            "confirm by reading the file.",
            find_definitions,
        )
    )
    registry.register(
        _make_tool(
            "find_references",
            "Find every occurrence of a symbol across the repository. "
            "Whole-word search.",
            find_references,
        )
    )
