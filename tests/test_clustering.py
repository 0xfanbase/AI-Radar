"""Tests for watcher/clustering.py.

All fixtures here are small, hand-built ``Item`` instances -- clustering
logic operates purely on already-parsed ``Item`` objects, so there's no
real fetcher payload to save as a saved-response fixture (unlike the HN/
arXiv/lab fetcher tests). Every scenario's expected Jaccard similarity is
called out in a comment and was cross-checked against the real
``tokenize_title`` implementation before being written down here.
"""
from __future__ import annotations

import random

import hashlib

from watcher.clustering import Cluster, cluster_items, compute_cluster_hash
from watcher.models import Item, normalize_url


def make_item(
    *,
    source_type: str = "hn",
    source_name: str = "hn",
    title: str,
    url: str,
    published_at: str,
) -> Item:
    return Item(
        source_type=source_type,
        source_name=source_name,
        title=title,
        url=url,
        published_at=published_at,
    )


def _member_urls(clusters: list[Cluster]) -> list[list[str]]:
    """Helper: each cluster's member URLs, in cluster/member order."""
    return [[item.url for item in cluster.items] for cluster in clusters]


# --------------------------------------------------------------------------
# Near-duplicate titles merge
# --------------------------------------------------------------------------


def test_near_duplicate_titles_merge_into_one_cluster():
    # tokenize_title(A) = {openai, releases, gpt, major, upgrades}
    # tokenize_title(B) = {openai, ships, gpt, major, features}
    # Jaccard = |{openai, gpt, major}| / 7 = 3/7 ~= 0.4286 >= 0.35
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="OpenAI Ships GPT-5 With Major New Features",
        url="https://techcrunch.com/2026/07/08/openai-gpt-5",
        published_at="2026-07-08T11:00:00Z",
        source_name="techcrunch",
        source_type="hn",
    )

    clusters = cluster_items([item_a, item_b])

    assert len(clusters) == 1
    assert clusters[0].items == [item_a, item_b]


def test_near_duplicate_titles_merge_regardless_of_input_order():
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="OpenAI Ships GPT-5 With Major New Features",
        url="https://techcrunch.com/2026/07/08/openai-gpt-5",
        published_at="2026-07-08T11:00:00Z",
        source_name="techcrunch",
        source_type="hn",
    )

    clusters = cluster_items([item_b, item_a])  # reversed input order

    assert len(clusters) == 1
    # Sorted by (published_at, source_name, url) regardless of input order.
    assert clusters[0].items == [item_a, item_b]


# --------------------------------------------------------------------------
# Dissimilar titles stay separate
# --------------------------------------------------------------------------


def test_dissimilar_titles_stay_in_separate_clusters():
    # tokenize_title(A) and tokenize_title(C) share zero tokens -> Jaccard 0.0
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_c = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://deepmind.google/blog/rl-benchmark",
        published_at="2026-07-08T12:00:00Z",
        source_name="deepmind",
        source_type="lab",
    )

    clusters = cluster_items([item_a, item_c])

    assert len(clusters) == 2
    assert _member_urls(clusters) == [[item_a.url], [item_c.url]]


def test_three_items_two_similar_one_dissimilar():
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="OpenAI Ships GPT-5 With Major New Features",
        url="https://techcrunch.com/2026/07/08/openai-gpt-5",
        published_at="2026-07-08T11:00:00Z",
        source_name="techcrunch",
        source_type="hn",
    )
    item_c = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://deepmind.google/blog/rl-benchmark",
        published_at="2026-07-08T12:00:00Z",
        source_name="deepmind",
        source_type="lab",
    )

    clusters = cluster_items([item_a, item_b, item_c])

    assert len(clusters) == 2
    assert _member_urls(clusters) == [
        [item_a.url, item_b.url],
        [item_c.url],
    ]


# --------------------------------------------------------------------------
# Exact-URL dedup overrides title dissimilarity
# --------------------------------------------------------------------------


def test_exact_normalized_url_match_merges_despite_dissimilar_titles():
    # Same story, same underlying URL (one copy carries UTM tracking
    # params normalize_url() strips), but headlines share zero tokens --
    # the exact-URL short-circuit must still merge them.
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release?utm_source=twitter&utm_campaign=launch",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_c = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T12:00:00Z",
        source_name="deepmind",
        source_type="hn",
    )

    clusters = cluster_items([item_a, item_c])

    assert len(clusters) == 1
    assert clusters[0].items == [item_a, item_c]


