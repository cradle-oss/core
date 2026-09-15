# CORE: Operating Contract

**Purpose:** A terminal-native developer agent designed to understand software, work across the developer's actual toolchain, make changes safely, and verify the result.

**Core Principle:** Understand software before you change it.

**Core Loop:** Observe → Inspect → Understand → Explore → Plan → Act → Verify

---

## Background

CORE grew out of VERTEX, an earlier browser-first software understanding workspace. VERTEX established the operating philosophy (evidence over claims, bounded context, observable work, honest failure states, real verification, and calm terminal UX), and those principles remain CORE's design foundation.

VERTEX became too narrow: it was primarily a UI around model calls. CORE extends the same philosophy into a full terminal-native developer workbench where the agent, tools, repository context, Git, GitHub, web, deployment, and verification form one coherent system. No manual context copying between systems. One run moves continuously across repository, Git state, GitHub PR, inline review threads, CI, web research, and deployment.

---

## Architecture

### Component Structure
- **Model Provider Layer**: `src/core/model/provider.py`: Abstracts LLM communication. Supports OpenAI-compatible APIs. Model sends tool calls; CORE executes them.
- **Tool Registry**: `src/core/tools/registry.py`: All capabilities exposed via registered tools. Each tool returns `ToolResult(success, output, evidence, truncated, error)`.
- **Agent Loop**: `src/core/agent/loop.py`: The core: request → inspect → plan → act → verify. Manages message history, tool dispatch, approval, and session recording. Supports investigation modes (`quick` 15 / `investigate` 30 / `deep` 80 iterations) that bound effort and prepend explicit mode instructions to the system prompt.
- **Git Subsystem**: `src/core/git/operations.py`: First-class Git wrapper. Understands branch, status, diff, log, remotes, working tree vs. index vs. commit. All destructive operations blocked by default.
- **Filesystem Tools**: `src/core/tools/filesystem.py`: `read_file`, `list_directory`, `search_files`, `search_text`. Bounded reads, honest truncation.
- **File Modification Tools**: `src/core/tools/files.py`: `write_file`, `create_file`, `edit_file`, `append_file`, `delete_file`. All non-approved by default; require user confirmation.
- **Search Engine**: `src/core/tools/search.py`: `search_code`, `find_definitions`, `find_references`. Ripgrep fast path when `rg` is installed; bounded Python walker fallback otherwise. Definitions are a heuristic (def/class/func/fn/interface/struct/impl/...); always confirm by reading the file.
- **Command Execution**: `src/core/tools/command.py`: Runs shell commands. Read-only commands auto-approved; destructive patterns blocked; high-risk commands need approval.
- **Context / Repo Inspector**: `src/core/context/repo.py`: Characterizes a repository: languages, package managers, manifests, structure, Git state.
- **Safety / Approval Layer**: `src/core/safety/approval.py`: Three-tier: read-only (always safe), file modifications (need approval), destructive (blocked or confirmed).
- **Session State**: `src/core/agent/state.py`: Records tool calls, successes, evidence. Persists to `~/.core/sessions/`.
- **Terminal UI**: `src/core/ui/terminal.py`: Honest display only. Every `▸` corresponds to real work. No fake progress.

### Integration Boundary
- **`src/core/integrations/base.py`**: Developer-workflow CLIs are first-class integrations, not arbitrary shell commands. Each integration probes availability (binary present + authenticated) *before* registering tools. Wraps the native CLI preferring structured JSON (`gh ... --json`, `vercel ... --json`). Returns bounded, structured evidence. The generic shell remains the escape hatch.
- **`src/core/integrations/github.py`**: `gh` integration. Probes `gh --version` + `gh api user --jq .login`. Nine read-only tools: `github_repo_info`, `github_pr_metadata` (title, state, reviewDecision, files, commits, reviews, labels, checks rollup), `github_pr_files`, `github_pr_diff` (cached per `owner/repo#num`, `path` filter), `github_pr_reviews`, `github_pr_threads` (inline comments grouped into threads via `gh api repos/…/pulls/N/comments`), `github_pr_checks`, `github_pr_runs`, `github_run_logs` (`--log-failed`).
- **`src/core/integrations/vercel.py`**: `vercel` integration. Probes `vercel --version` + `vercel whoami`. Two read-only tools: `vercel_deployments` (`vercel ls --json`), `vercel_inspect` (`vercel inspect --json --no-wait`).
- **`src/core/integrations/discovery.py`**: `discover_integrations()` returns the known integration list; `build_integration_briefing()` renders availability into the agent system context.
- **Registration rule:** an integration's tools are registered only when `available AND authenticated`. `require_available()` raises `IntegrationError`; tool handlers translate that into a failed `ToolResult`, never a crash.
- **Repo resolution:** GitHub tools resolve `owner/repo` from an explicit `repo=` argument, else from the current git remote via `GitRepository.remote_url`. A PR can be given as number, URL, `#number`, or `owner/repo#number`; an omitted PR number resolves to the PR for the current git branch.

