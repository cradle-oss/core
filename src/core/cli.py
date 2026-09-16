"""CORE CLI entry point."""

import json
import sys
from pathlib import Path

import click

from core import __version__
from core.agent.loop import MODE_ITERATIONS, AgentLoop
from core.agent.state import load_recent_session
from core.integrations.discovery import discover_integrations
from core.model.provider import (
    OpenAIProvider,
    ProviderConfig,
    ProviderConfigError,
    resolve_provider_config,
)
from core.ui.terminal import TerminalUI


def _resolve_root(ctx, param, value):
    """Validate and resolve the working directory."""
    if value:
        path = Path(value).resolve()
        if not path.exists():
            raise click.BadParameter(f"Directory does not exist: {value}")
        if not path.is_dir():
            raise click.BadParameter(f"Not a directory: {value}")
        return str(path)
    return str(Path.cwd().resolve())


@click.group()
@click.version_option(__version__, prog_name="core")
@click.option(
    "--root",
    "-C",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    default=".",
    help="Working directory to operate on.",
)
@click.option(
    "--model",
    default=None,
    help="Model override. Defaults to CORE_MODEL, else openrouter/auto "
    "when OPENROUTER_API_KEY is set, else gpt-6-astra with OPENAI_API_KEY.",
)
@click.option(
    "--verbose",
    is_flag=True,
    help="Show detailed tool activity.",
)
@click.option(
    "--trace",
    is_flag=True,
    help="Enable tracing output.",
)
@click.pass_context
def cli(ctx, root, model, verbose, trace):
    """CORE - A terminal-native agent harness for repository understanding.

    Observe → Inspect → Understand → Explore → Plan → Act → Verify.
    """
    ctx.ensure_object(dict)
    ctx.obj["root"] = root
    ctx.obj["model"] = model
    ctx.obj["verbose"] = verbose
    ctx.obj["trace"] = trace


def _resolve_provider_or_exit(
    model_arg: str | None,
) -> tuple[OpenAIProvider, ProviderConfig]:
    """Resolve the model provider or exit with setup instructions."""
    try:
        config = resolve_provider_config(model_arg)
    except ProviderConfigError as e:
        click.echo(f"CORE cannot reach a model: {e}", err=True)
        sys.exit(1)
    provider = OpenAIProvider(
        model=config.requested_model,
        api_key=config.api_key,
        base_url=config.base_url,
        provider=config.provider,
    )
    return provider, config


def _make_agent(ctx, mode: str = "investigate") -> AgentLoop:
    """Construct an AgentLoop from CLI context."""
    provider, _ = _resolve_provider_or_exit(ctx.obj["model"])
    return AgentLoop(
        provider=provider,
        root=ctx.obj["root"],
        verbose=ctx.obj["verbose"],
        trace=ctx.obj["trace"],
        mode=mode,
    )


def _print_agent_report(agent: AgentLoop) -> None:
    """Print which integrations are live in this run."""
    ui = TerminalUI(
        verbose=agent.verbose,
        trace=agent.trace,
    )
    if agent.integrated_tools:
        ui.info(f"Integrated tools: {', '.join(sorted(agent.integrated_tools))}")
    for integration in agent.integrations:
        ui.info(integration.availability().status)


@cli.command()
@click.argument("task", required=False)
@click.option(
    "--plan-only",
    is_flag=True,
    help="Inspect and produce a plan without making changes.",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    help="Never prompt for approval; only read-only operations allowed.",
)
@click.option(
    "--mode",
    type=click.Choice(list(MODE_ITERATIONS.keys())),
    default="investigate",
    help="Investigation depth: quick (fast), investigate (default), "
    "deep (exhaustive evidence).",
)
@click.option(
    "--deep",
    is_flag=True,
    help="Shorthand for --mode deep.",
)
@click.pass_context
def run(
    ctx,
    task: str | None,
    plan_only: bool,
    non_interactive: bool,
    mode: str,
    deep: bool,
):
    """Run the CORE agent on a task.

    TASK is a natural-language description of what you want done.
    If omitted, reads from stdin.
    """
    if not task:
        task = sys.stdin.read().strip()
        if not task:
            click.echo("No task provided. Pass a task or pipe it via stdin.", err=True)
            sys.exit(1)

    if deep:
        mode = "deep"

    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    provider, config = _resolve_provider_or_exit(ctx.obj["model"])
    ui.info(f"CORE v{__version__} working in {ctx.obj['root']}  [mode: {mode}]")
    ui.info(f"Provider: {config.provider}, requested model: {config.requested_model}")

    if non_interactive:
        interactive = False
    else:
        interactive = True

    agent = AgentLoop(
        provider=provider,
        root=ctx.obj["root"],
        verbose=ctx.obj["verbose"],
        trace=ctx.obj["trace"],
        interactive=interactive,
        mode=mode,
    )
    _print_agent_report(agent)

    try:
        if plan_only:
            plan = agent.plan(task)
            ui.result("\nPLAN:\n")
            ui.result(plan)
            return

        agent.chat(task)
    except KeyboardInterrupt:
        ui.warning("Interrupted by user.")
        sys.exit(130)

    if provider.last_actual_model:
        ui.info(f"Model used: {provider.last_actual_model}")
    agent.log_session()


