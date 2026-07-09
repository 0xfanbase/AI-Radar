"""Tests for watcher/sources/hn.py.

`fixtures/hn_algolia_response.json` is a REAL response captured live from
`https://hn.algolia.com/api/v1/search_by_date` (tags=story,
created_at_i > now-48h, hitsPerPage=150, page=0) on 2026-07-09 -- not
fabricated. Every test in this file is fixture/mock-based; `tests/conftest.py`'s
autouse guard would raise on any real network call here regardless.

A handful of small synthetic HN-hit-shaped dicts are also used below (not
claimed as "the live response") purely to exercise boundary conditions
(the broad-pool/final-candidacy threshold edges, the no-`url` fallback,
window merging) that the one real fixture snapshot happens not to contain
-- the same pattern `tests/test_arxiv_fetch.py` already uses for its own
inline edge-case snippets.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watcher import config, http
from watcher.models import Item
from watcher.sources import hn

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "hn_algolia_response.json"

# Fixed reference "now" a couple of hours after the fixture was captured,
# so every test in this file gets deterministic ages/velocities regardless
# of when the suite actually runs.
FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _load_fixture_payload() -> dict:
    with FIXTURE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_fixture_text() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _mock_robots_allow(requests_mock) -> None:
    requests_mock.get("https://hn.algolia.com/robots.txt", status_code=404)


def _mock_search_by_date(requests_mock, text: str) -> None:
    # No query string on the registered URL -- requests-mock then matches
    # any query, so this one canned response answers every windowed
    # request fetch_hn_items makes (see module docstring).
    requests_mock.get(hn.SEARCH_BY_DATE_URL, text=text)


# --------------------------------------------------------------------------
# _matches_keywords -- whole-word, not substring, matching
# --------------------------------------------------------------------------


def test_matches_keywords_true_for_standalone_ai_mention():
    assert hn._matches_keywords("AI is the future of everything")


def test_matches_keywords_false_for_ai_embedded_in_another_word():
    # Real HN title fetched live while building this fetcher: "ai" is a
    # substring of "Explained" but the title is not actually AI-relevant.
    assert not hn._matches_keywords("Chat Control 1.0 and 2.0 Explained")
    assert not hn._matches_keywords("Why skilled workers leave again")
    assert not hn._matches_keywords("This is a fair trial about a chair")


def test_matches_keywords_true_for_lab_name_and_multiword_phrase():
    assert hn._matches_keywords("Mistral's Robostral Navigate model")
    assert hn._matches_keywords("New chip export rules announced")


def test_matches_keywords_is_case_insensitive():
    assert hn._matches_keywords("OPENAI ships a new GPT release")


# --------------------------------------------------------------------------
# _item_url -- fallback to the HN discussion thread for text-only posts
# --------------------------------------------------------------------------


def test_item_url_uses_external_url_when_present():
    hit = {"objectID": "1", "url": "https://example.test/story"}
    assert hn._item_url(hit) == "https://example.test/story"


def test_item_url_falls_back_to_hn_thread_when_no_url():
    hit = {"objectID": "48838907", "title": "Ask HN: something"}
    assert hn._item_url(hit) == "https://news.ycombinator.com/item?id=48838907"


# --------------------------------------------------------------------------
# _search_windows -- lookback split to dodge Algolia's 1000-hit cap
# --------------------------------------------------------------------------


def test_search_windows_covers_full_lookback_without_gaps_or_overlap():
    now_ts = 1_000_000_000
    windows = hn._search_windows(now_ts, lookback_hours=48, window_hours=12)

    assert len(windows) == 4
    # Non-overlapping and contiguous: each window's lower bound equals the
    # previous window's lower bound (windows are yielded newest-first).
    for (lower, upper), (next_lower, next_upper) in zip(windows, windows[1:]):
        assert lower == next_upper
    assert windows[0][1] == now_ts
    assert windows[-1][0] == now_ts - 48 * 3600


def test_search_windows_handles_lookback_smaller_than_window():
    now_ts = 1_000_000_000
    windows = hn._search_windows(now_ts, lookback_hours=6, window_hours=12)

    assert windows == [(now_ts - 6 * 3600, now_ts)]


def test_search_windows_handles_non_evenly_divisible_lookback():
    now_ts = 1_000_000_000
    windows = hn._search_windows(now_ts, lookback_hours=30, window_hours=12)

    assert len(windows) == 3
    assert windows[-1][0] == now_ts - 30 * 3600
    assert windows[0][1] == now_ts


def test_build_search_url_shape():
    url = hn._build_search_url(100, 200)

    assert url.startswith(f"{hn.SEARCH_BY_DATE_URL}?")
    assert "tags=story" in url
    assert "created_at_i%3E100" in url  # ">" percent-encoded
    assert "created_at_i%3C%3D200" in url  # "<=" percent-encoded
    assert "hitsPerPage=1000" in url


# --------------------------------------------------------------------------
# fetch_hn_items -- robots gate
# --------------------------------------------------------------------------


def test_fetch_hn_items_returns_empty_list_when_robots_disallows(requests_mock, tmp_path):
    requests_mock.get(
        "https://hn.algolia.com/robots.txt",
        text="User-agent: *\nDisallow: /\n",
    )

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert items == []
    search_calls = [
        req for req in requests_mock.request_history
        if req.url.startswith(hn.SEARCH_BY_DATE_URL)
    ]
    assert search_calls == []


def test_fetch_hn_items_proceeds_when_robots_404s(requests_mock, tmp_path):
    # Confirmed live on 2026-07-09: hn.algolia.com/robots.txt itself 404s,
    # which watcher.http.check_robots_allowed treats as allow-all.
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, json.dumps({"hits": []}))

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert items == []
    search_calls = [
        req for req in requests_mock.request_history
        if req.url.startswith(hn.SEARCH_BY_DATE_URL)
    ]
    assert len(search_calls) == 4  # one per 12h window across a 48h lookback


# --------------------------------------------------------------------------
# fetch_hn_items -- against the real captured fixture
# --------------------------------------------------------------------------


def test_fetch_hn_items_applies_full_pipeline_to_real_fixture(requests_mock, tmp_path):
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _load_fixture_text())

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    # Computed by hand against the real fixture with FIXED_NOW: exactly
    # three AI-relevant stories clear broad-pool (>=20 pts) + keyword +
    # final-candidacy (>=50 pts OR velocity>=5.0) out of this 150-hit
    # slice, all three via the points>=50 branch.
    assert [item.title for item in items] == [
        "I Think I Have LLM Burnout",
        "We made Grok 4.5, GPT-5.5, and Claude build the same apps",
        "Suspecting AI cheating, Ivy League prof ordered in-person final; scores fell 50%",
    ]
    assert all(isinstance(item, Item) for item in items)
    assert all(item.source_type == "hn" for item in items)
    assert all(item.source_name == "hn" for item in items)
    # Sorted by points descending.
    assert [item.points for item in items] == [158, 100, 92]


def test_fetch_hn_items_normalizes_top_real_item_fully(requests_mock, tmp_path):
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _load_fixture_text())

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)
    top = items[0]

    assert top.title == "I Think I Have LLM Burnout"
    assert top.url == "https://www.alecscollon.com/blog/llm-burnout/"
    assert top.published_at == "2026-07-09T01:56:28Z"
    assert top.points == 158
    assert top.num_comments == 107
    assert top.extra["objectID"] == "48839984"
    assert top.extra["author"] == "sosodev"
    expected_velocity = 158 / (
        (FIXED_NOW - hn._parse_created_at("2026-07-09T01:56:28Z")).total_seconds() / 3600.0
    )
    assert top.extra["velocity_pts_per_hour"] == round(expected_velocity, 3)


def test_fetch_hn_items_deduplicates_hits_seen_in_multiple_windows(requests_mock, tmp_path):
    # The same canned payload answers all four windowed requests, so every
    # hit is "seen" up to four times across windows; the objectID-keyed
    # merge must still produce exactly one Item per qualifying story.
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _load_fixture_text())

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    object_ids = [item.extra["objectID"] for item in items]
    assert len(object_ids) == len(set(object_ids))


def test_fetch_hn_items_sends_descriptive_user_agent(requests_mock, tmp_path):
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _load_fixture_text())

    session = http.build_session()
    hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    search_request = next(
        req for req in requests_mock.request_history
        if req.url.startswith(hn.SEARCH_BY_DATE_URL)
    )
    assert search_request.headers["User-Agent"] == config.USER_AGENT


# --------------------------------------------------------------------------
# fetch_hn_items -- synthetic boundary cases (broad pool vs. final
# candidacy thresholds), using small hand-built HN-hit-shaped dicts
# --------------------------------------------------------------------------


def _synthetic_payload(hits: list[dict]) -> str:
    return json.dumps({"hits": hits})


def test_fetch_hn_items_excludes_below_broad_pool_threshold_even_with_high_velocity(
    requests_mock, tmp_path
):
    # 19 points, posted 6 minutes ago: velocity = 19/0.1 = 190, comfortably
    # above the velocity threshold, but points fall one short of the
    # broad-pool pre-filter (20) -- excluded at stage 1, never reaches the
    # final-candidacy check.
    hit = {
        "objectID": "1001",
        "title": "New AI model released today",
        "url": "https://example.test/1001",
        "points": 19,
        "num_comments": 2,
        "author": "tester",
        "created_at": "2026-07-09T03:54:00Z",
    }
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _synthetic_payload([hit]))

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert items == []


def test_fetch_hn_items_includes_velocity_only_qualifier_above_broad_pool_floor(
    requests_mock, tmp_path
):
    # 25 points (clears the 20-point broad pool floor, below the 50-point
    # final-candidacy floor), posted 1 hour ago: velocity = 25.0 >= 5.0 --
    # qualifies via the velocity branch alone.
    hit = {
        "objectID": "1002",
        "title": "AI startup raises new funding round",
        "url": "https://example.test/1002",
        "points": 25,
        "num_comments": 4,
        "author": "tester",
        "created_at": "2026-07-09T03:00:00Z",
    }
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _synthetic_payload([hit]))

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert len(items) == 1
    assert items[0].points == 25
    assert items[0].extra["velocity_pts_per_hour"] == pytest.approx(25.0)


def test_fetch_hn_items_excludes_low_points_low_velocity_non_ai_or_ai(requests_mock, tmp_path):
    # 60 points but posted 2 days ago (age well beyond the lookback window
    # would already exclude it server-side; here posted at the very edge
    # of the lookback with low velocity) still qualifies via points alone.
    old_but_high_points = {
        "objectID": "1003",
        "title": "AI regulation debate continues",
        "url": "https://example.test/1003",
        "points": 60,
        "num_comments": 10,
        "author": "tester",
        "created_at": "2026-07-08T00:00:00Z",
    }
    low_everything = {
        "objectID": "1004",
        "title": "AI newsletter roundup",
        "url": "https://example.test/1004",
        "points": 21,
        "num_comments": 1,
        "author": "tester",
        "created_at": "2026-07-08T00:00:00Z",
    }
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(
        requests_mock, _synthetic_payload([old_but_high_points, low_everything])
    )

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert [item.extra["objectID"] for item in items] == ["1003"]


def test_fetch_hn_items_excludes_non_ai_keyword_story_regardless_of_points(
    requests_mock, tmp_path
):
    hit = {
        "objectID": "1005",
        "title": "A Roomba recorded a woman on the toilet",
        "url": "https://example.test/1005",
        "points": 500,
        "num_comments": 300,
        "author": "tester",
        "created_at": "2026-07-09T02:00:00Z",
    }
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _synthetic_payload([hit]))

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert items == []


def test_fetch_hn_items_falls_back_to_hn_thread_url_for_self_post(requests_mock, tmp_path):
    hit = {
        "objectID": "1006",
        "title": "Ask HN: best AI agent for research?",
        "points": 55,
        "num_comments": 20,
        "author": "tester",
        "created_at": "2026-07-09T02:00:00Z",
        # No "url" field -- a self/text post.
    }
    _mock_robots_allow(requests_mock)
    _mock_search_by_date(requests_mock, _synthetic_payload([hit]))

    session = http.build_session()
    items = hn.fetch_hn_items(session, now=FIXED_NOW, cache_dir=tmp_path)

    assert len(items) == 1
    assert items[0].url == "https://news.ycombinator.com/item?id=1006"


# --------------------------------------------------------------------------
# Opt-in live smoke test -- excluded by default (pytest.ini: -m "not
# live"); run explicitly with `python -m pytest -m live`.
# --------------------------------------------------------------------------


@pytest.mark.live
def test_live_hn_algolia_api_is_reachable_and_returns_parseable_items():
    """Confirms the real endpoint still returns parseable, filterable
    hits and that robots.txt still allow-alls (via 404) -- does not assert
    on any specific story, since real HN content changes constantly.
    """
    session = http.build_session()
    items = hn.fetch_hn_items(session)

    assert isinstance(items, list)
    assert all(isinstance(item, Item) for item in items)
    assert all(item.source_type == "hn" for item in items)
