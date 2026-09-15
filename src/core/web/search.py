"""Web search providers: zero-config DuckDuckGo + optional API backend.

DuckDuckGo HTML is a best-effort, potentially fragile keyless backend. It is
NOT presented as a stable API. When CORE_SEARCH_API_KEY is configured, a
structured JSON search backend is used instead (Brave-shaped by default).

Every result identifies the backend that actually ran together with the
query, result count, whether the response was complete or limited, and any
failure reason. Never build around one provider's response format.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from core.web.transport import WebFetcher

DEFAULT_API_URL = "https://api.search.brave.com/res/v1/web/search"


@dataclass
class SearchHit:
    title: str = ""
    url: str = ""
    snippet: str = ""


@dataclass
class SearchOutput:
    backend: str
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    count: int = 0
    complete: bool = False
    error: str | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None


class WebSearchProvider(ABC):
    """Interface every search provider implements."""

    name: str = ""

    @abstractmethod
    def search(self, query: str, count: int = 8) -> SearchOutput: ...


class DuckDuckGoProvider(WebSearchProvider):
    """Keyless DuckDuckGo HTML backend (best-effort, zero-config)."""

    name = "duckduckgo-html"

    def __init__(self, fetcher: WebFetcher | None = None):
        self.fetcher = fetcher or WebFetcher()

    def search(self, query: str, count: int = 8) -> SearchOutput:
        params = urllib.parse.urlencode({"q": query})
        url = f"https://html.duckduckgo.com/html/?{params}"
        try:
            result = self.fetcher.fetch(url)
        except Exception as exc:  # noqa: BLE001 - bounded provider boundary
            return SearchOutput(
                backend=self.name,
                query=query,
                error=f"fetch failed: {exc}",
                note="DuckDuckGo HTML is best-effort and may be blocked or "
                "rate-limited at any time; only partial/unverified results.",
            )
        if result.http_code != 200 or result.error:
            return SearchOutput(
                backend=self.name,
                query=query,
                error=result.error or f"HTTP {result.http_code}",
                note="DuckDuckGo HTML is best-effort and may be blocked or "
                "rate-limited at any time; only partial/unverified results.",
            )

        soup = BeautifulSoup(result.body, "html.parser")
        hits: list[SearchHit] = []
        for a in soup.find_all("a", class_="result__a")[: max(count, 1)]:
            title = a.get_text(" ", strip=True)
            href = a.get("href") or ""
            url = self._clean_redirect(href) if href else ""
            snippet = ""
            container = a.find_parent(["div", "li"])
            if container is not None:
                snap = container.find(class_="result__snippet")
                if snap is not None:
                    snippet = snap.get_text(" ", strip=True)[:600]
            if title:
                hits.append(SearchHit(title=title[:300], url=url, snippet=snippet))

        return SearchOutput(
            backend=self.name,
            query=query,
            hits=hits,
            count=len(hits),
            complete=False,
            error=None,
            note="DuckDuckGo HTML backend: best-effort, results may be "
            "partial or blocked; verify claims against the target site.",
        )

    @staticmethod
    def _clean_redirect(href: str) -> str:
        """Decode DuckDuckGo's //duckduckgo.com/l/?uddg= redirect wrapper."""
        if "duckduckgo.com/l/" in href:
            parsed = urllib.parse.urlparse(href)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params and params["uddg"]:
                return params["uddg"][0]
        if href.startswith("//"):
            href = "https:" + href
        return href


class ApiKeyProvider(WebSearchProvider):
    """Structured JSON search backend behind CORE_SEARCH_API_KEY.

    Default endpoint shape is Brave Search. Override the endpoint with
    CORE_SEARCH_ENDPOINT to target any compatible backend; response
    normalization accepts `web.results`, `results`, `organic`, or `data`.
    """

    name = "configured-api"

    def __init__(self, fetcher: WebFetcher | None = None):
        self.fetcher = fetcher or WebFetcher()
        self.api_key = os.environ.get("CORE_SEARCH_API_KEY", "")
        self.endpoint = os.environ.get("CORE_SEARCH_ENDPOINT", DEFAULT_API_URL)

    def search(self, query: str, count: int = 8) -> SearchOutput:
        if not self.api_key:
            return SearchOutput(
                backend=self.name,
                query=query,
                error="CORE_SEARCH_API_KEY is not set",
                note="Configure CORE_SEARCH_API_KEY (and optionally "
                "CORE_SEARCH_ENDPOINT) to use the API search backend.",
            )
        url = f"{self.endpoint}?{urllib.parse.urlencode({'q': query, 'count': count})}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            result = self.fetcher.fetch(url, headers=headers)
        except Exception as exc:  # noqa: BLE001 - bounded provider boundary
            return SearchOutput(
                backend=self.name,
                query=query,
                error=f"fetch failed: {exc}",
            )
        if result.http_code != 200:
            return SearchOutput(
                backend=self.name,
                query=query,
                error=result.error or f"HTTP {result.http_code}",
                note="Search API rejected the request (auth or quota issue).",
            )
        try:
            data = json.loads(result.body)
        except json.JSONDecodeError:
            return SearchOutput(
                backend=self.name,
                query=query,
                error="search API returned invalid JSON",
            )

        rows = (
            data.get("web", {}).get("results")
            or data.get("results")
            or data.get("organic")
            or data.get("data")
            or []
        )
        hits: list[SearchHit] = []
        for row in rows[: max(count, 1)]:
            if not isinstance(row, dict):
                continue
            title = row.get("title") or ""
            url = row.get("url") or row.get("link") or ""
            snippet = row.get("description") or row.get("snippet") or ""
            if title:
                hits.append(
                    SearchHit(
                        title=str(title)[:300],
                        url=str(url),
                        snippet=str(snippet)[:600],
                    )
                )

        host = urllib.parse.urlsplit(self.endpoint).netloc or self.endpoint
        return SearchOutput(
            backend=f"{self.name} ({host})",
            query=query,
            hits=hits,
            count=len(hits),
            complete=True,
            error=None,
            note=f"results as returned by {host}; verify against the source.",
        )


def discover_search_provider() -> WebSearchProvider:
    """Pick the active provider based on configuration."""
    api_key = os.environ.get("CORE_SEARCH_API_KEY")
    if api_key:
        return ApiKeyProvider()
    return DuckDuckGoProvider()


def search_brief(provider: WebSearchProvider | None = None) -> str:
    """One-line briefing for the agent system context."""
    provider = provider or discover_search_provider()
    if isinstance(provider, ApiKeyProvider):
        host = urllib.parse.urlsplit(provider.endpoint).netloc or provider.endpoint
        return f"web search: {provider.name} ({host}) with CORE_SEARCH_API_KEY"
    return "web search: duckduckgo-html (zero-config, best-effort, may be limited)"