@cli.command(name="inspect")
@click.pass_context
def inspect_cmd(ctx):
    """Inspect the repository and report its structure."""
    from core.context.repo import RepoInspector

    root = ctx.obj["root"]
    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    ui.info(f"Inspecting repository at {root}")

    try:
        inspector = RepoInspector(root)
        profile = inspector.inspect()
    except FileNotFoundError as e:
        ui.error(str(e))
        sys.exit(1)

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

    if profile.git_state:
        lines.append(f"\nGit state: {json.dumps(profile.git_state, indent=2)}")

    if profile.top_level:
        lines.append("\nTop-level entries:")
        lines.extend(f"  {e}" for e in profile.top_level)

    ui.result("\n".join(lines))


@cli.command(name="web")
@click.argument("url")
@click.option(
    "--extract/--no-extract",
    default=True,
    help="Extract readable content after fetching (default: on).",
)
@click.option(
    "--inspect",
    "do_inspect",
    is_flag=True,
    help="Also dump structural inspection (links, scripts, headings).",
)
@click.option(
    "--render",
    "do_render",
    is_flag=True,
    help="Also attempt headless rendering of the page.",
)
@click.pass_context
def web_cmd(ctx, url: str, extract: bool, do_inspect: bool, do_render: bool):
    """Fetch and analyze a URL (no model required).

    Fetch → extract → (inspect|render). Reports the transport used, HTTP
    status, and honors robots.txt + rate limits.
    """
    from core.web import WebContext

    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    web = WebContext()
    ui.info(web.briefing())

    fetch = web.fetcher.fetch(url)
    if not fetch.ok:
        ui.error(f"fetch failed for {url}: {fetch.error or f'HTTP {fetch.http_code}'}")
        sys.exit(1)

    ui.result(
        f"\nFETCH {fetch.final_url}\n"
        f"transport={fetch.transport} http={fetch.http_code} "
        f"len={len(fetch.body)} chars" + (" [truncated]" if fetch.truncated else "")
    )

    if extract:
        doc = web.extractor.extract(fetch.body, fetch.final_url, plain=True)
        ui.result("\nEXTRACT (readable content)\n")
        ui.result(doc["markdown"][:4000])

    if do_inspect:
        report = web.extractor.inspect(fetch.body, fetch.final_url)
        lines = [
            f"Title: {report.title or '(none)'}",
            f"Language: {report.language or '?'} Charset: {report.charset or '?'}",
            f"Doctype: {report.doctype or '(none)'}",
        ]
        if report.canonical:
            lines.append(f"Canonical: {report.canonical}")
        lines.append("Counts:")
        lines.extend(f"  {k}: {v}" for k, v in report.counts.items())
        ui.result("\nINSPECT\n")
        ui.result("\n".join(lines))

    if do_render:
        render = web.renderer.render(url, screenshot=False)
        if not render.ok:
            ui.info(f"render unavailable: {render.error}")
        else:
            ui.result(f"\nRENDER via {render.binary} ({render.elapsed_ms}ms)\n")
            ui.result(render.dom[:3000])


@cli.command(name="git")
@click.pass_context
def git_cmd(ctx):
    """Inspect repository Git state."""
    from core.git.operations import GitError, GitRepository

    root = ctx.obj["root"]
    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    try:
        repo = GitRepository(root)
    except GitError as e:
        ui.error(str(e))
        sys.exit(1)

    lines = [
        f"Root: {repo.root}",
        f"Branch: {repo.current_branch()}",
        f"Remotes: {', '.join(repo.remotes()) or 'none'}",
    ]
    status = repo.status()
    counts = (
        f"Staged: {len(status['staged'])}, "
        f"Unstaged: {len(status['unstaged'])}, "
        f"Untracked: {len(status['untracked'])}"
    )
    lines.append(f"Working tree: {counts}")

    log = repo.log(num=10)
    if log:
        lines.append("\nRecent history:")
        lines.append(log)

    ui.result("\n".join(lines))


