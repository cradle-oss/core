"""Tests for the Web subsystem: transport, extract, search, crawl, compare."""

import json
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from core.web.compare import compare
from core.web.context import WebContext
from core.web.crawl import CrawlResult, crawl
from core.web.extract import InspectReport, LinkBundle, WebExtractor
from core.web.search import (
    ApiKeyProvider,
    DuckDuckGoProvider,
    discover_search_provider,
    search_brief,
)
from core.web.tools import register_web_tools
from core.web.transport import FetchResult, WebFetcher, truncate_for_agent

# ---------------------------------------------------------------------------
# Fixtures: local HTTP server
# ---------------------------------------------------------------------------

# Content pages
INDEX_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>Test Page</title>
    <meta name="description" content="A test page for CORE web subsystem">
    <meta property="og:title" content="Test Page OG">
    <meta property="og:image" content="https://example.com/og.png">
    <link rel="canonical" href="https://localhost:{port}/">
</head>
<body>
  <nav><a href="/other">other</a></nav>
  <header>Site Header</header>
  <main>
    <h1>Welcome</h1>
    <p>This is the main content of the test page.</p>
    <ul>
      <li><a href="https://example.com/ext">External Example</a></li>
      <li><a href="/about">About</a></li>
    </ul>
    <table><tr><td>cell</td></tr></table>
  </main>
  <script>var tracker = true;</script>
  <style>body {{ color: red; }}</style>
  <footer>Footer content</footer>
</body>
</html>
"""

ABOUT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>About</title></head>
<body>
  <main>
    <h1>About Us</h1>
    <p>We are the about page with useful information.</p>
  </main>
</body>
</html>
"""

JS_APP_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>JS App</title></head>
<body>
  <div id="root"></div>
  <script>window.__APP__ = true;</script>
  <noscript>JavaScript required</noscript>
