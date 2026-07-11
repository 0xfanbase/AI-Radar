"""Tests for watcher/queue_writer.py.

Covers, in order: the "sources" entry shaping (url/source_type/title plus
the outlet/points derivation rules), excluding already-carded clusters,
capping at MAX_QUEUE_SIZE (with rank re-numbered 1..N post-filter), the
schema-valid load/save round trip, and the combined write_queue() entrypoint
watcher/cli.py actually calls. No live network anywhere -- every cluster
here is built from plain watcher.models.Item fixtures fed through the real
clustering/ranking pipeline, exactly like tests/test_ledger.py's own
fixture-level rehearsal.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from jsonschema import ValidationError

from watcher.clustering import cluster_items
from watcher.config import MAX_QUEUE_SIZE
from watcher.ledger import empty_ledger
from watcher.models import Item
from watcher.queue_writer import build_queue, load_queue, save_queue, write_queue
from watcher.ranking import rank_clusters

FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _ranked(items: list[Item], *, limit: int | None = None):
    clusters = cluster_items(items)
    return rank_clusters(clusters, now=FIXED_NOW, limit=limit or max(len(clusters), 1))


# --------------------------------------------------------------------------
# _source_entry / build_queue shaping -- outlet/points derivation
# --------------------------------------------------------------------------


def test_source_entry_hn_item_gets_domain_outlet_and_points():
    items = [
        Item(
            source_type="hn",
            source_name="hn",
            title="Some Startup Ships A New AI Product",
            url="https://www.techcrunch.com/2026/07/08/ai-product-launch",
            published_at="2026-07-08T10:00:00Z",
            points=123,
        )
    ]
    ranked = _ranked(items)
    queue = build_queue(ranked, empty_ledger())

    assert len(queue) == 1
    [source] = queue[0]["sources"]
    assert source["url"] == items[0].url
    assert source["source_type"] == "hn"
    assert source["title"] == items[0].title
    assert source["outlet"] == "techcrunch.com"  # leading "www." stripped
    assert source["points"] == 123


def test_source_entry_lab_and_arxiv_items_get_null_outlet_and_points():
    items = [
        Item(
            source_type="lab",
            source_name="openai",
            title="OpenAI Releases GPT-5",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
        ),
        Item(
            source_type="arxiv",
            source_name="arxiv",
            title="A Paper About Something Unrelated Entirely",
            url="https://arxiv.org/abs/2607.00001",
            published_at="2026-07-08T09:00:00Z",
        ),
    ]
    ranked = _ranked(items)
    queue = build_queue(ranked, empty_ledger())

    sources_by_url = {
        source["url"]: source for entry in queue for source in entry["sources"]
    }
    assert sources_by_url[items[0].url]["outlet"] is None
    assert sources_by_url[items[0].url]["points"] is None
    assert sources_by_url[items[1].url]["outlet"] is None
    assert sources_by_url[items[1].url]["points"] is None


def test_build_queue_raw_url_used_not_normalized_url():
    # A tracked query param must survive into queue.json's sources[].url --
    # the analyst needs a real, followable link, not the clustering/ledger
    # dedup key (which strips tracking params via normalize_url).
    items = [
        Item(
            source_type="lab",
            source_name="deepmind",
            title="Google DeepMind Publishes New Benchmark Results",
            url="https://deepmind.google/blog/new-benchmark?utm_source=hn",
            published_at="2026-07-08T08:00:00Z",
        )
    ]
    ranked = _ranked(items)
    queue = build_queue(ranked, empty_ledger())
    assert queue[0]["sources"][0]["url"] == "https://deepmind.google/blog/new-benchmark?utm_source=hn"


def test_build_queue_includes_one_source_entry_per_cluster_member():
    # Two items that cluster together (exact-URL match) -> ALL source URLs
    # per CLAUDE.md's "write data/queue.json (<=8 clusters, each with ALL
    # source URLs)".
    items = [
        Item(
            source_type="lab",
            source_name="openai",
            title="OpenAI Releases GPT-5 With Major Upgrades",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="OpenAI Ships GPT-5 With Major New Features",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T11:00:00Z",
            points=200,
        ),
    ]
    ranked = _ranked(items)
    assert len(ranked) == 1  # exact-URL match merges into one cluster

    queue = build_queue(ranked, empty_ledger())
    assert len(queue) == 1
    assert len(queue[0]["sources"]) == 2


# --------------------------------------------------------------------------
# Excluding already-carded clusters
# --------------------------------------------------------------------------


def test_build_queue_excludes_cluster_with_non_null_card_id():
    published_item = Item(
        source_type="arxiv",
        source_name="arxiv",
        title="Already Published Paper About Something",
        url="https://arxiv.org/abs/published",
        published_at="2026-07-08T09:00:00Z",
    )
    fresh_item = Item(
        source_type="arxiv",
        source_name="arxiv",
        title="A Brand New Unrelated Paper Entirely",
        url="https://arxiv.org/abs/fresh",
        published_at="2026-07-08T09:00:00Z",
    )
    ranked = _ranked([published_item, fresh_item])
    published_hash = next(
        r.cluster_hash for r in ranked if r.cluster.items[0].url == published_item.url
    )

    ledger = {
        "version": 1,
        "entries": {
            published_hash: {
                "card_id": "2026-07-08-already-published",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-08",
                "member_urls": [published_item.url],
            }
        },
    }

    queue = build_queue(ranked, ledger)
    urls_in_queue = {source["url"] for entry in queue for source in entry["sources"]}
    assert published_item.url not in urls_in_queue
    assert fresh_item.url in urls_in_queue


def test_build_queue_keeps_cluster_with_null_card_id_ledger_entry():
    item = Item(
        source_type="arxiv",
        source_name="arxiv",
        title="A Paper Already Seen But Not Yet Carded",
        url="https://arxiv.org/abs/queued-already",
        published_at="2026-07-08T09:00:00Z",
    )
    ranked = _ranked([item])
    cluster_hash = ranked[0].cluster_hash

    ledger = {
        "version": 1,
        "entries": {
            cluster_hash: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-08",
                "member_urls": [item.url],
            }
        },
    }

    queue = build_queue(ranked, ledger)
    assert len(queue) == 1
    assert queue[0]["cluster_hash"] == cluster_hash


# --------------------------------------------------------------------------
# Capping at MAX_QUEUE_SIZE, rank re-numbered 1..N post-filter
# --------------------------------------------------------------------------


# Genuinely topically-unrelated headlines (shares at most one token with
# any other after stopword-stripping, well under the 0.35 Jaccard
# threshold) -- same fixture style as
# tests/test_clustering_ranking_integration.py's own unrelated-headlines
# list, so clustering never spuriously merges any of these.
_UNRELATED_HEADLINES = [
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
]


def test_build_queue_caps_at_max_queue_size_default():
    items = [
        Item(
            source_type="hn",
            source_name="hn",
            title=headline,
            url=f"https://news.ycombinator.com/item?id={n}",
            published_at="2026-07-08T10:00:00Z",
            points=n,
        )
        for n, headline in enumerate(_UNRELATED_HEADLINES, start=1)  # 11 distinct clusters
    ]
    ranked = _ranked(items)
    assert len(ranked) == 11  # nothing merged, all distinct

    queue = build_queue(ranked, empty_ledger())
    assert len(queue) == MAX_QUEUE_SIZE == 8


def test_build_queue_respects_explicit_limit_override():
    items = [
        Item(
            source_type="hn",
            source_name="hn",
            title=headline,
            url=f"https://news.ycombinator.com/item?id=lim{n}",
            published_at="2026-07-08T10:00:00Z",
            points=n,
        )
        for n, headline in enumerate(_UNRELATED_HEADLINES[:5], start=1)
    ]
    ranked = _ranked(items)
    queue = build_queue(ranked, empty_ledger(), limit=3)
    assert len(queue) == 3


def test_build_queue_rank_is_renumbered_1_to_n_after_filtering():
    # Three clusters: the highest-ranked one is already carded and must be
    # excluded, so the remaining two must be renumbered 1, 2 -- not keep
    # their original pre-filter ranks (which would be 2, 3).
    items = [
        Item(
            source_type="hn",
            source_name="hn",
            title="Robotics Startup Unveils Warehouse Automation System",
            url="https://news.ycombinator.com/item?id=900",
            published_at="2026-07-08T10:00:00Z",
            points=500,
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="Climate Scientists Release Satellite Ocean Temperature Dataset",
            url="https://news.ycombinator.com/item?id=901",
            published_at="2026-07-08T10:00:00Z",
            points=100,
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="Biotech Firm Announces Gene Editing Therapy Trial Results",
            url="https://news.ycombinator.com/item?id=902",
            published_at="2026-07-08T10:00:00Z",
            points=10,
        ),
    ]
    ranked = _ranked(items)
    assert [r.rank for r in ranked] == [1, 2, 3]  # pre-filter ranks

    already_carded_hash = ranked[0].cluster_hash
    ledger = {
        "version": 1,
        "entries": {
            already_carded_hash: {
                "card_id": "2026-07-08-highest",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-08",
                "member_urls": [items[0].url],
            }
        },
    }

    queue = build_queue(ranked, ledger)
    assert len(queue) == 2
    assert [entry["rank"] for entry in queue] == [1, 2]
    assert queue[0]["cluster_hash"] == ranked[1].cluster_hash
    assert queue[1]["cluster_hash"] == ranked[2].cluster_hash


def test_build_queue_score_and_cluster_hash_match_ranked_cluster():
    item = Item(
        source_type="lab",
        source_name="anthropic",
        title="Anthropic Announces A New Model Family",
        url="https://anthropic.com/news/new-model-family",
        published_at="2026-07-08T10:00:00Z",
    )
    ranked = _ranked([item])
    queue = build_queue(ranked, empty_ledger())
    assert queue[0]["cluster_hash"] == ranked[0].cluster_hash
    assert queue[0]["score"] == ranked[0].score


def test_build_queue_empty_when_everything_already_carded():
    item = Item(
        source_type="arxiv",
        source_name="arxiv",
        title="A Paper That Has Already Been Fully Published",
        url="https://arxiv.org/abs/all-done",
        published_at="2026-07-08T09:00:00Z",
    )
    ranked = _ranked([item])
    ledger = {
        "version": 1,
        "entries": {
            ranked[0].cluster_hash: {
                "card_id": "2026-07-08-done",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-08",
                "member_urls": [item.url],
            }
        },
    }
    assert build_queue(ranked, ledger) == []


# --------------------------------------------------------------------------
# load_queue / save_queue -- schema-valid round trip
# --------------------------------------------------------------------------


def test_load_queue_missing_file_returns_empty_list(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_queue(missing) == []


def test_save_then_load_round_trips_and_is_schema_valid(tmp_path):
    path = tmp_path / "queue.json"
    item = Item(
        source_type="lab",
        source_name="openai",
        title="OpenAI Releases GPT-5",
        url="https://openai.com/blog/gpt-5-release",
        published_at="2026-07-08T10:00:00Z",
    )
    ranked = _ranked([item])
    queue = build_queue(ranked, empty_ledger())

    save_queue(queue, path)
    assert path.is_file()

    loaded = load_queue(path)
    assert loaded == queue


def test_save_queue_rejects_schema_invalid_payload_and_writes_nothing(tmp_path):
    path = tmp_path / "queue.json"
    bad = [{"cluster_hash": "x", "rank": 0, "score": 1.0, "sources": []}]  # rank<1, empty sources

    with pytest.raises(ValidationError):
        save_queue(bad, path)

    assert not path.exists()


def test_save_queue_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "queue.json"
    save_queue([], path)
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_load_queue_rejects_schema_invalid_file(tmp_path):
    path = tmp_path / "queue.json"
    bad = [{"cluster_hash": "x", "rank": 1, "score": 1.0, "sources": []}]  # sources minItems=1
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_queue(path)


# --------------------------------------------------------------------------
# write_queue -- the combined entrypoint watcher/cli.py calls
# --------------------------------------------------------------------------


def test_write_queue_builds_saves_and_returns_payload(tmp_path):
    path = tmp_path / "queue.json"
    items = [
        Item(
            source_type="hn",
            source_name="hn",
            title="A Fresh AI Story About Chips Nobody Has Seen",
            url="https://news.ycombinator.com/item?id=555",
            published_at="2026-07-08T10:00:00Z",
            points=50,
        )
    ]
    ranked = _ranked(items)

    result = write_queue(ranked, empty_ledger(), path=path)

    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == result
    assert len(result) == 1
    assert result[0]["rank"] == 1
