"""HN Algolia Search API fetcher for the Phase 1 watcher.

Algorithm, per the approved build plan and CLAUDE.md's selection rule
("HN AI stories above a points threshold"), applied in three stages:

1. **Broad pool.** Every ``story``-tagged HN item in the last
   ``HN_LOOKBACK_HOURS`` hours with at least
   ``BROAD_POOL_POINTS_THRESHOLD`` points, pulled via the Algolia
   ``search_by_date`` endpoint.
2. **Client-side keyword filter for AI relevance,** matched whole-word
   (not substring) against ``watcher.config.HN_KEYWORDS``.
3. **Final candidacy.** ``points >= HN_POINTS_THRESHOLD`` OR
   ``velocity (points / age_hours) >= HN_VELOCITY_THRESHOLD_PTS_PER_HOUR``.

Only items clearing all three stages are returned as
:class:`watcher.models.Item` instances.

Two live-API details shape the implementation below, both discovered via
real calls to ``https://hn.algolia.com`` made while building this fetcher
(2026-07-09), not assumed from the plan -- both logged in
``IMPROVEMENT_BACKLOG.md``:

1. The live index currently rejects ``numericFilters`` on ``points`` /
   ``num_comments`` outright (``HTTP 400 "invalid numeric
   attribute(points), attribute not specified in
   numericAttributesForFiltering setting"``), for every endpoint tried
   (``search_by_date`` and ``search``, tags on or off). Only
   ``created_at_i`` still works as a server-side numeric filter, so stage
   1's points threshold is applied entirely client-side, on top of a
   broad time-bounded pull.
2. Algolia caps any single query at 1000 accessible hits
   (``paginationLimitedTo`` -- confirmed live: page 1 of a query with
   2326 total matches came back empty with an explicit message to that
   effect). A plain single ``created_at_i > cutoff`` filter across the
   full 48h lookback can exceed 1000 hits on a busy news day, silently
   truncating how far back the lookback actually reaches. To avoid that,
   the lookback window is split into fixed-size, non-overlapping
   sub-windows (``_SEARCH_WINDOW_HOURS`` hours each) queried separately
   and merged/de-duplicated by ``objectID`` -- each sub-window
   comfortably clears the 1000-hit cap even on a high-volume day (verified
   live: four 12h windows over the same 48h span returned 574/591/576/585
   hits respectively, summing exactly to the un-windowed query's own
   reported ``nbHits``).

``hn.algolia.com/robots.txt`` was confirmed live to 404 (checked
alongside stage-1/2 above), which this project's shared
``check_robots_allowed()`` treats as allow-all -- unlike
``export.arxiv.org`` (see IMPROVEMENT_BACKLOG.md), there is no robots
policy conflict to flag for this source. The gate is still called here
on every fetch, exactly like every other fetcher, rather than assumed to
stay that way.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import requests

from watcher.config import (
    CACHE_DIR,
    HN_KEYWORDS,
    HN_LOOKBACK_HOURS,
    HN_POINTS_THRESHOLD,
    HN_VELOCITY_THRESHOLD_PTS_PER_HOUR,
)
from watcher.http import check_robots_allowed, fetch
from watcher.models import Item

logger = logging.getLogger(__name__)

SOURCE_NAME = "hn"
SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"

# Stage-1 pre-filter, per the approved plan ("broad pool via search_by_date
# with points>=20"). Deliberately looser than HN_POINTS_THRESHOLD
# (watcher/config.py's final-candidacy points bar) -- it only shrinks the
# pool before the keyword/velocity stages, it never gates publication by
# itself. Kept local to this module rather than added to
# watcher/config.py: this commit's file scope is hn.py only, and
# config.py's own docstring already reserves HN_POINTS_THRESHOLD
# specifically for the final-candidacy bar (same pattern as
# watcher/sources/arxiv.py's locally-scoped ARXIV_MAX_RESULTS). Logged in
# IMPROVEMENT_BACKLOG.md.
BROAD_POOL_POINTS_THRESHOLD = 20

# Chunk size for the windowed search_by_date queries -- see module
# docstring point 2. 12h keeps every window comfortably under Algolia's
# 1000-hit cap even on a busy news day (verified live at ~580 hits/12h
# window against a real ~2300-hits/48h day).
_SEARCH_WINDOW_HOURS = 12

# Floor for age-in-hours when computing velocity, so a story posted only
# seconds ago can't divide-by-near-zero into an absurd number. One minute
# is generously small: reaching BROAD_POOL_POINTS_THRESHOLD (20 points)
# within under a minute of posting essentially never happens on HN, so
# this floor is a safety net, not a behavior the algorithm relies on.
_MIN_AGE_HOURS = 1.0 / 60.0

_KEYWORD_PATTERNS = tuple(
    re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
    for keyword in HN_KEYWORDS
)


def _matches_keywords(title: str) -> bool:
    """Whole-word match against ``HN_KEYWORDS`` -- not a naive substring
    check.

    A naive ``any(keyword in title.lower() for keyword in HN_KEYWORDS)``
    would false-positive on the single-letter-pair keyword ``"ai"``
    against ordinary English words that merely contain that letter pair
    (confirmed against a real HN title fetched live while building this
    fetcher: "Chat Control 1.0 and 2.0 Explained" contains "ai" inside
    "Expl-ai-ned"). Word-boundary regex avoids that false positive while
    still matching multi-word phrase keywords (e.g. "chip export")
    correctly at their own boundaries.
    """
    return any(pattern.search(title) for pattern in _KEYWORD_PATTERNS)


def _item_url(hit: dict) -> str:
    """The story's own linked URL, falling back to its HN discussion
    thread for text-only posts (Ask HN, some Show HN) that carry no
    external ``url`` field.
    """
    url = hit.get("url")
    if url:
        return url
    return f"https://news.ycombinator.com/item?id={hit['objectID']}"


def _parse_created_at(value: str) -> datetime:
    # HN Algolia's created_at is ISO 8601 UTC with a trailing "Z", which
    # datetime.fromisoformat doesn't accept directly on this project's
    # supported Python versions.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _age_hours(created_at: datetime, now: datetime) -> float:
    return max((now - created_at).total_seconds() / 3600.0, _MIN_AGE_HOURS)


def _search_windows(
    now_ts: int, lookback_hours: int, window_hours: int
) -> list[tuple[int, int]]:
    """Non-overlapping ``(lower, upper]`` epoch-second bounds covering the
    last ``lookback_hours``, each spanning at most ``window_hours``.

    See module docstring point 2 for why this is windowed rather than one
    query across the full lookback.
    """
    lookback_seconds = lookback_hours * 3600
    window_seconds = window_hours * 3600
    earliest = now_ts - lookback_seconds

    num_windows = max(1, math.ceil(lookback_seconds / window_seconds))
    windows = []
    for i in range(num_windows):
        upper = now_ts - i * window_seconds
        lower = max(earliest, now_ts - (i + 1) * window_seconds)
        if lower >= upper:
            continue
        windows.append((lower, upper))
    return windows


def _build_search_url(lower_ts: int, upper_ts: int) -> str:
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{lower_ts},created_at_i<={upper_ts}",
        "hitsPerPage": 1000,
    }
    return f"{SEARCH_BY_DATE_URL}?{urlencode(params)}"


def fetch_hn_items(
    session: requests.Session,
    *,
    now: datetime | None = None,
    lookback_hours: int = HN_LOOKBACK_HOURS,
    broad_pool_points_threshold: int = BROAD_POOL_POINTS_THRESHOLD,
    points_threshold: int = HN_POINTS_THRESHOLD,
    velocity_threshold: float = HN_VELOCITY_THRESHOLD_PTS_PER_HOUR,
    cache_dir: Path = CACHE_DIR,
) -> list[Item]:
    """Fetch the HN Algolia candidate pool of AI-relevant stories.

    Returns ``[]`` (never raises) if ``robots.txt`` disallows the fetch --
    "drop that source, never circumvent a disallow" per CLAUDE.md, applied
    uniformly here exactly as it is for every other fetcher. A genuine
    fetch failure against a live window (e.g. retries exhausted in
    ``watcher.http.fetch``) propagates instead of being silently
    swallowed -- the caller decides whether to skip HN for the run, same
    contract as ``watcher.http.fetch`` itself. ``watcher.cli.run`` is that
    caller, and wraps this call in ``_fetch_source`` so the decision is
    "skip HN for the run," never "crash the whole run."
    """
    if not check_robots_allowed(SEARCH_BY_DATE_URL):
        logger.warning(
            "robots.txt disallows the HN Algolia API query -- skipping HN "
            "source for this run."
        )
        return []

    now = now or datetime.now(timezone.utc)
    now_ts = int(now.timestamp())

    hits_by_id: dict[str, dict] = {}
    for lower_ts, upper_ts in _search_windows(now_ts, lookback_hours, _SEARCH_WINDOW_HOURS):
        url = _build_search_url(lower_ts, upper_ts)
        result = fetch(session, url, cache_dir=cache_dir)
        payload = json.loads(result.text)
        for hit in payload.get("hits", []):
            object_id = hit.get("objectID")
            if object_id is not None:
                hits_by_id[object_id] = hit

    items: list[Item] = []
    for hit in hits_by_id.values():
        points = hit.get("points") or 0
        if points < broad_pool_points_threshold:
            continue

        title = hit.get("title") or ""
        if not title or not _matches_keywords(title):
            continue

        created_at_raw = hit.get("created_at")
        if not created_at_raw:
            logger.warning("Skipping HN hit missing created_at: %r", hit.get("objectID"))
            continue

        age_hours = _age_hours(_parse_created_at(created_at_raw), now)
        velocity = points / age_hours

        if points < points_threshold and velocity < velocity_threshold:
            continue

        items.append(
            Item(
                source_type="hn",
                source_name=SOURCE_NAME,
                title=title,
                url=_item_url(hit),
                published_at=created_at_raw,
                points=points,
                num_comments=hit.get("num_comments"),
                extra={
                    "objectID": hit.get("objectID"),
                    "author": hit.get("author"),
                    "velocity_pts_per_hour": round(velocity, 3),
                },
            )
        )

    items.sort(key=lambda item: (-(item.points or 0), item.url))
    return items
