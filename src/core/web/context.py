"""WebContext: shared state for the Web toolset.

One context per AgentLoop. Bundles the transport, extractor, renderer, and
search provider so tools read consistent availability/configuration, and
exposes a briefing for the agent system context.
"""

from __future__ import annotations

from core.web.extract import WebExtractor
from core.web.renderer import WebRenderer
from core.web.search import discover_search_provider, search_brief
from core.web.transport import WebFetcher


class WebContext:
    def __init__(
        self,
        fetcher: WebFetcher | None = None,
        extractor: WebExtractor | None = None,
        renderer: WebRenderer | None = None,
        search=None,
    ) -> None:
        self.fetcher = fetcher or WebFetcher()
        self.extractor = extractor or WebExtractor()
        self.renderer = renderer or WebRenderer()
        self.search = search if search is not None else discover_search_provider()

    def briefing(self) -> str:
        fetch_backend = (
            f"fetch: curl ({self.fetcher._curl})"
            if self.fetcher._curl
            else "fetch: urllib fallback (curl not installed)"
        )
        rate = (
            f", {self.fetcher.min_domain_delay}s min delay per domain, "
            f"robots.txt respected"
        )
        lines = [
            fetch_backend + rate,
            self.renderer.binary_info(),
            search_brief(self.search),
        ]
        return "\n".join(lines)
