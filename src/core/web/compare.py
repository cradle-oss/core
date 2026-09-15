"""Page/version comparison: fetch + extract → difflib summary."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from core.web.extract import WebExtractor
from core.web.transport import WebFetcher, truncate_for_agent

COMPARE_LIMIT = 20_000


@dataclass
class CompareResult:
    url_a: str
    url_b: str
    title_a: str | None
    title_b: str | None
    identical: bool
    similarity: float
    changed_chars_a: int
    changed_chars_b: int
    diff_summary: str
    truncated: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def compare(
    fetcher: WebFetcher,
    extractor: WebExtractor,
    url_a: str,
    url_b: str,
) -> CompareResult:
    """Fetch two URLs and compare their extracted readable content."""
    fa = fetcher.fetch(url_a)
    fb = fetcher.fetch(url_b)
    if not fa.ok:
        return CompareResult(
            url_a=url_a,
            url_b=url_b,
            title_a=None,
            title_b=None,
            identical=False,
            similarity=0.0,
            changed_chars_a=0,
            changed_chars_b=0,
            diff_summary="",
            truncated=False,
            error=f"failed to fetch {url_a}: {fa.error or f'HTTP {fa.http_code}'}",
        )
    if not fb.ok:
        return CompareResult(
            url_a=url_a,
            url_b=url_b,
            title_a=None,
            title_b=None,
            identical=False,
            similarity=0.0,
            changed_chars_a=0,
            changed_chars_b=0,
            diff_summary="",
            truncated=False,
            error=f"failed to fetch {url_b}: {fb.error or f'HTTP {fb.http_code}'}",
        )
    doc_a = extractor.extract(fa.body, fa.final_url, plain=True)
    doc_b = extractor.extract(fb.body, fb.final_url, plain=True)
    lines_a = doc_a["text"].splitlines()
    lines_b = doc_b["text"].splitlines()
    matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)
    similarity = round(matcher.ratio(), 4)
    identical = similarity == 1.0

    diff_lines = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=url_a[:200],
            tofile=url_b[:200],
            lineterm="",
            n=3,
        )
    )
    raw_diff = "\n".join(diff_lines)
    diff, truncated = truncate_for_agent(raw_diff, COMPARE_LIMIT)

    changed_a = sum(
        len(b)
        for tag, _, _, a1, a2 in matcher.get_opcodes()
        if tag in {"delete", "replace"}
        for b in lines_a[a1:a2]
    )
    changed_b = sum(
        len(b)
        for tag, _, _, b1, b2 in matcher.get_opcodes()
        if tag in {"insert", "replace"}
        for b in lines_b[b1:b2]
    )
    return CompareResult(
        url_a=url_a,
        url_b=url_b,
        title_a=doc_a.get("title"),
        title_b=doc_b.get("title"),
        identical=identical,
        similarity=similarity,
        changed_chars_a=changed_a,
        changed_chars_b=changed_b,
        diff_summary=diff,
        truncated=truncated,
    )
