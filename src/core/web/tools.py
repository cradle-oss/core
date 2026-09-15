"""Web tools registered into the agent registry.

Read-only, safe, auto-approved like other investigation tools. Each tool
returns ToolResult with evidence naming the backend that actually ran and
any bounds/truncation applied. Evidence levels stay distinct: fetched HTML,
extracted readable content, rendered DOM, screenshot.
"""

from __future__ import annotations

import json

from core.tools.registry import Tool, ToolResult
from core.web.compare import compare
from core.web.context import WebContext
from core.web.crawl import crawl
from core.web.transport import truncate_for_agent

BODY_LIMIT = 60_000
LINKS_LIMIT = 200


def _tool(
    name: str,
    description: str,
    handler,
    props: dict,
    required: list[str] | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        parameters={"type": "object", "properties": props, "required": required or []},
        handler=handler,
        safe=True,
    )


def _url_prop() -> dict:
    return {"type": "string", "description": "Absolute http(s) URL"}


def register_web_tools(registry, context: WebContext | None = None) -> None:
    """Register the Web toolset. Accepts an optional shared WebContext."""
    ctx = context or WebContext()

    # ---- fetch ----

    def web_fetch(url: str) -> ToolResult:
        result = ctx.fetcher.fetch(url)
        if not result.ok:
            return ToolResult(
                success=False,
                output="",
                evidence=(
                    f"web_fetch {url}: {result.transport}, "
                    f"HTTP {result.http_code}, failed"
                ),
                error=result.error or f"HTTP {result.http_code}",
            )
        body, truncated = truncate_for_agent(result.body, BODY_LIMIT)
        evidence = (
            f"web_fetch {result.final_url}: {result.transport}, "
            f"HTTP {result.http_code}, {len(result.body)} chars"
            + (" [stored bounded]" if result.truncated else "")
            + (" [truncated for agent]" if truncated else "")
        )
        return ToolResult(
            success=True,
            output=body,
            evidence=evidence,
            truncated=truncated or result.truncated,
        )

    # ---- extract ----

    def web_extract(url: str) -> ToolResult:
        fetch = ctx.fetcher.fetch(url)
        if not fetch.ok:
            return ToolResult(
                success=False,
                output="",
                evidence=f"web_extract: fetch failed HTTP {fetch.http_code}",
                error=fetch.error or f"HTTP {fetch.http_code}",
            )
        doc = ctx.extractor.extract(fetch.body, fetch.final_url, plain=True)
        return ToolResult(
            success=True,
            output=doc["markdown"],
            evidence=(
                f"extracted readable content from {url}: title={doc['title']}, "
                f"container={doc.get('container', '')}, {len(doc['markdown'])} chars"
                + (" [truncated]" if doc["truncated"] else "")
            ),
            truncated=doc["truncated"],
        )

    # ---- inspect ----

    def web_inspect(url: str) -> ToolResult:
        fetch = ctx.fetcher.fetch(url)
        if not fetch.ok:
            return ToolResult(
                success=False,
                output="",
                evidence="web_inspect: fetch failed",
                error=fetch.error or f"HTTP {fetch.http_code}",
            )
        report = ctx.extractor.inspect(fetch.body, fetch.final_url)
        lines = [
            f"URL: {report.url}",
            f"Title: {report.title or '(none)'}",
            f"Language: {report.language or '?'}  Charset: {report.charset or '?'}",
            f"Doctype: {report.doctype or '(none)'}",
        ]
        if report.canonical:
            lines.append(f"Canonical: {report.canonical}")
        for name, value in report.counts.items():
            lines.append(f"  {name}: {value}")
        lines.append("")
        if report.external_scripts:
            lines.append(f"External scripts ({len(report.external_scripts)}):")
            lines.extend(f"  {s}" for s in report.external_scripts)
        if report.stylesheets:
            lines.append(f"Stylesheets ({len(report.stylesheets)}):")
            lines.extend(f"  {s}" for s in report.stylesheets)
        if report.headings:
            lines.append("\nHeadings:")
            lines.extend(f"  <{n}> {t}" for n, t in report.headings[:25])
        body = "\n".join(lines)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"web_inspect {report.url}: "
                f"{report.counts['links']} links, "
                f"{report.counts['scripts']} scripts, "
                f"{report.counts['stylesheets']} stylesheets"
            ),
        )

    # ---- links ----

    def web_links(url: str) -> ToolResult:
        fetch = ctx.fetcher.fetch(url)
        if not fetch.ok:
            return ToolResult(
                success=False,
                output="",
                evidence="web_links: fetch failed",
                error=fetch.error or f"HTTP {fetch.http_code}",
            )
        bundle = ctx.extractor.links(fetch.body, fetch.final_url)
        lines = [f"Internal links: {bundle.counts['internal']}"]
        for entry in bundle.internal[: int(LINKS_LIMIT / 2)]:
            lines.append(f"  {entry['url']}  :: {entry['text']}")
        lines.append(f"External links: {bundle.counts['external']}")
        for entry in bundle.external[: int(LINKS_LIMIT / 2)]:
            lines.append(f"  {entry['url']}  :: {entry['text']}")
        body = "\n".join(lines)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"web_links {url}: {bundle.counts['internal']} internal, "
                f"{bundle.counts['external']} external"
            ),
        )

    # ---- metadata ----

    def web_metadata(url: str) -> ToolResult:
        fetch = ctx.fetcher.fetch(url)
        if not fetch.ok:
            return ToolResult(
                success=False,
                output="",
                evidence="web_metadata: fetch failed",
                error=fetch.error or f"HTTP {fetch.http_code}",
            )
        meta = ctx.extractor.metadata(fetch.body, fetch.final_url)
        body = json.dumps(meta, indent=2, ensure_ascii=False)
        return ToolResult(
            success=True,
            output=body,
            evidence=f"web_metadata {url}: {len(meta)} fields",
        )

    # ---- search ----

    def web_search(query: str, count: int = 8) -> ToolResult:
        out = ctx.search.search(query, int(min(count, 20)))
        if not out.ok:
            return ToolResult(
                success=False,
                output="",
                evidence=(f"web_search backend={out.backend} query={query!r} failed"),
                error=out.error,
            )
        lines = [
            f"Search backend: {out.backend}",
            f"Query: {out.query}",
            f"Results: {out.count}"
            + (
                "" if out.complete else " (may be partial/limited; best-effort backend)"
            ),
            f"Note: {out.note}" if out.note else "",
            "",
        ]
        for i, hit in enumerate(out.hits, 1):
            lines.append(f"{i}. {hit.title}")
            lines.append(f"   {hit.url}")
            if hit.snippet:
                lines.append(f"   {hit.snippet}")
        body = "\n".join(lines)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"web_search backend={out.backend} query={out.query!r} "
                f"count={out.count} complete={out.complete}"
            ),
        )

    # ---- crawl ----

    def web_crawl(
        start_url: str,
        scope: str = "host",
        path_prefix: str = "",
        max_pages: int = 10,
        max_depth: int = 2,
    ) -> ToolResult:
        res = crawl(
            ctx.fetcher,
            ctx.extractor,
            start_url,
            scope=scope,
            path_prefix=path_prefix,
            max_pages=int(max_pages),
            max_depth=int(max_depth),
        )
        lines = [
            f"Start: {res.start_url}",
            f"Scope: {res.scope}" + (f" prefix {path_prefix}" if path_prefix else ""),
            f"Stopped: {res.stopped_reason}",
            "",
        ]
        for page in res.pages:
            status = "ok" if page.ok else "FAILED"
            lines.append(
                f"[{status}] HTTP {page.http_code} {page.url}"
                + (f"  <{page.title}>" if page.title else "")
            )
        if res.errors:
            lines.append("\nErrors:")
            lines.extend(f"  {e}" for e in res.errors[:20])
        body = "\n".join(lines)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"web_crawl {res.start_url}: {len(res.pages)} pages, "
                f"{res.total_links_found} links, stopped: {res.stopped_reason}"
            ),
        )

    # ---- render ----

    def web_render(
        url: str, screenshot: bool = False, wait_ms: int = 5000
    ) -> ToolResult:
        render = ctx.renderer.render(
            url, screenshot=bool(screenshot), wait_ms=int(wait_ms)
        )
        if not render.ok:
            return ToolResult(
                success=False,
                output="",
                evidence=f"web_render failed via {render.binary or 'no renderer'}",
                error=render.error,
            )
        dom, truncated = truncate_for_agent(render.dom, BODY_LIMIT)
        lines = [
            f"Renderer: {render.binary}",
            f"Rendered DOM ({len(render.dom)} chars, {render.elapsed_ms}ms):",
            dom if dom else "(empty rendered DOM)",
        ]
        for note in render.notes:
            lines.append(f"Note: {note}")
        if render.screenshot_path:
            w, h = render.screenshot_dims or (0, 0)
            lines.append("")
            lines.append(f"Screenshot captured: {render.screenshot_path} ({w}x{h})")
            lines.append(
                "Note: screenshot is evidence only; CORE has NOT visually "
                "interpreted these pixels (no vision model this milestone)."
            )
        body = "\n".join(lines)
        return ToolResult(
            success=True,
            output=body,
            evidence=(
                f"web_render via {render.binary}: {len(render.dom)} DOM chars, "
                f"{render.elapsed_ms}ms, screenshot={bool(render.screenshot_path)}"
            ),
            truncated=truncated or render.dom_truncated,
        )

    # ---- compare ----

    def web_compare(url_a: str, url_b: str) -> ToolResult:
        res = compare(ctx.fetcher, ctx.extractor, url_a, url_b)
        if not res.ok:
            return ToolResult(success=False, output="", error=res.error)
        lines = [
            f"A: {res.url_a}  <{res.title_a}>",
            f"B: {res.url_b}  <{res.title_b}>",
            f"Similarity: {res.similarity:.2%}  identical={res.identical}",
            f"Changed chars: A={res.changed_chars_a}, B={res.changed_chars_b}",
            "",
            res.diff_summary or "(no differences in extracted content)",
        ]
        return ToolResult(
            success=True,
            output="\n".join(lines),
            evidence=(
                f"web_compare {res.url_a} vs {res.url_b}: "
                f"similarity={res.similarity} identical={res.identical}"
            ),
            truncated=res.truncated,
        )

    tools = [
        _tool(
            "web_fetch",
            "Fetch a URL and return the raw HTML/text (bounded). Reports the "
            "transport used (curl vs fallback), HTTP status, and truncation.",
            web_fetch,
            {"url": _url_prop()},
            ["url"],
        ),
        _tool(
            "web_extract",
            "Fetch a URL and extract the main readable content as Markdown, "
            "stripping nav/scripts/styles noise. Use when read_fetch body is "
            "too noisy for the task.",
            web_extract,
            {"url": _url_prop()},
            ["url"],
        ),
        _tool(
            "web_inspect",
            "Understand a page: doctype, title, language, meta counts, "
            "headings, external scripts, stylesheets, link split.",
            web_inspect,
            {"url": _url_prop()},
            ["url"],
        ),
        _tool(
            "web_links",
            "List internal and external links found on a page with anchor "
            "text (bounded to 200 each).",
            web_links,
            {"url": _url_prop()},
            ["url"],
        ),
        _tool(
            "web_metadata",
            "Inspect a page's metadata: title, description, canonical, "
            "Open Graph, Twitter Card, language, charset.",
            web_metadata,
            {"url": _url_prop()},
            ["url"],
        ),
        _tool(
            "web_search",
            "Search the web. Reports the backend (zero-config DuckDuckGo by "
            "default; configured API when CORE_SEARCH_API_KEY is set) and "
            "whether results are partial/best-effort. Verify claims at the "
            "target site; a search hit is not verified evidence.",
            web_search,
            {
                "query": {"type": "string", "description": "Search query"},
                "count": {"type": "integer", "description": "Max results (<=20)"},
            },
            ["query"],
        ),
        _tool(
            "web_crawl",
            "Crawl related pages within an explicit scope. Defaults to same "
            "host only, bounded pages (<=30) and depth (<=5). Robts and "
            "rate limits are always respected.",
            web_crawl,
            {
                "start_url": _url_prop(),
                "scope": {
                    "type": "string",
                    "description": "'host' (same domain) or 'path' "
                    "(same domain + prefix).",
                },
                "path_prefix": {
                    "type": "string",
                    "description": "Required when scope='path'.",
                },
                "max_pages": {"type": "integer", "description": "<=30"},
                "max_depth": {"type": "integer", "description": "<=5"},
            },
            ["start_url"],
        ),
        _tool(
            "web_render",
            "Render a JS-heavy page in a headless Chrome-family browser and "
            "return the rendered DOM (and optionally a screenshot). Use only "
            "when fetch/extract is insufficient. Screenshots are captured as "
            "evidence, not visually interpreted.",
            web_render,
            {
                "url": _url_prop(),
                "screenshot": {
                    "type": "boolean",
                    "description": "Also capture a screenshot (evidence only).",
                },
                "wait_ms": {
                    "type": "integer",
                    "description": "Virtual time budget for JS (ms).",
                },
            },
            ["url"],
        ),
        _tool(
            "web_compare",
            "Fetch two pages (or two versions of one page) and compare their "
            "extracted readable content: similarity, changed sections, diff.",
            web_compare,
            {
                "url_a": _url_prop(),
                "url_b": _url_prop(),
            },
            ["url_a", "url_b"],
        ),
    ]
    for tool in tools:
        registry.register(tool)
