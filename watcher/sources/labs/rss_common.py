"""Shared feedparser-based RSS parsing for lab sources that publish an RSS
feed -- currently OpenAI (``openai.py``) and Google DeepMind
(``deepmind.py``).

Per IMPROVEMENT_BACKLOG.md's note on ``tests/conftest.py``'s network-block
seam: ``feedparser.parse(url)`` would fetch the URL itself via its own
``urllib`` path, bypassing the shared ``requests.Session`` (and the
default-test-run network guard that patches it). Every fetcher here goes
through ``watcher.http.fetch`` first to get raw response text via the
shared session, and only then hands that *text* (never a URL) to
``feedparser.parse``.
"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests

from watcher.config import CACHE_DIR
from watcher.http import check_robots_allowed, fetch
from watcher.models import Item

logger = logging.getLogger(__name__)


def _entry_published_at(entry) -> str:
    """Best-effort ISO 8601 UTC timestamp from a feedparser entry.

    Prefers feedparser's own ``*_parsed`` ``struct_time`` fields (already
    normalized to UTC by feedparser regardless of the feed's original
    timezone offset) over the raw string fields, falling back to whatever
    raw date string the entry carries if no parsed struct is available,
    and to ``""`` if neither exists. Never raises on a missing or
    unparseable date -- a fetcher-wide "degrade gracefully" posture.
    """
    for key in ("published_parsed", "updated_parsed"):
        struct = entry.get(key)
        if struct:
            timestamp = calendar.timegm(struct)
            return (
                datetime.fromtimestamp(timestamp, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
    return (entry.get("published") or entry.get("updated") or "").strip()


def parse_rss_feed(raw_text: str, *, source_name: str) -> list[Item]:
    """Parse a raw RSS/Atom feed body into a list of Items.

    feedparser is deliberately lenient: a malformed feed sets
    ``feed.bozo`` (with details in ``feed.bozo_exception``) but frequently
    still yields usable ``entries`` (e.g. a single stray unescaped ``&``
    in one title does not stop the rest of the document from parsing).
    Per CLAUDE.md's "degrade gracefully, never crash" posture, a bozo feed
    is logged as a warning and whatever entries feedparser could still
    recover are used as-is; a feed that is bozo *and* yields zero entries
    naturally falls out of this as an empty list -- a clean skip, never a
    raised exception.

    Also skips (rather than raises on) any entry missing a title or link,
    same posture as every other Phase 1 fetcher's parser.
    """
    parsed = feedparser.parse(raw_text)

    if parsed.bozo:
        logger.warning(
            "Feed for %s is malformed (bozo_exception=%s) -- using "
            "whatever entries feedparser could still recover.",
            source_name, getattr(parsed, "bozo_exception", None),
        )

    items: list[Item] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        if not title or not url:
            logger.warning(
                "Skipping %s feed entry missing title or link: %r",
                source_name, entry,
            )
            continue

        items.append(
            Item(
                source_type="lab",
                source_name=source_name,
                title=title,
                url=url,
                published_at=_entry_published_at(entry),
                points=None,
                num_comments=None,
                extra={"summary": (entry.get("summary") or "").strip()},
            )
        )

    return items


def fetch_rss_lab_items(
    session: requests.Session,
    url: str,
    *,
    source_name: str,
    cache_dir: Path = CACHE_DIR,
) -> list[Item]:
    """Fetch and parse a lab's RSS feed through the shared fetch-discipline
    layer (robots.txt gate, retries/backoff, ETag cache).

    Returns ``[]`` (never raises) if ``robots.txt`` disallows the fetch --
    "drop that source, never circumvent a disallow" per CLAUDE.md, applied
    uniformly here exactly as it is for every other Phase 1 fetcher.
    """
    if not check_robots_allowed(url):
        logger.warning(
            "robots.txt disallows %s -- skipping %s for this run.",
            url, source_name,
        )
        return []

    result = fetch(session, url, cache_dir=cache_dir)
    return parse_rss_feed(result.text, source_name=source_name)
