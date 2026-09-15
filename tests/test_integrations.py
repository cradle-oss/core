import json
import subprocess

import pytest

from core.integrations.base import CommandResult, IntegrationError
from core.integrations.github import GitHubIntegration, parse_github_ref
from core.integrations.vercel import VercelIntegration
from core.tools.registry import ToolRegistry


class FakeRunner:
    """Canned CommandResult responses keyed by argv tokens (substring match)."""

    def __init__(self, handlers):
        # handlers: list of (tokens, CommandResult) consulted in order
        self._handlers = handlers
        self.calls = []

    def _matches(self, tokens, argv):
        return all(any(tok in arg for arg in argv) for tok in tokens)

    def __call__(self, argv, timeout=60, cwd=None):
        self.calls.append((list(argv)[:], cwd))
        for tokens, result in self._handlers:
            if self._matches(tokens, argv):
                return result
        return CommandResult(returncode=1, stdout="", stderr="unhandled command")


def ok(stdout="", stderr="", code=0):
    return CommandResult(returncode=code, stdout=stdout, stderr=stderr)


def test_parse_github_ref():
    assert parse_github_ref("acme/widgets") == ("acme", "widgets", None)
    assert parse_github_ref("acme/widgets#42") == ("acme", "widgets", 42)
    assert parse_github_ref("#42") == (None, None, 42)
    assert parse_github_ref("42") == (None, None, 42)
    assert parse_github_ref("42#7") == (None, None, 42)
    assert parse_github_ref("https://github.com/acme/widgets/pull/42") == (
        "acme",
        "widgets",
        42,
    )
    assert parse_github_ref("https://github.com/acme/widgets") == (
        "acme",
        "widgets",
        None,
    )
    assert parse_github_ref(None) == (None, None, None)


def test_github_probe_available_and_authenticated():
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0 (2025-01-01)\n")),
                (["api", "user"], ok("octocat\n")),
            ]
        )
    )
    av = integration.availability()
    assert av.available
    assert av.authenticated
    assert av.version == "2.100.0"
    assert av.identity == "octocat"
    assert "available" in av.status


def test_github_probe_installed_but_unauthenticated():
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("", stderr="not logged in", code=1)),
            ]
        )
    )
    av = integration.availability()
    assert av.available
    assert not av.authenticated
    assert "not authenticated" in av.status


def test_github_probe_unavailable():
    integration = GitHubIntegration(
        runner=FakeRunner([(["--version"], ok("", code=127))])
    )
    av = integration.availability()
    assert not av.available
    assert "unavailable" in av.status


def test_github_tools_register_only_when_available():
    unavailable = GitHubIntegration(
        runner=FakeRunner([(["--version"], ok("", code=127))])
    )
    registry = ToolRegistry()
    registered = unavailable.register_tools(registry)
    assert registered == []
    assert registry.get("github_pr_metadata") is None

    available = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
            ]
        )
    )
    registry = ToolRegistry()
    registered = available.register_tools(registry)
    names = {t.name for t in registered}
    assert names == {
        "github_repo_info",
        "github_pr_metadata",
        "github_pr_files",
        "github_pr_diff",
        "github_pr_reviews",
        "github_pr_threads",
        "github_pr_checks",
        "github_pr_runs",
        "github_run_logs",
    }
    tool = registry.get("github_pr_diff")
    assert tool is not None
    assert tool.safe  # read-only integration tools

    # Calling an unavailable integration's tool returns a clean error result
    result = unavailable._github_pr_metadata(pr=1)
    assert not result.success
    assert "not installed" in (result.error or "")


def test_github_repo_info_from_git_remote(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "https://github.com/acme/widgets.git",
        ],
        check=True,
    )
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
                (
                    ["repo", "view"],
                    ok(
                        json.dumps(
                            {
                                "nameWithOwner": "acme/widgets",
                                "description": "Widgets API",
                                "visibility": "PUBLIC",
                                "primaryLanguage": {"name": "Python"},
                                "languages": {"nodes": [{"name": "Python"}]},
                                "defaultBranchRef": {"name": "main"},
                                "isPrivate": False,
                                "viewerPermission": "READ",
                                "url": "https://github.com/acme/widgets",
                            }
                        )
                    ),
                ),
            ]
        )
    )
    result = integration._github_repo_info(repo=None, workdir=str(tmp_path))
    assert result.success
    assert "acme/widgets" in result.output
    assert "Python" in result.output
    # the run used -R acme/widgets
    of_interest = [call for call in integration._runner.calls if "repo" in call[0]]
    assert of_interest
    assert "acme/widgets" in of_interest[0][0]


