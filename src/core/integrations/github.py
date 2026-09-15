"""GitHub integration for CORE.

Wraps the GitHub CLI (gh). Uses structured JSON output exclusively.
Native CLI first: never scrape github.com.

Availability: `gh` installed and authenticated.
"""

import json

from core.git.operations import GitError, GitRepository
from core.integrations.base import (
    Availability,
    Integration,
    IntegrationError,
    truncate_output,
)
from core.tools.registry import ToolResult

PR_VIEW_FIELDS = (
    "number,title,body,state,author,createdAt,updatedAt,mergedAt,mergedBy,"
    "baseRefName,headRefName,url,additions,deletions,changedFiles,isDraft,"
    "mergeable,reviewDecision,labels,commits,reviews,statusCheckRollup"
)

MAX_BODY = 4000
MAX_META = 30000
MAX_DIFF = 60000


def parse_github_ref(ref: str | None) -> tuple[str | None, str | None, int | None]:
    """Parse 'owner/repo', a GitHub URL, '#12', or 'owner/repo#12'.

    Returns (owner, repo, pull_number_or_None).
    """
    if not ref:
        return None, None, None
    ref = ref.strip()
    pr: int | None = None

    if "github.com/" in ref:
        path = ref.split("github.com/", 1)[1].rstrip("/")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            if len(parts) > 2 and parts[2] == "pull" and len(parts) > 3:
                try:
                    pr = int(parts[3])
                except ValueError:
                    pr = None
            return owner, repo, pr

    if ref.startswith("#"):
        try:
            return None, None, int(ref[1:])
        except ValueError:
            return None, None, None

    base, _, suffix = ref.partition("#")
    if suffix:
        try:
            pr = int(suffix)
        except ValueError:
            pr = None
    if base.isdigit():
        pr = int(base)
        return None, None, pr
    if "/" in base:
        owner, _, repo = base.partition("/")
        return owner or None, repo or None, pr
    return None, base or None, pr


