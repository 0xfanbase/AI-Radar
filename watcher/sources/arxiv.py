"""arXiv fetcher (cs.AI / cs.CL / cs.LG) for the Phase 1 watcher.

Queries the free, keyless arXiv Atom API
(``https://export.arxiv.org/api/query``) for the most recently submitted
papers across the three categories named in the approved build plan, and
normalizes each ``<entry>`` into a :class:`watcher.models.Item` with
``source_type="arxiv"``. "Daily top papers" has no popularity signal on
arXiv itself (unlike HN's points/velocity), so it is interpreted as "most
recently submitted" -- sorted by ``submittedDate`` descending, capped at
``ARXIV_MAX_RESULTS`` -- with arXiv's own "unusual velocity" folded into
cross-source corroboration at the ranking stage instead (per the approved
plan's own note); logged in IMPROVEMENT_BACKLOG.md.

Goes through the shared fetch-discipline layer (``watcher/http.py``): a
single combined OR-query across all three categories (one polite request
per run rather than three), the shared ``requests.Session``, and the
``robots.txt`` gate -- never circumvented, even though (as discovered and
logged during this commit) ``export.arxiv.org``'s ``robots.txt`` currently
disallows all paths for every user agent, which means this fetcher
presently returns an empty list against the real network until that
policy conflict is explicitly resolved (see IMPROVEMENT_BACKLOG.md).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import requests

from watcher.config import ARXIV_CATEGORIES, CACHE_DIR
from watcher.http import check_robots_allowed, fetch
from watcher.models import Item

logger = logging.getLogger(__name__)

ARXIV_API_BASE_URL = "https://export.arxiv.org/api/query"

# "Daily top papers" per category is spec-silent on an exact count; this is
# the simplest reasonable single-request cap across all three combined
# categories, generous enough to cover a full day's cs.AI/cs.CL/cs.LG
# submission volume with room to spare for the ranking stage to narrow
# down to the queue's top <=8. Logged in IMPROVEMENT_BACKLOG.md. Kept
# local to this module (rather than added to watcher/config.py) since this
# commit's file scope is limited to the arXiv fetcher itself.
ARXIV_MAX_RESULTS = 50

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _normalize_whitespace(text: str) -> str:
    """Collapse any run of whitespace (including embedded newlines from
    arXiv's line-wrapped Atom XML) into single spaces, and strip ends.
    """
    return re.sub(r"\s+", " ", text).strip()


def build_search_query(categories: tuple[str, ...] = ARXIV_CATEGORIES) -> str:
    """Build the arXiv ``search_query`` value OR-ing every category."""
    return " OR ".join(f"cat:{category}" for category in categories)


def build_query_url(
    categories: tuple[str, ...] = ARXIV_CATEGORIES,
    max_results: int = ARXIV_MAX_RESULTS,
) -> str:
    """Build the full arXiv Atom API query URL for this fetcher.

    One combined request across all categories (via OR) rather than one
    request per category -- fewer outbound calls (politer per the fetch-
    discipline rules), and arXiv itself already dedupes an entry that
    belongs to more than one queried category, so no client-side merge is
    needed either. Sorted by ``submittedDate`` descending: see this
    module's docstring for why that is "daily top papers" here.
    """
    params = {
        "search_query": build_search_query(categories),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max_results,
    }
    return f"{ARXIV_API_BASE_URL}?{urlencode(params)}"


def parse_arxiv_atom(atom_text: str) -> list[Item]:
    """Parse a raw arXiv Atom API response body into a list of Items.

    Skips (rather than raises on) any ``<entry>`` missing a title or a
    usable URL -- malformed upstream data degrades this source gracefully,
    same posture as every other fetcher in this project.
    """
    root = ET.fromstring(atom_text)
    items: list[Item] = []

    for entry in root.findall(_tag(_ATOM_NS, "entry")):
        raw_title = entry.findtext(_tag(_ATOM_NS, "title")) or ""
        title = _normalize_whitespace(raw_title)

        url = None
        for link in entry.findall(_tag(_ATOM_NS, "link")):
            if link.get("rel") == "alternate":
                url = link.get("href")
                break
        if not url:
            url = entry.findtext(_tag(_ATOM_NS, "id"))

        if not title or not url:
            logger.warning("Skipping arXiv entry missing title or url: %r", entry)
            continue

        published_at = (
            entry.findtext(_tag(_ATOM_NS, "published"))
            or entry.findtext(_tag(_ATOM_NS, "updated"))
            or ""
        )
        summary = _normalize_whitespace(entry.findtext(_tag(_ATOM_NS, "summary")) or "")

        categories = [
            category.get("term")
            for category in entry.findall(_tag(_ATOM_NS, "category"))
            if category.get("term")
        ]
        primary_category_el = entry.find(_tag(_ARXIV_NS, "primary_category"))
        primary_category = (
            primary_category_el.get("term")
            if primary_category_el is not None
            else (categories[0] if categories else None)
        )
        authors = [
            _normalize_whitespace(name)
            for author in entry.findall(_tag(_ATOM_NS, "author"))
            for name in [author.findtext(_tag(_ATOM_NS, "name")) or ""]
            if name.strip()
        ]

        items.append(
            Item(
                source_type="arxiv",
                source_name="arxiv",
                title=title,
                url=url,
                published_at=published_at,
                points=None,
                num_comments=None,
                extra={
                    "summary": summary,
                    "categories": categories,
                    "primary_category": primary_category,
                    "authors": authors,
                },
            )
        )

    return items


def fetch_arxiv_items(
    session: requests.Session,
    *,
    categories: tuple[str, ...] = ARXIV_CATEGORIES,
    max_results: int = ARXIV_MAX_RESULTS,
    cache_dir: Path = CACHE_DIR,
) -> list[Item]:
    """Fetch and normalize the latest cs.AI/cs.CL/cs.LG papers from arXiv.

    Returns ``[]`` (never raises) if ``robots.txt`` disallows the fetch --
    "drop that source, never circumvent a disallow" per CLAUDE.md, applied
    uniformly here exactly as it is for every other fetcher.
    """
    query_url = build_query_url(categories=categories, max_results=max_results)

    if not check_robots_allowed(query_url):
        logger.warning(
            "robots.txt disallows the arXiv API query -- skipping arXiv "
            "source for this run."
        )
        return []

    result = fetch(session, query_url, cache_dir=cache_dir)
    return parse_arxiv_atom(result.text)
