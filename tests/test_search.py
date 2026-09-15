import subprocess

import pytest

from core.tools import search as search_module
from core.tools.search import SearchEngine


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app.py").write_text(
        "def widget():\n    return 'new'\nis_widget_hook()\n\nvalue = widget()\n",
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text("widget = 'shadow'\n", encoding="utf-8")
    (tmp_path / "note.md").write_text("no code here\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def walker_only(monkeypatch):
    """Force the Python walker backend regardless of rg presence."""
    monkeypatch.setattr(search_module, "_rg_available", lambda: False)


def test_search_code_walker(repo, walker_only):
    engine = SearchEngine(repo)
    result = engine.search_lines("widget")
    assert result.success
    assert "app.py:1" in result.output
    assert "other.py:1" in result.output
    assert "python walker" in result.evidence


def test_search_code_whole_word(repo, walker_only):
    engine = SearchEngine(repo)
    # 'widget' as a substring of 'is_widget_hook' must not match whole-word.
    result = engine.search_lines("widget", whole_word=True)
    assert result.success
    assert "app.py:1" in result.output
    assert "app.py:5" in result.output
    assert "app.py:3" not in result.output


def test_find_definitions(repo, walker_only):
    engine = SearchEngine(repo)
    result = engine.find_definitions("widget")
    assert result.success
    assert "app.py:1" in result.output  # def widget()
    assert "other.py:1" not in result.output  # assignment, not a definition
    assert "heuristic" in result.evidence


def test_find_references(repo, walker_only):
    engine = SearchEngine(repo)
    result = engine.find_references("widget")
    assert "app.py:1" in result.output
    assert "app.py:5" in result.output
    assert "other.py:1" in result.output
    assert "app.py:3" not in result.output
    assert "note.md" not in result.output


def test_search_excludes_dependency_dirs(tmp_path, walker_only):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("hidden = True\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("visible = True\n", encoding="utf-8")

    engine = SearchEngine(tmp_path)
    result = engine.search_lines("hidden = True")
    assert result.success
    assert result.output == ""  # excluded dir not scanned
    assert result.evidence.startswith("No matches")


def test_search_no_matches(repo, walker_only):
    engine = SearchEngine(repo)
    result = engine.search_lines("zzz_nothing_here")
    assert result.success
    assert result.output == ""


def test_rg_backend_parses_output(repo, monkeypatch):
    """When rg is present, results parse as path:line:text."""

    def fake_run(cmd, capture_output=True, text=True, timeout=60):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="app.py:5:value = widget()\n", stderr=""
        )

    monkeypatch.setattr(search_module.subprocess, "run", fake_run)
    monkeypatch.setattr(search_module, "_rg_available", lambda: True)

    engine = SearchEngine(repo)
    result = engine.search_lines("widget")
    assert result.success
    assert "app.py:5" in result.output
    assert "ripgrep" in result.evidence


def test_search_include_filter(tmp_path, walker_only):
    (tmp_path / "a.py").write_text("token_here = 1\n", encoding="utf-8")
    (tmp_path / "a.txt").write_text("token_here\n", encoding="utf-8")

    engine = SearchEngine(tmp_path)
    result = engine.search_lines("token_here", include="*.py")
    assert result.success
    assert "a.py" in result.output
    assert "a.txt" not in result.output


def test_search_caps_results(repo, walker_only):
    (repo / "big.py").write_text(
        "\n".join(f"marker_{i} = {i}" for i in range(200)), encoding="utf-8"
    )
    engine = SearchEngine(repo)
    result = engine.search_lines("marker_", max_results=10)
    assert result.success
    assert result.truncated


def test_find_definitions_missing_symbol(repo, walker_only):
    engine = SearchEngine(repo)
    result = engine.find_definitions("totally_absent_symbol")
    assert result.success
    assert result.output == ""