def test_exact_url_match_checked_before_jaccard_across_multiple_clusters():
    # cluster1 seed dissimilar from the new item's title; cluster2 seed's
    # URL exactly (normalized) matches the new item -- must join cluster2
    # via the URL short-circuit, never fall through to a Jaccard check
    # that would otherwise find no match at all.
    cluster1_seed = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://deepmind.google/blog/rl-benchmark",
        published_at="2026-07-08T09:00:00Z",
        source_name="deepmind",
        source_type="lab",
    )
    cluster2_seed = make_item(
        title="Totally Unrelated Headline About Something Else Entirely",
        url="https://example.com/story",
        published_at="2026-07-08T10:00:00Z",
        source_name="example",
        source_type="hn",
    )
    same_url_different_title = make_item(
        title="A Completely Different Set Of Words Here",
        url="https://example.com/story/",  # trailing slash normalizes the same
        published_at="2026-07-08T11:00:00Z",
        source_name="other-outlet",
        source_type="hn",
    )

    clusters = cluster_items([cluster1_seed, cluster2_seed, same_url_different_title])

    assert len(clusters) == 2
    assert _member_urls(clusters) == [
        [cluster1_seed.url],
        [cluster2_seed.url, same_url_different_title.url],
    ]


# --------------------------------------------------------------------------
# Deterministic ordering across repeated runs on the same input
# --------------------------------------------------------------------------


def test_deterministic_across_repeated_runs_on_same_input():
    items = [
        make_item(
            title="OpenAI Releases GPT-5 With Major Upgrades",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
            source_name="openai",
            source_type="lab",
        ),
        make_item(
            title="OpenAI Ships GPT-5 With Major New Features",
            url="https://techcrunch.com/2026/07/08/openai-gpt-5",
            published_at="2026-07-08T11:00:00Z",
            source_name="techcrunch",
            source_type="hn",
        ),
        make_item(
            title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
            url="https://deepmind.google/blog/rl-benchmark",
            published_at="2026-07-08T12:00:00Z",
            source_name="deepmind",
            source_type="lab",
        ),
        make_item(
            title="Mistral Announces Large 3 With Longer Context Window",
            url="https://mistral.ai/news/large-3",
            published_at="2026-07-08T09:30:00Z",
            source_name="mistral",
            source_type="lab",
        ),
    ]

    first_run = _member_urls(cluster_items(items))
    second_run = _member_urls(cluster_items(items))

    assert first_run == second_run


def test_deterministic_regardless_of_input_shuffle_order():
    items = [
        make_item(
            title="OpenAI Releases GPT-5 With Major Upgrades",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
            source_name="openai",
            source_type="lab",
        ),
        make_item(
            title="OpenAI Ships GPT-5 With Major New Features",
            url="https://techcrunch.com/2026/07/08/openai-gpt-5",
            published_at="2026-07-08T11:00:00Z",
            source_name="techcrunch",
            source_type="hn",
        ),
        make_item(
            title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
            url="https://deepmind.google/blog/rl-benchmark",
            published_at="2026-07-08T12:00:00Z",
            source_name="deepmind",
            source_type="lab",
        ),
        make_item(
            title="Mistral Announces Large 3 With Longer Context Window",
            url="https://mistral.ai/news/large-3",
            published_at="2026-07-08T09:30:00Z",
            source_name="mistral",
            source_type="lab",
        ),
    ]

    baseline = _member_urls(cluster_items(items))

    rng = random.Random(1234)
    for _ in range(5):
        shuffled = items[:]
        rng.shuffle(shuffled)
        assert _member_urls(cluster_items(shuffled)) == baseline


