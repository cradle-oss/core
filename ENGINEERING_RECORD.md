# VERTEX Engineering Record

External engineering record for VERTEX, a terminal-native agent harness for
repository understanding and safe execution. Updated as the project evolves.

---

## 2026-09-15 - v0.1.0 - Inaugural vertical slice

**Version:** 0.1.0
**Date:** 2026-09-15

### What changed
Initial vertical slice proving the core loop: user request → agent loop → model
→ real tool → observable result → verification. Established the full component
architecture as separate modules rather than a monolith:
- Agent loop (`src/vertex/agent/loop.py`)
- Model provider layer (`src/vertex/model/provider.py`, OpenAI-compatible)
- Tool registry and 18 tools: filesystem, file modification, command execution,
  Git inspection, repository inspection
- Git subsystem as a first-class wrapper (`src/vertex/git/operations.py`)
- Repository inspector (`src/vertex/context/repo.py`)
- Safety/approval layer (three-tier: read-only auto-approve, modifications need
  approval, destructive blocked)
- Session state persisted to `~/.vertex/sessions/`
- Terminal UI with honest, evidence-based output (no fake progress)

### Why
VERTEX was previously a browser-first inspection product. This project re-hosts
the VERTEX philosophy (bounded context, evidence, honest limits) in a
terminal-native harness that adds the missing half: real execution, Git mastery,
and verification. Git is treated as a first-class system, not another shell
command.

### Evidence / Verification
- 25 tests passing via `.venv/bin/pytest tests/ -v`
- Lint clean via `.venv/bin/ruff check src/ tests/`
- `vertex inspect`, `vertex git`, `vertex session` commands verified against the
  VERTEX repo itself
- End-to-end vertical slice verified with a mock provider: agent loop dispatched
  a real `inspect_repository` tool call, observed its output, recorded the call
  with evidence in the session, and produced a final response
- Git wrapper handles empty repos gracefully (no commits yet)

### Known limitations
- Only OpenAI-compatible models supported
- No streaming output, no parallel tool execution
- No multi-turn session resume (manual log review only)
- Auto-commit/push intentionally disabled; requires explicit user approval
- Edit/modify tools require interactive approval; non-interactive mode is
  read-only for state changes

### Next concrete step
Prove a real end-to-end task on a non-VERTEX repository: agent inspects a foreign
repo (Git state, manifests, structure), makes a small verified change (test +
diff + status), and reports evidence-backed results under interactive approval.

---

## 2026-09-15 - v0.2.0 - Developer workbench

**Version:** 0.2.0
**Date:** 2026-09-15

### What changed
VERTEX becomes a developer workbench: repository → Git state → GitHub PR → inline
review threads → CI checks and failed-run logs → Vercel deployments, carried
continuously by the agent with evidence from each system feeding the next.
- Integration boundary (`src/vertex/integrations/base.py`): `Integration` ABC
  with cached availability probing (binary present + authenticated) and a runner
  that wraps native CLIs in exec-form. Availability is three-state: unavailable /
  installed-but-unauthenticated / available-as-<identity>. `require_available()`
  raises `IntegrationError`; tool handlers turn that into a failed `ToolResult`,
  never a crash.
- GitHub integration (`integrations/github.py`): probes `gh --version` +
  `gh api user --jq .login`. Nine read-only tools: `github_repo_info`,
  `github_pr_metadata`, `github_pr_files`, `github_pr_diff` (cached per
  `owner/repo#num`, with a `path` filter that splits diff sections),
  `github_pr_reviews`, `github_pr_threads` (via `gh api repos/O/R/pulls/N/comments`
  --paginate, grouped into threads by `in_reply_to_id`), `github_pr_checks`,
  `github_pr_runs`, `github_run_logs` (`--log-failed`).
- Vercel integration (`integrations/vercel.py`): probes `vercel --version` +
  `vercel whoami`. Two read-only tools: `vercel_deployments` (`vercel ls --json`),
  `vercel_inspect` (`vercel inspect --json --no-wait`).
- Repo resolution: GitHub tools resolve `owner/repo` from an explicit `repo=`
  arg, else the current git remote (`GitRepository.remote_url`). PRs may be a
  number, URL, `#N`, or `owner/repo#N`; omitted PR resolves to the PR for the
  current branch. No remote → clean failed ToolResult explaining the gap.
- Search engine (`tools/search.py`): `search_code` / `find_definitions` /
  `find_references`. Ripgrep fast path when installed; otherwise an honest
  bounded Python walker that reports which backend actually ran. Definitions are
  a heuristic; evidence demands confirming by reading the file.
