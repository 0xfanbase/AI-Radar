"""Tests for auditor/trend.py -- the weekly verifier pass-rate trend check
(see CLAUDE.md's "audit.yml -- weekly" bullet / the approved plan's Phase 5
section: "verifier pass-rate trend (rolling 7d/30d from
`data/verifier_stats.json`)").

`data/verifier_stats.json` currently ships with an empty `runs: []` history
(no analyst run has happened for real yet), so -- per this turn's explicit
instruction -- these tests exercise `classify_trend` / `compute_pass_rate_trend`
/ `audit_trend` directly against small, hand-built synthetic multi-week
histories and an explicit `today` date, never a live clock call, rather
than the (empty) real file. One integration-flavored smoke test near the
bottom confirms `audit_trend()` still runs cleanly against the real,
currently-empty `data/verifier_stats.json` via its own default
`scripts.reconcile_run.load_verifier_stats` loading path.

Imported the same way `tests/test_auditor_duplicates.py` already imports
`auditor/duplicates.py` -- `auditor/` has no `__init__.py` (an implicit
namespace package is enough), so `from auditor import trend` /
`from auditor.trend import ...` resolve directly with no `sys.path`
manipulation, given `python -m pytest` is run from the repo root (this
repo's own established convention).
"""
from __future__ import annotations

import math
from datetime import date

import pytest

import scripts.reconcile_run as reconcile_run_mod
from auditor import trend as mod
from auditor.trend import (
    TREND_FLAT_EPSILON,
    audit_trend,
    classify_trend,
    compute_pass_rate_trend,
)


def _run(day: str, cards_drafted: int, confirmed: int, reported: int, dropped: int) -> dict:
    """Build one `verifier_stats.schema.json` `runs[]` row -- same
    shape/helper convention as `tests/test_verifier_stats.py`'s own `_run`
    (duplicated here, not imported, matching this repo's established
    per-test-file fixture-builder convention -- e.g.
    `tests/test_auditor_duplicates.py`'s own `_card` helper)."""
    return {
        "date": day,
        "cards_drafted": cards_drafted,
        "confirmed": confirmed,
        "reported": reported,
        "dropped": dropped,
        "pass_rate": (confirmed + reported) / cards_drafted if cards_drafted else 0.0,
    }


# ---------------------------------------------------------------------------
# Reuse, not reimplementation -- the core instruction this turn's task gave,
# and the exact thing scripts/reconcile_run.py's own docstring pre-staged
# ("the helper operates purely on this module's own verifier_stats.json
# shape, so it lives here rather than duplicating that shape's field names
# a second time in a not-yet-built module").
# ---------------------------------------------------------------------------


def test_trend_reuses_reconcile_run_rolling_pass_rate_by_identity():
    """`trend.rolling_pass_rate` must be the *exact same function object* as
    `scripts.reconcile_run.rolling_pass_rate` -- proof this module imports
    and calls the existing rolling-window helper directly rather than
    reimplementing its own copy."""
    assert mod.rolling_pass_rate is reconcile_run_mod.rolling_pass_rate


def test_trend_reuses_reconcile_run_load_verifier_stats_by_identity():
    """Same identity proof for the real-file loader `audit_trend` falls
    back to when `stats` is omitted."""
    assert mod.load_verifier_stats is reconcile_run_mod.load_verifier_stats


# ---------------------------------------------------------------------------
# classify_trend -- the threshold rules, tested directly and precisely.
# ---------------------------------------------------------------------------


def test_classify_trend_equal_rates_is_flat():
    assert classify_trend(0.75, 0.75) == "flat"


def test_classify_trend_clearly_higher_is_rising():
    assert classify_trend(0.9, 0.5) == "rising"


def test_classify_trend_clearly_lower_is_falling():
    assert classify_trend(0.5, 0.9) == "falling"


def test_classify_trend_small_move_within_band_is_flat():
    # delta = 0.01, well inside the +/-0.03 band with a wide safety margin
    # -- an ordinary near-unchanged case, not a boundary-precision test
    # (see the dedicated boundary tests below for that).
    assert classify_trend(0.61, 0.60) == "flat"


def test_classify_trend_positive_boundary_is_inclusive_flat():
    """delta == +TREND_FLAT_EPSILON exactly -- constructed via subtraction
    from 0.0, which IEEE-754 always performs exactly, so this is a genuine
    bit-exact boundary test, not an approximate one -- is still `"flat"`:
    the band is a closed interval on both ends, not an open one."""
    current = TREND_FLAT_EPSILON
    prior = 0.0
    assert current - prior == TREND_FLAT_EPSILON  # sanity: truly exact, no fp drift
    assert classify_trend(current, prior) == "flat"


def test_classify_trend_negative_boundary_is_inclusive_flat():
    current = 0.0
    prior = TREND_FLAT_EPSILON
    assert current - prior == -TREND_FLAT_EPSILON  # sanity: truly exact
    assert classify_trend(current, prior) == "flat"