def test_sort_key_orders_by_published_at_then_source_name_then_url():
    # Same published_at -- tie-break falls to source_name, then url.
    # Titles share zero tokens (confirmed Jaccard 0.0) so they can only
    # land in the same cluster via a clustering bug, never legitimately.
    item_z_source = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://z-outlet.com/a",
        published_at="2026-07-08T10:00:00Z",
        source_name="z-outlet",
        source_type="hn",
    )
    item_a_source = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://a-outlet.com/b",
        published_at="2026-07-08T10:00:00Z",
        source_name="a-outlet",
        source_type="hn",
    )

    clusters = cluster_items([item_z_source, item_a_source])

    # Dissimilar titles -> two separate single-item clusters; cluster
    # *creation* order must follow the sort key (a-outlet before
    # z-outlet), not input order.
    assert len(clusters) == 2
    assert clusters[0].items[0] is item_a_source
    assert clusters[1].items[0] is item_z_source


# --------------------------------------------------------------------------
# "First matching cluster" precedence
# --------------------------------------------------------------------------


def test_joins_first_matching_cluster_when_multiple_would_match():
    # Both existing clusters' seeds are individually Jaccard-similar
    # enough to `candidate` (0.4286 each), but NOT similar enough to each
    # other (0.25, so they never merge into one cluster first): seed_one
    # = {breakthrough, computing, labs, quantum, research}, seed_two =
    # {computing, milestone, project, quantum, team}, candidate =
    # {breakthrough, computing, milestone, quantum, today}. `candidate`
    # must join the earliest-created (first) matching cluster, seed_one's.
    seed_one = make_item(
        title="Quantum Computing Breakthrough At Research Labs",
        url="https://anthropic.com/news/update-one",
        published_at="2026-07-08T08:00:00Z",
        source_name="anthropic",
        source_type="lab",
    )
    seed_two = make_item(
        title="Quantum Computing Milestone For New Team Project",
        url="https://anthropic.com/news/update-two",
        published_at="2026-07-08T09:00:00Z",
        source_name="anthropic",
        source_type="lab",
    )
    candidate = make_item(
        title="Quantum Computing Breakthrough Milestone Today",
        url="https://news-outlet.com/anthropic-update",
        published_at="2026-07-08T10:00:00Z",
        source_name="news-outlet",
        source_type="hn",
    )

    clusters = cluster_items([seed_one, seed_two, candidate])

    assert len(clusters) == 2
    assert clusters[0].items == [seed_one, candidate]
    assert clusters[1].items == [seed_two]


# --------------------------------------------------------------------------
# Empty input / single item edge cases
# --------------------------------------------------------------------------


def test_empty_input_returns_no_clusters():
    assert cluster_items([]) == []


def test_single_item_forms_its_own_cluster():
    item = make_item(
        title="A Single Story With No Peers",
        url="https://example.com/only-story",
        published_at="2026-07-08T10:00:00Z",
        source_name="example",
        source_type="hn",
    )

    clusters = cluster_items([item])

    assert len(clusters) == 1
    assert clusters[0].items == [item]
    assert clusters[0].seed is item


# --------------------------------------------------------------------------
# cluster_hash -- sha256 of sorted normalized member URLs (feeds
# data/ledger.json / data/queue.json's idempotency key).
# --------------------------------------------------------------------------


def test_cluster_hash_matches_sha256_of_sorted_normalized_urls():
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="OpenAI Ships GPT-5 With Major New Features",
        url="https://techcrunch.com/2026/07/08/openai-gpt-5",
        published_at="2026-07-08T11:00:00Z",
        source_name="techcrunch",
        source_type="hn",
    )

    clusters = cluster_items([item_a, item_b])
    assert len(clusters) == 1

    expected_urls = sorted({normalize_url(item_a.url), normalize_url(item_b.url)})
    expected = hashlib.sha256("\n".join(expected_urls).encode("utf-8")).hexdigest()

    assert clusters[0].cluster_hash == expected
    assert clusters[0].cluster_hash == compute_cluster_hash(clusters[0].normalized_urls)


def test_cluster_hash_independent_of_member_join_order():
    # Same eventual membership, built up in a different order (via a
    # third, differently-timed item added to the shuffle) -- the hash
    # only depends on which normalized URLs end up in the cluster, not
    # the order they joined in.
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="OpenAI Ships GPT-5 With Major New Features",
        url="https://techcrunch.com/2026/07/08/openai-gpt-5",
        published_at="2026-07-08T09:00:00Z",  # earlier -- becomes the seed instead
        source_name="techcrunch",
        source_type="hn",
    )

    forward = cluster_items([item_a, item_b])
    backward = cluster_items([item_b, item_a])

    assert len(forward) == 1 and len(backward) == 1
    assert forward[0].cluster_hash == backward[0].cluster_hash


