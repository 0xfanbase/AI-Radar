"""DeepSeek fetcher (Phase 1 lab source): sitemap-diff + h1-extraction.

Confirmed live on 2026-07-09: DeepSeek's API docs site
(``https://api-docs.deepseek.com``) is a Docusaurus SPA with no RSS feed
and no server-rendered news index to anchor-scrape, but its
``sitemap.xml`` is a plain static XML file listing every ``/news/<slug>``
article page (see ``fixtures/lab_deepseek_sitemap_v1.xml`` for the real
captured response). New stories are detected by diffing the current
sitemap against the sitemap this fetcher last saw -- a small persisted
state file under ``data/.cache/`` -- and each newly-seen ``/news/`` URL's
headline is read via a plain ``<h1>`` extraction of its article page (see
``fixtures/lab_deepseek_news_page.html`` for a real captured example).

The persisted "last-seen sitemap" state is a dedicated file
(``deepseek_sitemap_seen.json``) rather than reusing
``watcher.http``'s own ETag response cache: that cache is keyed by
``sha256(url)`` and is *overwritten* with the new body on every successful
fetch as part of its own conditional-GET bookkeeping (see
``watcher/http.py``), so by the time this fetcher could read it the
"previous" body would already be gone. A dedicated state file, written
only by this module and read *before* today's sitemap fetch overwrites
anything, keeps the diff correct without reaching into another module's
private cache internals. Logged in IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlsplit

import requests

from watcher.config import CACHE_DIR
from watcher.http import check_robots_allowed, fetch
from watcher.models import Item
from watcher.sources.labs.html_common import (
    diff_new_sitemap_urls,
    extract_h1_text,
    parse_sitemap_urls,
)

logger = logging.getLogger(__name__)

DEEPSEEK_SITEMAP_URL = "https://api-docs.deepseek.com/sitemap.xml"
SOURCE_NAME = "deepseek"

_NEWS_PATH_PREFIX = "/news/"
_SITEMAP_STATE_FILENAME = "deepseek_sitemap_seen.json"


def _sitemap_state_path(cache_dir: Path) -> Path:
    return cache_dir / _SITEMAP_STATE_FILENAME


def _load_previous_sitemap_urls(cache_dir: Path) -> list[str]:
    """Read the sitemap URL list this fetcher saw last run.

    Returns ``[]`` if no state file exists yet (first-ever run: every
    current URL is "new", though only ``/news/`` ones become Items -- see
    ``fetch_deepseek_items``) or if the file is missing/corrupt -- never
    raises, same posture as ``watcher.http``'s own cache-entry loader.
    """
    path = _sitemap_state_path(cache_dir)
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("urls", []))
    except (OSError, json.JSONDecodeError):
        return []


def _store_sitemap_urls(cache_dir: Path, urls: list[str]) -> None:
    path = _sitemap_state_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"urls": urls}, f)


def _is_news_article_url(url: str, path_prefix: str = _NEWS_PATH_PREFIX) -> bool:
    path = urlsplit(url).path
    return path.startswith(path_prefix) and len(path) > len(path_prefix)


def fetch_deepseek_items(
    session: requests.Session,
    *,
    cache_dir: Path = CACHE_DIR,
    sitemap_url: str = DEEPSEEK_SITEMAP_URL,
) -> list[Item]:
    """Diff the current sitemap against the last-seen one, then fetch each
    newly-appeared ``/news/`` article page and extract its ``<h1>`` as the
    title.

    Returns ``[]`` (never raises) if ``robots.txt`` disallows the sitemap
    fetch. A newly-discovered article page that itself fails to fetch, or
    has no ``<h1>``, is skipped individually (logged) rather than
    aborting the whole run -- consistent with every other Phase 1
    fetcher's "degrade gracefully" posture.
    """
    if not check_robots_allowed(sitemap_url):
        logger.warning(
            "robots.txt disallows %s -- skipping %s for this run.",
            sitemap_url, SOURCE_NAME,
        )
        return []

    sitemap_result = fetch(session, sitemap_url, cache_dir=cache_dir)
    current_urls = parse_sitemap_urls(sitemap_result.text)
    previous_urls = _load_previous_sitemap_urls(cache_dir)
    new_urls = diff_new_sitemap_urls(previous_urls, current_urls)
    _store_sitemap_urls(cache_dir, current_urls)

    items: list[Item] = []
    for url in new_urls:
        if not _is_news_article_url(url):
            continue
        if not check_robots_allowed(url):
            logger.warning(
                "robots.txt disallows %s -- skipping this DeepSeek article.",
                url,
            )
            continue
        try:
            article_result = fetch(session, url, cache_dir=cache_dir)
        except requests.exceptions.RequestException as exc:
            logger.warning("Failed to fetch new DeepSeek article %s: %s", url, exc)
            continue

        title = extract_h1_text(article_result.text)
        if not title:
            logger.warning("Skipping DeepSeek article with no <h1>: %s", url)
            continue

        items.append(
            Item(
                source_type="lab",
                source_name=SOURCE_NAME,
                title=title,
                url=url,
                published_at="",
                points=None,
                num_comments=None,
                extra={"slug": urlsplit(url).path.rsplit("/", 1)[-1]},
            )
        )

    return items
