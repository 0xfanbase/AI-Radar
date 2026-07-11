"""Verifier pass-rate trend check (`audit.yml`'s weekly, pure-code, no-LLM
pass -- see CLAUDE.md's "audit.yml -- weekly" bullet and the approved build
plan's section 6: "verifier pass-rate trend (rolling 7d/30d from
`data/verifier_stats.json`)").

**Reuse, not reimplementation, per this turn's own explicit instruction and
`scripts/reconcile_run.py`'s own docstring, which pre-staged exactly this
need:** the actual rolling-window pooling arithmetic --
``(confirmed + reported) / cards_drafted`` pooled across every
``verifier_stats.json`` ``runs[]`` row whose ``date`` falls in a trailing
window, with a division-by-zero guard that returns ``None`` rather than
raising or fabricating a ``0.0`` when a window drafted no cards at all --
already exists as :func:`scripts.reconcile_run.rolling_pass_rate` and is
imported and called directly here, not reimplemented. That module's own
docstring says as much: "this directly pre-stages the data shape Phase 5's
weekly `audit.yml` will need for its own 'verifier pass-rate trend (rolling
7d/30d)' check ... the auditor itself is out of this turn's scope, but the
helper operates purely on this module's own `verifier_stats.json` shape, so
it lives here rather than duplicating that shape's field names a second
time in a not-yet-built module." This module is that not-yet-built module,
now built, and it composes that helper rather than re-deriving its logic.

**What this module adds on top of the reused helper**, per this turn's own
scope:

1. **The rolling 7-day and 30-day figures** (:func:`compute_pass_rate_trend`):
   two calls to :func:`~scripts.reconcile_run.rolling_pass_rate` with
   ``window_days=7`` and ``window_days=30`` respectively, both anchored on
   one explicit ``as_of``/``today`` date -- never a live ``datetime.now()``
   call anywhere in this module, so every function here is fully
   unit-testable against a synthetic, hand-built history and an explicit
   date, matching this project's established "no live clock calls inside
   pure-code logic" convention (`watcher/velocity.py`'s own
   ``compute_whats_moving`` follows the identical discipline for its own
   trend check).
2. **A trend classification vs. the prior week's rate**
   (:func:`classify_trend`): the rolling 7-day rate anchored on ``today``
   compared against the rolling 7-day rate anchored on ``today`` minus 7
   days -- i.e. two adjacent, non-overlapping 7-day windows
   (``[today-13, today-7]`` and ``[today-6, today]``), reusing
   :func:`~scripts.reconcile_run.rolling_pass_rate` a second time with a
   shifted ``as_of`` rather than writing a second, parallel window-pooling
   loop here. This is "vs. the prior week's rate" read literally: a
   week-over-week comparison, not a same-weekday-last-week single-day
   comparison and not a comparison against the rolling 30-day figure (which
   is reported alongside purely as context, per the plan's own "rolling
   7d/30d" wording -- it is never itself a trend-classification input).

**Trend labels: `rising`/`falling`/`flat`, plus a fourth `insufficient_data`
state -- a spec-silent naming/scope choice, logged in full in
`IMPROVEMENT_BACKLOG.md`.** This turn's own task text names exactly three
states, `rising`/`falling`/`flat` (deliberately not reusing
`watcher/velocity.py`'s `accelerating`/`cooling`/`flat` naming for its own,
different, HN-mention-count trend check -- no `schemas/audit.schema.json`
exists yet to lock in verbatim wording for *this* check the way
`whats_moving.schema.json`'s enum already locks in its own, so there is no
established-schema reason to match velocity's naming here). A real fourth
state is still needed and is kept distinct from `flat` rather than folded
into it: when either the current or the prior-week rolling rate is `None`
(no cards were drafted anywhere in that window -- the same
division-by-zero guard `rolling_pass_rate` itself already returns `None`
for), there is no rate to compare at all, which is a materially different
finding from "the rate held genuinely steady." Collapsing that into `flat`
would misreport "nothing happened to measure" as "the pass rate didn't
move," which is not the same claim. This turn's task text itself
distinguishes the two as separate things to test ("the trend
classification thresholds, **and** the drafted=0 guard"), which is read
here as confirming, not contradicting, that the guard case is its own
outcome rather than a fourth way to reach `flat`.

**The flat-band threshold** (:data:`TREND_FLAT_EPSILON`, 0.03 -- three
percentage points of pass rate) is a second spec-silent choice, also
logged in full in `IMPROVEMENT_BACKLOG.md`. Unlike `velocity.py`'s own
`_classify_trend` (exact `>`/`<`/`==` on integer daily HN-mention counts,
where exact equality is a common, meaningful outcome), a pass rate is a
continuous ratio of two integers; requiring bit-for-bit equality to ever
call it `flat` would make that label practically unreachable for real
data. A small symmetric epsilon band around zero delta is the simplest
reasonable threshold rule that still lets a genuinely-nearly-unchanged
rate read as `flat` instead of a coin-flip `rising`/`falling` label
driven by noise.

Every function in this module is pure and filesystem-free except
:func:`audit_trend`, which -- matching `auditor.linkrot.audit_link_rot` /
`auditor.lexicon_audit.audit_lexicon` / `auditor.duplicates.audit_duplicates`'s
own established convention -- accepts an already-loaded ``stats`` dict
directly (for testability) and only falls back to loading the real,
on-disk `data/verifier_stats.json` (via
`scripts.reconcile_run.load_verifier_stats`, reused directly rather than a
second, independently-written loader) when ``stats`` is omitted.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from scripts.reconcile_run import load_verifier_stats, rolling_pass_rate

__all__ = [
    "TREND_FLAT_EPSILON",
    "classify_trend",
    "compute_pass_rate_trend",
    "audit_trend",
]

# Spec-silent flat-band threshold -- see module docstring for the full
# reasoning. A week-over-week pass-rate delta whose absolute value is
# strictly greater than this counts as a real move (`rising`/`falling`);
# anything within the band (inclusive of the boundary itself, so a delta
# of exactly +/-0.03 is still `flat`, not a coin-flip) counts as `flat`.
TREND_FLAT_EPSILON = 0.03

# The one state `rolling_pass_rate`'s own `None` return (its
# division-by-zero guard: no run in a window drafted any cards at all)
# maps to here, kept distinct from `flat` -- see module docstring.
INSUFFICIENT_DATA = "insufficient_data"


def classify_trend(current: float | None, prior: float | None) -> str:
    """Classify a week-over-week pass-rate move as ``"rising"``,
    ``"falling"``, ``"flat"``, or ``"insufficient_data"``.

    ``current`` and ``prior`` are two rolling-7-day pass rates (each
    either a float in ``[0, 1]`` or ``None``, matching
    :func:`scripts.reconcile_run.rolling_pass_rate`'s own return type).
    Either being ``None`` -- meaning that window drafted no cards at all --
    makes a real comparison impossible, so this returns
    ``"insufficient_data"`` rather than guessing or silently treating a
    missing rate as ``0.0`` (which would misreport "no data" as "every
    card failed").

    Otherwise compares ``current - prior`` against
    :data:`TREND_FLAT_EPSILON`: strictly above the band is ``"rising"``,
    strictly below (more negative than the band's negation) is
    ``"falling"``, and anything inside the closed band -- including
    exactly on either boundary -- is ``"flat"``.
    """
    if current is None or prior is None:
        return INSUFFICIENT_DATA
    delta = current - prior
    if delta > TREND_FLAT_EPSILON:
        return "rising"
    if delta < -TREND_FLAT_EPSILON:
        return "falling"
    return "flat"


def compute_pass_rate_trend(
    runs: Sequence[Mapping[str, Any]], *, today: date
) -> dict[str, Any]:
    """The full rolling-7d/30d pass-rate trend computation for one explicit
    ``today``, over an explicit ``runs`` history
    (``verifier_stats.schema.json``'s own ``runs[]`` shape).

    Never reads the real clock or the real `data/verifier_stats.json` file
    itself -- both are the caller's job (see :func:`audit_trend` for the
    real-file convenience wrapper) -- so this is fully unit-testable
    against a synthetic, hand-built history and an explicit date.

    Returns::

        {
            "as_of": "<today, ISO date>",
            "rolling_7d_pass_rate": <float in [0,1]> | None,
            "rolling_30d_pass_rate": <float in [0,1]> | None,
            "prior_week_pass_rate": <float in [0,1]> | None,
            "trend": "rising" | "falling" | "flat" | "insufficient_data",
        }

    ``rolling_7d_pass_rate``/``rolling_30d_pass_rate`` are each
    :func:`~scripts.reconcile_run.rolling_pass_rate` over the trailing 7/30
    days ending on ``today`` (inclusive); ``prior_week_pass_rate`` is that
    same 7-day computation anchored 7 days earlier (``today - 7``), i.e.
    the adjacent, non-overlapping week immediately before the current
    rolling-7d window. ``trend`` is :func:`classify_trend` applied to
    ``rolling_7d_pass_rate`` vs. ``prior_week_pass_rate`` -- the rolling
    30-day figure is reported for context only and is never itself a
    trend-classification input (see module docstring).

    A ``None`` in any of the three rate fields means that specific window
    drafted no cards at all (this function's own division-by-zero guard,
    inherited unchanged from :func:`~scripts.reconcile_run.rolling_pass_rate`)
    -- this function never raises on that, and never substitutes a
    fabricated ``0.0`` in its place.
    """
    rolling_7d = rolling_pass_rate(runs, window_days=7, as_of=today)
    rolling_30d = rolling_pass_rate(runs, window_days=30, as_of=today)
    prior_week = rolling_pass_rate(
        runs, window_days=7, as_of=today - timedelta(days=7)
    )
    return {
        "as_of": today.isoformat(),
        "rolling_7d_pass_rate": rolling_7d,
        "rolling_30d_pass_rate": rolling_30d,
        "prior_week_pass_rate": prior_week,
        "trend": classify_trend(rolling_7d, prior_week),
    }


def audit_trend(
    stats: Mapping[str, Any] | None = None, *, today: date
) -> dict[str, Any]:
    """Run the full verifier pass-rate trend check and return a summary
    dict, per this repo's established `auditor.*` convenience-wrapper
    convention (`auditor.linkrot.audit_link_rot`,
    `auditor.lexicon_audit.audit_lexicon`,
    `auditor.duplicates.audit_duplicates`).

    ``stats`` lets a caller (or a test) pass an already-loaded
    ``verifier_stats.schema.json``-shaped dict directly (``{"version": 1,
    "runs": [...]}``), for testability without touching the real file on
    disk. When ``stats`` is omitted (``None``), it is loaded from the real
    `data/verifier_stats.json` via
    `scripts.reconcile_run.load_verifier_stats` (reused directly, not
    reimplemented -- that function already returns the correct
    empty-history shape gracefully if the file is missing, and
    schema-validates it otherwise). ``today`` is still always required and
    explicit -- this function never falls back to a live clock call
    either, matching :func:`compute_pass_rate_trend`.

    Returns :func:`compute_pass_rate_trend`'s own dict, computed over
    ``stats["runs"]`` (or ``[]`` if that key is missing/empty).
    """
    if stats is None:
        stats = load_verifier_stats()
    return compute_pass_rate_trend(stats.get("runs") or [], today=today)