</body>
</html>
"""

ROBOTS_DISALLOW = "User-agent: *\nDisallow: /secret\n"


class _TestHandler(SimpleHTTPRequestHandler):
    """Route paths to canned content."""

    pages: dict[str, tuple[str, str]] = {}
    server_port: int = 0

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in self.pages:
            content, mime = self.pages[path]
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content.encode())
        elif path == "/robots.txt":
            content = ROBOTS_DISALLOW.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def log_message(self, *args):  # suppress noisy logs during tests
        pass


@pytest.fixture(scope="module")
def test_server():
    """Start a local HTTP server for the test module."""
    server = HTTPServer(("127.0.0.1", 0), _TestHandler)
    port = server.server_address[1]
    _TestHandler.server_port = port
    _TestHandler.pages = {
        "/": (INDEX_HTML.format(port=port), "text/html"),
        "/about": (ABOUT_HTML, "text/html"),
        "/js-app": (JS_APP_HTML, "text/html"),
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)  # let it start
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture()
def fetcher(test_server):
    """Non-rate-limited, robots-disabled fetcher for fast unit tests."""
    return WebFetcher(
        min_domain_delay=0.0,
        respect_robots=False,
        timeout=10,
    )


@pytest.fixture()
def fetcher_with_robots(test_server):
    """Fetcher that respects robots.txt."""
    return WebFetcher(
        min_domain_delay=0.0,
        respect_robots=True,
        timeout=10,
    )


@pytest.fixture()
def extractor():
    return WebExtractor()


# ---------------------------------------------------------------------------
# transport.py tests
# ---------------------------------------------------------------------------


class TestWebFetcher:
    def test_fetch_index(self, fetcher, test_server):
        result = fetcher.fetch(f"{test_server}/")
        assert result.ok
        assert result.http_code == 200
        assert "Welcome" in result.body
        assert result.transport in ("curl", "urllib (curl not installed)")

    def test_fetch_404(self, fetcher, test_server):
        result = fetcher.fetch(f"{test_server}/nonexistent")
        assert not result.ok
        assert result.http_code == 404

    def test_fetch_invalid_url(self, fetcher):
        result = fetcher.fetch("not-a-url")
        assert not result.ok
        assert "invalid URL" in result.error

    def test_fetch_robots_blocked(self, fetcher_with_robots, test_server):
        result = fetcher_with_robots.fetch(f"{test_server}/secret")
        assert not result.ok
        assert "robots.txt" in result.error

    def test_fetch_robots_allowed(self, fetcher_with_robots, test_server):
        result = fetcher_with_robots.fetch(f"{test_server}/")
        assert result.ok

    def test_rate_limit_enforced(self, test_server):
        slow = WebFetcher(min_domain_delay=2.0, respect_robots=False, timeout=10)
        t0 = time.monotonic()
        slow.fetch(f"{test_server}/")
        slow.fetch(f"{test_server}/about")
        elapsed = time.monotonic() - t0
        assert elapsed >= 1.8  # should have waited ~2s

    def test_headers_param_forwarded(self, fetcher, test_server):
        result = fetcher.fetch(f"{test_server}/", headers={"X-Custom": "test-value"})
        assert result.ok

    def test_urllib_fallback(self, test_server):
        """Force urllib path by patching _curl to None."""
        fetcher = WebFetcher(min_domain_delay=0.0, respect_robots=False, timeout=10)
        fetcher._curl = None
        result = fetcher.fetch(f"{test_server}/")
        assert result.ok
        assert result.transport == "urllib (curl not installed)"

    def test_store_limit_truncation(self, fetcher, test_server):
        """Very large page gets truncated at STORE_LIMIT."""
        big = "x" * 500_000
        _TestHandler.pages["/big"] = (big, "text/plain")
        try:
            result = fetcher.fetch(f"{test_server}/big")
            assert result.ok
            assert result.truncated
            assert len(result.body) <= 400_001  # STORE_LIMIT + margin
        finally:
            del _TestHandler.pages["/big"]


class TestTruncateForAgent:
    def test_short_text_unchanged(self):
        text, truncated = truncate_for_agent("hello", 100)
        assert text == "hello"
        assert not truncated

    def test_long_text_truncated(self):
        text, truncated = truncate_for_agent("x" * 200, 100)
        assert len(text) < 200
        assert "[truncated:" in text
        assert truncated


# ---------------------------------------------------------------------------
# extract.py tests
# ---------------------------------------------------------------------------


class TestWebExtractor:
    def test_extract_main_content(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/").body
        doc = extractor.extract(body, f"{test_server}/", plain=True)
        assert doc["title"] == "Test Page"
        assert "Welcome" in doc["markdown"]
        assert "Site Header" not in doc["markdown"]
        assert "tracker" not in doc["markdown"]  # script stripped

    def test_extract_no_script(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/js-app").body
        doc = extractor.extract(body, f"{test_server}/js-app", plain=True)
        assert "APP__" not in doc["markdown"]
        assert "JavaScript required" not in doc["markdown"]

    def test_extract_plain_fields(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/").body
        doc = extractor.extract(body, f"{test_server}/", plain=True)
        assert {"text", "markdown", "title", "truncated"} == set(doc)

    def test_inspect_returns_report(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/").body
        report = extractor.inspect(body, f"{test_server}/")
        assert isinstance(report, InspectReport)
        assert report.title == "Test Page"
        assert report.language == "en"
        assert report.doctype == "html"
        assert report.counts["links"] >= 2
        assert report.counts["scripts"] >= 1

    def test_links_split(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/").body
        bundle = extractor.links(body, f"{test_server}/")
        assert isinstance(bundle, LinkBundle)
        assert bundle.counts["internal"] >= 1
        assert bundle.counts["external"] >= 1
        ext_urls = [e["url"] for e in bundle.external]
        assert "https://example.com/ext" in ext_urls

    def test_metadata_og(self, fetcher, extractor, test_server):
        body = fetcher.fetch(f"{test_server}/").body
        meta = extractor.metadata(body, f"{test_server}/")
        assert meta["title"] == "Test Page"
        assert meta["og_title"] == "Test Page OG"
        assert "canonical" in meta
        assert meta["canonical"].startswith("https://localhost:")
        assert meta["language"] == "en"


# ---------------------------------------------------------------------------
# search.py tests
# ---------------------------------------------------------------------------

SAMPLE_DDG_HTML = """\
<html><body>
<div class="results">
<div class="result result--web">
  <a class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc">
    Example Page Title
  </a>
  <a class="result__snippet">This is the snippet from Example.</a>
