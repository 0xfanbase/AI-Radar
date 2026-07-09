"""Registry aggregating all four AI-lab fetchers behind one call.

Two labs publish RSS (OpenAI, Google DeepMind) and go through
``rss_common.py``; two do not (Anthropic, DeepSeek) and go through
``html_common.py``. The watcher CLI (a later commit) only needs to know
about this module, not each individual lab fetcher.

Alibaba Qwen was considered and rejected as a fifth Phase 1 lab source
during build planning: its legacy blog is stale and redirects to a
JS-only SPA with no feed and no server-rendered content to scrape (per
the approved build plan; logged in IMPROVEMENT_BACKLOG.md) -- so the four
labs registered here are the full Phase 1 lab set.

**Recency window (Phase 1 PM checkpoint fix).** ``fetch_all_lab_items``
also applies ``config.LAB_RECENCY_WINDOW_DAYS`` (14 days) to the combined
pool before returning it: any Item with a *parseable* ``published_at``
older than the window is dropped. This lives here (the aggregation point)
rather than in each individual lab fetcher, since the defect it fixes is
specific to the *pool* OpenAI's un-windowed archive-serving RSS feed
floods, not to any one fetcher's own parsing correctness -- see
``config.LAB_RECENCY_WINDOW_DAYS``'s own comment for the full "Introducing
GPT-..." mega-cluster story this fixes. An Item with an unparseable/empty
``published_at`` (DeepSeek's own Items always carry one) is *not* dropped
-- DeepSeek's sitemap-diff already gates newness structurally, so there is
nothing further to window, and dropping it here for lacking a date it was
never going to have would silently zero out that entire source. Logged in
IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from watcher.config import CACHE_DIR, LAB_RECENCY_WINDOW_DAYS
from watcher.models import Item
from watcher.sources.labs.anthropic import fetch_anthropic_items
from watcher.sources.labs.deepmind import fetch_deepmind_items
from watcher.sources.labs.deepseek import fetch_deepseek_items
from watcher.sources.labs.openai import fetch_openai_items

logger = logging.getLogger(__name__)

# Ordered so a deterministic run always fetches the same labs in the same
# sequence (matters for ETag-cache warm/cold behavior in tests and for
# reading run logs).
LAB_FETCHERS = {
    "openai": fetch_openai_items,
    "deepmind": fetch_deepmind_items,
    "anthropic": fetch_anthropic_items,
    "deepseek": fetch_deepseek_items,
}


def _parse_iso8601(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse, tolerant of a trailing ``Z`` and of an
    empty/unparseable string (returns ``None`` rather than raising) --
    same tolerant posture as ``watcher/ranking.py``'s and
    ``watcher/velocity.py``'s own same-named private helpers (each kept
    local to its own module rather than shared, per this project's
    established pattern; logged in IMPROVEMENT_BACKLOG.md).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_within_recency_window(
    item: Item, *, now: datetime, window_days: int = LAB_RECENCY_WINDOW_DAYS
) -> bool:
    """``True`` unless ``item.published_at`` both parses *and* is older
    than ``window_days``. An unparseable/empty ``published_at`` always
    passes (kept) -- see the module docstring's "Recency window" section
    for why (DeepSeek's undated Items are already newness-gated by its
    own sitemap-diff, not by a date this filter could check).
    """
    published = _parse_iso8601(item.published_at)
    if published is None:
        return True
    age_days = (now - published).total_seconds() / 86400.0
    return age_days <= window_days


def fetch_all_lab_items(
    session: requests.Session,
    *,
    cache_dir: Path = CACHE_DIR,
    now: datetime | None = None,
) -> list[Item]:
    """Fetch every registered lab source, never letting one lab's failure
    take down the others, then drop any Item whose parseable
    ``published_at`` is older than ``config.LAB_RECENCY_WINDOW_DAYS``
    (Phase 1 PM checkpoint fix -- see the module docstring's "Recency
    window" section).

    Each individual fetcher already returns ``[]`` (never raises) on a
    ``robots.txt`` disallow; this wraps every call in a broad ``except``
    as well so an unexpected upstream failure (a genuine network error
    after retries are exhausted, an upstream response so malformed even
    the lenient parser chokes) degrades to "skip this lab for this run"
    rather than aborting the whole watcher run over one source. Spec-
    silent extension of the fetch-discipline "skip cleanly" rule to
    unexpected errors, not just robots.txt disallows; logged in
    IMPROVEMENT_BACKLOG.md.

    ``now`` defaults to the real current UTC time; ``watcher/cli.py``
    passes its own already-current ``now`` explicitly so the whole
    pipeline run shares one clock reading, matching this project's "no
    incidental live-clock reads inside pure-code logic" convention.
    """
    now = now or datetime.now(timezone.utc)
    items: list[Item] = []
    for lab_name, fetch_fn in LAB_FETCHERS.items():
        try:
            lab_items = fetch_fn(session, cache_dir=cache_dir)
        except Exception:
            logger.exception(
                "Lab fetcher %r failed unexpectedly; skipping it for this run.",
                lab_name,
            )
            continue
        items.extend(lab_items)

    recent_items = [item for item in items if _is_within_recency_window(item, now=now)]
    dropped = len(items) - len(recent_items)
    if dropped:
        logger.info(
            "Dropped %d lab item(s) older than the %d-day recency window.",
            dropped, LAB_RECENCY_WINDOW_DAYS,
        )
    return recent_items
