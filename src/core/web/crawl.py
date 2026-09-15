"""Bounded web crawl: BFS within an explicit scope, respecting robots + rate.

Do not attempt to bypass access controls or produce uncontrolled recursive
crawls. Every crawl has an explicit scope, max pages, and max depth.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from core.web.extract import WebExtractor
from core.web.transport import WebFetcher

DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_DEPTH = 2


@dataclass
class CrawlPage:
    url: str
    http_code: int = 0
    title: str | None = None
    ok: bool = False
    error: str | None = None


@dataclass
class CrawlResult:
    start_url: str
    scope: str
    path_prefix: str
    pages: list[CrawlPage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stopped_reason: str = ""
    total_links_found: int = 0
    max_pages: int = DEFAULT_MAX_PAGES
    max_depth: int = DEFAULT_MAX_DEPTH


def _scope_match(
    url: str,
    scope: str,
    start_host: str,
    path_prefix: str,
) -> tuple[bool, str | None]:
    parts = urllib.parse.urlsplit(url)
    host = (parts.netloc or "").lower()
    if not parts.scheme or parts.scheme not in {"http", "https"}:
        return False, "non-http scheme"
    if scope == "host":
        if host != start_host:
            return False, "outside host scope"
    elif scope == "path":
        if host != start_host:
            return False, "outside host scope"
        if not path_prefix or not parts.path.startswith(path_prefix):
            return False, "outside path prefix"
    else:
        return False, f"unknown scope {scope!r}"
    if any(
        s in parts.path
        for s in ["/api/", "/.well-known/", "/cgi-bin/", ".pdf", ".zip", ".gz"]
    ):
        return False, "auto-excluded resource"
    return True, None


def crawl(
    fetcher: WebFetcher,
    extractor: WebExtractor,
    start_url: str,
    *,
    scope: str = "host",
    path_prefix: str = "",
    max_pages: int = DEFAULT_MAX_PAGES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> CrawlResult:
    """Crawl the web starting at `start_url`, respecting robots + rate limits."""
    max_pages = min(max_pages, 30)
    max_depth = min(max_depth, 5)
    start_parts = urllib.parse.urlsplit(start_url)
    start_host = (start_parts.netloc or "").lower()
    if start_parts.scheme not in {"http", "https"}:
        return CrawlResult(
            start_url=start_url,
            scope=scope,
            path_prefix=path_prefix,
            stopped_reason=f"invalid start URL scheme: {start_parts.scheme!r}",
        )
    result = CrawlResult(
        start_url=start_url,
        scope=scope,
        path_prefix=path_prefix,
        max_pages=max_pages,
        max_depth=max_depth,
    )
    from collections import deque

    queue: deque[tuple[str, int]] = deque()
    queue.append((start_url, 0))
    visited: set[str] = set()

    while queue and len(result.pages) < max_pages:
        url, depth = queue.popleft()
        norm = re.sub(r"#.*", "", url).rstrip("/")
        if norm in visited:
            continue
        visited.add(norm)
        fetch = fetcher.fetch(url)
        page = CrawlPage(
            url=url,
            http_code=fetch.http_code,
            ok=fetch.ok,
            error=fetch.error,
        )
        if fetch.ok and fetch.body:
            doc = extractor.extract(fetch.body, fetch.final_url, plain=True)
            page.title = doc.get("title")
        result.pages.append(page)
        if not fetch.ok:
            result.errors.append(f"{url}: {fetch.error or f'HTTP {fetch.http_code}'}")
            continue
        if depth < max_depth:
            links = extractor.links(fetch.body, fetch.final_url)
            result.total_links_found += (
                links.counts["internal"] + links.counts["external"]
            )
            for entry in links.internal:
                child = entry["url"]
                if not child:
                    continue
                child_norm = re.sub(r"#.*", "", child).rstrip("/")
                if child_norm in visited:
                    continue
                in_scope, _ = _scope_match(child, scope, start_host, path_prefix)
                if in_scope:
                    queue.append((child, depth + 1))

    if len(result.pages) >= max_pages:
        result.stopped_reason = f"reached max_pages ({max_pages})"
    elif not queue:
        result.stopped_reason = "all reachable pages visited within scope"
    else:
        result.stopped_reason = "depth limit reached"
    return result
