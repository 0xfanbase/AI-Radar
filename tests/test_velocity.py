"""Tests for watcher/velocity.py.

No live network anywhere -- every item here is a plain watcher.models.Item
built directly (no fetcher involved), and every computation takes an
explicit ``now`` rather than calling ``datetime.now()``/``date.today()``
itself, so no freezegun (or any other time-mocking library) is needed:
passing a fixed ``now`` argument is sufficient for full determinism.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from jsonschema import ValidationError

from watcher.models import Item
from watcher.velocity import (
    TOPIC_KEYWORDS,
    WINDOW_DAYS,
    classify_topics,
    compute_whats_moving,
    load_whats_moving,
    save_whats_moving,
)

FIXED_NOW = datetime(2026, 7, 9, 8, 0, 0, tzinfo=timezone.utc)


def _hn_item(title: str, days_ago: float, points: int = 10) -> Item:
    published = FIXED_NOW - timedelta(days=days_ago)
    return Item(
        source_type="hn",
        source_name="hn",
        title=title,
        url=f"https://news.ycombinator.com/item?id={hash((title, days_ago)) % 10_000_000}",
        published_at=published.strftime("%Y-%m-%dT%H:%M:%SZ"),
        points=points,
    )


# --------------------------------------------------------------------------
# classify_topics
# --------------------------------------------------------------------------


def test_classify_topics_matches_expected_topic_for_keyword():
    assert "chips/compute" in classify_topics("Nvidia Unveils New GPU Cluster For AI Training")
    assert "policy" in classify_topics("New Export Controls Target AI Chips")
    assert "China" in classify_topics("DeepSeek Releases New Model From China")
    assert "funding" in classify_topics("AI Startup Raises Series B At New Valuation")
    assert "safety" in classify_topics("Researchers Warn About AI Alignment Risks")
    assert "open-source" in classify_topics("New Open Weights Model Published On GitHub")


def test_classify_topics_returns_empty_list_when_nothing_matches():
    assert classify_topics("A Completely Unrelated Headline About Gardening") == []


def test_classify_topics_whole_word_matching_avoids_substring_false_positive():
    # "ai" as a keyword substring inside an unrelated word must not trigger
    # a match -- mirrors watcher/sources/hn.py's own whole-word rationale.
    # None of our topic keyword sets use a bare "ai" token, but this checks
    # a similarly short keyword ("china") isn't substring-matched either.
    assert "China" not in classify_topics("Appalachian Trail Hikers Break Speed Record")


def test_classify_topics_can_match_multiple_topics_at_once():
    matched = classify_topics("China Announces New Export Controls On AI Chips")
    assert "China" in matched
    assert "policy" in matched
    assert "chips/compute" in matched


def test_topic_keywords_covers_every_schema_topic_exactly_once():
    # Dict order must match schemas/whats_moving.schema.json's own topic
    # enum order exactly (models, research, chips/compute, policy,
    # products, safety, open-source, China, funding).
    expected_order = [
        "models", "research", "chips/compute", "policy", "products",
        "safety", "open-source", "China", "funding",
    ]
    assert list(TOPIC_KEYWORDS.keys()) == expected_order


# --------------------------------------------------------------------------
# compute_whats_moving -- shape, day bucketing, window edges
# --------------------------------------------------------------------------


def test_compute_whats_moving_always_emits_all_nine_topics():
    payload = compute_whats_moving([], now=FIXED_NOW)
    assert payload["window_days"] == WINDOW_DAYS == 7
    assert len(payload["topics"]) == 9
    assert {t["topic"] for t in payload["topics"]} == set(TOPIC_KEYWORDS)
    # All-zero daily_counts, every topic classified "flat" (recent == older).
    for topic_entry in payload["topics"]:
        assert topic_entry["daily_counts"] == [0] * 7
        assert topic_entry["trend"] == "flat"


def test_compute_whats_moving_generated_at_is_utc_z_suffixed():
    payload = compute_whats_moving([], now=FIXED_NOW)
    assert payload["generated_at"] == "2026-07-09T08:00:00Z"


def test_compute_whats_moving_buckets_today_into_last_index():
    items = [_hn_item("New GPU Chips Announced Today", days_ago=0)]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"] == [0, 0, 0, 0, 0, 0, 1]


def test_compute_whats_moving_buckets_six_days_ago_into_first_index():
    items = [_hn_item("New GPU Chips Announced Last Week", days_ago=6)]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"] == [1, 0, 0, 0, 0, 0, 0]


def test_compute_whats_moving_excludes_items_older_than_window():
    items = [_hn_item("Old GPU Chips Story From Way Back", days_ago=7.5)]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"] == [0] * 7


def test_compute_whats_moving_ignores_non_hn_items():
    non_hn = Item(
        source_type="lab",
        source_name="openai",
        title="New GPU Chips Partnership Announced",
        url="https://openai.com/blog/chips",
        published_at=FIXED_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    payload = compute_whats_moving([non_hn], now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"] == [0] * 7


def test_compute_whats_moving_skips_unparseable_published_at_without_raising():
    bad = Item(
        source_type="hn",
        source_name="hn",
        title="New GPU Chips Story With Bad Timestamp",
        url="https://news.ycombinator.com/item?id=1",
        published_at="not-a-real-timestamp",
        points=10,
    )
    payload = compute_whats_moving([bad], now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"] == [0] * 7


def test_compute_whats_moving_one_mention_can_increment_multiple_topics():
    items = [_hn_item("China Unveils New Export Controls On AI Chips", days_ago=0)]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    by_topic = {t["topic"]: t["daily_counts"] for t in payload["topics"]}
    assert by_topic["China"][-1] == 1
    assert by_topic["policy"][-1] == 1
    assert by_topic["chips/compute"][-1] == 1
    assert by_topic["safety"][-1] == 0


# --------------------------------------------------------------------------
# Trend classification
# --------------------------------------------------------------------------


def test_trend_accelerating_when_recent_three_days_exceed_oldest_three():
    items = [_hn_item(f"New GPU Chips Headline {n}", days_ago=d) for n, d in enumerate([0, 0, 1])]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["trend"] == "accelerating"


def test_trend_cooling_when_oldest_three_days_exceed_recent_three():
    items = [_hn_item(f"New GPU Chips Headline {n}", days_ago=d) for n, d in enumerate([6, 6, 5])]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["trend"] == "cooling"


def test_trend_flat_when_recent_and_oldest_three_days_are_equal():
    items = [
        _hn_item("New GPU Chips Headline A", days_ago=6),
        _hn_item("New GPU Chips Headline B", days_ago=0),
    ]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["trend"] == "flat"


def test_trend_ignores_middle_day_entirely():
    # A spike only on the middle day (index 3, "3 days ago") must not
    # affect the accelerating/cooling/flat verdict either way.
    items = [_hn_item(f"New GPU Chips Middle Spike {n}", days_ago=3) for n in range(5)]
    payload = compute_whats_moving(items, now=FIXED_NOW)
    chips = next(t for t in payload["topics"] if t["topic"] == "chips/compute")
    assert chips["daily_counts"][3] == 5
    assert chips["trend"] == "flat"


# --------------------------------------------------------------------------
# load_whats_moving / save_whats_moving -- schema-valid round trip
# --------------------------------------------------------------------------


def test_load_whats_moving_missing_file_returns_none(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_whats_moving(missing) is None


def test_save_then_load_round_trips_and_is_schema_valid(tmp_path):
    path = tmp_path / "whats_moving.json"
    items = [_hn_item("New GPU Chips Story Today", days_ago=0)]

    saved = save_whats_moving(items, now=FIXED_NOW, path=path)
    assert path.is_file()

    loaded = load_whats_moving(path)
    assert loaded == saved


def test_save_whats_moving_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "whats_moving.json"
    save_whats_moving([], now=FIXED_NOW, path=path)
    assert path.is_file()


def test_load_whats_moving_rejects_schema_invalid_file(tmp_path):
    path = tmp_path / "whats_moving.json"
    bad = {"generated_at": "2026-07-09T08:00:00Z", "window_days": 5, "topics": []}  # window_days must be 7
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_whats_moving(path)


def test_save_whats_moving_output_is_schema_valid_via_full_pipeline(tmp_path):
    path = tmp_path / "whats_moving.json"
    items = [
        _hn_item("China Export Controls On AI Chips", days_ago=0),
        _hn_item("Startup Raises Series A Funding Round", days_ago=2),
        _hn_item("New Open Weights Model On GitHub", days_ago=5),
    ]
    # save_whats_moving validates internally (would raise if invalid); a
    # second explicit load-and-validate round trip is the real assertion.
    save_whats_moving(items, now=FIXED_NOW, path=path)
    reloaded = load_whats_moving(path)
    assert reloaded is not None
    assert len(reloaded["topics"]) == 9
