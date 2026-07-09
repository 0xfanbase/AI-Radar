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
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from watcher.config import CACHE_DIR
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


def fetch_all_lab_items(
    session: requests.Session, *, cache_dir: Path = CACHE_DIR
) -> list[Item]:
    """Fetch every registered lab source, never letting one lab's failure
    take down the others.

    Each individual fetcher already returns ``[]`` (never raises) on a
    ``robots.txt`` disallow; this wraps every call in a broad ``except``
    as well so an unexpected upstream failure (a genuine network error
    after retries are exhausted, an upstream response so malformed even
    the lenient parser chokes) degrades to "skip this lab for this run"
    rather than aborting the whole watcher run over one source. Spec-
    silent extension of the fetch-discipline "skip cleanly" rule to
    unexpected errors, not just robots.txt disallows; logged in
    IMPROVEMENT_BACKLOG.md.
    """
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
    return items
