"""Regression tests for the 2026-07 watcher-robustness pass.

Covers, in one place (fixture/mock-based, per tests/conftest.py's
network-block convention):

- robots.txt policy memoization + path-scoped arXiv API exemption
  (watcher/http.py::check_robots_allowed)
- the 304-with-no-cache-entry poisoning fix (watcher/http.py::fetch)
- age-based ETag-cache pruning (watcher/http.py::prune_cache)
- HN per-item malformed-timestamp tolerance, Algolia truncation warning,
  and hour-quantized window URLs (watcher/sources/hn.py)
- naive-timestamp coercion in ranking (watcher/ranking.py)
- DeepSeek's transient-article-failure retry -- "seen" recorded only
  after successful handling (watcher/sources/labs/deepseek.py)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from watcher import http as watcher_http
from watcher import ranking
from watcher.sources import hn
from watcher.sources.labs import deepseek

FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# check_robots_allowed -- memoization + path-scoped exemption (B15, B12)
# --------------------------------------------------------------------------


def test_robots_policy_fetched_once_per_host_per_run(requests_mock):
    requests_mock.get(
        "https://example.com/robots.txt",
        text="User-agent: *\nDisallow: /private/\n",
    )
    assert watcher_http.check_robots_allowed("https://example.com/public/a")
    assert watcher_http.check_robots_allowed("https://example.com/public/b")
    assert not watcher_http.check_robots_allowed("https://example.com/private/x")
    # One robots.txt fetch total -- the verdict is still evaluated per
    # full URL against the memoized parser.
    assert requests_mock.call_count == 1


def test_robots_fetch_failure_memoized_as_skip_for_the_run(requests_mock):
    requests_mock.get(
        "https://down.example/robots.txt", exc=requests.exceptions.ConnectionError
    )
    assert not watcher_http.check_robots_allowed("https://down.example/a")
    assert not watcher_http.check_robots_allowed("https://down.example/b")
    assert requests_mock.call_count == 1


def test_robots_cache_reset_between_tests_actually_works(requests_mock):
    # The previous test memoized down.example as a failure; the autouse
    # conftest fixture must have cleared it so this test's own mock wins.
    requests_mock.get("https://down.example/robots.txt", status_code=404)
    assert watcher_http.check_robots_allowed("https://down.example/a")


def test_arxiv_api_path_is_exempt_without_any_robots_fetch(requests_mock):
    assert watcher_http.check_robots_allowed(
        "https://export.arxiv.org/api/query?search_query=cat:cs.AI"
    )
    assert requests_mock.call_count == 0


def test_arxiv_non_api_path_is_fully_robots_gated(requests_mock):
    # CLAUDE.md: the exemption "never applies to HTML/website fetching."
    # A non-/api/ URL on the exempt host must hit robots.txt like any
    # other URL -- previously the whole host was exempt.
    requests_mock.get(
        "https://export.arxiv.org/robots.txt",
        text="User-agent: *\nDisallow: /\n",
    )
    assert not watcher_http.check_robots_allowed("https://export.arxiv.org/abs/1234.5678")
    assert requests_mock.call_count == 1


# --------------------------------------------------------------------------
# fetch() -- 304 with no cache entry (B20)
# --------------------------------------------------------------------------


def test_304_without_cache_entry_refetches_instead_of_caching_empty_body(
    requests_mock, tmp_path
):
    url = "https://example.com/feed.xml"
    requests_mock.get(
        url,
        [
            {"status_code": 304},
            {"status_code": 200, "text": "real body"},
        ],
    )
    session = watcher_http.build_session()
    result = watcher_http.fetch(session, url, cache_dir=tmp_path)
    assert result.text == "real body"
    assert result.status_code == 200
    # The cache entry written must carry the real body, not the 304's "".
    cached = json.loads(
        watcher_http._cache_path(url, tmp_path).read_text(encoding="utf-8")
    )
    assert cached["body"] == "real body"


def test_repeated_304_without_cache_entry_raises_instead_of_caching(
    requests_mock, tmp_path
):
    url = "https://example.com/feed.xml"
    requests_mock.get(url, status_code=304)
    session = watcher_http.build_session()
    with pytest.raises(requests.HTTPError):
        watcher_http.fetch(session, url, cache_dir=tmp_path)
    assert not watcher_http._cache_path(url, tmp_path).exists()


# --------------------------------------------------------------------------
# prune_cache (A11's eviction half)
# --------------------------------------------------------------------------


def _write_cache_entry(cache_dir: Path, name: str, fetched_at) -> Path:
    path = cache_dir / name
    entry = {"etag": None, "last_modified": None, "body": "x"}
    if fetched_at is not None:
        entry["fetched_at"] = fetched_at
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def test_prune_cache_removes_stale_keeps_fresh_and_foreign_files(tmp_path):
    stale_name = "a" * 64 + ".json"
    fresh_name = "b" * 64 + ".json"
    garbage_name = "c" * 64 + ".json"
    stale = _write_cache_entry(
        tmp_path, stale_name, (FIXED_NOW - timedelta(days=30)).isoformat()
    )
    fresh = _write_cache_entry(
        tmp_path, fresh_name, (FIXED_NOW - timedelta(days=1)).isoformat()
    )
    garbage = tmp_path / garbage_name
    garbage.write_text("not json", encoding="utf-8")
    # A sibling STATE file sharing data/.cache (e.g. the DeepSeek
    # sitemap-seen list) must never be touched by pruning.
    state = tmp_path / "deepseek_sitemap_seen.json"
    state.write_text('{"urls": []}', encoding="utf-8")

    removed = watcher_http.prune_cache(tmp_path, max_age_days=14, now=FIXED_NOW)
    assert removed == 2
    assert not stale.exists()
    assert not garbage.exists()
    assert fresh.exists()
    assert state.exists()


def test_prune_cache_missing_directory_is_a_noop(tmp_path):
    assert watcher_http.prune_cache(tmp_path / "nope", now=FIXED_NOW) == 0


# --------------------------------------------------------------------------
# HN fetcher -- per-item timestamp tolerance (C5), truncation warning
# (B18), hour-quantized window URLs (A11)
# --------------------------------------------------------------------------


def _hn_payload(hits, nb_hits=None):
    payload = {"hits": hits}
    payload["nbHits"] = nb_hits if nb_hits is not None else len(hits)
    return json.dumps(payload)


def _hn_hit(object_id, title, created_at, points=100):
    return {
        "objectID": object_id,
        "title": title,
        "url": f"https://example.com/{object_id}",
        "created_at": created_at,
        "points": points,
        "num_comments": 5,
        "author": "someone",
    }


def test_one_malformed_hn_timestamp_skips_the_item_not_the_source(
    requests_mock, caplog
):
    requests_mock.get("https://hn.algolia.com/robots.txt", status_code=404)
    requests_mock.get(
        hn.SEARCH_BY_DATE_URL,
        text=_hn_payload(
            [
                _hn_hit("1", "New AI model ships", "not-a-timestamp"),
                _hn_hit("2", "Another AI story", "2026-07-09T01:00:00Z"),
            ]
        ),
    )
    session = watcher_http.build_session()
    with caplog.at_level("WARNING"):
        items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=Path("/nonexistent"))
    # Previously the malformed timestamp raised ValueError and aborted
    # the WHOLE HN source for the day; now only that item is dropped.
    assert [i.extra["objectID"] for i in items] == ["2"]
    assert any("malformed created_at" in rec.message for rec in caplog.records)


def test_algolia_truncation_cap_is_warned_about(requests_mock, caplog, tmp_path):
    requests_mock.get("https://hn.algolia.com/robots.txt", status_code=404)
    requests_mock.get(
        hn.SEARCH_BY_DATE_URL,
        text=_hn_payload(
            [_hn_hit("1", "AI story", "2026-07-09T01:00:00Z")], nb_hits=2326
        ),
    )
    session = watcher_http.build_session()
    with caplog.at_level("WARNING"):
        hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)
    assert any("truncated" in rec.message for rec in caplog.records)


def test_hn_window_urls_are_identical_within_the_same_hour(requests_mock, tmp_path):
    requests_mock.get("https://hn.algolia.com/robots.txt", status_code=404)
    requests_mock.get(hn.SEARCH_BY_DATE_URL, text=_hn_payload([]))
    session = watcher_http.build_session()

    hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)
    first_urls = [
        r.url for r in requests_mock.request_history if "search_by_date" in r.url
    ]
    hn.fetch_hn_items(
        session, now=FIXED_NOW + timedelta(minutes=25, seconds=13), cache_dir=tmp_path
    )
    all_urls = [
        r.url for r in requests_mock.request_history if "search_by_date" in r.url
    ]
    second_urls = all_urls[len(first_urls):]
    # Un-quantized, the second run's URLs embedded a different `now` and
    # the ETag cache could never hit; floored to the hour they're
    # byte-identical.
    assert first_urls == second_urls


# --------------------------------------------------------------------------
# ranking -- naive timestamp coercion (B14)
# --------------------------------------------------------------------------


def test_parse_iso8601_coerces_naive_timestamps_to_utc():
    parsed = ranking._parse_iso8601("2026-07-09T01:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    # Comparable against an aware `now` without raising TypeError.
    assert (FIXED_NOW - parsed).total_seconds() == 3 * 3600


def test_parse_iso8601_leaves_aware_timestamps_alone():
    parsed = ranking._parse_iso8601("2026-07-09T01:00:00Z")
    assert parsed == datetime(2026, 7, 9, 1, 0, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# DeepSeek fetcher -- transient failure never loses a story (B5)
# --------------------------------------------------------------------------

_SITEMAP_TMPL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
)


def _sitemap_xml(*urls: str) -> str:
    return _SITEMAP_TMPL.format(
        urls="".join(f"<url><loc>{u}</loc></url>" for u in urls)
    )


def test_deepseek_transient_article_failure_is_retried_next_run(requests_mock, tmp_path):
    ok_url = "https://api-docs.deepseek.com/news/news-good"
    flaky_url = "https://api-docs.deepseek.com/news/news-flaky"
    sitemap = _sitemap_xml(ok_url, flaky_url)

    requests_mock.get("https://api-docs.deepseek.com/robots.txt", status_code=404)
    requests_mock.get(deepseek.DEEPSEEK_SITEMAP_URL, text=sitemap)
    requests_mock.get(ok_url, text="<html><h1>Good story</h1></html>")
    requests_mock.get(flaky_url, exc=requests.exceptions.ConnectionError)

    session = watcher_http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)
    assert [i.title for i in items] == ["Good story"]

    # The flaky URL must NOT be recorded as seen -- previously the whole
    # sitemap was stored up front and one transient failure lost the
    # story permanently.
    state = json.loads(
        (tmp_path / "deepseek_sitemap_seen.json").read_text(encoding="utf-8")
    )
    assert ok_url in state["urls"]
    assert flaky_url not in state["urls"]

    # Next run: the article is reachable now -- it must surface as new.
    watcher_http.clear_robots_cache()
    requests_mock.get(flaky_url, text="<html><h1>Flaky story recovered</h1></html>")
    items_next = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)
    assert [i.title for i in items_next] == ["Flaky story recovered"]
    state_after = json.loads(
        (tmp_path / "deepseek_sitemap_seen.json").read_text(encoding="utf-8")
    )
    assert flaky_url in state_after["urls"]


def test_deepseek_non_news_urls_are_marked_seen_without_fetching(requests_mock, tmp_path):
    non_news = "https://api-docs.deepseek.com/guides/tool_calls"
    requests_mock.get("https://api-docs.deepseek.com/robots.txt", status_code=404)
    requests_mock.get(deepseek.DEEPSEEK_SITEMAP_URL, text=_sitemap_xml(non_news))
    session = watcher_http.build_session()
    items = deepseek.fetch_deepseek_items(session, cache_dir=tmp_path)
    assert items == []
    state = json.loads(
        (tmp_path / "deepseek_sitemap_seen.json").read_text(encoding="utf-8")
    )
    assert non_news in state["urls"]