def test_github_pr_metadata_render(tmp_path):
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
                (
                    ["pr", "view"],
                    ok(
                        json.dumps(
                            {
                                "number": 12,
                                "title": "Add widgets",
                                "state": "OPEN",
                                "isDraft": False,
                                "mergeable": "MERGEABLE",
                                "reviewDecision": "APPROVED",
                                "author": {"login": "octocat"},
                                "createdAt": "2026-01-01T00:00:00Z",
                                "baseRefName": "main",
                                "headRefName": "feat-widgets",
                                "additions": 10,
                                "deletions": 2,
                                "changedFiles": 1,
                                "body": "Adds the widgets module.",
                                "labels": [{"name": "feature"}],
                                "commits": [
                                    {
                                        "oid": "abcd",
                                        "messageHeadline": "add widgets",
                                        "authors": [{"login": "octocat"}],
                                    }
                                ],
                                "files": [
                                    {
                                        "path": "src/widget.py",
                                        "additions": 10,
                                        "deletions": 2,
                                        "status": "modified",
                                    }
                                ],
                                "reviews": [
                                    {
                                        "author": {"login": "reviewer"},
                                        "state": "APPROVED",
                                        "submittedAt": "2026-01-02T00:00:00Z",
                                        "body": "lgtm",
                                    }
                                ],
                                "statusCheckRollup": [
                                    {
                                        "name": "CI",
                                        "status": "COMPLETED",
                                        "conclusion": "SUCCESS",
                                    }
                                ],
                            }
                        )
                    ),
                ),
            ]
        )
    )
    result = integration._github_pr_metadata(
        pr="12", repo="acme/widgets", workdir=str(tmp_path)
    )
    assert result.success
    assert "Add widgets" in result.output
    assert "APPROVED" in result.output
    assert "src/widget.py" in result.output
    assert "octocat" in result.output
    assert "'Add widgets'" in result.evidence


def test_github_pr_accidental_unresolvable_no_repo(tmp_path):
    # tmp_path has no git remote and no repo arg -> clean error, no crash
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
            ]
        )
    )
    result = integration._github_repo_info(repo=None, workdir=str(tmp_path))
    assert not result.success
    assert "github.com remote" in (result.error or "")


def test_github_pr_diff_cached_and_filtered(tmp_path):
    diff = (
        "diff --git a/src/widget.py b/src/widget.py\n"
        "index 0001..0002 100644\n"
        "--- a/src/widget.py\n"
        "+++ b/src/widget.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def make():\n"
        "-    return 'old'\n"
        "+    return 'new'\n"
        "diff --git a/README.md b/README.md\n"
        "index 0003..0004 100644\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,2 +1,2 @@\n"
        "-old\n"
        "+new\n"
    )
    runner = FakeRunner(
        [
            (["--version"], ok("gh version 2.100.0\n")),
            (["api", "user"], ok("octocat\n")),
            (["pr", "diff"], ok(diff)),
        ]
    )
    integration = GitHubIntegration(runner=runner)

    result = integration._github_pr_diff(pr="12", repo="acme/widgets")
    assert result.success
    assert "src/widget.py" in result.output
    assert "README.md" in result.output

    filtered = integration._github_pr_diff(
        pr="12", repo="acme/widgets", path="src/widget.py"
    )
    assert filtered.success
    assert "README.md" not in filtered.output

    # cached: only one pr diff call happened
    diff_calls = [c for c in runner.calls if "diff" in c[0]]
    assert len(diff_calls) == 1

    missing = integration._github_pr_diff(pr="12", repo="acme/widgets", path="nope.py")
    assert missing.success
    assert missing.output == ""