### PR Review Workflow
`core pr-review` drives the agent through a deep (80-iteration) review: understand the repository → GitHub repo info → PR metadata → PR files → full diff (path-filtered per file) → surrounding code via search/read tools → inline review threads and prior reviews → CI checks and failed-run logs → findings tied to exact changed lines. No claim without evidence; unusable systems are reported explicitly.

### Web Capability
Web access is a first-class capability, not a single "fetch URL" tool. It participates in the same agent context and evidence system as Code/Git/GitHub.
- **`src/core/web/transport.py`**: `WebFetcher`: bounded HTTP transport. Prefers `curl` (native CLI first), honest `urllib` fallback. Honors `robots.txt` (`urllib.robotparser`, allow on robots fetch failure), enforces a per-domain minimum delay (default 1.0s), bounds retained body (STORE_LIMIT) and reports exactly which transport ran. `truncate_for_agent()` bounds text for context with explicit truncation evidence.
- **`src/core/web/extract.py`**: `WebExtractor`: `extract()` (bs4 + html2text → readable Markdown/text; picks `main`/`article`/largest container; strips scripts/styles/nav/footer/hidden noise; structure preserved), `inspect()` (doctype/title/lang/charset/canonical/meta/headings/scripts/stylesheets/link split), `links()`, `metadata()` (Open Graph/Twitter/Article normalized). Extraction is a heuristic over received HTML; it is never a claim about a rendered app.
- **`src/core/web/search.py`**: `WebSearchProvider` interface. `DuckDuckGoProvider` ("duckduckgo-html") is the zero-config, best-effort, explicitly-not-a-stable-API backend. `ApiKeyProvider` uses `CORE_SEARCH_API_KEY` (+ optional `CORE_SEARCH_ENDPOINT`, Brave-shaped by default) for structured JSON. Results always report backend, query, count, complete/limited, and failure reason. `discover_search_provider()` picks based on config; `search_brief()` feeds the system context.
- **`src/core/web/renderer.py`**: `WebRenderer`: probes for an installed Chrome/Brave/Chromium and drives it headless via subprocess (`--dump-dom`, optional `--screenshot`). Some builds reject `--virtual-time-budget`/`--screenshot` ("Multiple targets are not supported in headless mode"); the renderer degrades honestly (drops the time budget, then screenshot) and reports notes. Absent browsers degrade gracefully; the Web subsystem never depends on rendering. Screenshot dimensions are recorded; pixels are **not** visually interpreted (no vision model this milestone).
- **`src/core/web/crawl.py`**: `crawl()`: bounded BFS within an explicit scope (`host` or `path`), capped pages (≤30) and depth (≤5), auto-excludes `/api/`, `/.well-known/`, `/cgi-bin/`, and binary resources. Never an uncontrolled recursive crawl.
- **`src/core/web/compare.py`**: `compare()`: fetch + extract two pages, `difflib` similarity + unified diff. For page/version comparison.
- **`src/core/web/context.py`, `tools.py`**: `WebContext` bundles fetcher/extractor/renderer/search and produces a briefing. `register_web_tools()` registers nine read-only, auto-approved tools: `web_fetch`, `web_extract`, `web_inspect`, `web_links`, `web_metadata`, `web_search`, `web_crawl`, `web_render`, `web_compare`.
- **Evidence levels are distinct:** fetched HTML → extracted readable content → rendered DOM → screenshot. "URL reached" ≠ "page understood"; "HTML received" ≠ "rendered application understood"; "search result found" ≠ "claim verified". Never claim rendered-page understanding when only HTML was fetched, and state explicitly when only partial content was obtained.
- **Access boundaries:** respect robots.txt, site terms, authentication boundaries, rate limits, and restrictions. Never attempt to bypass authentication, CAPTCHAs, paywalls, or technical access controls.
- **`core web <url> [--inspect] [--render]`**: single-shot fetch/extract/inspect with no model or API key required.

---

## Important Directories
- `src/core/`: Source package
- `src/core/tools/`: All tool implementations
- `src/core/integrations/`: First-class CLI integrations (directory)
- `src/core/integrations/github.py`, `vercel.py`, `discovery.py`, `base.py`
- `src/core/web/`: Web subsystem (fetch/extract/search/render/crawl/compare)
- `src/core/git/`: Git operations wrapper
- `src/core/context/`: Repository inspection
- `src/core/safety/`: Approval and safety
- `src/core/ui/`: Terminal output
- `tests/`: Pytest test suite

---

