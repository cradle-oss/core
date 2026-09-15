"""HTML → readable content and structural inspection.

Distinct evidence levels, per the operating contract:
- fetched HTML (transport.py)
- extracted readable content (this module)
- rendered DOM (renderer.py)

Extraction is a heuristic. It never claims to represent a rendered
application; it only describes the static HTML that was actually received.
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Doctype, Tag

from core.web.transport import truncate_for_agent

NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "form",
    "nav",
    "footer",
    "aside",
}

MAX_CONTENT = 60_000
MAX_LINKS = 200


@dataclass
class ExtractedPage:
    url: str
    title: str | None
    description: str | None
    language: str | None
    markdown: str
    text: str
    truncated: bool
    selected: str  # which container the extractor picked

    @property
    def text_lines(self) -> list[str]:
        return self.text.splitlines()


@dataclass
class InspectReport:
    url: str
    doctype: str | None
    title: str | None
    language: str | None
    charset: str | None
    canonical: str | None
    meta: list[dict[str, str]] = field(default_factory=list)
    headings: list[tuple[str, str]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    external_scripts: list[str] = field(default_factory=list)
    stylesheets: list[str] = field(default_factory=list)
    links: dict[str, int] = field(default_factory=dict)


@dataclass
class LinkBundle:
    url: str
    internal: list[dict[str, str]] = field(default_factory=list)
    external: list[dict[str, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {"internal": len(self.internal), "external": len(self.external)}


def _is_hidden(tag: Tag) -> bool:
    attrs = tag.attrs or {}
    if "hidden" in attrs:
        return True
    style = attrs.get("style") or ""
    if re.search(r"(display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I):
        return True
    return False


def _absolute(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return None
    try:
        return urllib.parse.urljoin(base, href)
    except ValueError:
        return None


class WebExtractor:
    """Structured extraction + inspection of static HTML."""

    # ---- os ----

    @staticmethod
    def _clean_soup(html: str) -> BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for tag in list(soup.find_all(True)):
            if tag.name in NOISE_TAGS or _is_hidden(tag):
                tag.decompose()
        return soup

    # ---- extract ----

    def extract(self, html: str, url: str, *, plain: bool = False) -> dict:
        """Choose the main content container, then render to markdown/text."""
        soup = self._clean_soup(html)
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        description = None
        for meta in soup.find_all("meta"):
            if (meta.get("name") or "").lower() == "description":
                description = (meta.get("content") or "").strip() or None
                break

        selected, container = self._pick_container(soup)
        if container is None:
            selected, container = "whole body", soup.body if soup.body else soup

        html_fragment = str(container)
        from html2text import HTML2Text

        h = HTML2Text()
        h.body_width = 120
        h.ignore_images = False
        h.ignore_emphasis = True
        h.ignore_links = False
        h.ignore_tables = False
        h.unicode_snob = True
        markdown = h.handle(html_fragment)
        markdown = markdown.strip()

        text = html_lib.unescape(
            re.sub(r"\s+", " ", container.get_text(" ", strip=True))
        )
        truncated = False
        markdown, truncated = truncate_for_agent(markdown, MAX_CONTENT)

        language = soup.html.get("lang") if soup.html else None
        page = ExtractedPage(
            url=url,
            title=title or None,
            description=description,
            language=language or None,
            markdown=markdown,
            text=text,
            truncated=truncated,
            selected=selected,
        )
        if plain:
            return {
                "text": page.text,
                "markdown": page.markdown,
                "title": page.title,
                "truncated": page.truncated,
            }
        return page

    def _pick_container(self, soup: BeautifulSoup) -> tuple[str, Tag | None]:
        for name, sel in (
            ("main landmark", "main"),
            ("article element", "article"),
        ):
            tag = soup.find(sel)
            if tag is not None:
                return name, tag

        best: Tag | None = None
        best_len = 0
        for tag in soup.find_all(["div", "section", "td"]):
            text = tag.get_text(" ", strip=True)
            n = len(text)
            if n > best_len:
                best, best_len = tag, n
        if best is not None and best_len >= 200:
            return (
                f"largest container "
                f"({best.name}@{len(best.get_text(strip=True))} chars)",
                best,
            )
        return "whole body", None

    # ---- inspect ----

    def inspect(self, html: str, url: str) -> InspectReport:
        soup = self._clean_soup(html)
        base = url

        doctype = None
        for child in soup.contents:
            if isinstance(child, Doctype):
                doctype = str(child).strip()
                break
            if isinstance(child, Tag):
                break
        title = soup.title.string.strip() if soup.title and soup.title.string else None

        canonical = None
        meta: list[dict[str, str]] = []
        for m in soup.find_all("meta"):
            name = m.get("name") or m.get("property") or m.get("http-equiv") or ""
            content = (m.get("content") or "").strip()
            if name:
                meta.append({"name": name, "content": content[:300]})

        canonical_tag = soup.find("link", rel=lambda v: v and "canonical" in v)
        if canonical_tag is not None:
            canonical = canonical_tag.get("href")

        headings: list[tuple[str, str]] = []
        for htag in soup.find_all(re.compile(r"^h[1-6]$")):
            text = htag.get_text(" ", strip=True)
            if text:
                headings.append((htag.name, text[:200]))
                if len(headings) >= 40:
                    break

        scripts_ext: list[str] = []
        styles: list[str] = []
        for s in soup.find_all("script", src=True):
            abs_url = _absolute(base, s["src"])
            if abs_url and abs_url not in scripts_ext and len(scripts_ext) < 30:
                scripts_ext.append(abs_url)
        for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
            href = link.get("href")
            if href:
                abs_url = _absolute(base, href)
                if abs_url and abs_url not in styles and len(styles) < 30:
                    styles.append(abs_url)

        charset = None
        if soup.meta and (soup.meta.get("charset") or ""):
            charset = soup.meta.get("charset")

        internal, external = 0, 0
        honors = urllib.parse.urlsplit(base)
        for a in soup.find_all("a", href=True):
            abs_url = _absolute(base, a["href"])
            if not abs_url:
                continue
            if urllib.parse.urlsplit(abs_url).netloc == honors.netloc:
                internal += 1
            else:
                external += 1

        # Scripts/styles/forms are real structural facts of the received HTML;
        # count them from the raw soup, not the noise-cleaned one.
        raw = BeautifulSoup(html, "html.parser")
        counts = {
            "links": len(raw.find_all("a", href=True)),
            "images": len(raw.find_all("img")),
            "scripts": len(raw.find_all("script")),
            "external_scripts": len(scripts_ext),
            "stylesheets": len(styles),
            "forms": len(raw.find_all("form")),
            "iframes": len(raw.find_all("iframe")),
            "buttons": len(raw.find_all("button")),
            "tables": len(raw.find_all("table")),
            "internal_links": internal,
            "external_links": external,
            "code_blocks": len(raw.find_all(["pre", "code"])),
        }

        return InspectReport(
            url=url,
            doctype=doctype,
            title=title,
            language=soup.html.get("lang") if soup.html else None,
            charset=charset,
            canonical=canonical,
            meta=meta[:20],
            headings=headings,
            counts=counts,
            external_scripts=scripts_ext,
            stylesheets=styles,
            links={"internal": internal, "external": external},
        )

    # ---- links / metadata ----

    def links(self, html: str, url: str) -> LinkBundle:
        soup = self._clean_soup(html)
        honors = urllib.parse.urlsplit(url)
        bundle = LinkBundle(url=url)
        for a in soup.find_all("a", href=True):
            abs_url = _absolute(url, a["href"])
            if not abs_url:
                continue
            text = a.get_text(" ", strip=True)
            entry = {"url": abs_url[:1000], "text": text[:200]}
            if urllib.parse.urlsplit(abs_url).netloc == honors.netloc:
                if len(bundle.internal) < MAX_LINKS:
                    bundle.internal.append(entry)
            else:
                if len(bundle.external) < MAX_LINKS:
                    bundle.external.append(entry)
        return bundle

    def metadata(self, html: str, url: str) -> dict:
        soup = self._clean_soup(html)
        title = soup.title.string.strip() if soup.title and soup.title.string else None
        charset = None
        if soup.meta and soup.meta.get("charset"):
            charset = soup.meta["charset"]
        out: dict[str, str | None] = {
            "title": title,
            "description": None,
            "canonical": None,
            "language": soup.html.get("lang") if soup.html else None,
            "charset": charset,
        }
        for m in soup.find_all("meta"):
            key = (m.get("name") or m.get("property") or "").strip().lower()
            value = (m.get("content") or "").strip()
            if not key:
                continue
            key_short = re.sub(r"^(og|twitter|article):", r"\1_", key).replace(":", "_")
            if key_short in out:
                out[key_short] = value[:600]
                continue
            out.setdefault(key_short, value[:600])
        tt = soup.find("link", rel=lambda v: v and "canonical" in v)
        if tt is not None:
            out["canonical"] = tt.get("href")
        return out
