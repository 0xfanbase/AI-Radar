"""Tests for data/verifier_stats.json's append/read helpers and the rolling
pass-rate computation, all in scripts/reconcile_run.py.

Covers, in order: compute_verifier_stats_row's per-run arithmetic
(including the cards_drafted == 0 "skip run" edge case), the
load/append/save round trip (never mutating an input dict in place,
matching this repo's established ledger/queue-writer convention), and
rolling_pass_rate's pooled-across-a-window computation -- exercised
exclusively against a synthetic, hand-built `runs[]` history list with a
fixed, explicitly-passed `as_of` date, never `datetime.now()` or any other
live-clock read.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from jsonschema import ValidationError

from scripts.reconcile_run import (
    VERIFIER_STATS_VERSION,
    append_verifier_stats_row,
    compute_verifier_stats_row,
    empty_verifier_stats,
    load_verifier_stats,
    reconcile_run,
    rolling_pass_rate,
    save_verifier_stats,
)
from watcher.ledger import load_ledger, save_ledger
from watcher.schema_validate import validate

NOW = datetime(2026, 7, 9, 7, 15, 0, tzinfo=timezone.utc)


def _cluster(cluster_hash: str, card_id: str, rank: int = 1) -> dict:
    return {"cluster_hash": cluster_hash, "proposed_card_id": card_id, "rank": rank}


# --------------------------------------------------------------------------
# compute_verifier_stats_row
# --------------------------------------------------------------------------


def test_compute_verifier_stats_row_counts_confirmed_reported_dropped():
    clusters = [
        _cluster("h1", "card-1"),
        _cluster("h2", "card-2"),
        _cluster("h3", "card-3"),
        _cluster("h4", "card-4"),
    ]
    cards_by_cluster = {
        "h1": {"id": "card-1", "status": "confirmed"},
        "h2": {"id": "card-2", "status": "confirmed"},
        "h3": {"id": "card-3", "status": "reported"},
        # h4 has no card -> dropped
    }

    row = compute_verifier_stats_row(clusters, cards_by_cluster, now=NOW)

    assert row == {
        "date": "2026-07-09",
        "cards_drafted": 4,
        "confirmed": 2,
        "reported": 1,
        "dropped": 1,
        "pass_rate": pytest.approx(3 / 4),
    }
    validate({"version": 1, "runs": [row]}, "verifier_stats")


def test_compute_verifier_stats_row_empty_run_has_zero_counts_and_zero_pass_rate():
    row = compute_verifier_stats_row([], {}, now=NOW)

    assert row == {
        "date": "2026-07-09",
        "cards_drafted": 0,
        "confirmed": 0,
        "reported": 0,
        "dropped": 0,
        "pass_rate": 0.0,
    }
    validate({"version": 1, "runs": [row]}, "verifier_stats")


def test_compute_verifier_stats_row_all_confirmed_pass_rate_is_one():
    clusters = [_cluster("h1", "card-1"), _cluster("h2", "card-2")]
    cards_by_cluster = {
        "h1": {"id": "card-1", "status": "confirmed"},
        "h2": {"id": "card-2", "status": "confirmed"},
    }

    row = compute_verifier_stats_row(clusters, cards_by_cluster, now=NOW)

    assert row["pass_rate"] == 1.0


def test_compute_verifier_stats_row_all_dropped_pass_rate_is_zero():
    clusters = [_cluster("h1", "card-1"), _cluster("h2", "card-2")]

    row = compute_verifier_stats_row(clusters, {}, now=NOW)

    assert row["dropped"] == 2
    assert row["pass_rate"] == 0.0


# --------------------------------------------------------------------------
# load / append / save round trip
# --------------------------------------------------------------------------


def test_load_verifier_stats_missing_file_returns_empty_shape(tmp_path):
    stats = load_verifier_stats(tmp_path / "does-not-exist.json")
    assert stats == empty_verifier_stats() == {"version": VERIFIER_STATS_VERSION, "runs": []}


def test_append_verifier_stats_row_does_not_mutate_input():
    stats = empty_verifier_stats()
    row = {
        "date": "2026-07-09",
        "cards_drafted": 1,
        "confirmed": 1,
        "reported": 0,
        "dropped": 0,
        "pass_rate": 1.0,
    }

    new_stats = append_verifier_stats_row(stats, row)

    assert stats["runs"] == []  # original untouched
    assert new_stats["runs"] == [row]


def test_append_then_save_then_load_round_trips(tmp_path):
    stats = empty_verifier_stats()
    row_1 = {
        "date": "2026-07-08",
        "cards_drafted": 5,
        "confirmed": 3,
        "reported": 1,
        "dropped": 1,
        "pass_rate": 0.8,
    }
    row_2 = {
        "date": "2026-07-09",
        "cards_drafted": 4,
        "confirmed": 2,
        "reported": 1,
        "dropped": 1,
        "pass_rate": 0.75,
    }
    path = tmp_path / "verifier_stats.json"

    stats = append_verifier_stats_row(stats, row_1)
    save_verifier_stats(stats, path)
    stats = load_verifier_stats(path)
    stats = append_verifier_stats_row(stats, row_2)
    save_verifier_stats(stats, path)

    loaded = load_verifier_stats(path)
    assert loaded["runs"] == [row_1, row_2]  # append order preserved
    validate(loaded, "verifier_stats")


def test_save_verifier_stats_rejects_invalid_payload(tmp_path):
    with pytest.raises(ValidationError):
        save_verifier_stats({"version": 1, "runs": [{"date": "2026-07-09"}]}, tmp_path / "s.json")


# --------------------------------------------------------------------------
# rolling_pass_rate -- pooled window computation over a synthetic history,
# never live time.
# --------------------------------------------------------------------------


def _run(day: str, cards_drafted: int, confirmed: int, reported: int, dropped: int) -> dict:
    return {
        "date": day,
        "cards_drafted": cards_drafted,
        "confirmed": confirmed,
        "reported": reported,
        "dropped": dropped,
        "pass_rate": (confirmed + reported) / cards_drafted if cards_drafted else 0.0,
    }


# A synthetic 10-day history, hand-built (never generated from real time),
# spanning 2026-07-01 .. 2026-07-10, with a deliberate zero-card "skip" day
# in the middle (07-05) to prove a quiet day is weightless rather than
# corrupting the pooled rate.
SYNTHETIC_HISTORY = [
    _run("2026-07-01", 8, 6, 2, 0),  # 8 drafted, 8 pass
    _run("2026-07-02", 8, 5, 1, 2),  # 8 drafted, 6 pass
    _run("2026-07-03", 5, 4, 1, 0),  # 5 drafted, 5 pass
    _run("2026-07-04", 5, 3, 0, 2),  # 5 drafted, 3 pass
    _run("2026-07-05", 0, 0, 0, 0),  # skip day -- weightless
    _run("2026-07-06", 8, 8, 0, 0),  # 8 drafted, 8 pass
    _run("2026-07-07", 8, 6, 1, 1),  # 8 drafted, 7 pass
    _run("2026-07-08", 5, 2, 1, 2),  # 5 drafted, 3 pass
    _run("2026-07-09", 5, 3, 2, 0),  # 5 drafted, 5 pass
    _run("2026-07-10", 8, 4, 2, 2),  # 8 drafted, 6 pass
]


def test_rolling_pass_rate_7d_window_pools_only_the_trailing_7_days():
    # as_of 2026-07-10, window_days=7 -> covers 07-04 .. 07-10 inclusive
    # (7 days): rows for 07-04, 07-05, 07-06, 07-07, 07-08, 07-09, 07-10.
    # drafted = 5+0+8+8+5+5+8 = 39; pass = 3+0+8+7+3+5+6 = 32
    rate = rolling_pass_rate(SYNTHETIC_HISTORY, window_days=7, as_of=date(2026, 7, 10))
    assert rate == pytest.approx(32 / 39)


def test_rolling_pass_rate_30d_window_pools_the_entire_synthetic_history():
    # window_days=30 as_of 07-10 comfortably covers the whole 10-day
    # synthetic history (window start would be 06-11).
    total_drafted = sum(r["cards_drafted"] for r in SYNTHETIC_HISTORY)
    total_pass = sum(r["confirmed"] + r["reported"] for r in SYNTHETIC_HISTORY)

    rate = rolling_pass_rate(SYNTHETIC_HISTORY, window_days=30, as_of=date(2026, 7, 10))

    assert rate == pytest.approx(total_pass / total_drafted)


def test_rolling_pass_rate_1d_window_is_just_the_as_of_days_own_rate():
    rate = rolling_pass_rate(SYNTHETIC_HISTORY, window_days=1, as_of=date(2026, 7, 1))
    assert rate == pytest.approx(8 / 8)


def test_rolling_pass_rate_window_containing_only_a_skip_day_is_none():
    rate = rolling_pass_rate(SYNTHETIC_HISTORY, window_days=1, as_of=date(2026, 7, 5))
    assert rate is None


def test_rolling_pass_rate_as_of_date_before_any_history_is_none():
    rate = rolling_pass_rate(SYNTHETIC_HISTORY, window_days=7, as_of=date(2026, 6, 1))
    assert rate is None


def test_rolling_pass_rate_empty_history_is_none():
    assert rolling_pass_rate([], window_days=7, as_of=date(2026, 7, 10)) is None


def test_rolling_pass_rate_moving_the_window_forward_changes_the_pooled_rate():
    """As the synthetic history "rolls" forward day by day, the windowed
    rate is recomputed from a different slice each time -- not cached or
    order-dependent."""
    rate_at_day_3 = rolling_pass_rate(
        SYNTHETIC_HISTORY, window_days=3, as_of=date(2026, 7, 3)
    )
    rate_at_day_6 = rolling_pass_rate(
        SYNTHETIC_HISTORY, window_days=3, as_of=date(2026, 7, 6)
    )
    # day 3 window = 07-01..07-03: drafted 8+8+5=21, pass 8+6+5=19
    assert rate_at_day_3 == pytest.approx(19 / 21)
    # day 6 window = 07-04..07-06: drafted 5+0+8=13, pass 3+0+8=11
    assert rate_at_day_6 == pytest.approx(11 / 13)
    assert rate_at_day_3 != rate_at_day_6


# --------------------------------------------------------------------------
# end-to-end: reconcile_run appends a real row via the full compose
# function, on top of a synthetic pre-existing runs[] history.
# --------------------------------------------------------------------------


def test_reconcile_run_appends_one_row_onto_an_explicit_synthetic_history(tmp_path):
    cluster_hash = "d" * 64
    ledger = {
        "version": 1,
        "entries": {
            cluster_hash: {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example-lab.test/blog/example"],
            }
        },
    }
    run_plan = {
        "version": 1,
        "generated_at": "2026-07-09T07:00:00Z",
        "degradation_level": 0,
        "run_mode": "normal",
        "cards_cap": 8,
        "clusters": [_cluster(cluster_hash, "2026-07-09-example-card")],
        "reason": "normal run",
    }
    # A pre-existing, hand-built synthetic history (not derived from any
    # real prior reconcile_run call) -- proves append_verifier_stats_row
    # is used, not a fresh empty_verifier_stats() every time.
    verifier_stats = {"version": 1, "runs": [SYNTHETIC_HISTORY[0]]}

    cards_dir = tmp_path / "cards"
    cards_dir.mkdir()
    card_path = cards_dir / "2026-07-09-example-card.json"
    card_path.write_text(
        '{"id": "2026-07-09-example-card", "date": "2026-07-09", '
        '"headline": "h", "what_happened": "w", "why_it_matters": "w", '
        '"one_liner": "o", "topics": ["models"], "status": "confirmed", '
        '"citations": [{"url": "https://example-lab.test/x", '
        '"outlet": "Example", "quote": "q"}], "lexicon_terms": [], '
        '"generated_at": "2026-07-09T07:15:00Z", "model": "claude-sonnet-4-5", '
        '"correction_note": null}',
        encoding="utf-8",
    )

    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()

    new_ledger, new_verifier_stats, card_index, company_index = reconcile_run(
        run_plan, ledger, verifier_stats, cards_dir=cards_dir,
        companies_dir=companies_dir, now=NOW,
    )

    assert len(new_verifier_stats["runs"]) == 2
    assert new_verifier_stats["runs"][0] == SYNTHETIC_HISTORY[0]  # untouched
    appended = new_verifier_stats["runs"][1]
    assert appended["date"] == "2026-07-09"
    assert appended["cards_drafted"] == 1
    assert appended["confirmed"] == 1
    assert appended["pass_rate"] == 1.0
    validate(new_verifier_stats, "verifier_stats")

    assert new_ledger["entries"][cluster_hash]["status"] == "published"
    assert new_ledger["entries"][cluster_hash]["card_id"] == "2026-07-09-example-card"

    # Phase 8: company_index is regenerated unconditionally on every run,
    # even one (like this one) that touched no company profile at all --
    # an empty companies/ directory yields a valid, empty index.
    assert company_index == {"version": 1, "companies": []}
    assert (companies_dir / "index.json").is_file()
    assert len(card_index["cards"]) == 1