## Development Commands
```bash
# Install dependencies
.venv/bin/pip install -e .

# Run tests
.venv/bin/pytest tests/ -v

# Lint
.venv/bin/ruff check src/ tests/

# Format
.venv/bin/ruff format src/ tests/

# Run CORE (requires OPENAI_API_KEY)
export OPENAI_API_KEY=sk-...
.venv/bin/core run "inspect this repository"
.venv/bin/core run --mode deep "add a test for main function"
.venv/bin/core run --deep "trace how deploy errors propagate"
.venv/bin/core inspect
.venv/bin/core git
.venv/bin/core integrations
.venv/bin/core web https://example.com/
.venv/bin/core web https://example.com/ --inspect
.venv/bin/core web https://example.com/ --render
.venv/bin/core pr-review 42
.venv/bin/core pr-review #42 --repo acme/widgets
.venv/bin/core session --show
```

---

## Conventions
- All tools must return `ToolResult` with success, output, evidence, truncated, error fields.
- Evidence must describe what was actually observed (line counts, char counts, file names, exit codes).
- Git operations use `GitRepository` wrapper. No raw subprocess calls outside `git/operations.py`.
- Tools marked `safe=False` require approval; all others are auto-approved.
- No fake spinners, no fake progress indicators, no commented-out work logs.

---

## Safety Rules
- **NEVER** run `git reset --hard`, `git clean -fd`, `git checkout -- .`, `git restore .`, `git push --force`, or `rm -rf /` without explicit user confirmation.
- **NEVER** claim a test passed unless it actually ran.
- **NEVER** claim a file was read unless `read_file` or `inspect_repository` was actually called.
- **NEVER** invent file paths, APIs, commands, or tests that don't exist.
- **NEVER** auto-commit or auto-push without explicit user request.
- **ALWAYS** distinguish staged vs. unstaged vs. untracked in Git status.
- **ALWAYS** report exit codes from commands, even when they fail.

---

## Git Expectations
Git is a first-class system. Before any code change:
1. Check `git status` for existing modifications.
2. Understand what branch is checked out.
3. Understand what is staged vs. unstaged.
4. Make changes.
5. Run `git diff` to confirm changes are correct.
6. Run appropriate verification (tests, linter).
7. Only commit if explicitly asked by user.

Understanding:
- Working tree = files on disk
- Index = staged area
- Local branch = named pointer to a commit
- Remote-tracking branch = state of remote last fetched
- Commit = immutable snapshot with parent, author, message
- Tag = named pointer to a commit

---

## Verification Expectations
After any code change:
1. Show what was changed (`git diff` or file read).
2. Run appropriate tests if available.
3. Run linter/type-checker if configured.
4. Report evidence of what passed, what failed, what was not tested.
5. Never claim "it works" without running something.

---

## Known Limitations (v0.4.0)
- Only OpenAI-compatible models supported (gpt-4 etc).
- No streaming output.
- No parallel tool execution.
- No agent-to-agent collaboration.
- No streaming terminal progress.
- No multi-turn session resume (manual log review only).
- No automatic stash/save workflow.
- Git conflict resolution is user-guided, not automated.
- No credential management; relies on environment.
- GitHub/Vercel integrations are read-only in this milestone (no mutating GH API/CLI tools, no deployments/create). Mutating operations are the next milestone.
- `github_pr_runs` lists runs but does not yet correlate per-PR runs to checks in one tool; correlation happens across `github_pr_checks` + `github_pr_runs`.
- Availability probing runs real subprocess probes (`gh --version`, `gh api user`) at agent construction; offline environments register no tools (graceful).
- Search definitions are heuristic; symbol resolution is not language-tree-aware.
- DuckDuckGo HTML search is zero-config and best-effort: it is explicitly not a stable API and may break at any time; results always report that status.
- Headless renderer screenshots are captured as evidence only; CORE does not visually interpret them (no vision model in this milestone).
- Some Chrome-family builds (e.g. certain Brave builds on macOS) reject `--virtual-time-budget` or `--screenshot` in combination with other flags; the renderer degrades honestly (DOM-only) and reports notes.

---