class GitHubIntegration(Integration):
    name = "github"

    def _base_command(self) -> list[str]:
        return ["gh"]

    def _probe_availability(self) -> Availability:
        version_res = self.run(["--version"], timeout=20)
        if not version_res.ok or not version_res.stdout.strip():
            return Availability(name=self.name, available=False)
        parts = version_res.stdout.strip().splitlines()[0].split()
        version = parts[2] if len(parts) >= 3 else (parts[-1] if parts else None)

        auth_res = self.run(["api", "user", "--jq", ".login"], timeout=20)
        identity = None
        if auth_res.ok:
            identity = auth_res.stdout.strip() or None
        return Availability(
            name=self.name,
            available=True,
            version=version,
            authenticated=auth_res.ok,
            identity=identity,
            detail="gh " + (version or "?"),
        )

    # ---- repo resolution ----

    @staticmethod
    def _repo_from_git(workdir: str) -> tuple[str | None, str | None]:
        """Derive owner/repo from the current git remote, when present."""
        try:
            repo = GitRepository(workdir)
        except GitError:
            return None, None
        remotes = repo.remotes()
        if not remotes:
            return None, None
        url = repo.remote_url(remotes[0])
        if not url:
            return None, None
        if "github.com" in url:
            if url.startswith("git@github.com:"):
                path = url.split("git@github.com:", 1)[1]
            elif "://" in url:
                path = url.split("://", 1)[1].split("github.com/", 1)[-1]
            else:
                path = url
            path = path.removesuffix(".git")
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                return parts[0], parts[1]
        return None, None

    def _resolve_repo(self, repo: str | None, workdir: str) -> tuple[str, str]:
        """Resolve owner/repo from explicit arg, else git remote."""
        if repo:
            owner, repo_name, _ = parse_github_ref(repo)
            if owner and repo_name:
                return owner, repo_name
            raise IntegrationError(f"Could not parse repository reference: {repo!r}")
        owner, repo_name = self._repo_from_git(workdir)
        if owner and repo_name:
            return owner, repo_name
        raise IntegrationError(
            "No repository specified and the current git remote has no "
            "github.com remote. Pass `repo='owner/name'`."
        )

    def _resolve_pr(
        self, pr: str | int | None, repo, workdir
    ) -> tuple[int | None, str, str]:
        """Resolve PR number + owner/repo from args, URL, or remote."""
        owner, repo_name = self._resolve_repo(repo, workdir)
        pr_num: int | None = None
        if pr is not None:
            owner2, repo2, num = parse_github_ref(str(pr))
            owner = owner2 or owner
            repo_name = repo2 or repo_name
            pr_num = pr_num or num
        if pr_num is None and isinstance(pr, int):
            pr_num = pr
        if pr_num is None and isinstance(pr, str) and pr.isdigit():
            pr_num = int(pr)
        if pr_num is None:
            # PR for current branch
            try:
                g = GitRepository(workdir)
                branch = g.current_branch()
                if branch and not branch.startswith("(detached"):
                    out = self.run(
                        [
                            "pr",
                            "view",
                            branch,
                            "-R",
                            f"{owner}/{repo_name}",
                            "--json",
                            "number",
                        ],
                        timeout=30,
                    )
                    if out.ok and out.stdout.strip():
                        pr_num = int(json.loads(out.stdout)["number"])
            except (GitError, ValueError, json.JSONDecodeError):
                pr_num = None
        return pr_num, owner, repo_name

    # ---- tool handlers ----

    def _github_repo_info(self, repo=None, workdir=".") -> ToolResult:
        try:
            self.require_available()
            owner, repo_name = self._resolve_repo(repo, workdir)
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        reference = f"{owner}/{repo_name}"
        out = self.run(
            [
                "repo",
                "view",
                reference,
                "--json",
                "nameWithOwner,description,visibility,primaryLanguage,"
                "languages,defaultBranchRef,stargazerCount,forkCount,"
                "isPrivate,createdAt,updatedAt,viewerPermission,url,homepageUrl",
            ],
            timeout=40,
        )
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"gh repo view failed: {out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        try:
            data = json.loads(out.stdout)
            lang_nodes = data.get("languages") or {}
            if isinstance(lang_nodes, dict):
                lang_nodes = lang_nodes.get("nodes", [])
            compact = {
                "nameWithOwner": data.get("nameWithOwner"),
                "description": str(data.get("description") or "")[:2000],
                "visibility": data.get("visibility"),
                "primaryLanguage": (data.get("primaryLanguage") or {}).get("name"),
                "languages": [
                    lang.get("name") for lang in lang_nodes if isinstance(lang, dict)
                ][:10],
                "defaultBranch": (data.get("defaultBranchRef") or {}).get("name"),
                "isPrivate": data.get("isPrivate"),
                "viewerPermission": data.get("viewerPermission"),
                "url": data.get("url"),
            }
            body = json.dumps(compact, indent=2)
            return ToolResult(
                success=True,
                output=body,
                evidence=f"gh repo view {reference}: {len(body)} chars",
            )
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

    def _github_pr_metadata(self, pr=None, repo=None, workdir=".") -> ToolResult:
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False,
                    output="",
                    error="No PR number or URL provided and no PR for the "
                    "current branch.",
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))

        ref = f"{owner}/{repo_name}"
        out = self.run(
            ["pr", "view", str(pr_num), "-R", ref, "--json", PR_VIEW_FIELDS],
            timeout=40,
        )
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"gh pr view #{pr_num} failed: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        return self._render_pr_json(out.stdout, owner, repo_name, pr_num)

    def _render_pr_json(self, raw: str, owner, repo, pr_num) -> ToolResult:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

        compact = self._compact_pr(data)
        body = json.dumps(compact, indent=2)
        body, truncated = truncate_output(body, MAX_META)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"gh pr view #{pr_num} ({owner}/{repo}): "
                f"title={compact.get('title')!r} state={compact.get('state')}"
            ),
            truncated=truncated,
        )

    def _compact_pr(self, data: dict) -> dict:
        labels = [lab.get("name") for lab in data.get("labels", [])]
        commits = [
            {
                "oid": c.get("oid", "")[:10],
                "message": (c.get("messageHeadline") or c.get("message") or "")[:200],
                "author": (c.get("authors") or [{}])[0].get("login"),
            }
            for c in data.get("commits", [])
        ][:30]
        files = [
            {
                "path": f.get("path"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "status": f.get("status"),
            }
            for f in data.get("files", [])
        ]
        reviews = [
            {
                "author": r.get("author", {}).get("login"),
                "state": r.get("state"),
                "submittedAt": r.get("submittedAt"),
                "body": (r.get("body") or "")[:600],
            }
            for r in data.get("reviews", [])
        ][:20]
        return {
            "number": data.get("number"),
            "title": data.get("title"),
            "state": data.get("state"),
            "isDraft": data.get("isDraft"),
            "mergeable": data.get("mergeable"),
            "mergeStateStatus": data.get("mergeStateStatus"),
            "reviewDecision": data.get("reviewDecision"),
            "author": (data.get("author") or {}).get("login"),
            "createdAt": data.get("createdAt"),
            "updatedAt": data.get("updatedAt"),
            "mergedAt": data.get("mergedAt"),
            "mergedBy": (data.get("mergedBy") or {}).get("login"),
            "baseRef": data.get("baseRefName"),
            "headRef": data.get("headRefName"),
            "additions": data.get("additions"),
            "deletions": data.get("deletions"),
            "changedFiles": data.get("changedFiles"),
            "labels": labels,
            "url": data.get("url"),
            "body": (data.get("body") or "")[:MAX_BODY],
            "commits": commits,
            "files": files,
            "reviews": reviews,
            "checks": [
                {
                    "name": s.get("name") or (s.get("context") or {}).get("name"),
                    "status": s.get("status"),
                    "conclusion": s.get("conclusion"),
                }
                for s in data.get("statusCheckRollup", [])
            ][:40],
        }

    def _github_pr_files(self, pr=None, repo=None, workdir=".") -> ToolResult:
        """Compact, focused list of files changed in a PR."""
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False, output="", error="No PR reference provided."
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        ref = f"{owner}/{repo_name}"
        out = self.run(
            ["pr", "view", str(pr_num), "-R", ref, "--json", "files"],
            timeout=40,
        )
        if not out.ok:
            return ToolResult(
                success=False, output="", error=f"gh failed: {out.stderr.strip()}"
            )
        try:
            data = json.loads(out.stdout)
            files = data.get("files", [])
            lines = [
                f"{f.get('status', '?')}: +{f.get('additions', 0)} "
                f"-{f.get('deletions', 0)} {f.get('path')}"
                for f in files
            ]
            return ToolResult(
                success=True,
                output="\n".join(lines) if lines else "(no changed files)",
                evidence=f"PR #{pr_num}: {len(files)} changed files",
            )
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

    def _fetch_diff(self, owner: str, repo: str, pr_num: int) -> tuple[str, str | None]:
        """Fetch (and cache) the full PR diff."""
        ref = f"{owner}/{repo}"
        key = f"{ref}#{pr_num}"
        if key in self._diff_cache:
            return self._diff_cache[key], None
        out = self.run(["pr", "diff", str(pr_num), "-R", ref], timeout=90)
        if not out.ok:
            return "", out.stderr.strip() or "gh pr diff failed"
        self._diff_cache[key] = out.stdout
        return out.stdout, None

    def _github_pr_diff(self, pr=None, repo=None, path=None, workdir=".") -> ToolResult:
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False, output="", error="No PR reference provided."
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        diff, err = self._fetch_diff(owner, repo_name, pr_num)
        if err:
            return ToolResult(success=False, output="", error=err)

        if path:
            diff = self._filter_diff_by_path(diff, path)
            if not diff.strip():
                return ToolResult(
                    success=True,
                    output="",
                    evidence=f"No diff hunks for {path} in PR #{pr_num}",
                )
        diff, truncated = truncate_output(diff, MAX_DIFF)
        return ToolResult(
            success=True,
            output=diff,
            evidence=f"PR #{pr_num} diff ({len(diff)} chars)"
            + (f", filtered to {path}" if path else "")
            + (" [truncated]" if truncated else ""),
            truncated=truncated,
        )

    @staticmethod
    def _filter_diff_by_path(diff: str, path: str) -> str:
        """Extract diff hunks belonging to a specific file path."""
        sections = diff.split("\ndiff --git ")
        wanted = []
        for section in sections:
            if section.startswith("diff --git "):
                content = section
            elif section.startswith("a/"):
                content = "diff --git " + section
            elif not section.strip():
                content = ""
            else:
                content = "diff --git " + section
            if content and f"b/{path}" in content.splitlines()[0][:200]:
                wanted.append(content.rstrip())
        return "\n\n".join(wanted)

    def _github_pr_reviews(self, pr=None, repo=None, workdir=".") -> ToolResult:
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False, output="", error="No PR reference provided."
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        ref = f"{owner}/{repo_name}"
        out = self.run(
            ["pr", "view", str(pr_num), "-R", ref, "--json", "reviews,reviewDecision"],
            timeout=40,
        )
        if not out.ok:
            return ToolResult(
                success=False, output="", error=f"gh failed: {out.stderr.strip()}"
            )
        try:
            data = json.loads(out.stdout)
            lines = [f"reviewDecision: {data.get('reviewDecision')}"]
            for r in data.get("reviews", []):
                lines.append(
                    f"- [{r.get('state')}] {r.get('author', {}).get('login')} "
                    f"on {r.get('submittedAt', '')}\n"
                    f"  {(r.get('body') or '(no body)')[:800]}"
                )
            body = "\n".join(lines)
            return ToolResult(
                success=True,
                output=body,
                evidence=f"PR #{pr_num}: {len(data.get('reviews', []))} reviews",
            )
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

    def _github_pr_threads(self, pr=None, repo=None, workdir=".") -> ToolResult:
        """Inline review comment threads (via gh api, paginated)."""
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False, output="", error="No PR reference provided."
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))

        endpoint = f"repos/{owner}/{repo_name}/pulls/{pr_num}/comments"
        out = self.run(["api", endpoint, "--paginate"], timeout=90)
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"gh api {endpoint} failed: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        try:
            comments = json.loads(out.stdout) if out.stdout.strip() else []
            if isinstance(comments, dict):
                comments = comments.get("comments", [])
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

        if not comments:
            return ToolResult(
                success=True,
                output="(no inline review comments)",
                evidence=f"PR #{pr_num}: 0 inline review comments",
            )

        # Group into threads by in_reply_to_id
        heads: list[dict] = []
        for c in comments:
            if c.get("in_reply_to_id") is None:
                heads.append({"comment": c, "replies": []})
        for c in comments:
            if c.get("in_reply_to_id") is not None:
                for head in heads:
                    if head["comment"]["id"] == c["in_reply_to_id"]:
                        head["replies"].append(c)
                        break

        lines = []
        for head in heads[:25]:
            c = head["comment"]
            quoted = c.get("original_line") or c.get("line")
            lines.append(
                f"THREAD on {c.get('path')}:{quoted} by "
                f"{c.get('user', {}).get('login')}:"
            )
            lines.append(f"  {(c.get('body') or '')[:1000]}")
            for reply in head["replies"][:10]:
                lines.append(
                    f"  ↳ {reply.get('user', {}).get('login')}: "
                    f"{(reply.get('body') or '')[:800]}"
                )
        body = "\n".join(lines)
        body, truncated = truncate_output(body, 30000)
        return ToolResult(
            success=True,
            output=body or "(no threads)",
            evidence=f"PR #{pr_num}: {len(heads)} inline threads"
            + (" [truncated]" if truncated else ""),
            truncated=truncated,
        )

    def _github_pr_checks(self, pr=None, repo=None, workdir=".") -> ToolResult:
        try:
            self.require_available()
            pr_num, owner, repo_name = self._resolve_pr(pr, repo, workdir)
            if pr_num is None:
                return ToolResult(
                    success=False, output="", error="No PR reference provided."
                )
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        ref = f"{owner}/{repo_name}"
        out = self.run(["pr", "checks", str(pr_num), "-R", ref, "--json"], timeout=40)
        if not out.ok:
            return ToolResult(
                success=True,
                output="(no checks reported)",
                evidence=(
                    f"gh pr checks #{pr_num}: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        try:
            data = json.loads(out.stdout)
            lines = [
                f"- {c.get('name')}: {c.get('state')} ({c.get('workflowName') or '?'})"
                + (f" {c.get('bucket')}" if c.get("bucket") else "")
                for c in data
            ]
            total_failure = sum(
                1 for c in data if c.get("state") in {"FAILURE", "ERROR"}
            )
            return ToolResult(
                success=True,
                output="\n".join(lines) if lines else "(no checks)",
                evidence=f"PR #{pr_num}: {len(data)} checks, {total_failure} failing",
            )
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

    def _github_pr_runs(self, pr=None, repo=None, workdir=".") -> ToolResult:
        """List Actions runs, optionally for a PR."""
        try:
            self.require_available()
            owner, repo_name = self._resolve_repo(repo, workdir)
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        ref = f"{owner}/{repo_name}"
        args = [
            "run",
            "list",
            "-R",
            ref,
            "--json",
            "databaseId,displayTitle,event,headBranch,headSha,status,"
            "conclusion,workflowName,createdAt,url",
            "--limit",
            "20",
        ]
        out = self.run(args, timeout=40)
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=f"gh run list failed: {out.stderr.strip()}",
            )
        try:
            data = json.loads(out.stdout)
            lines = []
            for r in data:
                lines.append(
                    f"- run {r.get('databaseId')} [{r.get('status')}"
                    + (f"/{r.get('conclusion')}" if r.get("conclusion") else "")
                    + f"] {r.get('workflowName')} on {r.get('headBranch')} "
                    f"({r.get('displayTitle', '')[:120]})"
                )
            return ToolResult(
                success=True,
                output="\n".join(lines) if lines else "(no runs)",
                evidence=f"gh run list -R {ref}: {len(data)} runs",
            )
        except json.JSONDecodeError:
            return ToolResult(
                success=False, output="", error="gh returned invalid JSON"
            )

    def _github_run_logs(self, run_id, repo=None, workdir=".") -> ToolResult:
        """Fetch failed-run logs for diagnosis."""
        if not run_id:
            return ToolResult(success=False, output="", error="run_id is required.")
        try:
            self.require_available()
            owner, repo_name = self._resolve_repo(repo, workdir)
        except IntegrationError as e:
            return ToolResult(success=False, output="", error=str(e))
        ref = f"{owner}/{repo_name}"
        out = self.run(
            ["run", "view", str(run_id), "-R", ref, "--log-failed"],
            timeout=90,
        )
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"gh run view {run_id} failed: "
                    f"{out.stderr.strip() or out.stdout.strip()}"
                ),
            )
        logs = out.stdout
        logs, truncated = truncate_output(logs, 40000)
        return ToolResult(
            success=True,
            output=logs or "(no failed logs)",
            evidence=f"gh run view {run_id}: {len(logs)} chars"
            + (" [truncated]" if truncated else ""),
            truncated=truncated,
        )

    # ---- tool registration ----

    def tools(self):
        from core.tools.registry import Tool

        def build_tool(name, desc, handler, required=("pr",)):
            props = {
                "pr": {
                    "type": "string",
                    "description": "PR number, URL, or '#number'. "
                    "Empty = PR for the current git branch.",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository as owner/name. "
                    "Empty = current git remote.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Actions run id (number).",
                },
                "path": {
                    "type": "string",
                    "description": "Restrict the diff to one file path.",
                },
                "workdir": {
                    "type": "string",
                    "description": "Repository root to derive the remote from.",
                },
            }
            return Tool(
                name=name,
                description=desc,
                parameters={
                    "type": "object",
                    "properties": props,
                    "required": [r for r in required if r in props],
                },
                handler=handler,
                safe=True,
            )

        return [
            build_tool(
                "github_repo_info",
                "Inspect a GitHub repository: description, languages, "
                "visibility, default branch. Repo from arg or current git "
                "remote.",
                self._github_repo_info,
                required=(),
            ),
            build_tool(
                "github_pr_metadata",
                "Full PR metadata: title, state, review decision, changed "
                "files, commits, reviews, labels, body, checks rollup.",
                self._github_pr_metadata,
            ),
            build_tool(
                "github_pr_files",
                "Compact list of files changed in a PR with +/- counts.",
                self._github_pr_files,
            ),
            build_tool(
                "github_pr_diff",
                "Full unified diff of a PR. Optional path filters to one file.",
                self._github_pr_diff,
            ),
            build_tool(
                "github_pr_reviews",
                "Summarize the reviews on a PR (states + bodies + decision).",
                self._github_pr_reviews,
            ),
            build_tool(
                "github_pr_threads",
                "Inline review comment threads: line, author, body, replies.",
                self._github_pr_threads,
            ),
            build_tool(
                "github_pr_checks",
                "CI status for a PR: each check name + state.",
                self._github_pr_checks,
            ),
            build_tool(
                "github_pr_runs",
                "List recent Actions runs (status, workflow, conclusion).",
                self._github_pr_runs,
                required=(),
            ),
            build_tool(
                "github_run_logs",
                "Fetch failed-run logs for a given Actions run id.",
                self._github_run_logs,
                required=("run_id",),
            ),
        ]

    def __init__(self, runner=None):
        super().__init__(runner)
        self._diff_cache: dict[str, str] = {}
