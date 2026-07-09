"""Tests for the Phase 2 ledger extension's *behavior* (not just its
schema shape, already covered by tests/test_p2_schemas.py): the extended
v2 fields (``verifier_outcome``, the ``status: "dropped"`` => ``card_id:
null`` invariant) round-trip through the real ``watcher.ledger.
load_ledger``/``save_ledger`` functions, and ``scripts.reconcile_run.
reconcile_ledger`` (the code that actually *produces* a finalized
published-or-dropped entry after an analyst/verifier run) leaves
``card_id`` permanently ``null`` for a dropped ``cluster_hash`` across
repeated reconciliation attempts.

No live clock anywhere: every ``now`` is an explicit, fixed
``datetime``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from jsonschema import ValidationError

from scripts.reconcile_run import DROPPED_REASON_NO_CARD, reconcile_ledger
from watcher.ledger import (
    apply_run,
    empty_ledger,
    load_ledger,
    save_ledger,
    unpublished_clusters,
)
from watcher.models import Item
from watcher.ranking import rank_clusters
from watcher.clustering import cluster_items
from watcher.schema_validate import validate

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 10, 9, 30, 0, tzinfo=timezone.utc)
FIXED_NOW = datetime(2026, 7, 9, 4, 0, 0, tzinfo=timezone.utc)


def _ranked(cluster_hash: str, url: str = "https://example.test/x"):
    """Same fixture-building helper as tests/test_ledger.py's own
    ``_ranked``: a real ``RankedCluster`` with its ``cluster_hash``
    overwritten to a caller-chosen value, so a test can target a specific
    ledger key without fighting real hash derivation."""
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
    return ranked.__class__(
        rank=ranked.rank, score=ranked.score, cluster_hash=cluster_hash, cluster=ranked.cluster
    )

CLUSTER_HASH = "a" * 64
CARD_ID = "2026-07-09-example-release-abc123"


def _queued_ledger(cluster_hash: str = CLUSTER_HASH, **entry_overrides) -> dict:
    entry = {
        "card_id": None,
        "status": "queued",
        "first_seen": "2026-07-08",
        "last_seen": "2026-07-09",
        "member_urls": ["https://example-lab.test/blog/example-model-5"],
    }
    entry.update(entry_overrides)
    return {"version": 1, "entries": {cluster_hash: entry}}


def _cluster(cluster_hash: str = CLUSTER_HASH, card_id: str = CARD_ID) -> dict:
    return {"cluster_hash": cluster_hash, "proposed_card_id": card_id, "rank": 1}


def _card(card_id: str = CARD_ID, status: str = "confirmed") -> dict:
    return {"id": card_id, "status": status}


# --------------------------------------------------------------------------
# Extended v2 fields round-trip through the real load/save functions
# --------------------------------------------------------------------------


def test_dropped_entry_with_verifier_outcome_round_trips_through_save_and_load(tmp_path):
    ledger = {
        "version": 1,
        "entries": {
            CLUSTER_HASH: {
                "card_id": None,
                "status": "dropped",
                "first_seen": "2026-07-05",
                "last_seen": "2026-07-06",
                "member_urls": ["https://example-news.test/2026/07/05/rumored-model"],
                "verifier_outcome": {
                    "last_attempted_at": "2026-07-06T10:00:00Z",
                    "dropped_reason": "no citation survived re-fetch; hollowed-out card",
                    "demoted_from_confirmed": False,
                },
            }
        },
    }
    path = tmp_path / "ledger.json"

    save_ledger(ledger, path)
    loaded = load_ledger(path)

    assert loaded == ledger
    validate(loaded, "ledger")  # must not raise


def test_published_entry_with_demoted_verifier_outcome_round_trips(tmp_path):
    """A card that was demoted (CONFIRMED -> REPORTED) but not dropped
    keeps its card_id/status: published, with verifier_outcome recording
    the demotion."""
    ledger = {
        "version": 1,
        "entries": {
            CLUSTER_HASH: {
                "card_id": CARD_ID,
                "status": "published",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example-lab.test/blog/example-model-5"],
                "verifier_outcome": {
                    "last_attempted_at": "2026-07-09T08:00:00Z",
                    "demoted_from_confirmed": True,
                },
            }
        },
    }
    path = tmp_path / "ledger.json"

    save_ledger(ledger, path)
    loaded = load_ledger(path)

    assert loaded == ledger
    assert loaded["entries"][CLUSTER_HASH]["card_id"] == CARD_ID
    validate(loaded, "ledger")


def test_pre_phase2_entry_without_verifier_outcome_still_round_trips(tmp_path):
    """Every Phase 1 entry (no verifier_outcome key at all) must still
    round-trip unchanged through the extended schema."""
    ledger = empty_ledger()
    ledger["entries"][CLUSTER_HASH] = {
        "card_id": None,
        "status": "queued",
        "first_seen": "2026-07-09",
        "last_seen": "2026-07-09",
        "member_urls": ["https://example-lab.test/blog/example-model-5"],
    }
    path = tmp_path / "ledger.json"

    save_ledger(ledger, path)
    loaded = load_ledger(path)

    assert loaded == ledger
    assert "verifier_outcome" not in loaded["entries"][CLUSTER_HASH]


# --------------------------------------------------------------------------
# reconcile_ledger: a cluster with no resulting card is finalized "dropped"
# --------------------------------------------------------------------------


def test_reconcile_ledger_drops_a_cluster_with_no_resulting_card():
    ledger = _queued_ledger()
    clusters = [_cluster()]

    new_ledger = reconcile_ledger(clusters, ledger, cards_by_cluster={}, now=NOW)

    entry = new_ledger["entries"][CLUSTER_HASH]
    assert entry["status"] == "dropped"
    assert entry["card_id"] is None
    assert entry["verifier_outcome"]["last_attempted_at"] == "2026-07-09T12:00:00Z"
    assert entry["verifier_outcome"]["dropped_reason"] == DROPPED_REASON_NO_CARD
    validate(new_ledger, "ledger")  # must not raise -- satisfies the if/then


def test_reconcile_ledger_publishes_a_cluster_with_a_resulting_card():
    ledger = _queued_ledger()
    clusters = [_cluster()]
    cards_by_cluster = {CLUSTER_HASH: _card()}

    new_ledger = reconcile_ledger(clusters, ledger, cards_by_cluster, now=NOW)

    entry = new_ledger["entries"][CLUSTER_HASH]
    assert entry["status"] == "published"
    assert entry["card_id"] == CARD_ID
    # A published entry with no verifier_outcome key added by this step
    # (the analyst/verifier's own conversation isn't itself persisted
    # here) is still a fully valid ledger entry.
    validate(new_ledger, "ledger")


def test_reconcile_ledger_does_not_mutate_the_input_ledger():
    ledger = _queued_ledger()
    original_entry = dict(ledger["entries"][CLUSTER_HASH])
    clusters = [_cluster()]

    reconcile_ledger(clusters, ledger, cards_by_cluster={}, now=NOW)

    assert ledger["entries"][CLUSTER_HASH] == original_entry


def test_reconcile_ledger_unknown_cluster_hash_is_skipped_not_raised(caplog):
    """A run_plan cluster_hash with no existing ledger entry at all
    (shouldn't happen in the real pipeline) is defensively skipped with a
    warning, not a crash."""
    ledger = empty_ledger()  # no entries at all
    clusters = [_cluster()]

    with caplog.at_level("WARNING"):
        new_ledger = reconcile_ledger(clusters, ledger, cards_by_cluster={}, now=NOW)

    assert new_ledger["entries"] == {}
    assert "no existing ledger entry" in caplog.text


# --------------------------------------------------------------------------
# permanence: a dropped cluster_hash's card_id stays null forever
# --------------------------------------------------------------------------


def test_dropped_cluster_hash_card_id_stays_null_across_repeated_reconciliation():
    """Simulates the cluster resurfacing (still uncorroborated, same
    exact cluster_hash) across multiple later runs that also fail to
    produce a card -- card_id must never become non-null for this hash
    while it keeps failing to publish."""
    ledger = _queued_ledger()
    clusters = [_cluster()]

    ledger = reconcile_ledger(clusters, ledger, cards_by_cluster={}, now=NOW)
    assert ledger["entries"][CLUSTER_HASH]["status"] == "dropped"
    assert ledger["entries"][CLUSTER_HASH]["card_id"] is None

    # A second, later reconciliation attempt for the exact same
    # cluster_hash, still with no card produced.
    ledger = reconcile_ledger(clusters, ledger, cards_by_cluster={}, now=LATER)
    assert ledger["entries"][CLUSTER_HASH]["status"] == "dropped"
    assert ledger["entries"][CLUSTER_HASH]["card_id"] is None
    assert (
        ledger["entries"][CLUSTER_HASH]["verifier_outcome"]["last_attempted_at"]
        == "2026-07-10T09:30:00Z"
    )
    validate(ledger, "ledger")


def test_dropped_cluster_hash_card_id_never_forced_non_null_by_schema():
    """Belt-and-suspenders: even attempting to hand-construct a dropped
    entry with a non-null card_id fails schema validation outright -- the
    schema itself is the permanent backstop for the "card_id stays null"
    invariant reconcile_ledger relies on."""
    ledger = {
        "version": 1,
        "entries": {
            CLUSTER_HASH: {
                "card_id": CARD_ID,
                "status": "dropped",
                "first_seen": "2026-07-05",
                "last_seen": "2026-07-06",
                "member_urls": ["https://example-news.test/2026/07/05/rumored-model"],
            }
        },
    }
    with pytest.raises(ValidationError):
        validate(ledger, "ledger")


def test_reconcile_ledger_end_to_end_via_save_and_load_round_trip(tmp_path):
    """Full loop: reconcile a mixed batch (one published, one dropped),
    persist via the real save_ledger/load_ledger, and confirm both
    outcomes survive the round trip untouched."""
    published_hash = "b" * 64
    dropped_hash = "c" * 64
    ledger = {
        "version": 1,
        "entries": {
            published_hash: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example-lab.test/blog/one"],
            },
            dropped_hash: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example-lab.test/blog/two"],
            },
        },
    }
    clusters = [
        {"cluster_hash": published_hash, "proposed_card_id": "card-one", "rank": 1},
        {"cluster_hash": dropped_hash, "proposed_card_id": "card-two", "rank": 2},
    ]
    cards_by_cluster = {published_hash: {"id": "card-one", "status": "reported"}}

    new_ledger = reconcile_ledger(clusters, ledger, cards_by_cluster, now=NOW)
    path = tmp_path / "ledger.json"
    save_ledger(new_ledger, path)
    loaded = load_ledger(path)

    assert loaded["entries"][published_hash]["status"] == "published"
    assert loaded["entries"][published_hash]["card_id"] == "card-one"
    assert loaded["entries"][dropped_hash]["status"] == "dropped"
    assert loaded["entries"][dropped_hash]["card_id"] is None


# --------------------------------------------------------------------------
# unpublished_clusters / apply_run: a "dropped" cluster_hash's null
# card_id must never be mistaken for a fresh, still-unpublished one.
# --------------------------------------------------------------------------


def _dropped_entry(**overrides) -> dict:
    entry = {
        "card_id": None,
        "status": "dropped",
        "first_seen": "2026-07-01",
        "last_seen": "2026-07-02",
        "member_urls": ["https://example-news.test/2026/07/01/rumored-model"],
        "verifier_outcome": {
            "last_attempted_at": "2026-07-02T10:00:00Z",
            "dropped_reason": "no citation survived re-fetch; hollowed-out card",
        },
    }
    entry.update(overrides)
    return entry


def test_unpublished_clusters_excludes_dropped_entries_despite_null_card_id():
    """A dropped entry's card_id is null by definition (same as a
    genuinely fresh, never-seen cluster's) -- unpublished_clusters must
    key off status, not card_id alone, or a permanently-dropped
    cluster_hash would silently resurrect the moment its exact member
    URLs reappear in a later fetch."""
    dropped = _ranked("hash-dropped", "https://example-news.test/2026/07/01/rumored-model")
    fresh = _ranked("hash-fresh", "https://example.test/brand-new")

    ledger = {
        "version": 1,
        "entries": {"hash-dropped": _dropped_entry()},
    }

    survivors = unpublished_clusters([dropped, fresh], ledger)

    assert survivors == [fresh]


def test_apply_run_never_requeues_a_dropped_cluster_hash_that_resurfaces():
    """End-to-end: the exact same cluster_hash that was previously
    dropped resurfaces in a later run's ranked_clusters (e.g. a feed
    still lists the same items) -- apply_run must not resurrect it into
    the surviving/queued set, and upsert_entries must leave its
    status/card_id exactly as they were (dropped/null), never reverting
    to "queued"."""
    cluster_hash = "hash-dropped"
    resurfaced = _ranked(cluster_hash, "https://example-news.test/2026/07/01/rumored-model")

    ledger = {
        "version": 1,
        "entries": {cluster_hash: _dropped_entry()},
    }

    survivors, new_ledger = apply_run([resurfaced], ledger, now=LATER)

    assert survivors == []
    entry = new_ledger["entries"][cluster_hash]
    assert entry["status"] == "dropped"
    assert entry["card_id"] is None
    # apply_run only upserts survivors -- a dropped entry that was
    # correctly excluded from the survivor set must be left completely
    # untouched (not even last_seen-bumped), matching the module's own
    # "already-published entry left completely untouched" convention for
    # any other finalized/terminal entry.
    assert entry == ledger["entries"][cluster_hash]
