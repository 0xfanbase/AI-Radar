"""Tests for watcher/ranking.py.

Uses its own local, minimal fake ``Item``/``Cluster`` fixtures (``FakeItem``/
``FakeCluster`` below) rather than importing ``watcher.clustering`` --
that module is being built concurrently in this same phase and its exact
``Cluster`` shape isn't this file's concern. ``watcher/ranking.py`` only
depends on the duck-typed shape described in its own module docstring
(an ``.items`` iterable of Item-alikes exposing ``source_type``/
``source_name``/``published_at``/``points``/``url``), which these fakes
satisfy, so this file is fully independent of clustering's actual
implementation.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from watcher.config import HN_VELOCITY_SCORE_FLOOR, MAX_QUEUE_SIZE, PRIMARY_SOURCE_WEIGHTS
from watcher.ranking import (
    RankedCluster,
    _tie_break_hash,
    cross_source_count,
    hn_velocity_score,
    primary_source_weight,
    rank_clusters,
    score_cluster,
)

# Fixed reference "now" so every age/velocity computation in this file is
# deterministic regardless of when the suite actually runs -- same pattern
# tests/test_hn_fetch.py uses for its own FIXED_NOW.
FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_ago(hours: float) -> str:
    return _iso(FIXED_NOW - timedelta(hours=hours))


# --------------------------------------------------------------------------
# Local fake Item/Cluster fixtures
# --------------------------------------------------------------------------


@dataclass
class FakeItem:
    source_type: str
    source_name: str
    published_at: str
    url: str = "https://example.com/default"
    points: int | None = None


@dataclass
class FakeCluster:
    items: list[FakeItem] = field(default_factory=list)
    # Deliberately optional and unset by default: most tests below exercise
    # ranking.py's self-computed fallback tie-break (a bare-bones cluster
    # with no .cluster_hash of its own). A couple of tests set this
    # explicitly to prove ranking.py *prefers* a cluster's own attribute
    # when present -- mirroring watcher.clustering.Cluster, which exposes
    # a real one.
    cluster_hash: str | None = None


def _single_item_cluster(
    url: str,
    source_type: str,
    source_name: str,
    published_at: str,
    points: int | None = None,
) -> FakeCluster:
    return FakeCluster(items=[FakeItem(source_type, source_name, published_at, url, points)])


# --------------------------------------------------------------------------
# primary_source_weight
# --------------------------------------------------------------------------


def test_primary_source_weight_is_max_over_cluster_items():
    cluster = FakeCluster(
        items=[
            FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/1"),
            FakeItem("hn", "hn", _hours_ago(1), "https://news.ycombinator.com/item?id=1", points=10),
            FakeItem("lab", "openai", _hours_ago(1), "https://openai.com/blog/1"),
        ]
    )
    assert primary_source_weight(cluster) == PRIMARY_SOURCE_WEIGHTS["lab"] == 3.0


def test_primary_source_weight_single_hn_item():
    cluster = FakeCluster(
        items=[FakeItem("hn", "hn", _hours_ago(1), "https://news.ycombinator.com/item?id=2", points=5)]
    )
    assert primary_source_weight(cluster) == PRIMARY_SOURCE_WEIGHTS["hn"] == 1.0


# --------------------------------------------------------------------------
# cross_source_count
# --------------------------------------------------------------------------


def test_cross_source_count_counts_distinct_source_names_only():
    cluster = FakeCluster(
        items=[
            FakeItem("lab", "openai", _hours_ago(1), "https://openai.com/blog/1"),
            FakeItem("lab", "openai", _hours_ago(2), "https://openai.com/blog/2"),  # same source_name again
            FakeItem("hn", "hn", _hours_ago(1), "https://news.ycombinator.com/item?id=3", points=10),
        ]
    )
    assert cross_source_count(cluster) == 2


def test_cross_source_count_three_distinct_sources():
    cluster = FakeCluster(
        items=[
            FakeItem("lab", "openai", _hours_ago(1), "https://openai.com/blog/1"),
            FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/2"),
            FakeItem("hn", "hn", _hours_ago(1), "https://news.ycombinator.com/item?id=4", points=10),
        ]
    )
    assert cross_source_count(cluster) == 3


def test_cross_source_count_single_item_cluster():
    cluster = FakeCluster(items=[FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/3")])
    assert cross_source_count(cluster) == 1


# --------------------------------------------------------------------------
# hn_velocity_score
# --------------------------------------------------------------------------


def test_hn_velocity_score_exact_formula_when_hn_item_present():
    # 4 hours old, 100 points -> 100 / max(4, 1) = 25.0
    cluster = _single_item_cluster(
        "https://news.ycombinator.com/item?id=5", "hn", "hn", _hours_ago(4), points=100
    )
    assert hn_velocity_score(cluster, now=FIXED_NOW) == pytest.approx(25.0)


def test_hn_velocity_score_floor_applied_when_no_hn_item_present():
    cluster = FakeCluster(
        items=[
            FakeItem("lab", "openai", _hours_ago(1), "https://openai.com/blog/3"),
            FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/4"),
        ]
    )
    assert hn_velocity_score(cluster, now=FIXED_NOW) == HN_VELOCITY_SCORE_FLOOR == 0.05


def test_hn_velocity_score_age_hours_floor_at_one_hour():
    # Posted only 30 minutes ago, 10 points. Without the age>=1 floor this
    # would be 10 / 0.5 = 20.0; the floor caps age at 1 hour so it must be
    # 10 / 1 = 10.0 instead.
    cluster = _single_item_cluster(
        "https://news.ycombinator.com/item?id=6", "hn", "hn", _hours_ago(0.5), points=10
    )
    assert hn_velocity_score(cluster, now=FIXED_NOW) == pytest.approx(10.0)


def test_hn_velocity_score_picks_highest_points_hn_item_when_multiple_present():
    # If two HN items somehow land in the same cluster, the strongest
    # signal (highest points) is used, not the first/last in list order.
    cluster = FakeCluster(
        items=[
            FakeItem("hn", "hn", _hours_ago(2), "https://news.ycombinator.com/item?id=7", points=10),  # velocity 5.0
            FakeItem("hn", "hn", _hours_ago(5), "https://news.ycombinator.com/item?id=8", points=50),  # velocity 10.0 <- wins
        ]
    )
    assert hn_velocity_score(cluster, now=FIXED_NOW) == pytest.approx(10.0)


def test_hn_velocity_score_floor_applied_when_hn_published_at_unparseable():
    # Defensive path: an HN item with an empty/unparseable published_at
    # (should not happen per watcher/sources/hn.py's own contract, but
    # ranking must degrade gracefully rather than crash).
    cluster = _single_item_cluster(
        "https://news.ycombinator.com/item?id=9", "hn", "hn", "", points=999
    )
    assert hn_velocity_score(cluster, now=FIXED_NOW) == HN_VELOCITY_SCORE_FLOOR


# --------------------------------------------------------------------------
# score_cluster -- exact formula, all three factors combined
# --------------------------------------------------------------------------


def test_score_cluster_exact_formula():
    cluster = FakeCluster(
        items=[
            FakeItem("lab", "openai", _hours_ago(1), "https://openai.com/blog/4"),
            FakeItem("hn", "hn", _hours_ago(2), "https://news.ycombinator.com/item?id=10", points=20),
        ]
    )
    # primary_source_weight = 3.0 (lab), cross_source_count = 2
    # (openai, hn), hn_velocity_score = 20 / max(2, 1) = 10.0
    expected = 3.0 * 2 * 10.0
    assert score_cluster(cluster, now=FIXED_NOW) == pytest.approx(expected)
    assert score_cluster(cluster, now=FIXED_NOW) == pytest.approx(
        primary_source_weight(cluster)
        * cross_source_count(cluster)
        * hn_velocity_score(cluster, now=FIXED_NOW)
    )


def test_score_cluster_no_hn_item_uses_floor_in_product():
    cluster = FakeCluster(items=[FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/5")])
    # primary_source_weight = 2.0, cross_source_count = 1, floor = 0.05
    assert score_cluster(cluster, now=FIXED_NOW) == pytest.approx(2.0 * 1 * 0.05)


# --------------------------------------------------------------------------
# rank_clusters -- descending sort, tie-break, top-8, determinism
# --------------------------------------------------------------------------


def test_rank_clusters_sorts_descending_by_score():
    low = _single_item_cluster(
        "https://news.ycombinator.com/item?id=11", "hn", "hn", _hours_ago(1), points=1
    )  # 1*1*(1/1) = 1.0
    mid = _single_item_cluster("https://arxiv.org/abs/6", "arxiv", "arxiv", _hours_ago(1))  # 2*1*0.05 = 0.1
    high = _single_item_cluster("https://openai.com/blog/5", "lab", "openai", _hours_ago(1))  # 3*1*0.05 = 0.15

    ranked = rank_clusters([low, mid, high], now=FIXED_NOW)
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert [r.cluster for r in ranked] == [low, high, mid]
    assert [r.rank for r in ranked] == [1, 2, 3]


def test_rank_clusters_tie_break_by_earliest_published_at():
    # Both clusters: single arxiv item, no HN item -> identical score
    # (2.0 * 1 * HN_VELOCITY_SCORE_FLOOR) regardless of published_at.
    earlier = _single_item_cluster("https://arxiv.org/abs/7", "arxiv", "arxiv", _hours_ago(10))
    later = _single_item_cluster("https://arxiv.org/abs/8", "arxiv", "arxiv", _hours_ago(1))

    assert score_cluster(earlier, now=FIXED_NOW) == pytest.approx(
        score_cluster(later, now=FIXED_NOW)
    )

    ranked = rank_clusters([later, earlier], now=FIXED_NOW)
    assert [r.cluster for r in ranked] == [earlier, later]


def test_rank_clusters_tie_break_by_member_url_hash_when_published_at_also_ties():
    same_published_at = _hours_ago(3)
    cluster_x = _single_item_cluster("https://arxiv.org/abs/x", "arxiv", "arxiv", same_published_at)
    cluster_y = _single_item_cluster("https://arxiv.org/abs/y", "arxiv", "arxiv", same_published_at)

    # Same score, same published_at -- only the deterministic member-URL
    # hash tie-break can decide the order.
    assert score_cluster(cluster_x, now=FIXED_NOW) == pytest.approx(
        score_cluster(cluster_y, now=FIXED_NOW)
    )

    expected_first = cluster_x if _tie_break_hash(cluster_x) < _tie_break_hash(cluster_y) else cluster_y
    expected_second = cluster_y if expected_first is cluster_x else cluster_x

    # Feed both input orderings -- the output order must not depend on
    # which one was listed first, only on the clusters' own content.
    ranked_xy = rank_clusters([cluster_x, cluster_y], now=FIXED_NOW)
    ranked_yx = rank_clusters([cluster_y, cluster_x], now=FIXED_NOW)
    assert [r.cluster for r in ranked_xy] == [expected_first, expected_second]
    assert [r.cluster for r in ranked_yx] == [expected_first, expected_second]


def test_rank_clusters_selects_top_8_of_more_than_8():
    # 10 clusters, scores 10.0, 9.0, ..., 1.0 (as hn points with 1h age).
    clusters = [
        _single_item_cluster(
            f"https://news.ycombinator.com/item?id={points}", "hn", "hn", _hours_ago(1), points=points
        )
        for points in range(1, 11)
    ]
    random.Random(42).shuffle(clusters)

    ranked = rank_clusters(clusters, now=FIXED_NOW)
    assert len(ranked) == MAX_QUEUE_SIZE == 8

    # Highest points -> highest velocity -> highest score for this
    # fixture set (all single-item HN clusters at the same 1h age).
    expected_points_desc = list(range(10, 2, -1))  # 10..3
    ranked_points = [r.cluster.items[0].points for r in ranked]
    assert ranked_points == expected_points_desc
    assert [r.rank for r in ranked] == list(range(1, 9))


def test_rank_clusters_respects_explicit_limit_override():
    clusters = [
        _single_item_cluster(
            f"https://news.ycombinator.com/item?id=lim{points}", "hn", "hn", _hours_ago(1), points=points
        )
        for points in range(1, 6)
    ]
    ranked = rank_clusters(clusters, now=FIXED_NOW, limit=3)
    assert [r.cluster.items[0].points for r in ranked] == [5, 4, 3]


def test_rank_clusters_is_deterministic_across_input_orderings():
    clusters = [
        _single_item_cluster(
            "https://news.ycombinator.com/item?id=d1", "hn", "hn", _hours_ago(2), points=30
        ),
        _single_item_cluster("https://anthropic.com/news/d2", "lab", "anthropic", _hours_ago(5)),
        _single_item_cluster("https://arxiv.org/abs/d3", "arxiv", "arxiv", _hours_ago(1)),
        FakeCluster(
            items=[
                FakeItem("lab", "openai", _hours_ago(3), "https://openai.com/blog/d4"),
                FakeItem(
                    "hn", "hn", _hours_ago(3), "https://news.ycombinator.com/item?id=d4b", points=15
                ),
            ]
        ),
        _single_item_cluster("https://arxiv.org/abs/d5", "arxiv", "arxiv", _hours_ago(9)),
    ]

    baseline = [id(r.cluster) for r in rank_clusters(clusters, now=FIXED_NOW)]

    for seed in range(5):
        shuffled = list(clusters)
        random.Random(seed).shuffle(shuffled)
        result = [id(r.cluster) for r in rank_clusters(shuffled, now=FIXED_NOW)]
        assert result == baseline


def test_rank_clusters_returns_ranked_cluster_instances():
    cluster = _single_item_cluster("https://arxiv.org/abs/only", "arxiv", "arxiv", _hours_ago(1))
    ranked = rank_clusters([cluster], now=FIXED_NOW)
    assert len(ranked) == 1
    assert isinstance(ranked[0], RankedCluster)
    assert ranked[0].rank == 1
    assert ranked[0].cluster is cluster
    # No .cluster_hash on this fixture -- ranked[0].cluster_hash falls
    # back to the self-computed sha256-of-member-URLs value.
    assert isinstance(ranked[0].cluster_hash, str) and ranked[0].cluster_hash


# --------------------------------------------------------------------------
# _tie_break_hash -- prefers a cluster's own .cluster_hash when present
# (matching watcher.clustering.Cluster's real attribute), falls back to a
# self-computed equivalent otherwise.
# --------------------------------------------------------------------------


def test_tie_break_hash_prefers_clusters_own_cluster_hash_attribute_when_present():
    cluster = FakeCluster(
        items=[FakeItem("arxiv", "arxiv", _hours_ago(1), "https://arxiv.org/abs/hashed")],
        cluster_hash="zzz-explicit-hash",
    )
    assert _tie_break_hash(cluster) == "zzz-explicit-hash"


def test_tie_break_hash_falls_back_to_self_computed_value_when_attribute_absent():
    cluster = _single_item_cluster("https://arxiv.org/abs/nohash", "arxiv", "arxiv", _hours_ago(1))
    # No cluster_hash attribute set (default None) -- falls back to a
    # real, non-empty sha256 hex digest, not an empty/placeholder string.
    result = _tie_break_hash(cluster)
    assert isinstance(result, str)
    assert len(result) == 64  # sha256 hex digest length
    int(result, 16)  # raises ValueError if not valid hex


def test_rank_clusters_tie_break_uses_cluster_hash_attribute_when_clusters_expose_it():
    same_published_at = _hours_ago(3)
    cluster_first = FakeCluster(
        items=[FakeItem("arxiv", "arxiv", same_published_at, "https://arxiv.org/abs/p1")],
        cluster_hash="aaa",
    )
    cluster_second = FakeCluster(
        items=[FakeItem("arxiv", "arxiv", same_published_at, "https://arxiv.org/abs/p2")],
        cluster_hash="bbb",
    )
    ranked = rank_clusters([cluster_second, cluster_first], now=FIXED_NOW)
    assert [r.cluster for r in ranked] == [cluster_first, cluster_second]
    assert [r.cluster_hash for r in ranked] == ["aaa", "bbb"]