@cli.command(name="integrations")
@click.pass_context
def integrations_cmd(ctx):
    """Show which developer-workflow integrations are usable right now."""
    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    for integration in discover_integrations():
        av = integration.availability()
        ui.result(av.status)
        if not av.available:
            ui.info("  → the tools of this integration are not registered in this run")
        elif not av.authenticated:
            ui.info("  → installed but requires authentication")
        else:
            names = [t.name for t in integration.tools()]
            ui.info(f"  → exposes: {', '.join(names)}")


@cli.command(name="pr-review")
@click.argument("pr", required=False)
@click.option(
    "--repo",
    default=None,
    help="Repository as owner/name. Defaults to the current git remote.",
)
@click.pass_context
def pr_review(ctx, pr: str | None, repo: str | None):
    """Deep-review a GitHub PR.

    Understands the repository, inspects PR metadata, the full diff, changed
    files, surrounding code, tests, CI checks, inline review threads, and
    review decisions, then reports findings tied to actual lines.
    """
    if not pr:
        click.echo("Provide a PR number, URL, or '#number'.", err=True)
        sys.exit(1)

    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    provider, config = _resolve_provider_or_exit(ctx.obj["model"])
    ui.info(f"CORE v{__version__}: deep PR review of {pr}")
    ui.info(f"Provider: {config.provider}, requested model: {config.requested_model}")

    agent = AgentLoop(
        provider=provider,
        root=ctx.obj["root"],
        verbose=ctx.obj["verbose"],
        trace=ctx.obj["trace"],
        mode="deep",
    )
    _print_agent_report(agent)

    pr_ref = pr
    if repo:
        pr_ref = repo if "github.com" in pr else f"{repo}#{pr.lstrip('#')}"

    task = (
        "Deeply review the following GitHub PR and report what needs attention.\n\n"
        f"PR: {pr_ref}\n\n"
        "Work through this systematically and show your progress:\n"
        "1. Understand the repository first (structure, language, tests).\n"
        "2. Inspect the PR metadata: title, state, review decision, changed "
        "files, commits.\n"
        "3. Inspect the PR checklist/checks (CI status).\n"
        "4. Read the full diff (use github_pr_diff; use the path filter to "
        "study individual files).\n"
        "5. Inspect surrounding code and callers of changed functions, using "
        "search and read tools.\n"
        "6. Inspect inline review threads and prior reviews.\n"
        "7. Check whether existing tests cover the change and whether tests "
        "were added.\n"
        "8. Build findings: separate definite bugs from concerns and "
        "questions. Tie each finding to the exact changed line.\n\n"
        "Do not comment on anything without evidence. If a concern cannot be "
        "verified from actual repositories/code, say so explicitly. "
        "Report which files you actually inspected and which you did not."
    )

    try:
        result = agent.chat(task)
        ui.result("\n---\n")
        ui.result(result.response)
    except KeyboardInterrupt:
        ui.warning("Interrupted by user.")
        sys.exit(130)

    if provider.last_actual_model:
        ui.info(f"Model used: {provider.last_actual_model}")
    agent.log_session()


@cli.command(name="session")
@click.option(
    "--show",
    is_flag=True,
    help="Show the most recent session's tool activity.",
)
@click.pass_context
def session_cmd(ctx, show: bool):
    """Show session information."""
    ui = TerminalUI(verbose=ctx.obj["verbose"], trace=ctx.obj["trace"])
    session = load_recent_session()
    if not session:
        ui.info("No sessions recorded yet.")
        return

    summary = session.summary()
    ui.result(
        f"Session from {summary['started_at']}\n"
        f"Repository: {summary['root']}\n"
        f"Version: {summary['version']}\n"
        f"Tool calls: {summary['tool_calls']} "
        f"({summary['succeeded']} ok, {summary['failed']} failed, "
        f"{summary['truncated']} truncated)"
    )

    if show:
        ui.result("\nTool activity:")
        for i, call in enumerate(session.tool_calls, 1):
            status = "ok" if call.success else "FAILED"
            trunc = " [truncated]" if call.truncated else ""
            ui.result(
                f"{i}. {call.tool}({json.dumps(call.arguments)}) -> {status}{trunc}"
            )
            if call.evidence:
                ui.info(f"   evidence: {call.evidence}")

    from core.agent.state import get_session_dir

    ui.info(f"Session file: {get_session_dir()}")


def main():
    """Entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
