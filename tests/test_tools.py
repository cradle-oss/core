import tempfile
from pathlib import Path

from core.git.operations import GitError, GitRepository
from core.tools.files import edit_file_tool, write_file_tool
from core.tools.filesystem import (
    list_directory_tool,
    read_file_tool,
    search_files_tool,
    search_text_tool,
)


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal Git repository for testing."""
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
    )
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "hello.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "initial"],
        check=True,
    )
    return tmp_path


def test_read_file():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "test.txt"
        path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        result = read_file_tool(str(path))
        assert result.success
        assert "line2" in result.output


def test_read_file_missing():
    result = read_file_tool("/nonexistent/file.txt")
    assert not result.success
    assert "does not exist" in result.error


def test_list_directory():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)
        (path / "a.txt").write_text("", encoding="utf-8")
        (path / "subdir").mkdir()
        result = list_directory_tool(td)
        assert result.success
        assert "a.txt" in result.output
        assert "subdir" in result.output or "subdir/" in result.output


def test_search_files_glob():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)
        (path / "test_a.py").write_text("", encoding="utf-8")
        (path / "test_b.py").write_text("", encoding="utf-8")
        (path / "other.txt").write_text("", encoding="utf-8")
        result = search_files_tool("*.py", td)
        assert result.success
        assert "test_a.py" in result.output
        assert "other.txt" not in result.output


def test_search_text():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)
        (path / "app.py").write_text(
            "import os\nimport sys\nprint('hello')\n", encoding="utf-8"
        )
        result = search_text_tool("import os", td, include="py")
        assert result.success
        assert "import os" in result.output


def test_write_and_edit_file():
    with tempfile.TemporaryDirectory() as td:
        result = write_file_tool("newfile.txt", "original content\n", workdir=td)
        assert result.success
        path = Path(td) / "newfile.txt"
        assert path.read_text(encoding="utf-8") == "original content\n"

        result = edit_file_tool(
            "newfile.txt",
            "original",
            "replaced",
            workdir=td,
        )
        assert result.success
        assert path.read_text(encoding="utf-8") == "replaced content\n"


def test_edit_file_not_found_string():
    with tempfile.TemporaryDirectory() as td:
        write_file_tool("f.txt", "hello world\n", workdir=td)
        result = edit_file_tool("f.txt", "missing text", "another", workdir=td)
        assert not result.success
        assert "not found" in result.error


def test_git_repository_status():
    with tempfile.TemporaryDirectory() as td:
        _make_repo(Path(td))
        repo = GitRepository(td)
        status = repo.status()
        assert status["staged"] == []
        assert status["unstaged"] == []
        assert repo.is_clean()
        assert repo.current_branch() == "master" or repo.current_branch() is not None


def test_git_repository_not_a_repo():
    with tempfile.TemporaryDirectory() as td:
        try:
            GitRepository(td)
            assert False, "Should have raised"
        except GitError:
            pass


def test_git_preserves_changes_detection():
    with tempfile.TemporaryDirectory() as td:
        _make_repo(Path(td))
        repo = GitRepository(td)
        assert repo.is_clean()

        (Path(td) / "hello.txt").write_text("changed\n", encoding="utf-8")
        assert not repo.is_clean()
        status = repo.status()
        assert "hello.txt" in status["unstaged"]