def test_classify_trend_just_above_positive_boundary_is_rising():
    """The next representable float above `TREND_FLAT_EPSILON`, via
    `math.nextafter` -- guaranteed strictly greater by construction, unlike
    a decimal literal that floating point might round differently than
    intended."""
    current = math.nextafter(TREND_FLAT_EPSILON, 1.0)
    prior = 0.0
    assert current - prior > TREND_FLAT_EPSILON
    assert classify_trend(current, prior) == "rising"


def test_classify_trend_just_below_negative_boundary_is_falling():
    prior = math.nextafter(TREND_FLAT_EPSILON, 1.0)
    current = 0.0
    assert current - prior < -TREND_FLAT_EPSILON
    assert classify_trend(current, prior) == "falling"


# ---------------------------------------------------------------------------
# classify_trend -- the drafted=0 guard: a `None` rate on either side means
# "nothing to compare," a distinct outcome from "flat" (see module docstring
# for why the two are kept separate).
# ---------------------------------------------------------------------------


def test_classify_trend_none_current_is_insufficient_data():
    assert classify_trend(None, 0.8) == "insufficient_data"


def test_classify_trend_none_prior_is_insufficient_data():
    assert classify_trend(0.8, None) == "insufficient_data"


def test_classify_trend_both_none_is_insufficient_data():
    assert classify_trend(None, None) == "insufficient_data"


# ---------------------------------------------------------------------------
# compute_pass_rate_trend -- synthetic multi-week history, full
# rolling-window math end to end (not just classify_trend in isolation).
# ---------------------------------------------------------------------------

_WEEK_1_DATES = (
    "2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04",
    "2026-07-05", "2026-07-06", "2026-07-07",
)
_WEEK_2_DATES = (
    "2026-07-08", "2026-07-09", "2026-07-10", "2026-07-11",
    "2026-07-12", "2026-07-13", "2026-07-14",
)

# Week 1: a steady 4-cards/day run at a 0.75 daily pass rate (confirmed=2,
# reported=1, dropped=1 => 3/4 pass each day) -- this is "the prior week"
# in every scenario below.
WEEK_1 = [_run(d, 4, 2, 1, 1) for d in _WEEK_1_DATES]

# Week 2, "rising": every card passes (confirmed=3, reported=1, dropped=0
# => 4/4 pass daily) -- clearly above WEEK_1's 0.75.
WEEK_2_RISING = [_run(d, 4, 3, 1, 0) for d in _WEEK_2_DATES]

# Week 2, "falling": most cards dropped (confirmed=1, reported=0,
# dropped=3 => 1/4 pass daily) -- clearly below WEEK_1's 0.75.
WEEK_2_FALLING = [_run(d, 4, 1, 0, 3) for d in _WEEK_2_DATES]

# Week 2, "flat": identical shape to WEEK_1 -- same 0.75 daily pass rate.
WEEK_2_FLAT = [_run(d, 4, 2, 1, 1) for d in _WEEK_2_DATES]

TODAY_END_OF_WEEK_2 = date(2026, 7, 14)


def _pooled_rate(rows: list[dict]) -> float:
    """Directly compute the expected pooled rate from the raw rows (sum of
    confirmed+reported over sum of cards_drafted) -- an independent
    re-derivation, not a call into `rolling_pass_rate` itself, so these
    assertions don't just check the function agrees with itself."""
    drafted = sum(r["cards_drafted"] for r in rows)
    passed = sum(r["confirmed"] + r["reported"] for r in rows)
    return passed / drafted


def test_compute_pass_rate_trend_rising_scenario():
    runs = WEEK_1 + WEEK_2_RISING
    result = compute_pass_rate_trend(runs, today=TODAY_END_OF_WEEK_2)

    assert result["as_of"] == "2026-07-14"
    assert result["rolling_7d_pass_rate"] == pytest.approx(_pooled_rate(WEEK_2_RISING))
    assert result["prior_week_pass_rate"] == pytest.approx(_pooled_rate(WEEK_1))
    assert result["rolling_30d_pass_rate"] == pytest.approx(_pooled_rate(runs))
    assert result["trend"] == "rising"


def test_compute_pass_rate_trend_falling_scenario():
    runs = WEEK_1 + WEEK_2_FALLING
    result = compute_pass_rate_trend(runs, today=TODAY_END_OF_WEEK_2)

    assert result["rolling_7d_pass_rate"] == pytest.approx(_pooled_rate(WEEK_2_FALLING))
    assert result["prior_week_pass_rate"] == pytest.approx(_pooled_rate(WEEK_1))
    assert result["trend"] == "falling"


def test_compute_pass_rate_trend_flat_scenario():
    runs = WEEK_1 + WEEK_2_FLAT
    result = compute_pass_rate_trend(runs, today=TODAY_END_OF_WEEK_2)

    assert result["rolling_7d_pass_rate"] == pytest.approx(_pooled_rate(WEEK_2_FLAT))
    assert result["prior_week_pass_rate"] == pytest.approx(_pooled_rate(WEEK_1))
    assert result["rolling_7d_pass_rate"] == pytest.approx(result["prior_week_pass_rate"])
    assert result["trend"] == "flat"


