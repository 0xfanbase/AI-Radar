"""End-to-end integration test: watcher/clustering.py -> watcher/ranking.py.

``clustering.py`` and ``ranking.py`` were built concurrently (per the
approved build plan's Phase 1 commit sequence) by two different passes.
``ranking.py`` was deliberately written against a duck-typed
``ClusterLike``/``ItemLike`` shape rather than a hard import of
``watcher.clustering.Cluster`` -- see its module docstring's ASSUMPTION
paragraph -- specifically so it would not block on that module landing
first. This file is the proof that the bet paid off: real
``watcher.clustering.cluster_items()`` output is fed **directly** into
``watcher.ranking.rank_clusters()`` (and the smaller per-cluster scoring
functions) with zero adaptation, wrapping, or format conversion in
between -- no shim module, no dict conversion, no re-fetching of fields.

If a future change to either module's shape ever breaks that contract,
this test -- not just each module's own unit tests against its own local
fixtures -- is what catches it.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from watcher.clustering import Cluster, cluster_items
from watcher.config import MAX_QUEUE_SIZE
from watcher.models import Item
from watcher.ranking import (
    RankedCluster,
    cross_source_count,
    hn_velocity_score,
    primary_source_weight,
    rank_clusters,
    score_cluster,
)

FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _item(
    *,
    source_type: str,
    source_name: str,
    title: str,
    url: str,
    published_at: str,
    points: int | None = None,
) -> Item:
    return Item(
        source_type=source_type,
        source_name=source_name,
        title=title,
        url=url,
        published_at=published_at,
        points=points,
    )


# --------------------------------------------------------------------------
# Fixture pool: a realistic mix of lab/arxiv/hn items, some of which should
# cluster together (exact-URL or Jaccard match), some of which shouldn't.
# --------------------------------------------------------------------------


def _build_items() -> list[Item]:
    return [
        # Cluster A: lab post + HN discussion of the same GPT-5 release
        # (near-duplicate titles, Jaccard >= 0.35 -- see test_clustering.py
        # for the same pair with the exact similarity worked out).
        _item(
            source_type="lab",
            source_name="openai",
            title="OpenAI Releases GPT-5 With Major Upgrades",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
        ),
        _item(
            source_type="hn",
            source_name="hn",
            title="OpenAI Ships GPT-5 With Major New Features",
            url="https://news.ycombinator.com/item?id=100",
            published_at="2026-07-08T11:00:00Z",
            points=200,
        ),
        # Cluster B: a lone arXiv paper, no corroboration -- floor velocity.
        _item(
            source_type="arxiv",
            source_name="arxiv",
            title="A Completely Unrelated Paper About Reinforcement Learning",
            url="https://arxiv.org/abs/2607.00001",
            published_at="2026-07-08T09:00:00Z",
        ),
        # Cluster C: a DeepMind post picked up by HN, same URL twice (one
        # copy carries a tracking param) -- exact-URL-normalization match.
        _item(
            source_type="lab",
            source_name="deepmind",
            title="Google DeepMind Publishes New Benchmark Results",
            url="https://deepmind.google/blog/new-benchmark?utm_source=hn",
            published_at="2026-07-08T08:00:00Z",
        ),
        _item(
            source_type="hn",
            source_name="hn",
            title="Totally different headline sharing zero title tokens",
            url="https://deepmind.google/blog/new-benchmark",
            published_at="2026-07-08T12:00:00Z",
            points=80,
        ),
        # Cluster D: a lone, low-signal HN story.
        _item(
            source_type="hn",
            source_name="hn",
            title="Someone Built A Small Weekend AI Side Project",
            url="https://news.ycombinator.com/item?id=101",
            published_at="2026-07-08T07:00:00Z",
            points=5,
        ),
    ]


# --------------------------------------------------------------------------
# The end-to-end pipeline itself
# --------------------------------------------------------------------------


def test_cluster_items_output_feeds_directly_into_rank_clusters():
    clusters = cluster_items(_build_items())

    # Sanity: clustering produced the 4 expected groups (A merged pair,
    # B lone arxiv, C merged pair via exact URL, D lone hn) before ranking
    # ever gets involved.
    assert len(clusters) == 4
    assert all(isinstance(cluster, Cluster) for cluster in clusters)

    # No adaptation layer: clusters (real watcher.clustering.Cluster
    # instances) are passed straight into rank_clusters().
    ranked = rank_clusters(clusters, now=FIXED_NOW)

    assert len(ranked) == 4  # fewer than MAX_QUEUE_SIZE, nothing dropped
    assert all(isinstance(entry, RankedCluster) for entry in ranked)
    # Every ranked entry's .cluster is literally one of clustering's own
    # Cluster objects -- not a copy, dict, or re-wrapped stand-in.
    for entry in ranked:
        assert isinstance(entry.cluster, Cluster)
        assert entry.cluster in clusters

    # Scores strictly descending (all four clusters have distinct scores
    # in this fixture set).
    scores = [entry.score for entry in ranked]
    assert scores == sorted(scores, reverse=True)

    # Ranks are 1..N in output order.
    assert [entry.rank for entry in ranked] == list(range(1, len(ranked) + 1))


def test_rank_clusters_cluster_hash_matches_real_cluster_hash_attribute():
    # ranking.py's tie-break prefers a cluster's own .cluster_hash
    # attribute when present, falling back to a self-computed equivalent
    # only for bare-bones duck-typed fixtures that lack one. Real
    # clustering.Cluster instances always expose the real attribute, so
    # RankedCluster.cluster_hash must come from *that* -- not merely
    # happen to match by re-deriving the same formula independently.
    clusters = cluster_items(_build_items())
    ranked = rank_clusters(clusters, now=FIXED_NOW)

    for entry in ranked:
        assert hasattr(entry.cluster, "cluster_hash")
        assert entry.cluster_hash == entry.cluster.cluster_hash


def test_per_cluster_scoring_functions_accept_real_cluster_instances():
    # The smaller scoring building blocks (not just rank_clusters as a
    # whole) must also operate directly on real Cluster objects -- this is
    # what "ranking.py operates on the real cluster objects clustering.py
    # produces, end to end" means at the function-signature level, not
    # just at the top-level pipeline call.
    clusters = cluster_items(_build_items())
    merged_gpt5_cluster = next(
        c for c in clusters if len(c.items) == 2 and c.items[0].source_name == "openai"
    )

    assert primary_source_weight(merged_gpt5_cluster) == 3.0  # lab beats hn
    assert cross_source_count(merged_gpt5_cluster) == 2  # openai + hn
    # HN item: 200 points, posted 2026-07-08T11:00:00Z -> 17h before
    # FIXED_NOW -> 200 / 17 hours.
    assert hn_velocity_score(merged_gpt5_cluster, now=FIXED_NOW) == (200 / 17.0)
    assert score_cluster(merged_gpt5_cluster, now=FIXED_NOW) == (
        3.0 * 2 * (200 / 17.0)
    )


def test_pipeline_selects_top_max_queue_size_from_a_larger_pool():
    # 12 single-item HN clusters covering 12 topically unrelated stories
    # (each title's stopword-stripped token set shares at most one token
    # with any other, well under the 0.35 Jaccard threshold, and every
    # URL is distinct) -- clustering must never merge any of them. More
    # than MAX_QUEUE_SIZE survive clustering, so ranking must trim to the
    # top MAX_QUEUE_SIZE by score (here, purely by HN velocity since
    # every other factor ties: same source_type, same published_at).
    unrelated_headlines = [
        "Robotics Startup Unveils Warehouse Automation System",
        "Climate Scientists Release Satellite Ocean Temperature Dataset",
        "Biotech Firm Announces Gene Editing Therapy Trial Results",
        "Automaker Reveals Electric Vehicle Battery Manufacturing Plant",
        "Video Game Studio Ships Long Awaited Fantasy Sequel",
        "Airline Industry Reports Record Summer Travel Demand",
        "Space Agency Confirms Successful Lunar Rover Landing",
        "Retail Chain Expands Grocery Delivery Service Nationwide",
        "Telecom Provider Launches Rural Broadband Expansion Project",
        "Music Streaming Platform Adds Lossless Audio Support",
        "Sports League Announces Expansion Franchise City Selection",
        "Pharmaceutical Company Recalls Contaminated Medication Batch",
    ]
    items = [
        _item(
            source_type="hn",
            source_name="hn",
            title=headline,
            url=f"https://news.ycombinator.com/item?id={200 + n}",
            published_at="2026-07-08T10:00:00Z",  # 18h before FIXED_NOW
            points=n,
        )
        for n, headline in enumerate(unrelated_headlines, start=1)
    ]

    clusters = cluster_items(items)
    assert len(clusters) == 12  # nothing spuriously merged

    ranked = rank_clusters(clusters, now=FIXED_NOW)

    assert len(ranked) == MAX_QUEUE_SIZE == 8
    ranked_points = [entry.cluster.items[0].points for entry in ranked]
    assert ranked_points == list(range(12, 4, -1))  # 12..5, highest points win


def test_pipeline_is_deterministic_across_input_orderings():
    items = _build_items()
    baseline = [
        entry.cluster_hash
        for entry in rank_clusters(cluster_items(items), now=FIXED_NOW)
    ]

    rng = random.Random(2026)
    for _ in range(5):
        shuffled = items[:]
        rng.shuffle(shuffled)
        result = [
            entry.cluster_hash
            for entry in rank_clusters(cluster_items(shuffled), now=FIXED_NOW)
        ]
        assert result == baseline
