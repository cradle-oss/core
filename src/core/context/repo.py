"""Repository inspection for CORE."""

from dataclasses import dataclass, field
from pathlib import Path

from core.git.operations import GitError, GitRepository


@dataclass
class RepoProfile:
    """A characterized repository profile."""

    root: str
    is_git_repo: bool
    git_branch: str | None = None
    git_state: dict | None = None
    languages: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    top_level: list[str] = field(default_factory=list)
    has_readme: bool = False
    has_agency: bool = False
    test_dir: bool = False
    ci_config: bool = False
    docs_dir: bool = False


# Manifest files and their associated ecosystems
MANIFESTS = {
    "requirements.txt": "Python (pip)",
    "pyproject.toml": "Python (poetry/hatch/uv)",
    "setup.py": "Python (setuptools)",
    "package.json": "JavaScript/TypeScript (npm/yarn/pnpm)",
    "Cargo.toml": "Rust (cargo)",
    "go.mod": "Go (modules)",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/JVM (Gradle)",
    "Gemfile": "Ruby (bundler)",
    "composer.json": "PHP (composer)",
    "mix.exs": "Elixir",
    "pubspec.yaml": "Dart/Flutter",
    ".csproj": ".NET",
    "CMakeLists.txt": "C/C++ (CMake)",
    "Makefile": "C/C++ (Make)",
}


class RepoInspector:
    """Inspect a repository to build an understanding profile."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()

    def inspect(self) -> RepoProfile:
        """Inspect the repository and build a profile."""
        if not self.root.exists():
            raise FileNotFoundError(f"Directory does not exist: {self.root}")

        profile = RepoProfile(root=str(self.root), is_git_repo=False)

        # Git state
        try:
            repo = GitRepository(self.root)
            profile.is_git_repo = True
            profile.git_branch = repo.current_branch()
            profile.git_state = repo.describe()
        except GitError:
            pass

        # Top-level contents
        try:
            top_level = sorted(
                [e for e in self.root.iterdir() if not e.name.startswith(".git")]
            )
            profile.top_level = [
                e.name + ("/" if e.is_dir() else "") for e in top_level
            ]
        except OSError:
            pass

        # Manifests and package managers
        for manifest, ecosystem in MANIFESTS.items():
            matches = list(self.root.glob(manifest))
            if matches:
                # Only add unique ecosystems
                if ecosystem not in profile.package_managers:
                    profile.package_managers.append(ecosystem)

        # Record all files (bounded - no arbitrary limits)
        all_files: list[str] = []
        try:
            for p in self.root.rglob("*"):
                relative = p.relative_to(self.root)
                parts = relative.parts
                if parts and parts[0] == ".git":
                    continue
                if any(
                    part
                    in {
                        ".venv",
                        "__pycache__",
                        "node_modules",
                        ".next",
                        "dist",
                        "build",
                    }
                    for part in parts
                ):
                    continue
                if p.is_file():
                    all_files.append(str(relative))
        except OSError:
            pass
        profile.files = all_files

        # Language inference from extensions
        languages = set()
        extension_map = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".jsx": "JavaScript/React",
            ".tsx": "TypeScript/React",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".c": "C",
            ".h": "C/C++ header",
            ".cpp": "C++",
            ".hpp": "C++ header",
            ".rb": "Ruby",
            ".php": "PHP",
            ".cs": "C#",
            ".swift": "Swift",
            ".kt": "Kotlin",
            ".vue": "Vue",
            ".html": "HTML",
            ".css": "CSS",
            ".scss": "SCSS",
            ".json": "JSON",
            ".sh": "Shell",
            ".bash": "Shell",
            ".zsh": "Shell",
        }
        for file in all_files:
            suffix = Path(file).suffix
            if suffix in extension_map:
                languages.add(extension_map[suffix])
        profile.languages = sorted(languages)

        # Feature detection
        profile.has_readme = any(
            f.lower() in {"readme.md", "readme.rst", "readme.txt"}
            for f in profile.top_level
        )
        profile.has_agency = any(
            f.lower() in {"agents.md", "agent.md"} for f in profile.top_level
        )
        profile.test_dir = any(
            d in profile.top_level
            for d in {"tests/", "test/", "spec/", "specs/", "integration_tests/"}
        )
        profile.ci_config = any(
            f in profile.top_level or f.endswith(".yml") or f.endswith(".yaml")
            for f in profile.top_level
        ) and any(".github" in str(x) or "gitlab-ci" in str(x).lower() for x in [])
        # More reliable CI detection
        ci_paths = [
            self.root / ".github",
            self.root / ".gitlab-ci.yml",
            self.root / "bitbucket-pipelines.yml",
            self.root / ".circleci",
        ]
        profile.ci_config = any(p.exists() for p in ci_paths)
        profile.docs_dir = any(
            d in profile.top_level for d in {"docs/", "doc/", "documentation/"}
        )

        return profile

    def relevant_files(
        self, search_term: str | None = None, limit: int = 30
    ) -> list[str]:
        """Suggest relevant files for a task, optionally filtered."""
        profile = self.inspect()
        files = profile.files

        # Prioritize by importance heuristic
        key_files = []
        for f in files:
            name = Path(f).name.lower()
            if name in {
                "readme.md",
                "agi.md",
                "agents.md",
                "pyproject.toml",
                "package.json",
                "requirements.txt",
                "setup.py",
                "setup.cfg",
                "Cargo.toml",
                "go.mod",
                "pom.xml",
                "Makefile",
                "CMakeLists.txt",
                "Dockerfile",
                "docker-compose.yml",
                "docker-compose.yaml",
                ".env.example",
                "config.py",
                "settings.py",
            }:
                key_files.append(f)

        if search_term:
            # Filter by search term relevance
            term_lower = search_term.lower()
            matches = [f for f in files if term_lower in f.lower()]
            return (matches + key_files)[:limit]
        return key_files[:limit]
