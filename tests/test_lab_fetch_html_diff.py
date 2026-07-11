"""Tests for the two HTML-based lab fetchers (Anthropic, DeepSeek) and
their shared scraping layer, ``watcher/sources/labs/html_common.py``.

`fixtures/lab_anthropic_news.html` is a REAL page captured live from
`https://www.anthropic.com/news` on 2026-07-09 -- not fabricated.
`fixtures/lab_deepseek_sitemap_v1.xml` is a REAL response captured live
from `https://api-docs.deepseek.com/sitemap.xml` on 2026-07-09.
`fixtures/lab_deepseek_sitemap_v2.xml` is that same real content with one
additional, plausible `/news/<slug>` URL appended
(`https://api-docs.deepseek.com/news/news260709`, following the sitemap's
own `newsYYMMDD` slug convention) purely to exercise new-slug detection --
it is not a real DeepSeek URL.
`fixtures/lab_deepseek_news_page.html` is a REAL article page captured
live from `https://api-docs.deepseek.com/news/news260424` on 2026-07-09.

Every test in this file is fixture/mock-based; `tests/conftest.py`'s
autouse guard would raise on any real network call here regardless.
"""
from __future__ import annotations

import json
from pathlib import Path

from watcher import http
from watcher.models import Item
from watcher.sources.labs import anthropic, deepseek, html_common

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _load_fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Anthropic anchor extraction -- against the real captured fixture
# --------------------------------------------------------------------------


def test_extract_news_anchors_finds_every_unique_news_story_deduped():
    anchors = html_common.extract_news_anchors(
        _load_fixture_text("lab_anthropic_news.html"),
        base_url="https://www.anthropic.com/news",
    )

    # The real fixture repeats several stories across a "featured" card
    # layout and a plain list layout -- both link to the same URL, so this
    # also proves first-occurrence-wins deduping collapses them to one.
    assert len(anchors) == 11
    urls = [a["url"] for a in anchors]
    assert len(urls) == len(set(urls))


def test_extract_news_anchors_normalizes_featured_grid_card():
    anchors = html_common.extract_news_anchors(
        _load_fixture_text("lab_anthropic_news.html"),
        base_url="https://www.anthropic.com/news",
    )
    redeploying = next(a for a in anchors if a["url"].endswith("/redeploying-fable-5"))

    assert redeploying["title"] == "Redeploying Fable 5"
    assert redeploying["url"] == "https://www.anthropic.com/news/redeploying-fable-5"
    assert redeploying["published_at"] == "2026-06-30T00:00:00Z"


def test_extract_news_anchors_normalizes_plain_list_card():
    anchors = html_common.extract_news_anchors(
        _load_fixture_text("lab_anthropic_news.html"),
        base_url="https://www.anthropic.com/news",
    )
    alberta = next(
        a for a in anchors if a["url"].endswith("/alberta-government-claude-cybersecurity")
    )

    assert alberta["title"] == (
        "Government of Alberta uses Claude to find and fix cybersecurity "
        "vulnerabilities across government systems"
    )
    assert alberta["published_at"] == "2026-07-06T00:00:00Z"


def test_extract_news_anchors_falls_back_to_anchor_text_when_no_title_class():
    # The real fixture's footer links to the Responsible Scaling Policy
    # story with a plain <a> containing no <time> and no title-classed
    # child element at all -- exercises the fallback path.
    anchors = html_common.extract_news_anchors(
        _load_fixture_text("lab_anthropic_news.html"),
        base_url="https://www.anthropic.com/news",
    )
    rsp = next(
        a for a in anchors
        if a["url"].endswith("/announcing-our-updated-responsible-scaling-policy")
    )

    assert rsp["title"] == "Responsible Scaling Policy"
    assert rsp["published_at"] == ""  # no <time> element -- no date available


def test_extract_news_anchors_excludes_bare_index_link():
    anchors = html_common.extract_news_anchors(
        _load_fixture_text("lab_anthropic_news.html"),
        base_url="https://www.anthropic.com/news",
    )
    assert not any(a["url"].rstrip("/") == "https://www.anthropic.com/news" for a in anchors)


