"""Tests for the two RSS-based lab fetchers (OpenAI, Google DeepMind) and
their shared parsing layer, ``watcher/sources/labs/rss_common.py``.

`fixtures/lab_openai_rss.xml` is a REAL response captured live from
`https://openai.com/news/rss.xml` on 2026-07-09 -- not fabricated.
`fixtures/lab_deepmind_rss.xml` is a REAL response captured live from
`https://deepmind.google/blog/rss.xml` on 2026-07-09 -- not fabricated.
`fixtures/lab_malformed_rss.xml` is a deliberately broken feed (an
unescaped `&` inside a `<title>`, the single most common real-world RSS
malformation) used to exercise feedparser's `.bozo` flag and confirm this
project's "recover what we can, never crash" posture around it.

Every test in this file is fixture/mock-based; `tests/conftest.py`'s
autouse guard would raise on any real network call here regardless.
"""
from __future__ import annotations

from pathlib import Path

from watcher import http
from watcher.models import Item
from watcher.sources.labs import deepmind, openai, registry, rss_common

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# parse_rss_feed -- against the real captured fixtures
# --------------------------------------------------------------------------


def test_parse_rss_feed_openai_fixture_is_well_formed_and_normalizes_first_entry():
    items = rss_common.parse_rss_feed(
        _load_fixture_text("lab_openai_rss.xml"), source_name="openai"
    )

    assert len(items) == 1033
    assert all(isinstance(item, Item) for item in items)

    first = items[0]
    assert first.source_type == "lab"
    assert first.source_name == "openai"
    assert first.title == "Our approach to government and national security partnerships"
    assert first.url == "https://openai.com/index/government-national-security-partnerships"
    assert first.published_at == "2026-07-08T13:30:00Z"
    assert first.points is None
    assert first.num_comments is None
    assert first.extra["summary"].startswith("Learn how OpenAI approaches")


def test_parse_rss_feed_deepmind_fixture_is_well_formed_and_normalizes_first_entry():
    items = rss_common.parse_rss_feed(
        _load_fixture_text("lab_deepmind_rss.xml"), source_name="deepmind"
    )

    assert len(items) == 100
    first = items[0]
    assert first.source_type == "lab"
    assert first.source_name == "deepmind"
    assert first.title == (
        "Google DeepMind and A24 announce first-of-its-kind research partnership"
    )
    assert first.url == (
        "https://deepmind.google/blog/"
        "google-deepmind-and-a24-announce-first-of-its-kind-research-partnership/"
    )
    assert first.published_at == "2026-07-03T14:25:43Z"


def test_parse_rss_feed_every_openai_item_is_well_formed():
    items = rss_common.parse_rss_feed(
        _load_fixture_text("lab_openai_rss.xml"), source_name="openai"
    )
    for item in items:
        assert item.title.strip() == item.title
        assert item.title
        assert item.url.startswith("https://")
        assert item.source_type == "lab"
        assert item.source_name == "openai"


# --------------------------------------------------------------------------
# parse_rss_feed -- malformed-feed / bozo-flag handling
# --------------------------------------------------------------------------


def test_parse_rss_feed_malformed_feed_recovers_entries_despite_bozo_flag():
    # fixtures/lab_malformed_rss.xml has an unescaped `&` inside one
    # <title>, making the document not well-formed XML -- feedparser sets
    # .bozo on it, but (per feedparser's own lenient SGML-ish fallback
    # parser) still recovers both items with correctly-decoded titles.
    # This must never raise and must not silently drop the recoverable
    # entries.
    items = rss_common.parse_rss_feed(
        _load_fixture_text("lab_malformed_rss.xml"), source_name="testlab"
    )

    assert len(items) == 2
    assert items[0].title == "Research & Development Update"
    assert items[0].url == (
        "https://example-lab.test/news/research-and-development-update"
    )
    assert items[1].title == "A Perfectly Fine Second Item"


def test_parse_rss_feed_totally_unparseable_text_returns_empty_list_not_a_crash():
    # Not XML/RSS at all -- feedparser sets .bozo and yields zero entries.
    # Must degrade to a clean empty list, never raise.
    items = rss_common.parse_rss_feed(
        "This is not XML or RSS at all, just plain text {}<><>",
        source_name="testlab",
    )
    assert items == []


def test_parse_rss_feed_skips_entries_missing_title_or_link():
    feed = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item>