# --------------------------------------------------------------------------
# Lab-lab Jaccard bar (Phase 1 PM checkpoint fix): two lab items compared
# against each other must clear config.LAB_LAB_JACCARD_SIMILARITY_THRESHOLD
# (0.65), not the general 0.35 -- prevents short, boilerplate-templated
# lab-announcement titles ("Introducing GPT-...") from chaining together
# into a mega-cluster purely on shared boilerplate tokens. A lab item
# compared against a non-lab seed (or vice versa) still uses the general
# 0.35 bar unchanged.
# --------------------------------------------------------------------------


def test_two_lab_items_below_lab_lab_bar_stay_separate():
    # tokenize("Introducing GPT-5") = {introducing, gpt, 5}
    # tokenize("Introducing GPT-5.4") = {introducing, gpt, 5.4}
    # Jaccard = |{introducing, gpt}| / |{introducing, gpt, 5, 5.4}| = 2/4 = 0.5
    # >= the general 0.35 bar, but < the 0.65 lab-lab bar -- must NOT merge.
    item_a = make_item(
        title="Introducing GPT-5",
        url="https://openai.com/index/introducing-gpt-5",
        published_at="2026-07-01T00:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="Introducing GPT-5.4",
        url="https://openai.com/index/introducing-gpt-5-4",
        published_at="2026-07-05T00:00:00Z",
        source_name="openai",
        source_type="lab",
    )

    clusters = cluster_items([item_a, item_b])

    assert len(clusters) == 2
    assert _member_urls(clusters) == [[item_a.url], [item_b.url]]


def test_two_lab_items_above_lab_lab_bar_still_merge():
    # tokenize("Introducing GPT-5.2") = {introducing, gpt, 5.2}
    # tokenize("Introducing GPT-5.2-Codex") = {introducing, gpt, 5.2, codex}
    # Jaccard = 3/4 = 0.75 >= the 0.65 lab-lab bar -- must still merge (a
    # companion article about the same release).
    item_a = make_item(
        title="Introducing GPT-5.2",
        url="https://openai.com/index/introducing-gpt-5-2",
        published_at="2026-07-01T00:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="Introducing GPT-5.2-Codex",
        url="https://openai.com/index/introducing-gpt-5-2-codex",
        published_at="2026-07-01T01:00:00Z",
        source_name="openai",
        source_type="lab",
    )

    clusters = cluster_items([item_a, item_b])

    assert len(clusters) == 1
    assert clusters[0].items == [item_a, item_b]


def test_lab_and_non_lab_item_still_use_the_general_bar():
    # Same 0.5 Jaccard pairing as the "stay separate" test above, but with
    # item_b's source_type changed to "hn" -- the general 0.35 bar applies
    # (not the stricter lab-lab one), so this pair still merges.
    item_a = make_item(
        title="Introducing GPT-5",
        url="https://openai.com/index/introducing-gpt-5",
        published_at="2026-07-01T00:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_b = make_item(
        title="Introducing GPT-5.4",
        url="https://news-outlet.test/introducing-gpt-5-4",
        published_at="2026-07-05T00:00:00Z",
        source_name="news-outlet",
        source_type="hn",
    )

    clusters = cluster_items([item_a, item_b])

    assert len(clusters) == 1
    assert clusters[0].items == [item_a, item_b]


def test_cluster_hash_differs_for_different_membership():
    item_a = make_item(
        title="OpenAI Releases GPT-5 With Major Upgrades",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
        source_name="openai",
        source_type="lab",
    )
    item_c = make_item(
        title="DeepMind Publishes New Reinforcement Learning Benchmark Results",
        url="https://deepmind.google/blog/rl-benchmark",
        published_at="2026-07-08T12:00:00Z",
        source_name="deepmind",
        source_type="lab",
    )

    clusters = cluster_items([item_a, item_c])

    assert len(clusters) == 2
    assert clusters[0].cluster_hash != clusters[1].cluster_hash