## Design Decisions
1. **Bounded reads**: `read_file` returns 2000 lines by default with clear truncation. Full dumps are avoided to keep context usable.
2. **Evidence field**: Every `ToolResult` includes an `evidence` string so the agent and user can trace what actually happened.
3. **Approval three-tier**: Read-only is safe, file modifications need approval, destructive Git is blocked until explicitly confirmed.
4. **No abstractions over abstractions**: `ToolRegistry` maps tool name → handler function. No proxy layer, no metaclasses, no config DSLs.
5. **Session persistence**: Tool call history saved to `~/.core/sessions/` for auditing, not for replay.
6. **Integration boundary**: Developer CLIs live behind `Integration`; tools register only when `available AND authenticated`; everything uses `--json` output; errors surface as failed `ToolResult`s, never exceptions.
7. **Injection mode**: `MODE_ITERATIONS` bounds iterations (quick=15, investigate=30, deep=80); `MODE_PREFIX` adds an explicit, prioritized instruction block to the system prompt so the model knows depth expectations.
8. **Search depth over breadth**: `search_code` prefers `rg` when present; the Python walker is the honest fallback that reports exactly which backend ran. Definitions are heuristic and must be confirmed by reading the file.
9. **Web evidence levels are explicit**: fetched HTML ≠ extracted readable content ≠ rendered DOM ≠ screenshot. Every Web `ToolResult` evidence string reports which backend ran (curl/urllib/brave-not-found/duckduckgo/...), what was obtained, and any bounds or truncation applied.
10. **Renderer staged fallback**: headless Chrome/Brave flags that fail with "Multiple targets are not supported in headless mode" degrade automatically: drop `--virtual-time-budget`, then drop `--screenshot`, returning DOM only with honest notes rather than failing entirely.

---

## Things Future Agents Must Not Assume
- Do not assume the user has Docker, Node, Python, or any specific runtime installed.
- Do not assume CI is configured.
- Do not assume tests exist.
- Do not assume the repo uses Git (it might use Mercurial, or nothing).
- Do not assume the agent has write access (sandboxed environments exist).
- Do not assume the LLM provider is available (network may be down).
- Do not assume the repository is clean before starting work.

---

## Version
Current: **0.4.0**

SemVer: Renamed from VERTEX to CORE. CORE is a terminal-native developer agent and toolchain workbench. VERTEX was the earlier browser-first system whose operating philosophy (evidence over claims, bounded context, observable work, honest failure states, real verification, calm terminal UX) CORE was built on. The name change reflects an actual change in scope: CORE is no longer primarily a UI around model calls but a coherent system where the agent, tools, repository context, Git, GitHub, web, deployment, and verification form one shared context. All source paths, CLI commands, session directories (`~/.core/sessions/`), environment variables (`CORE_SEARCH_API_KEY`, `CORE_SEARCH_ENDPOINT`, `CORE_MODEL`), and user-facing strings now use CORE. Historical VERTEX entries in `ENGINEERING_RECORD.md` are preserved as-is.

---

## Next milestone: prove CORE is execution-capable

The next milestone is the important one: prove CORE can execute in a foreign repository rather than merely inspect one.

Use a small foreign repository, not CORE itself. Run the complete workflow:

`inspect → find/grep → trace relevant code → understand → plan → approval → edit → diff → test → git status → final evidence`

Requirements:
- Make one small, real, useful change.
- Do not commit or push.
- Every operation shown in the terminal must be real.
- Use CORE's existing Git, filesystem/search, approval, edit, and verification capabilities.
- Capture the initial Git state and final Git state.
- Show the exact changed files and diff.
- Run the repository's appropriate test/lint/check command.
- If something cannot be determined, say so rather than guessing.
- Do not add new subsystems unless the foreign-repo test exposes a concrete missing capability.

At the end, report:
1. repository used
2. task given
3. investigation performed
4. change made
5. verification performed
6. final Git state
7. limitations encountered
8. concrete next milestone

The key distinction is that CORE already has modification capability. What it does not yet have is the broader execution workflow proven end-to-end on an unfamiliar repository.

This is the right next test for CORE.

---

## Release automation (pre-publication)

CORE uses SemVer and tag-based releases. The initial release workflow is intentionally minimal: GitHub Actions validates a tag, runs the existing checks, builds the Python distribution, and creates a GitHub Release with the generated artifacts. It does not publish to PyPI yet.

Versioning convention:
- package version comes from `pyproject.toml`
- git tags use the `vX.Y.Z` format, for example `v0.4.0`
- the workflow refuses to continue if the tag does not exactly match `pyproject.toml` (for example, `v0.4.0` must match version `0.4.0`)

Release flow:
1. create a SemVer tag locally, e.g. `git tag v0.4.0`
2. push the tag to GitHub: `git push origin v0.4.0`
3. GitHub Actions runs the release workflow
4. the workflow verifies the tag/package version, runs `pytest` and `ruff check`, builds the package, and publishes a GitHub Release with the generated `dist/*` artifacts

What CI verifies:
- tag format is valid (`vX.Y.Z`)
- tag matches the package version in `pyproject.toml`
- test suite passes (`pytest tests/ -q`)
- lint passes (`ruff check src/ tests/`)
- package builds successfully (`python -m build`)
- at least one artifact is created in `dist/`

What is deliberately not automated yet:
- no PyPI publish step
- no automatic tag creation from `main`
- no automatic version bumping
- no release automation beyond GitHub Release + artifact generation

This is the correct release discipline for the current phase: small, explicit, and evidence-based. The repository is not published until the release process is verified and the first public release is explicitly approved.