def test_fetch_anthropic_items_parses_real_fixture_through_shared_http_layer(
    requests_mock, tmp_path
):
    requests_mock.get(
        "https://www.anthropic.com/robots.txt",
        text="User-Agent: *\nAllow: /\n",
    )
    requests_mock.get(
        anthropic.ANTHROPIC_NEWS_URL, text=_load_fixture_text("lab_anthropic_news.html")
    )

    session = http.build_session()
    items = anthropic.fetch_anthropic_items(session, cache_dir=tmp_path)

    assert len(items) == 11
    assert all(isinstance(item, Item) for item in items)
    assert all(item.source_type == "lab" and item.source_name == "anthropic" for item in items)
    assert items[0].title == "Redeploying Fable 5"


# --------------------------------------------------------------------------
# DeepSeek h1 extraction -- against the real captured article page
# --------------------------------------------------------------------------


def test_extract_h1_text_reads_real_deepseek_article_headline():
    title = html_common.extract_h1_text(_load_fixture_text("lab_deepseek_news_page.html"))
    assert title == "DeepSeek V4 Preview Release"


def test_extract_h1_text_returns_none_when_no_h1_present():
    assert html_common.extract_h1_text("<html><body><p>No headline here.</p></body></html>") is None


# --------------------------------------------------------------------------
# DeepSeek sitemap parsing + v1-vs-v2 diff -- proving new-slug detection
# --------------------------------------------------------------------------


def test_parse_sitemap_urls_v1_fixture_has_expected_count_and_news_urls():
    urls = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    assert len(urls) == 60
    news_urls = [u for u in urls if "/news/" in u]
    assert len(news_urls) == 15
    assert "https://api-docs.deepseek.com/news/news260424" in news_urls


def test_parse_sitemap_urls_v2_fixture_has_one_more_url_than_v1():
    v1 = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    v2 = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v2.xml"))
    assert len(v2) == len(v1) + 1


def test_diff_new_sitemap_urls_detects_exactly_the_one_new_slug():
    v1 = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    v2 = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v2.xml"))

    new_urls = html_common.diff_new_sitemap_urls(v1, v2)

    assert new_urls == ["https://api-docs.deepseek.com/news/news260709"]


def test_diff_new_sitemap_urls_is_empty_when_nothing_changed():
    v1 = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    assert html_common.diff_new_sitemap_urls(v1, v1) == []


def test_parse_sitemap_urls_returns_empty_list_on_unparseable_xml():
    assert html_common.parse_sitemap_urls("not xml at all <<<") == []


# --------------------------------------------------------------------------
# fetch_deepseek_items -- integration: pre-seeded "previously-seen" state
# (as if a prior run had already seen the v1 sitemap) + a mocked v2
# sitemap response + a mocked article page for the one new slug, proving
# the full sitemap-diff-then-h1-extract pipeline end to end.
# --------------------------------------------------------------------------


def test_fetch_deepseek_items_detects_new_slug_and_extracts_its_h1(
    requests_mock, tmp_path
):
    v1_urls = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    state_path = tmp_path / "deepseek_sitemap_seen.json"
    state_path.write_text(json.dumps({"urls": v1_urls}), encoding="utf-8")

    requests_mock.get(
        "https://api-docs.deepseek.com/robots.txt", status_code=404
    )
    requests_mock.get(
        deepseek.DEEPSEEK_SITEMAP_URL, text=_load_fixture_text("lab_deepseek_sitemap_v2.xml")
    )
    new_article_url = "https://api-docs.deepseek.com/news/news260709"
    requests_mock.get(
        new_article_url,
        text="<html><body><h1>DeepSeek V4.1 Update</h1></body></html>",
    )

    session = http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)

    assert len(items) == 1
    assert items[0].source_type == "lab"
    assert items[0].source_name == "deepseek"
    assert items[0].title == "DeepSeek V4.1 Update"
    assert items[0].url == new_article_url
    assert items[0].extra["slug"] == "news260709"

    # State file is updated to the full v2 URL set, so the *next* run's
    # diff is against today's sitemap, not yesterday's.
    updated_state = json.loads(state_path.read_text(encoding="utf-8"))
    v2_urls = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v2.xml"))
    assert updated_state["urls"] == v2_urls


