"""Git subsystem for CORE - first-class Git capability."""

import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a Git operation fails."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class GitRepository:
    """A Git repository wrapper with structured operations."""

    def __init__(self, repo_path: str | Path = "."):
        self.path = Path(repo_path).resolve()
        if not self.is_repo(self.path):
            raise GitError(f"Not a Git repository: {self.path}")
        self.root = self.find_root(self.path)

    @staticmethod
    def find_root(start: Path) -> Path:
        """Find the repository root directory."""
        current = start
        while True:
            if (current / ".git").exists() or (current / ".git").is_dir():
                return current
            if current.parent == current:
                raise GitError(f"Not inside a Git repository: {start}")
            current = current.parent

    @staticmethod
    def is_repo(path: Path) -> bool:
        """Check if a directory contains a Git repository."""
        cmd = ["git", "-C", str(path), "rev-parse", "--git-dir"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run a Git command."""
        cmd = ["git", "-C", str(self.root), *args]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired as e:
            raise GitError(f"Git command timed out: {' '.join(args)}") from e
        except subprocess.SubprocessError as e:
            raise GitError(f"Git command failed: {' '.join(args)}") from e

        if check and result.returncode != 0:
            raise GitError(
                f"Git '{' '.join(args)}' failed: {result.stderr.strip()}",
                stderr=result.stderr,
            )
        return result

    def status(self) -> dict[str, list[str]]:
        """Get repository status as structured data."""
        result = self._run("status", "--porcelain")
        staged: list[str] = []
        unstaged: list[str] = []
        untracked: list[str] = []

        for line in result.stdout.splitlines():
            if not line:
                continue
            code = line[:2]
            path = line[3:]
            if code == "??":
                untracked.append(path)
            elif code.startswith(" ") and code[1] != " ":
                unstaged.append(path)
            elif code[0] != " ":
                staged.append(path)
            else:
                unstaged.append(path)

        return {
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        }

    def is_clean(self) -> bool:
        """Check if working tree is clean."""
        result = self._run("status", "--porcelain")
        return not result.stdout.strip()

    def diff(self, cached: bool = False, stat: bool = False) -> str:
        """Get the diff of working tree or staged changes."""
        args = ["diff"]
        if cached:
            args.append("--cached")
        if stat:
            args.append("--stat")
        result = self._run(*args)
        return result.stdout

    def diff_of(self, path: str) -> str:
        """Get diff of a specific file."""
        result = self._run("diff", "--", path)
        return result.stdout

    def log(self, num: int = 10, oneline: bool = True) -> str:
        """Get commit history. Returns empty string if there are no commits."""
        args = ["log"]
        if oneline:
            args.append("--oneline")
        args.extend([f"-{num}"])
        result = self._run(*args, check=False)
        if result.returncode != 0:
            return ""
        return result.stdout

    def show(self, ref: str, stat: bool = True) -> str:
        """Show a specific commit."""
        args = ["show"]
        if stat:
            args.append("--stat")
        args.append(ref)
        result = self._run(*args)
        return result.stdout

    def branch(self, all: bool = False) -> str:
        """List branches."""
        args = ["branch"]
        if all:
            args.append("-a")
        result = self._run(*args)
        return result.stdout

    def current_branch(self) -> str | None:
        """Get current branch name."""
        result = self._run("symbolic-ref", "--short", "HEAD", check=False)
        if result.returncode == 0:
            return result.stdout.strip()
        # Detached HEAD
        result = self._run("rev-parse", "--short", "HEAD")
        return f"(detached HEAD at {result.stdout.strip()})"

    def staged(self) -> bool:
        """Check if there is anything staged."""
        result = self._run("diff", "--cached", "--quiet", check=False)
        return result.returncode != 0

    def has_untracked(self) -> bool:
        """Check if there are untracked files."""
        result = self._run("ls-files", "--others", "--exclude-standard")
        return bool(result.stdout.strip())

    def log_for_file(self, path: str, num: int = 5) -> str:
        """Get commit history for a specific file."""
        args = ["log", "--oneline", f"-{num}", "--", path]
        result = self._run(*args)
        return result.stdout

    def describe(self) -> dict[str, str]:
        """Summarize the repository's Git state."""
        return {
            "root": str(self.root),
            "branch": self.current_branch() or "none",
            "status": self.status(),
            "is_clean": self.is_clean(),
            "have_staged": self.staged(),
            "have_untracked": self.has_untracked(),
        }

    def safe_uncached_changes(self) -> list[str]:
        """List files with unstaged modifications."""
        result = self._run("diff", "--name-only")
        return result.stdout.strip().splitlines() if result.stdout.strip() else []

    def remotes(self) -> list[str]:
        """List remote names."""
        result = self._run("remote", check=False)
        return result.stdout.splitlines() if result.stdout.strip() else []

    def default_branch(self) -> str | None:
        """Guess the default branch name."""
        result = self._run("symbolic-ref", "refs/remotes/origin/HEAD", check=False)
        if result.returncode == 0:
            return result.stdout.strip().replace("refs/remotes/origin/", "")
        return None

    def stash_list(self) -> str:
        """List stashes."""
        result = self._run("stash", "list")
        return result.stdout

    def remote_url(self, remote: str = "origin") -> str | None:
        """Get the URL for a named remote."""
        result = self._run("remote", "get-url", remote, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def has_remote(self, remote: str = "origin") -> bool:
        """Check whether a named remote exists."""
        return self.remote_url(remote) is not None

    def last_commit_message(self) -> str:
        """Get the last commit's message."""
        result = self._run("log", "-1", "--pretty=%B")
        return result.stdout.strip()
