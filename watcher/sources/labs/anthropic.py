"""Anthropic fetcher (Phase 1 lab source): bs4 anchor-scrape.

Confirmed live on 2026-07-09: Anthropic's public news index
(``https://www.anthropic.com/news``) offers no RSS feed, so this fetcher
parses the rendered page's own ``<a href="/news/...">`` anchors instead
(see ``fixtures/lab_anthropic_news.html`` for the real captured page, and
``watcher/sources/labs/html_common.py``'s ``extract_news_anchors()`` for
the extraction heuristic this relies on).
"""
from __future__ import annotations

from pathlib import Path

import requests

from watcher.config import CACHE_DIR
from watcher.http import check_robots_allowed, fetch
from watcher.models import Item
from watcher.sources.labs.html_common import extract_news_anchors

ANTHROPIC_NEWS_URL = "https://www.anthropic.com/news"
SOURCE_NAME = "anthropic"


def fetch_anthropic_items(
    session: requests.Session, *, cache_dir: Path = CACHE_DIR
) -> list[Item]:
    """Fetch Anthropic's news index and scrape it into a list of Items.

    Returns ``[]`` (never raises) if ``robots.txt`` disallows the fetch --
    same "skip cleanly" posture as every other Phase 1 fetcher.
    """
    if not check_robots_allowed(ANTHROPIC_NEWS_URL):
        return []

    result = fetch(session, ANTHROPIC_NEWS_URL, cache_dir=cache_dir)
    anchors = extract_news_anchors(result.text, base_url=ANTHROPIC_NEWS_URL)

    return [
        Item(
            source_type="lab",
            source_name=SOURCE_NAME,
            title=anchor["title"],
            url=anchor["url"],
            published_at=anchor["published_at"],
            points=None,
            num_comments=None,
            extra={},
        )
        for anchor in anchors
    ]