def test_fetch_deepseek_items_returns_empty_when_nothing_new_in_sitemap(
    requests_mock, tmp_path
):
    v1_urls = html_common.parse_sitemap_urls(_load_fixture_text("lab_deepseek_sitemap_v1.xml"))
    state_path = tmp_path / "deepseek_sitemap_seen.json"
    state_path.write_text(json.dumps({"urls": v1_urls}), encoding="utf-8")

    requests_mock.get("https://api-docs.deepseek.com/robots.txt", status_code=404)
    requests_mock.get(
        deepseek.DEEPSEEK_SITEMAP_URL, text=_load_fixture_text("lab_deepseek_sitemap_v1.xml")
    )

    session = http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)

    assert items == []


def test_extract_h1_text_used_by_deepseek_fetch_matches_real_fixture_headline(
    requests_mock, tmp_path
):
    # No pre-seeded state -> first-ever run: every current sitemap URL is
    # "new", but only /news/ ones become Items, so only the real article
    # fixture's own URL needs mocking here.
    requests_mock.get("https://api-docs.deepseek.com/robots.txt", status_code=404)
    sitemap_with_one_news_url = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://api-docs.deepseek.com/news/news260424</loc></url>"
        "</urlset>"
    )
    requests_mock.get(deepseek.DEEPSEEK_SITEMAP_URL, text=sitemap_with_one_news_url)
    requests_mock.get(
        "https://api-docs.deepseek.com/news/news260424",
        text=_load_fixture_text("lab_deepseek_news_page.html"),
    )

    session = http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)

    assert len(items) == 1
    assert items[0].title == "DeepSeek V4 Preview Release"
    assert items[0].url == "https://api-docs.deepseek.com/news/news260424"


# --------------------------------------------------------------------------
# robots.txt-disallow skip case -- synthetic disallow fixture, proving both
# HTML-based lab fetchers skip cleanly rather than crash or circumvent it.
# --------------------------------------------------------------------------


def test_fetch_anthropic_items_returns_empty_list_when_robots_disallows(
    requests_mock, tmp_path
):
    # Synthetic disallow (not real Anthropic policy -- anthropic.com
    # currently allows all per the live robots.txt check recorded in
    # IMPROVEMENT_BACKLOG.md).
    requests_mock.get(
        "https://www.anthropic.com/robots.txt",
        text="User-Agent: *\nDisallow: /news\n",
    )

    session = http.build_session()
    items = anthropic.fetch_anthropic_items(session, cache_dir=tmp_path)

    assert items == []
    news_calls = [
        req for req in requests_mock.request_history
        if req.url == anthropic.ANTHROPIC_NEWS_URL
    ]
    assert news_calls == []


def test_fetch_deepseek_items_returns_empty_list_when_robots_disallows(
    requests_mock, tmp_path
):
    # Synthetic disallow (not real DeepSeek docs policy -- that host has
    # no published robots.txt at all, a 404 treated as allow-all per
    # IMPROVEMENT_BACKLOG.md/watcher/http.py's convention).
    requests_mock.get(
        "https://api-docs.deepseek.com/robots.txt",
        text="User-agent: *\nDisallow: /sitemap.xml\n",
    )

    session = http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)

    assert items == []
    sitemap_calls = [
        req for req in requests_mock.request_history
        if req.url == deepseek.DEEPSEEK_SITEMAP_URL
    ]
    assert sitemap_calls == []
    # No state file should be written either -- a disallowed fetch never
    # even reaches the point of updating "last-seen" bookkeeping.
    assert not (tmp_path / "deepseek_sitemap_seen.json").exists()