</div>
<div class="result result--web">
  <a class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fother.com%2Fthing&amp;rut=def">
    Other Thing
  </a>
  <a class="result__snippet">Other snippet here.</a>
</div>
</div>
</body></html>
"""


class FakeSearchFetcher:
    """Bypasses transport; returns canned HTML for DuckDuckGo searches."""

    def __init__(self, html: str):
        self._html = html

    def fetch(self, url: str, headers=None):  # noqa: ARG002
        return FetchResult(
            url=url,
            final_url=url,
            http_code=200,
            body=self._html,
            truncated=False,
            transport="fake",
            headers={},
        )


class TestDuckDuckGoProvider:
    def test_parse_results(self):
        provider = DuckDuckGoProvider(fetcher=FakeSearchFetcher(SAMPLE_DDG_HTML))
        result = provider.search("test query")
        assert result.ok
        assert result.backend == "duckduckgo-html"
        assert result.count == 2
        assert not result.complete
        assert "best-effort" in result.note.lower() or "not a stable API" in result.note
        assert result.hits[0].title == "Example Page Title"
        assert result.hits[0].url == "https://example.com/page"
        assert "snippet" in result.hits[0].snippet.lower()

    def test_empty_results_page(self):
        provider = DuckDuckGoProvider(
            fetcher=FakeSearchFetcher("<html><body></body></html>")
        )
        result = provider.search("nothing")
        assert result.ok
        assert result.count == 0


class TestApiKeyProvider:
    def test_selects_when_env_set(self, monkeypatch):
        monkeypatch.setenv("CORE_SEARCH_API_KEY", "sk-test123")
        provider = discover_search_provider()
        assert isinstance(provider, ApiKeyProvider)

    def test_search_with_canned_response(self, monkeypatch):
        monkeypatch.setenv("CORE_SEARCH_API_KEY", "sk-test123")
        monkeypatch.delenv("CORE_SEARCH_ENDPOINT", raising=False)

        canned = {
            "web": {
                "results": [
                    {
                        "title": "Canned Result",
                        "url": "https://canned.com",
                        "description": "desc",
                    }
                ]
            }
        }

        def fake_fetch(self, url, headers=None):  # noqa: ARG001
            return FetchResult(
                url=url,
                final_url=url,
                http_code=200,
                body=json.dumps(canned),
                truncated=False,
                transport="fake",
                headers={},
            )

        monkeypatch.setattr(WebFetcher, "fetch", fake_fetch)
        provider = ApiKeyProvider()
        result = provider.search("canned")
        assert result.ok
        assert "api" in result.backend
        assert result.count == 1
        assert result.hits[0].title == "Canned Result"

    def test_no_key_returns_ddg(self, monkeypatch):
        monkeypatch.delenv("CORE_SEARCH_API_KEY", raising=False)
        provider = discover_search_provider()
        assert isinstance(provider, DuckDuckGoProvider)


class TestSearchBrief:
    def test_brief_with_ddg(self):
        brief = search_brief(DuckDuckGoProvider())
        assert "duckduckgo-html" in brief
        assert "best-effort" in brief.lower() or "best effort" in brief.lower()

    def test_brief_with_none(self):
        brief = search_brief(None)
        assert "duckduckgo" in brief.lower()


# ---------------------------------------------------------------------------
# crawl.py tests
# ---------------------------------------------------------------------------


class TestCrawl:
    def test_crawl_same_host(self, fetcher, extractor, test_server):
        result = crawl(
            fetcher,
            extractor,
            f"{test_server}/",
            scope="host",
            max_pages=5,
            max_depth=1,
        )
        assert isinstance(result, CrawlResult)
        assert result.stopped_reason in (
            "max_pages",
            "max_depth",
            "all reachable pages visited within scope",
        )
        assert len(result.pages) >= 1

    def test_crawl_path_scope(self, fetcher, extractor, test_server):
        result = crawl(
            fetcher,
            extractor,
            f"{test_server}/",
            scope="path",
            path_prefix="/about",
            max_pages=3,
            max_depth=2,
        )
        urls = [p.url for p in result.pages]
        # start URL is always visited; beyond it only /about is in prefix scope
        assert urls[0] == f"{test_server}/"
        assert f"{test_server}/about" in urls
        assert f"{test_server}/other" not in urls

    def test_crawl_respects_max_pages(self, fetcher, extractor, test_server):
        result = crawl(fetcher, extractor, f"{test_server}/", max_pages=1, max_depth=1)
        assert len(result.pages) <= 2  # start + maybe 1 linked


# ---------------------------------------------------------------------------
# compare.py tests
# ---------------------------------------------------------------------------


class TestCompare:
    def test_identical_pages(self, fetcher, extractor, test_server):
        res = compare(fetcher, extractor, f"{test_server}/", f"{test_server}/")
        assert res.identical
        assert res.similarity > 0.99

    def test_different_pages(self, fetcher, extractor, test_server):
        res = compare(fetcher, extractor, f"{test_server}/", f"{test_server}/about")
        assert not res.identical
        assert res.similarity < 1.0
        assert res.changed_chars_a > 0 or res.changed_chars_b > 0

    def test_fetch_failure(self, fetcher, extractor):
        res = compare(
            fetcher, extractor, "http://127.0.0.1:1/bad", "http://127.0.0.1:1/also"
        )
        assert not res.ok


# ---------------------------------------------------------------------------
# context.py tests
# ---------------------------------------------------------------------------


class TestWebContext:
    def test_context_briefing(self, test_server):
        ctx = WebContext()
        brief = ctx.briefing()
        assert "fetch:" in brief
        assert "renderer:" in brief
        assert "search:" in brief

    def test_context_uses_shared_fetcher(self):
        f = WebFetcher(min_domain_delay=0.5)
        ctx = WebContext(fetcher=f)
        assert ctx.fetcher is f
        assert ctx.fetcher.min_domain_delay == 0.5


# ---------------------------------------------------------------------------
# tools.py tests
# ---------------------------------------------------------------------------


class TestRegisterWebTools:
    def test_register_creates_nine_tools(self):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext()
        register_web_tools(reg, ctx)
        names = {t.name for t in reg._tools.values()}
        expected = {
            "web_fetch",
            "web_extract",
            "web_inspect",
            "web_links",
            "web_metadata",
            "web_search",
            "web_crawl",
            "web_render",
            "web_compare",
        }
        assert expected == names

    def test_web_fetch_tool(self, test_server):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext()
        register_web_tools(reg, ctx)
        result = reg.execute("web_fetch", {"url": f"{test_server}/"})
        assert result.success
        assert "Welcome" in result.output
        assert "web_fetch" in result.evidence

    def test_web_extract_tool(self, test_server):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext()
        register_web_tools(reg, ctx)
        result = reg.execute("web_extract", {"url": f"{test_server}/"})
        assert result.success
        assert "Welcome" in result.output

    def test_web_search_tool(self):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext(
            search=DuckDuckGoProvider(fetcher=FakeSearchFetcher(SAMPLE_DDG_HTML))
        )
        register_web_tools(reg, ctx)
        result = reg.execute("web_search", {"query": "test", "count": 5})
        assert result.success
        assert "duckduckgo-html" in result.evidence

    def test_web_compare_tool(self, test_server):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext()
        register_web_tools(reg, ctx)
        result = reg.execute(
            "web_compare",
            {"url_a": f"{test_server}/", "url_b": f"{test_server}/about"},
        )
        assert result.success
        assert "similarity=" in result.evidence

    def test_web_render_tool_graceful(self):
        from core.tools.registry import ToolRegistry

        reg = ToolRegistry()
        ctx = WebContext()
        register_web_tools(reg, ctx)
        result = reg.execute("web_render", {"url": "http://example.com"})
        # may succeed (brave available) or report graceful failure
        assert "web_render" in result.evidence or not result.success


# ---------------------------------------------------------------------------
# renderer.py tests
# ---------------------------------------------------------------------------

MULTI_TARGET_ERR = "Multiple targets are not supported in headless mode."


def _fake_completed(returncode, stdout="", stderr=""):
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _png(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00" * 8
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
    )


class TestWebRenderer:
    def test_graceful_when_no_binary(self, monkeypatch):
        from core.web.renderer import WebRenderer

        renderer = WebRenderer()
        monkeypatch.setattr(renderer, "_probe", lambda: None)
        assert not renderer.available()
        result = renderer.render("http://example.com")
        assert not result.ok
        assert "no Chrome-family browser" in result.error
        assert "unavailable" in renderer.binary_info()

    def test_multiple_targets_degrades_to_dump_dom(self, monkeypatch):
        from core.web import renderer as renderer_mod
        from core.web.renderer import WebRenderer

        calls = []

        def fake_run(cmd, capture_output=True, text=True, timeout=60):
            calls.append(cmd)
            if "--virtual-time-budget" in cmd:
                return _fake_completed(13, "", MULTI_TARGET_ERR)
            return _fake_completed(0, "<html><body>rendered</body></html>", "")

        monkeypatch.setattr(renderer_mod.subprocess, "run", fake_run)
        renderer = WebRenderer()
        monkeypatch.setattr(renderer, "_probe", lambda: "/fake/chrome")
        result = renderer.render("http://example.com", wait_ms=2000)
        assert result.ok
        assert "rendered" in result.dom
        assert result.notes
        assert any("time budget" in n for n in result.notes)
        assert len(calls) == 2  # first with budget, retry without

    def test_screenshot_unsupported_degrades(self, monkeypatch):
        from core.web import renderer as renderer_mod
        from core.web.renderer import WebRenderer

        def fake_run(cmd, capture_output=True, text=True, timeout=60):
            if "--screenshot" in cmd or "--virtual-time-budget" in cmd:
                return _fake_completed(13, "", MULTI_TARGET_ERR)
            return _fake_completed(0, "<html>dom</html>", "")

        monkeypatch.setattr(renderer_mod.subprocess, "run", fake_run)
        renderer = WebRenderer()
        monkeypatch.setattr(renderer, "_probe", lambda: "/fake/chrome")
        result = renderer.render("http://example.com", screenshot=True)
        assert result.ok
        assert result.dom
        assert result.screenshot_path is None
        assert any("screenshot" in n.lower() for n in result.notes)

    def test_screenshot_dims_parsed(self, monkeypatch):
        from core.web import renderer as renderer_mod
        from core.web.renderer import WebRenderer

        def fake_run(cmd, capture_output=True, text=True, timeout=60):
            if "--screenshot" in cmd:
                idx = cmd.index("--screenshot") + 1
                with open(cmd[idx], "wb") as f:
                    f.write(_png(1280, 720))
            elif "--virtual-time-budget" in cmd:
                return _fake_completed(13, "", MULTI_TARGET_ERR)
            return _fake_completed(0, "<html>dom</html>", "")

        monkeypatch.setattr(renderer_mod.subprocess, "run", fake_run)
        renderer = WebRenderer()
        monkeypatch.setattr(renderer, "_probe", lambda: "/fake/chrome")
        result = renderer.render("http://example.com", screenshot=True)
        assert result.screenshot_dims == (1280, 720)
        assert result.screenshot_path
        import os

        os.unlink(result.screenshot_path)

    def test_real_headless_dom(self):
        from core.web.renderer import WebRenderer

        renderer = WebRenderer()
        if not renderer.available():
            pytest.skip("no Chrome-family browser installed")
        result = renderer.render("https://example.com/", wait_ms=2000)
        assert result.ok
        assert "example" in result.dom.lower()