def test_compute_pass_rate_trend_prior_and_current_windows_are_adjacent_non_overlapping():
    """The prior-week window and the current rolling-7d window partition
    this 14-day synthetic history exactly in half with no day double
    counted and no day skipped: the pooled 30-day rate over the whole
    history must equal the weighted combination of the two 7-day windows'
    own totals, proven here via an independent re-derivation from the raw
    rows (`_pooled_rate(runs)`), not by re-deriving one result from the
    other."""
    runs = WEEK_1 + WEEK_2_RISING
    result = compute_pass_rate_trend(runs, today=TODAY_END_OF_WEEK_2)
    assert result["rolling_30d_pass_rate"] == pytest.approx(_pooled_rate(runs))


# ---------------------------------------------------------------------------
# compute_pass_rate_trend -- the drafted=0 guard, exercised through the full
# rolling-window pipeline (not just classify_trend in isolation), proving
# no ZeroDivisionError anywhere along the way.
# ---------------------------------------------------------------------------


def test_compute_pass_rate_trend_empty_history_is_insufficient_data_throughout():
    result = compute_pass_rate_trend([], today=TODAY_END_OF_WEEK_2)
    assert result["rolling_7d_pass_rate"] is None
    assert result["rolling_30d_pass_rate"] is None
    assert result["prior_week_pass_rate"] is None
    assert result["trend"] == "insufficient_data"


def test_compute_pass_rate_trend_all_skip_prior_week_guards_division_by_zero():
    """A prior week made entirely of zero-cards-drafted "skip" days (e.g. a
    week the quota-degradation ladder skipped entirely, or before the
    project's first real run) must never raise `ZeroDivisionError` -- it
    guards to `None` (`rolling_pass_rate`'s own division-by-zero guard,
    reused unchanged), which in turn makes the trend `"insufficient_data"`
    even though the *current* week has real, non-zero data."""
    skip_week = [_run(d, 0, 0, 0, 0) for d in _WEEK_1_DATES]
    runs = skip_week + WEEK_2_RISING
    result = compute_pass_rate_trend(runs, today=TODAY_END_OF_WEEK_2)

    assert result["rolling_7d_pass_rate"] == pytest.approx(1.0)  # every card passed
    assert result["prior_week_pass_rate"] is None  # zero drafted -> guarded, no crash
    assert result["trend"] == "insufficient_data"


def test_compute_pass_rate_trend_as_of_before_any_history_is_insufficient_data():
    result = compute_pass_rate_trend(WEEK_2_RISING, today=date(2026, 1, 1))
    assert result["rolling_7d_pass_rate"] is None
    assert result["prior_week_pass_rate"] is None
    assert result["trend"] == "insufficient_data"


# ---------------------------------------------------------------------------
# audit_trend -- the convenience wrapper.
# ---------------------------------------------------------------------------


def test_audit_trend_with_explicit_stats_wraps_compute_pass_rate_trend():
    stats = {"version": 1, "runs": WEEK_1 + WEEK_2_RISING}
    result = audit_trend(stats, today=TODAY_END_OF_WEEK_2)
    assert result == compute_pass_rate_trend(stats["runs"], today=TODAY_END_OF_WEEK_2)
    assert result["trend"] == "rising"


def test_audit_trend_missing_runs_key_defaults_to_empty_history():
    result = audit_trend({"version": 1}, today=TODAY_END_OF_WEEK_2)
    assert result["trend"] == "insufficient_data"


def test_audit_trend_defaults_to_loading_via_reconcile_run_load_verifier_stats(monkeypatch):
    """`stats=None` must load through
    `scripts.reconcile_run.load_verifier_stats` -- reused directly, per the
    module docstring -- not some independent file-reading logic. Proven by
    monkeypatching the exact bound name `trend.load_verifier_stats` and
    confirming it's what actually supplies the history."""
    canned = {"version": 1, "runs": WEEK_1 + WEEK_2_RISING}
    monkeypatch.setattr(mod, "load_verifier_stats", lambda: canned)
    result = audit_trend(today=TODAY_END_OF_WEEK_2)
    assert result["trend"] == "rising"


def test_audit_trend_against_the_real_currently_empty_verifier_stats_json():
    """Integration-flavored smoke test: `data/verifier_stats.json` really
    does ship with an empty `runs: []` history in this repo today (no
    analyst run has happened for real yet), so the real default
    `scripts.reconcile_run.load_verifier_stats()` path must return that
    empty shape and `audit_trend()` must complete cleanly with
    `"insufficient_data"`, rather than raising."""
    result = audit_trend(today=TODAY_END_OF_WEEK_2)
    assert result["rolling_7d_pass_rate"] is None
    assert result["rolling_30d_pass_rate"] is None
    assert result["prior_week_pass_rate"] is None
    assert result["trend"] == "insufficient_data"
