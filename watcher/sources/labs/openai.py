"""OpenAI fetcher (Phase 1 lab source): RSS feed via feedparser.

Confirmed live on 2026-07-09: ``https://openai.com/news/rss.xml`` returns
a well-formed RSS 2.0 feed (see ``fixtures/lab_openai_rss.xml`` for the
real captured response). Parsing itself is shared with the DeepMind
fetcher in ``rss_common.py``.
"""
from __future__ import annotations

from pathlib import Path

import requests

from watcher.config import CACHE_DIR
from watcher.models import Item
from watcher.sources.labs.rss_common import fetch_rss_lab_items

OPENAI_RSS_URL = "https://openai.com/news/rss.xml"
SOURCE_NAME = "openai"


def fetch_openai_items(
    session: requests.Session, *, cache_dir: Path = CACHE_DIR
) -> list[Item]:
    """Fetch and parse OpenAI's News RSS feed into a list of Items."""
    return fetch_rss_lab_items(
        session, OPENAI_RSS_URL, source_name=SOURCE_NAME, cache_dir=cache_dir
    )
