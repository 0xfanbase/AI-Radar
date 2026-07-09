"""Tests for watcher/ledger.py.

Covers, in order: cluster_hash order-independence (the property this
module's whole idempotency guarantee rests on), a schema-valid load/save
round trip, the filter/upsert primitives individually, and -- the
critical rehearsal -- running the full fetch-to-ledger flow twice on
identical fixture input and confirming the second run adds *zero* new
ledger entries (only last_seen bumps on existing keys, which is not a
new entry).
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from jsonschema import ValidationError

from watcher.clustering import cluster_items, compute_cluster_hash
from watcher.ledger import (
    LEDGER_VERSION,
    apply_run,
    empty_ledger,
    load_ledger,
    save_ledger,
    unpublished_clusters,
    upsert_entries,
)
from watcher.models import Item, normalize_url
from watcher.ranking import rank_clusters

FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# cluster_hash -- order-independence
# --------------------------------------------------------------------------
# watcher/ledger.py re-uses watcher.clustering.compute_cluster_hash (the
# schema's own documented owner of the formula) rather than maintaining a
# second hash implementation. This is the property the rest of this
# module's idempotency guarantee structurally depends on: shuffling the
# same set of member URLs must never change the resulting cluster_hash.


def test_cluster_hash_is_order_independent_of_url_shuffle():
    urls = [
        "https://openai.com/blog/gpt-5-release",
        "https://news.ycombinator.com/item?id=100",
        "https://arxiv.org/abs/2607.00001",
        "https://deepmind.google/blog/new-benchmark",
    ]
    baseline = compute_cluster_hash(urls)

    assert len(baseline) == 64  # sha256 hex digest length
    int(baseline, 16)  # raises ValueError if not valid hex

    for seed in range(5):
        shuffled = urls[:]
        random.Random(seed).shuffle(shuffled)
        assert compute_cluster_hash(shuffled) == baseline


def test_cluster_hash_order_independent_via_real_clusters_with_shuffled_items():
    # Same URL set, items fed to cluster_items() in different input
    # orders -- the resulting single cluster's cluster_hash (what ledger
    # upserts key on) must be identical either way.
    items = [
        Item(
            source_type="lab",
            source_name="openai",
            title="OpenAI Releases GPT-5",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T10:00:00Z",
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="OpenAI Releases GPT-5",
            url="https://openai.com/blog/gpt-5-release",
            published_at="2026-07-08T11:00:00Z",
            points=200,
        ),
    ]
    forward = cluster_items(items)
    backward = cluster_items(list(reversed(items)))
    assert len(forward) == 1
    assert len(backward) == 1
    assert forward[0].cluster_hash == backward[0].cluster_hash


# --------------------------------------------------------------------------
# empty_ledger / load_ledger / save_ledger -- schema-valid round trip
# --------------------------------------------------------------------------


def test_empty_ledger_matches_phase1_seed_shape():
    assert empty_ledger() == {"version": LEDGER_VERSION, "entries": {}}


def test_load_ledger_missing_file_returns_empty_ledger(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    assert load_ledger(missing) == empty_ledger()


def test_save_then_load_round_trips_and_is_schema_valid(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = {
        "version": 1,
        "entries": {
            "a" * 64: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example.test/a", "https://example.test/b"],
            }
        },
    }

    save_ledger(ledger, path)
    assert path.is_file()

    loaded = load_ledger(path)  # re-validates on load too
    assert loaded == ledger


def test_load_ledger_rejects_schema_invalid_file(tmp_path):
    path = tmp_path / "ledger.json"
    # Missing required "member_urls" on the entry -- schema-invalid.
    bad = {
        "version": 1,
        "entries": {
            "a" * 64: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
            }
        },
    }
    path.write_text(json.dumps(bad), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_ledger(path)


def test_save_ledger_rejects_schema_invalid_ledger_and_writes_nothing(tmp_path):
    path = tmp_path / "ledger.json"
    bad = {"version": 1, "entries": {"x": {"card_id": None}}}

    with pytest.raises(ValidationError):
        save_ledger(bad, path)

    assert not path.exists()


def test_save_ledger_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "ledger.json"
    save_ledger(empty_ledger(), path)
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8")) == empty_ledger()


# --------------------------------------------------------------------------
# unpublished_clusters -- filter on non-null card_id
# --------------------------------------------------------------------------


def _ranked(cluster_hash: str, url: str = "https://example.test/x"):
    items = [
        Item(
            source_type="arxiv",
            source_name="arxiv",
            title="Some Paper",
            url=url,
            published_at="2026-07-08T09:00:00Z",
        )
    ]
    clusters = cluster_items(items)
    ranked = rank_clusters(clusters, now=FIXED_NOW)[0]
    # Overwrite cluster_hash with the caller's chosen value so tests can
    # target specific ledger keys without fighting real hash derivation.
    return ranked.__class__(
        rank=ranked.rank, score=ranked.score, cluster_hash=cluster_hash, cluster=ranked.cluster
    )


def test_unpublished_clusters_drops_entries_with_non_null_card_id():
    published = _ranked("hash-published", "https://example.test/published")
    unpublished_existing = _ranked("hash-existing-null", "https://example.test/existing-null")
    brand_new = _ranked("hash-new", "https://example.test/new")

    ledger = {
        "version": 1,
        "entries": {
            "hash-published": {
                "card_id": "2026-07-09-example",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-08",
                "member_urls": ["https://example.test/published"],
            },
            "hash-existing-null": {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-08",
                "member_urls": ["https://example.test/existing-null"],
            },
        },
    }

    survivors = unpublished_clusters([published, unpublished_existing, brand_new], ledger)
    assert survivors == [unpublished_existing, brand_new]


def test_unpublished_clusters_empty_ledger_keeps_everything():
    a = _ranked("hash-a")
    b = _ranked("hash-b", "https://example.test/b")
    assert unpublished_clusters([a, b], empty_ledger()) == [a, b]


# --------------------------------------------------------------------------
# upsert_entries -- new hash vs. existing hash semantics
# --------------------------------------------------------------------------


def test_upsert_entries_new_hash_gets_queued_null_card_id_entry():
    cluster = _ranked("brand-new-hash", "https://example.test/brand-new")
    new_ledger = upsert_entries([cluster], empty_ledger(), now=date(2026, 7, 9))

    entry = new_ledger["entries"]["brand-new-hash"]
    assert entry["card_id"] is None
    assert entry["status"] == "queued"
    assert entry["first_seen"] == "2026-07-09"
    assert entry["last_seen"] == "2026-07-09"
    assert entry["member_urls"] == [normalize_url("https://example.test/brand-new")]


def test_upsert_entries_existing_hash_bumps_last_seen_only():
    cluster = _ranked("existing-hash", "https://example.test/existing")
    ledger = {
        "version": 1,
        "entries": {
            "existing-hash": {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-05",
                "member_urls": ["https://example.test/existing"],
            }
        },
    }

    new_ledger = upsert_entries([cluster], ledger, now=date(2026, 7, 9))
    entry = new_ledger["entries"]["existing-hash"]

    assert entry["first_seen"] == "2026-07-01"  # untouched
    assert entry["last_seen"] == "2026-07-09"  # bumped
    assert entry["card_id"] is None
    assert entry["status"] == "queued"


def test_upsert_entries_never_touches_card_id_or_status_of_existing_entry():
    cluster = _ranked("already-published", "https://example.test/already-published")
    ledger = {
        "version": 1,
        "entries": {
            "already-published": {
                "card_id": "2026-07-01-some-card",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-01",
                "member_urls": ["https://example.test/already-published"],
            }
        },
    }

    new_ledger = upsert_entries([cluster], ledger, now=date(2026, 7, 9))
    entry = new_ledger["entries"]["already-published"]

    assert entry["card_id"] == "2026-07-01-some-card"
    assert entry["status"] == "published"
    assert entry["last_seen"] == "2026-07-09"  # still bumped


def test_upsert_entries_does_not_mutate_input_ledger():
    cluster = _ranked("some-hash", "https://example.test/some")
    original = empty_ledger()
    original_copy = json.loads(json.dumps(original))

    upsert_entries([cluster], original, now=date(2026, 7, 9))

    assert original == original_copy


def test_upsert_entries_defaults_now_to_today_utc_when_omitted():
    cluster = _ranked("no-now-hash", "https://example.test/no-now")
    new_ledger = upsert_entries([cluster], empty_ledger())
    expected_today = datetime.now(timezone.utc).date().isoformat()
    assert new_ledger["entries"]["no-now-hash"]["first_seen"] == expected_today


# --------------------------------------------------------------------------
# apply_run -- filter then upsert, composed
# --------------------------------------------------------------------------


def test_apply_run_drops_published_and_upserts_only_survivors():
    published = _ranked("hash-published", "https://example.test/published2")
    fresh = _ranked("hash-fresh", "https://example.test/fresh")

    ledger = {
        "version": 1,
        "entries": {
            "hash-published": {
                "card_id": "2026-07-01-card",
                "status": "published",
                "first_seen": "2026-07-01",
                "last_seen": "2026-07-01",
                "member_urls": ["https://example.test/published2"],
            }
        },
    }

    survivors, new_ledger = apply_run([published, fresh], ledger, now=date(2026, 7, 9))

    assert survivors == [fresh]
    # The published entry's ledger record is completely untouched --
    # not even a last_seen bump -- since it never entered the upsert step.
    assert new_ledger["entries"]["hash-published"] == ledger["entries"]["hash-published"]
    assert new_ledger["entries"]["hash-fresh"]["card_id"] is None
    assert new_ledger["entries"]["hash-fresh"]["status"] == "queued"
    assert len(new_ledger["entries"]) == 2


# --------------------------------------------------------------------------
# Fixture-level rehearsal of the Phase 1 acceptance criterion: running
# the fetch-to-ledger flow twice on IDENTICAL fixture input adds ZERO new
# ledger entries the second time.
# --------------------------------------------------------------------------


def _fixture_items() -> list[Item]:
    """A realistic mixed-source pool, same shape as the clustering/ranking
    integration test's fixture -- lab + HN + arXiv items, some of which
    cluster together (exact-URL or Jaccard match)."""
    return [
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
            url="https://news.ycombinator.com/item?id=100",
            published_at="2026-07-08T11:00:00Z",
            points=200,
        ),
        Item(
            source_type="arxiv",
            source_name="arxiv",
            title="A Completely Unrelated Paper About Reinforcement Learning",
            url="https://arxiv.org/abs/2607.00001",
            published_at="2026-07-08T09:00:00Z",
        ),
        Item(
            source_type="lab",
            source_name="deepmind",
            title="Google DeepMind Publishes New Benchmark Results",
            url="https://deepmind.google/blog/new-benchmark?utm_source=hn",
            published_at="2026-07-08T08:00:00Z",
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="Totally different headline sharing zero title tokens",
            url="https://deepmind.google/blog/new-benchmark",
            published_at="2026-07-08T12:00:00Z",
            points=80,
        ),
        Item(
            source_type="hn",
            source_name="hn",
            title="Someone Built A Small Weekend AI Side Project",
            url="https://news.ycombinator.com/item?id=101",
            published_at="2026-07-08T07:00:00Z",
            points=5,
        ),
    ]


def _run_watcher_flow(items: list[Item], ledger: dict, *, now: datetime) -> tuple[list, dict]:
    """Stand-in for the watcher CLI's fetch -> cluster -> rank -> ledger
    pipeline, fixture-level: real cluster_items/rank_clusters/apply_run,
    just fed fixture Items directly instead of live HTTP fetches."""
    clusters = cluster_items(items)
    ranked = rank_clusters(clusters, now=now)
    return apply_run(ranked, ledger, now=now)


def test_second_identical_run_adds_zero_new_ledger_entries(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    items = _fixture_items()

    # --- Run 1: fresh ledger, first time seeing these clusters. ---
    ledger = load_ledger(ledger_path)  # missing file -> empty_ledger()
    _survivors_1, ledger_after_run_1 = _run_watcher_flow(items, ledger, now=FIXED_NOW)
    save_ledger(ledger_after_run_1, ledger_path)

    entries_after_run_1 = ledger_after_run_1["entries"]
    assert len(entries_after_run_1) > 0  # sanity: something was ingested
    keys_after_run_1 = set(entries_after_run_1.keys())

    # --- Run 2: identical fixture input (even shuffled arrival order,
    # as a real second fetch's item order is not guaranteed to match the
    # first), one day later. ---
    shuffled_items = items[:]
    random.Random(99).shuffle(shuffled_items)
    later = FIXED_NOW.replace(day=FIXED_NOW.day + 1)

    ledger_before_run_2 = load_ledger(ledger_path)
    assert ledger_before_run_2 == ledger_after_run_1  # round-tripped intact

    _survivors_2, ledger_after_run_2 = _run_watcher_flow(
        shuffled_items, ledger_before_run_2, now=later
    )
    save_ledger(ledger_after_run_2, ledger_path)

    entries_after_run_2 = ledger_after_run_2["entries"]
    keys_after_run_2 = set(entries_after_run_2.keys())

    # The critical assertion: zero *new* keys on the second identical run.
    assert keys_after_run_2 == keys_after_run_1
    assert len(entries_after_run_2) == len(entries_after_run_1)

    # Every entry's first_seen is unchanged, but last_seen has moved
    # forward -- a timestamp bump is explicitly not a new entry.
    for cluster_hash, entry_1 in entries_after_run_1.items():
        entry_2 = entries_after_run_2[cluster_hash]
        assert entry_2["first_seen"] == entry_1["first_seen"]
        assert entry_2["last_seen"] == later.date().isoformat()
        assert entry_2["last_seen"] != entry_1["last_seen"]
        assert entry_2["card_id"] == entry_1["card_id"] is None
        assert entry_2["status"] == entry_1["status"] == "queued"

    # Final on-disk ledger is still schema-valid after both runs.
    reloaded = load_ledger(ledger_path)
    assert reloaded == ledger_after_run_2


def test_third_run_with_a_genuinely_new_story_adds_exactly_one_entry(tmp_path):
    ledger_path = tmp_path / "ledger.json"
    items = _fixture_items()

    ledger = load_ledger(ledger_path)
    _survivors, ledger = _run_watcher_flow(items, ledger, now=FIXED_NOW)
    save_ledger(ledger, ledger_path)
    keys_before = set(ledger["entries"].keys())

    # A brand-new, unrelated story lands on top of the same fixture pool.
    new_story = Item(
        source_type="lab",
        source_name="anthropic",
        title="Anthropic Announces A Totally New Model Family",
        url="https://anthropic.com/news/totally-new-model-family",
        published_at="2026-07-09T09:00:00Z",
    )

    ledger = load_ledger(ledger_path)
    _survivors, ledger = _run_watcher_flow(items + [new_story], ledger, now=FIXED_NOW)
    save_ledger(ledger, ledger_path)

    keys_after = set(ledger["entries"].keys())
    assert len(keys_after) == len(keys_before) + 1
    assert keys_before <= keys_after