- Investigation modes: `MODE_ITERATIONS` (quick=15, investigate=30, deep=80)
  bounds agent effort; `MODE_PREFIX` injects explicit depth expectations into
  the system prompt. CLI exposes `--mode` and `--deep` on `run`.
- CLI: `vertex integrations` reports availability per integration; `vertex
  pr-review <pr> [--repo]` seeds a deep (80-iteration) review workflow: repository → PR metadata → files → full diff → surrounding code via
  search/read → threads/reviews → CI checks/runs/logs → findings tied to exact
  changed lines.

### Why
The developer's loop does not live in one system. A change travels from IDE
through Git into a PR, where reviewers and CI react and deployment follows. If the
assistant can only see the local repository, half the evidence is missing. Making
`gh` and `vercel` first-class, availability-gated integrations (native CLI +
`--json`, never scraping) gives the agent real cross-system context with honest
bounds, and reuses the developer's existing credentials instead of re-inventing
OAuth flows.

### Evidence / Verification
- 52 tests passing via `.venv/bin/pytest tests/ -v` (25 existing + 2 agent-loop
  mode tests + 15 integration tests with canned CLI outputs + 10 search tests
  covering both the walker fallback and the rg parse path)
- Lint clean via `.venv/bin/ruff check src/ tests/` and `.venv/bin/ruff format`
- Integration tests use a FakeRunner returning canned `gh`/`vercel` outputs:
  availability probing (available / installed-unauthenticated / unavailable),
  tool registration gated on availability AND authentication, PR metadata render,
  diff caching + path filtering, thread grouping, repo-from-git-remote resolution
- Search tests force the Python walker (rg not installed in this environment) and
  separately mock `subprocess.run` to verify rg output parsing
- Environment probed: `gh` 2.100.0 (authenticated as bniladridas), `vercel`
  59.1.4 (authenticated), `rg` NOT installed (walker fallback exercised), git
  2.50.1, python 3.14.7

### Known limitations (added this milestone)
- GitHub/Vercel integrations are read-only; mutating operations (commenting,
  approving, re-running checks, deployments/create) are the next milestone
- `github_pr_runs` lists runs but does not correlate per-PR runs to checks in one
  tool; correlation happens across `github_pr_checks` + `github_pr_runs`
- Availability probing runs real subprocess probes at agent construction;
  offline environments just register no tools (graceful)
- Search definitions are heuristic; symbol resolution is not language-tree-aware

### Next concrete step
Make the GitHub integration mutating behind explicit approval: post a positioned
review comment or issue comment from a collected findings list, and add the
`vertex pr-comment` flow reusing the PR review pipeline's cached diff + threads.

---

## 2026-09-15 - Web Capability Milestone (v0.3.0)

### What landed
Built the first-class VERTEX Web subsystem (`src/vertex/web/`): nine read-only
auto-approved tools wired into the agent system context alongside Git/GitHub/Vercel
via `WebContext` + `register_web_tools`. Each tool returns evidence naming the
transport/search backend used and any truncation/bounds applied.

### New capabilities (all safe=True, no approval required)
- **web_fetch**: bounded HTTP via curl (primary) or urllib fallback; robots.txt
  respected; per-domain rate limit (MIN_DOMAIN_DELAY 1.0s); STORE_LIMIT 400k chars;
  evidence reports transport, HTTP code, truncation
- **web_extract**: bs4 + html2text → readable Markdown/text; container selection
  (main > article > largest div/section/td ≥ 200 chars); strips scripts/styles/
  nav/footer/noise; MAX_CONTENT 60k; evidence reports title, container, char count
- **web_inspect**: structural report from received HTML: doctype (bs4 Doctype
  element scan), title, language, charset, canonical, heading tree, external script
  list, stylesheet list, counts (scripts/styles/links/forms/tables/code blocks);
  counts use raw soup (not noise-cleaned)
- **web_links**: internal vs external link split (≤200 each), anchor text
- **web_metadata**: normalized meta bundle (title, description, canonical,
  language, charset, og_*, twitter_*, article_* from raw soup)
- **web_search**: `WebSearchProvider` ABC with two backends:
  - DuckDuckGoProvider: zero-config HTML scraping (best-effort, explicitly
    reported as fragile/not-a-stable-API); parses result__a + result__snippet
    classes; decodes //duckduckgo.com/l/?uddg= redirect wrapper
  - ApiKeyProvider: VERTEX_SEARCH_API_KEY (optional VERTEX_SEARCH_ENDPOINT);
    Authorization: Bearer; normalizes web.results/results/organic/data
  - discover_search_provider() picks based on env; search_brief() feeds system context
- **web_crawl**: bounded BFS (host or path scope); max 30 pages, max depth 5;
  auto-excludes /api/, /.well-known/, /cgi-bin/, .pdf/.zip/.gz
