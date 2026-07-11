"""AI-lab fetchers for the Phase 1 watcher (OpenAI, Google DeepMind,
Anthropic, DeepSeek).

Each lab module exposes one ``fetch_<lab>_items(session, *, cache_dir=...)``
function returning a list of :class:`watcher.models.Item` with
``source_type="lab"``. Two labs publish RSS (OpenAI, DeepMind) and share
``rss_common.py``'s feedparser-based parsing; two do not (Anthropic,
DeepSeek) and share ``html_common.py``'s BeautifulSoup-based scraping.
``registry.py`` aggregates all four behind one call for the watcher CLI.
"""
