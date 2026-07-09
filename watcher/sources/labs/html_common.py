"""Shared BeautifulSoup/XML scraping helpers for lab sources with no RSS
feed -- Anthropic's news-index anchor-scrape (``anthropic.py``) and
DeepSeek's sitemap-diff + h1-extraction (``deepseek.py``).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Anchor-scrape (Anthropic's /news index page)
# --------------------------------------------------------------------------

# Anthropic's news page nests a category label, a <time>, a headline
# element, and (for "featured" cards) a body-preview paragraph all inside
# one <a> -- a plain anchor.get_text() would concatenate all of them. The
# real fixture (fixtures/lab_anthropic_news.html) consistently marks its
# headline element with a class name *containing* "title" (e.g. an
# `h4.headline-6 ...__title` for the featured-grid layout, a
# `span ...__title` for the plain list layout) regardless of which card
# layout is used -- matched by substring, not exact class name, since
# these are hashed CSS-module class names that change on every rebuild.
# Spec-silent scrape heuristic; logged in IMPROVEMENT_BACKLOG.md.
_TITLE_CLASS_RE = re.compile(r"title", re.IGNORECASE)
_TITLE_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "span", "p", "div")


def _extract_anchor_title(anchor) -> str:
    """Best-effort headline text for one <a> anchor.

    Falls back to the anchor's own text (excluding any <time> descendant,
    so the date string doesn't get glued onto the headline) if no
    title-classed element is found -- a future markup change degrades to a
    slightly messier title rather than an empty one.
    """
    title_el = anchor.find(
        lambda tag: tag.name in _TITLE_TAGS
        and tag.has_attr("class")
        and any(_TITLE_CLASS_RE.search(cls) for cls in tag["class"])
    )
    if title_el is not None:
        return _clean_text(title_el.get_text(" ", strip=True))

    parts = []
    for descendant in anchor.descendants:
        if isinstance(descendant, str) and descendant.find_parent("time") is None:
            stripped = descendant.strip()
            if stripped:
                parts.append(stripped)
    return _clean_text(" ".join(parts))


def _extract_anchor_date_iso(anchor) -> str:
    """Parse the anchor's <time> text (e.g. "Jun 30, 2026") into an ISO
    8601 UTC timestamp at midnight -- day granularity is all the source
    page provides. Returns "" (never raises) if there is no <time>
    element, or its text doesn't match the expected "Mon D, YYYY" format.
    """
    time_el = anchor.find("time")
    if time_el is None:
        return ""
    date_text = _clean_text(time_el.get_text(" ", strip=True))
    try:
        parsed = datetime.strptime(date_text, "%b %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        logger.warning("Could not parse anchor date text %r", date_text)
        return ""
    return parsed.isoformat().replace("+00:00", "Z")


def extract_news_anchors(
    html_text: str,
    *,
    base_url: str,
    path_prefix: str = "/news/",
) -> list[dict]:
    """Scrape every anchor whose resolved href's path starts with
    ``path_prefix`` and has content beyond it (excludes a bare link back
    to the index page itself, e.g. ``href="/news"``), deduped by resolved
    URL with first-occurrence-wins (the real page repeats the same story
    in more than one card layout -- e.g. a "featured" card and a "recent"
    list entry both linking to the same story).

    Returns a list of ``{"url", "title", "published_at"}`` dicts in
    document order. Never raises: an anchor with no extractable title is
    skipped and logged, not fatal to the rest of the page.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    seen: set[str] = set()
    results: list[dict] = []

    for anchor in soup.find_all("a", href=True):
        absolute_url = urljoin(base_url, anchor["href"])
        path = urlsplit(absolute_url).path
        if not path.startswith(path_prefix) or len(path) <= len(path_prefix):
            continue
        if absolute_url in seen:
            continue
        seen.add(absolute_url)

        title = _extract_anchor_title(anchor)
        if not title:
            logger.warning(
                "Skipping anchor with no extractable title: %s", absolute_url
            )
            continue

        results.append(
            {
                "url": absolute_url,
                "title": title,
                "published_at": _extract_anchor_date_iso(anchor),
            }
        )

    return results


# --------------------------------------------------------------------------
# h1-extraction (DeepSeek article pages)
# --------------------------------------------------------------------------


def extract_h1_text(html_text: str) -> str | None:
    """Return the page's first <h1>'s cleaned text, or None if it has
    none. Never raises on malformed HTML -- BeautifulSoup's own
    "html.parser" backend is itself tolerant of broken markup.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    h1 = soup.find("h1")
    if h1 is None:
        return None
    text = _clean_text(h1.get_text(" ", strip=True))
    return text or None


# --------------------------------------------------------------------------
# Sitemap parsing + diff (DeepSeek)
# --------------------------------------------------------------------------

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _sitemap_tag(name: str) -> str:
    return f"{{{_SITEMAP_NS}}}{name}"


def parse_sitemap_urls(xml_text: str) -> list[str]:
    """Parse a ``sitemap.xml`` body into an ordered list of ``<loc>`` URLs.

    Returns ``[]`` (never raises) on unparseable XML -- a malformed
    sitemap degrades to "no URLs seen this run" rather than crashing the
    fetcher, same posture as every other Phase 1 parser.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Could not parse sitemap XML: %s", exc)
        return []

    urls = []
    for url_el in root.findall(_sitemap_tag("url")):
        loc_el = url_el.find(_sitemap_tag("loc"))
        if loc_el is not None and loc_el.text:
            urls.append(loc_el.text.strip())
    return urls


def diff_new_sitemap_urls(
    previous_urls: list[str], current_urls: list[str]
) -> list[str]:
    """Return the URLs present in ``current_urls`` but not in
    ``previous_urls`` -- the "new slugs since the last run" this fetcher
    needs, preserving ``current_urls``'s own order.
    """
    previous_set = set(previous_urls)
    return [url for url in current_urls if url not in previous_set]