- **web_render**: headless Brave/Chrome via subprocess; staged honest fallback:
  try --virtual-time-budget + screenshot → on "Multiple targets are not supported
  in headless mode" (observed on this Brave build) degrade to --dump-dom only →
  if screenshot requested but unsupported, return DOM with a note; screenshot
  dimensions parsed from PNG header (struct unpack) but pixels not visually
  interpreted (no vision model); absent browsers report graceful "unavailable"
- **web_compare**: fetch + extract two pages; difflib SequenceMatcher ratio +
  unified_diff (n=3); bounded diff (COMPARE_LIMIT 20k chars)

### Files added/changed
- `pyproject.toml`: added `beautifulsoup4>=4.12.0`, `html2text>=2024.2.26`
- `src/vertex/web/__init__.py`: exports WebContext + register_web_tools
- `src/vertex/web/transport.py`: WebFetcher (curl primary, urllib fallback,
  robots.txt via urllib.robotparser, per-domain rate limit, optional headers)
- `src/vertex/web/extract.py`: WebExtractor (extract/inspect/links/metadata,
  bs4+html2text, Doctype element scan for doctype detection, raw-soup counts)
- `src/vertex/web/search.py`: WebSearchProvider, DuckDuckGoProvider,
  ApiKeyProvider, discover_search_provider, search_brief
- `src/vertex/web/renderer.py`: WebRenderer (binary probe, staged fallback,
  screenshot dims via struct, notes field for honest degradation reporting)
- `src/vertex/web/crawl.py`: crawl() BFS with scope, auto-exclusions
- `src/vertex/web/compare.py`: compare() difflib page comparison
- `src/vertex/web/context.py`: WebContext (shared state, briefing)
- `src/vertex/web/tools.py`: register_web_tools (9 tools, all safe=True)
- `src/vertex/agent/loop.py`: WEB_CAPABILITY_PREFIX prompt block; WebContext
  + register_web_tools wired after core tools; web briefing in chat()/plan()
  system messages; _web_block() helper
- `src/vertex/cli.py`: `vertex web <url>` command (--inspect/--render flags)
- `src/vertex/__init__.py` + `pyproject.toml`: version bump to 0.3.0
- `AGENTS.md`: Web Capability section, web directories, dev commands, v0.3.0
  known limitations, design decisions #9/#10, version section

### Tests
`tests/test_web.py`: 43 tests covering:
- Transport: fetch (curl path, urllib forced fallback, 404, invalid URL, robots
  blocked/allowed, rate-limit wait ≥ delay, headers forwarded, STORE_LIMIT
  truncation)
- Extract: main content selection, no-script stripping, plain dict fields,
  inspect report (doctype/title/language/links/scripts), internal/external link
  split, metadata og:*
- Search: DDG parse (canned HTML, redirect cleanup, empty results, best-effort
  note), ApiKeyProvider selection (monkeypatched env), canned Brave response,
  discover_search_provider, search_brief
- Crawl: host scope, path scope (start URL always visited, prefix filtering),
  max_pages cap
- Compare: identical pages, different pages, fetch failure
- Context: briefing string, shared fetcher
- Tools: all nine tools registered (names), web_fetch/web_extract/web_search/
  web_compare via registry, web_render graceful
- Renderer: graceful when no binary, "Multiple targets" degradation to DOM-only,
  screenshot unsupported degradation, screenshot dims parsed from PNG, real
  headless Brave DOM test

### Real bugs found and fixed (caught by live end-to-end testing)
1. **urllib.robotparser removed in Python 3.14**: `import urllib.robotparser`
   now explicit in transport.py (implicit submodule access dropped)
2. **subprocess.CompletedProcess has no .ok**: `if not result.ok` in curl
   handler replaced with `if result.returncode != 0`
3. **bs4 4.15 Doctype detection**: `soup.doctype` returns None; replaced with
   content-scan for `isinstance(child, Doctype)` element
4. **bs4 4.15 None.attrs on malformed tags**: `_is_hidden` changed to read
   `tag.attrs or {}` before checking for "hidden"/"style" keys
5. **difflib.unified_diff(context=3) invalid kwarg**: changed to `n=3`
6. **Brave headless "Multiple targets"**: `--virtual-time-budget` and
   `--screenshot` trigger this on certain Brave builds when combined with
   `--no-sandbox`/`--disable-dev-shm-usage`; renderer now stages attempts and
   degrades honestly to DOM-only with a note
7. **_screenshot_from off-by-one**: length check changed from `> 24` to `>= 24`
8. **inspect() counted scripts after noise decomposition**: script/style/form
   counts now computed from raw soup; headings/meta from cleaned soup

