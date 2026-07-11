"""Google DeepMind fetcher (Phase 1 lab source): RSS feed via feedparser.

Confirmed live on 2026-07-09: ``https://deepmind.google/blog/rss.xml``
returns a well-formed RSS 2.0 feed (see ``fixtures/lab_deepmind_rss.xml``
for the real captured response). Parsing itself is shared with the
OpenAI fetcher in ``rss_common.py``.
"""
from __future__ import annotations

from pathlib import Path

import requests

from watcher.config import CACHE_DIR
from watcher.models import Item
from watcher.sources.labs.rss_common import fetch_rss_lab_items

DEEPMIND_RSS_URL = "https://deepmind.google/blog/rss.xml"
SOURCE_NAME = "deepmind"


def fetch_deepmind_items(
    session: requests.Session, *, cache_dir: Path = CACHE_DIR
) -> list[Item]:
    """Fetch and parse Google DeepMind's blog RSS feed into a list of Items."""
    return fetch_rss_lab_items(
        session, DEEPMIND_RSS_URL, source_name=SOURCE_NAME, cache_dir=cache_dir
    )