<title></title>
<link>https://example.test/no-title</link>
</item>
<item>
<title>Missing Link Item</title>
</item>
<item>
<title>A Perfectly Fine Item</title>
<link>https://example.test/fine-item</link>
</item>
</channel>
</rss>
"""
    items = rss_common.parse_rss_feed(feed, source_name="testlab")

    assert len(items) == 1
    assert items[0].title == "A Perfectly Fine Item"


# --------------------------------------------------------------------------
# fetch_rss_lab_items / fetch_openai_items / fetch_deepmind_items --
# integration through watcher/http.py's shared fetch-discipline layer, via
# requests_mock (no real network).
# --------------------------------------------------------------------------


def test_fetch_openai_items_parses_real_fixture_through_shared_http_layer(
    requests_mock, tmp_path
):
    requests_mock.get(
        "https://openai.com/robots.txt",
        text="User-agent: *\nAllow: /\n",
    )
    requests_mock.get(openai.OPENAI_RSS_URL, text=_load_fixture_text("lab_openai_rss.xml"))

    session = http.build_session()
    items = openai.fetch_openai_items(session, cache_dir=tmp_path)

    assert len(items) == 1033
    assert items[0].source_name == "openai"
    assert items[0].title == "Our approach to government and national security partnerships"


def test_fetch_deepmind_items_parses_real_fixture_through_shared_http_layer(
    requests_mock, tmp_path
):
    requests_mock.get(
        "https://deepmind.google/robots.txt",
        text="User-agent: *\nAllow: /\n",
    )
    requests_mock.get(
        deepmind.DEEPMIND_RSS_URL, text=_load_fixture_text("lab_deepmind_rss.xml")
    )

    session = http.build_session()
    items = deepmind.fetch_deepmind_items(session, cache_dir=tmp_path)

    assert len(items) == 100
    assert items[0].source_name == "deepmind"
    assert items[0].title == (
        "Google DeepMind and A24 announce first-of-its-kind research partnership"
    )


def test_fetch_openai_items_sends_descriptive_user_agent(requests_mock, tmp_path):
    from watcher import config

    requests_mock.get(
        "https://openai.com/robots.txt", text="User-agent: *\nAllow: /\n"
    )
    requests_mock.get(openai.OPENAI_RSS_URL, text=_load_fixture_text("lab_openai_rss.xml"))

    session = http.build_session()
    openai.fetch_openai_items(session, cache_dir=tmp_path)

    feed_request = next(
        req for req in requests_mock.request_history
        if req.url == openai.OPENAI_RSS_URL
    )
    assert feed_request.headers["User-Agent"] == config.USER_AGENT


def test_fetch_rss_lab_items_returns_empty_list_when_robots_disallows(
    requests_mock, tmp_path
):
    # Synthetic disallow fixture (not real OpenAI/DeepMind policy -- both
    # currently allow-all per the live robots.txt checks recorded in
    # IMPROVEMENT_BACKLOG.md): confirms the "skip cleanly, never crash,
    # never circumvent a disallow" fetch-discipline rule applies uniformly
    # to the RSS-based lab fetchers too, not just arXiv/HTML ones.
    requests_mock.get(
        "https://example-lab.test/robots.txt",
        text="User-agent: *\nDisallow: /news/rss.xml\n",
    )

    session = http.build_session()
    items = rss_common.fetch_rss_lab_items(
        session,
        "https://example-lab.test/news/rss.xml",
        source_name="testlab",
        cache_dir=tmp_path,
    )

    assert items == []
    # No feed request should have even been attempted once robots.txt
    # disallowed the fetch.
    feed_calls = [
        req for req in requests_mock.request_history
        if req.url == "https://example-lab.test/news/rss.xml"
    ]
    assert feed_calls == []


# --------------------------------------------------------------------------
# registry.fetch_all_lab_items -- aggregates all four lab fetchers behind
# one call, never letting one lab's failure take down the others.
# --------------------------------------------------------------------------


def test_registry_lists_all_four_labs():
    assert set(registry.LAB_FETCHERS) == {"openai", "deepmind", "anthropic", "deepseek"}


def test_fetch_all_lab_items_aggregates_every_registered_fetcher(monkeypatch):
    def fake_fetch(name):
        def _fetch(session, *, cache_dir):
            return [
                Item(
                    source_type="lab",
                    source_name=name,
                    title=f"{name} item",
                    url=f"https://example.test/{name}",
                    published_at="2026-07-09T00:00:00Z",
                )
            ]
        return _fetch

    fake_registry = {name: fake_fetch(name) for name in registry.LAB_FETCHERS}
    monkeypatch.setattr(registry, "LAB_FETCHERS", fake_registry)

    items = registry.fetch_all_lab_items(object(), cache_dir=Path("/unused"))

    assert {item.source_name for item in items} == {
        "openai", "deepmind", "anthropic", "deepseek",
    }
    assert len(items) == 4


def test_fetch_all_lab_items_skips_a_failing_lab_without_crashing(monkeypatch):
    def raising_fetch(session, *, cache_dir):
        raise RuntimeError("simulated upstream failure")

    def ok_fetch(session, *, cache_dir):
        return [
            Item(
                source_type="lab",
                source_name="openai",
                title="fine",
                url="https://example.test/fine",
                published_at="2026-07-09T00:00:00Z",
            )
        ]

    monkeypatch.setattr(
        registry,
        "LAB_FETCHERS",
        {"openai": ok_fetch, "deepmind": raising_fetch},
    )

    items = registry.fetch_all_lab_items(object(), cache_dir=Path("/unused"))

    assert len(items) == 1
    assert items[0].source_name == "openai"
