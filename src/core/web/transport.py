"""Web transport: fetch a URL, respecting robots.txt and rate limits.

Native CLI first: prefer `curl` when present; urllib is the honest stdlib
fallback. Both report exactly which transport ran and what was observed.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass

USER_AGENT = "Mozilla/5.0 (compatible; CORE-CLI terminal research agent)"
DEFAULT_TIMEOUT = 30
MAX_BYTES = 2_000_000  # guard for curl --max-filesize
STORE_LIMIT = 400_000  # chars retained in a fetch result
MIN_DOMAIN_DELAY = 1.0  # seconds between requests to the same domain


@dataclass
class FetchResult:
    url: str
    final_url: str
    http_code: int
    body: str
    truncated: bool
    transport: str
    headers: dict[str, str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.http_code < 400


@dataclass
class _HostGate:
    last_hit: float = 0.0


class WebFetcher:
    """Bounded, polite HTTP fetcher with robots + per-domain rate limiting."""

    def __init__(
        self,
        user_agent: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        min_domain_delay: float = MIN_DOMAIN_DELAY,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent or USER_AGENT
        self.timeout = timeout
        self.min_domain_delay = min_domain_delay
        self.respect_robots = respect_robots
        self._curl = shutil.which("curl")
        self._gates: dict[str, _HostGate] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    # ---- helpers ----

    @staticmethod
    def _host(url: str) -> str | None:
        try:
            return (urllib.parse.urlsplit(url).netloc or None).lower()
        except ValueError:
            return None

    def _wait_for_slot(self, host: str) -> None:
        """Enforce the minimum delay between requests to the same domain."""
        gate = self._gates.setdefault(host, _HostGate())
        wait = self.min_domain_delay - (time.monotonic() - gate.last_hit)
        if wait > 0:
            time.sleep(wait)
        gate.last_hit = time.monotonic()

    def _robots_allowed(self, url: str) -> tuple[bool, str]:
        """Check robots.txt for the URL (allow on any robots fetch failure)."""
        parts = urllib.parse.urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"}:
            return False, "only http/https URLs are allowed"
        origin = f"{scheme}://{parts.netloc}"
        rp = self._robots.get(origin)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{origin}/robots.txt"
            req = urllib.request.Request(
                robots_url, headers={"User-Agent": self.user_agent}
            )
            try:
                with urllib.request.urlopen(req, timeout=min(10, self.timeout)) as res:
                    rp.parse(res.read().decode("utf-8", "replace").splitlines())
            except (OSError, ValueError, urllib.error.URLError):
                # robots.txt unavailable → treat as unrestricted, as is standard.
                rp = None
            self._robots[origin] = rp  # type: ignore[assignment]
        if rp is None:
            return True, ""
        allowed = rp.can_fetch("*", url)
        return allowed, "" if allowed else (f"disallowed by {robots_url}")

    # ---- transports ----

    def _curl_fetch(self, url: str, headers: dict[str, str]) -> FetchResult:
        cmd = [
            "curl",
            "-sSL",
            "--fail",
            "--max-time",
            str(self.timeout),
            "--max-filesize",
            str(MAX_BYTES),
            "-A",
            self.user_agent,
        ]
        for key, value in headers.items():
            cmd += ["-H", f"{key}: {value}"]
        cmd += [
            "-w",
            "\nCORE_HTTP:%{http_code}",
            "-o",
            "-",
            url,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout + 15
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return FetchResult(
                url=url,
                final_url=url,
                http_code=0,
                body="",
                truncated=False,
                transport="curl",
                headers={},
                error=f"curl invocation failed: {exc}",
            )

        code = 0
        body = result.stdout
        # strip the trailing status marker
        if "CORE_HTTP:" in body:
            body, marker = body.rsplit("CORE_HTTP:", 1)
            try:
                code = int(marker.strip().splitlines()[0])
            except ValueError:
                code = 0
            body = body.rstrip("\n")
        truncated = result.returncode == 63  # curl: file too large (--max-filesize)
        if result.returncode != 0 and not truncated:
            return FetchResult(
                url=url,
                final_url=url,
                http_code=code,
                body="",
                truncated=False,
                transport="curl",
                headers={},
                error=(
                    f"curl exit {result.returncode}: "
                    f"{result.stderr.strip() or 'no error detail'}"
                ),
            )
        if len(body) > STORE_LIMIT:
            body = body[:STORE_LIMIT]
            truncated = True
        return FetchResult(
            url=url,
            final_url=url,
            http_code=code,
            body=body,
            truncated=truncated,
            transport="curl",
            headers={},
        )

    def _urllib_fetch(self, url: str, headers: dict[str, str]) -> FetchResult:
        merged = {"User-Agent": self.user_agent, **headers}
        req = urllib.request.Request(url, headers=merged)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                raw = res.read(MAX_BYTES + 1)
                body = raw[:STORE_LIMIT].decode("utf-8", "replace")
                truncated = len(body) > STORE_LIMIT or len(raw) > MAX_BYTES
                headers = {
                    k: v
                    for k, v in res.headers.items()
                    if k.lower() in {"content-type", "content-length", "server"}
                }
                return FetchResult(
                    url=url,
                    final_url=res.geturl(),
                    http_code=res.status,
                    body=body,
                    truncated=truncated,
                    transport="urllib (curl not installed)",
                    headers=headers,
                )
        except urllib.error.HTTPError as exc:
            return FetchResult(
                url=url,
                final_url=exc.geturl() or url,
                http_code=exc.code,
                body="",
                truncated=False,
                transport="urllib (curl not installed)",
                headers={},
                error=f"HTTP {exc.code}: {exc.reason}",
            )
        except (urllib.error.URLError, OSError) as exc:
            return FetchResult(
                url=url,
                final_url=url,
                http_code=0,
                body="",
                truncated=False,
                transport="urllib (curl not installed)",
                headers={},
                error=f"request failed: {exc}",
            )

    # ---- public API ----

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        """Fetch a URL, returning bounded body + honest provenance."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
            return FetchResult(
                url=url,
                final_url=url,
                http_code=0,
                body="",
                truncated=False,
                transport="none",
                headers={},
                error=f"invalid URL (must be absolute http/https): {url!r}",
            )
        host = self._host(url)
        if self.respect_robots:
            allowed, reason = self._robots_allowed(url)
            if not allowed:
                return FetchResult(
                    url=url,
                    final_url=url,
                    http_code=0,
                    body="",
                    truncated=False,
                    transport="none",
                    headers={},
                    error=f"robots.txt: {reason}",
                )
        if host:
            self._wait_for_slot(host)
        headers = headers or {}
        if self._curl:
            return self._curl_fetch(url, headers)
        return self._urllib_fetch(url, headers)


def truncate_for_agent(text: str, limit: int = 60000) -> tuple[str, bool]:
    """Bound web-derived text for agent context; returns (text, truncated)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [truncated: {len(text) - limit} more chars]", True