def test_github_pr_diff_single_file(tmp_path):
    # a one-file diff has no "\ndiff --git " separator: no double prefix
    single = (
        "diff --git a/src/widget.py b/src/widget.py\n"
        "index 0001..0002 100644\n"
        "--- a/src/widget.py\n"
        "+++ b/src/widget.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def make():\n"
        "-    return 'old'\n"
        "+    return 'new'\n"
    )
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
                (["pr", "diff"], ok(single)),
            ]
        )
    )
    filtered = integration._github_pr_diff(
        pr="12", repo="acme/widgets", path="src/widget.py"
    )
    assert filtered.success
    assert filtered.output.startswith("diff --git a/src/widget.py b/src/widget.py")
    assert "diff --git diff --git" not in filtered.output


def test_github_pr_threads_groups_replies(tmp_path):
    comments = json.dumps(
        [
            {
                "id": 1,
                "in_reply_to_id": None,
                "path": "src/widget.py",
                "line": 5,
                "body": "should this be nullable?",
                "user": {"login": "alice"},
            },
            {
                "id": 2,
                "in_reply_to_id": 1,
                "path": "src/widget.py",
                "line": 5,
                "body": "yes, fixed",
                "user": {"login": "bob"},
            },
        ]
    )
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
                (["api", "repos/"], ok(comments)),
            ]
        )
    )
    result = integration._github_pr_threads(
        pr="12", repo="acme/widgets", workdir=str(tmp_path)
    )
    assert result.success
    assert "THREAD on src/widget.py:5" in result.output
    assert "should this be nullable?" in result.output
    assert "bob: yes, fixed" in result.output


def test_github_run_logs_requires_run_id():
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
            ]
        )
    )
    result = integration._github_run_logs(run_id=None)
    assert not result.success
    assert "run_id is required" in (result.error or "")


def test_github_requires_repo_without_remote(tmp_path):
    integration = GitHubIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("gh version 2.100.0\n")),
                (["api", "user"], ok("octocat\n")),
            ]
        )
    )
    with pytest.raises(IntegrationError):
        integration._resolve_repo(None, str(tmp_path))


def test_vercel_probe_and_tools(tmp_path):
    integration = VercelIntegration(
        runner=FakeRunner(
            [
                (["--version"], ok("59.1.4\n")),
                (["whoami"], ok("bniladridas\n")),
                (
                    ["ls", "--json"],
                    ok(
                        json.dumps(
                            [
                                {
                                    "readyState": "READY",
                                    "url": "acme.vercel.app",
                                    "created": 1760000000000,
                                    "target": "production",
                                }
                            ]
                        )
                    ),
                ),
                (
                    "inspect",
                    ok(
                        json.dumps(
                            {
                                "url": "acme.vercel.app",
                                "state": "READY",
                                "target": "production",
                                "created": 1760000000000,
                                "buildTime": 1200,
                                "creator": {"username": "bniladridas"},
                                "inspectorUrl": "https://inspect.acme",
                                "meta": {},
                            }
                        )
                    ),
                ),
            ]
        )
    )
    av = integration.availability()
    assert av.available
    assert av.authenticated
    assert av.identity == "bniladridas"

    deployments = integration._vercel_deployments(workdir=str(tmp_path))
    assert deployments.success
    assert "acme.vercel.app" in deployments.output

    inspected = integration._vercel_inspect(
        deployment="acme.vercel.app", workdir=str(tmp_path)
    )
    assert inspected.success
    assert "READY" in inspected.output
    assert "inspectorUrl" in inspected.output

    missing = integration._vercel_inspect(deployment="")
    assert not missing.success
    assert "required" in (missing.error or "")


def test_vercel_unavailable():
    integration = VercelIntegration(
        runner=FakeRunner([(["--version"], ok("", code=127))])
    )
    av = integration.availability()
    assert not av.available
    result = integration._vercel_deployments()
    assert not result.success
    assert "not installed" in (result.error or "")


def test_truncate_output():
    from core.integrations.base import truncate_output

    text = "x" * 100
    bounded, truncated = truncate_output(text, limit=50)
    assert truncated
    assert len(bounded) < len(text)
    assert "truncated" in bounded

    short, truncated = truncate_output("abc", limit=50)
    assert not truncated
    assert short == "abc"