### Verification
- 96 tests passing (53 core + 43 web)
- ruff check + format clean across src/ and tests/
- Live smoke: `vertex web https://example.com/ --inspect` (fetch/extract/inspect
  via real curl), DDG search (3 real hits returned), real Brave headless DOM
  (561 chars) with staged degradation notes

### Next concrete step
Wire `vertex web` search into the agent's live research workflows; add the
`web_fetch` + `web_extract` tool-chain to the PR review deep-dive (inspect
linked issues/docs from review threads); make the GitHub/Vercel integration
tooling mutating behind explicit approval (comment, approve, re-run, deploy).

---

## 2026-09-15 - Rename from VERTEX to CORE (v0.4.0)

### What happened
The project was renamed from VERTEX to CORE to reflect a genuine change in scope:

- **VERTEX** was a browser-first software understanding workspace - primarily a UI
  around model calls, focused on reading code and producing explanations.
- **CORE** is a terminal-native developer agent and toolchain workbench - a coherent
  system where the agent, tools, repository context, Git, GitHub, web, deployment,
  and verification form one shared context with no manual context copying.

The operating philosophy carries forward unchanged:
evidence over claims, bounded context, observable work, honest failure states, real
verification, and calm terminal UX.

### What was renamed
- Source directory: `src/vertex/` → `src/core/`
- Package name: `vertex` → `core`
- CLI command: `vertex` → `core`
- Session directory: `~/.vertex/sessions/` → `~/.core/sessions/`
- Environment variables: `VERTEX_SEARCH_API_KEY` → `CORE_SEARCH_API_KEY`,
  `VERTEX_SEARCH_ENDPOINT` → `CORE_SEARCH_ENDPOINT`,
  `VERTEX_MODEL` → `CORE_MODEL`
- All imports throughout the codebase
- Agent system prompt, user-facing terminal output, CLI help text
- AGENTS.md, pyproject.toml, test fixtures

### What was preserved
- Historical entries in this file (v0.1.0 through v0.3.0) remain unchanged -
  they document the work done under the VERTEX name.
- All design principles, tool implementations, test coverage (96 tests), and
  the complete architecture carried forward unchanged.
- The VERTEX lessons (from the browser-first era) remain CORE's design foundation.

### Verification
- 96 tests passing across all modules (agent, tools, integrations, web, safety,
  registry, search)
- ruff check + format clean
- All imports resolve correctly under the new `core` package name
- CLI entry point `core run`, `core web`, `core pr-review`, `core integrations`
  all documented in AGENTS.md

### Key architectural insight
The rename is not cosmetic. VERTEX was built around model calls - the agent
observed, and the human acted. CORE reverses this: the agent operates across
repository, Git, GitHub, web, deployment, and verification as first-class systems.
The agent is the primary actor; the human remains the decision-maker, but the
agent carries context between systems so the human does not have to.

---

## 2026-09-15 - Next Milestone: prove CORE is execution-capable

### What the next milestone should prove
The real question is no longer whether CORE can inspect a repository. The real
question is whether CORE can successfully execute a complete task inside an
unfamiliar codebase.

The milestone is intentionally modest: one small, real, useful change in a
foreign repository is stronger evidence than a large feature attempt.

### Recommended workflow
- Foreign repo → inspect → find/grep → trace relevant code → understand → plan
- Approval → edit → diff → test → git status → final evidence
- No commit, no push, no large subsystem work unless the foreign-repo test
  reveals a missing capability

### Builder handoff wording
> **Next milestone: prove CORE is execution-capable.**
>
> Use a small foreign repository, not CORE itself.
>
> Run the complete workflow:
>
> `inspect → find/grep → trace relevant code → understand → plan → approval → edit → diff → test → git status → final evidence`
>
> Requirements:
>
> * Make one small, real, useful change.
> * Do not commit or push.
> * Every operation shown in the terminal must be real.
> * Use CORE's existing Git, filesystem/search, approval, edit, and verification capabilities.
> * Capture the initial Git state and final Git state.
> * Show the exact changed files and diff.
> * Run the repository's appropriate test/lint/check command.
> * If something cannot be determined, say so rather than guessing.
> * Do not add new subsystems unless the foreign-repo test exposes a concrete missing capability.
>
> At the end, report:
>
> 1. repository used
> 2. task given
> 3. investigation performed
> 4. change made
> 5. verification performed
> 6. final Git state
> 7. limitations encountered
> 8. concrete next milestone
>
> Update `AGENTS.md` and `ENGINEERING_RECORD.md` only after the workflow is actually verified.

### Why this matters
CORE already has modification capability. The missing proof is not another large
subsystem; it is the end-to-end execution loop operating in a real, unfamiliar
repository with real evidence and honest limits.

This is the right next test for CORE.
