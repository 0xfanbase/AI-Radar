#!/usr/bin/env python3
"""Reconciler (``scripts/reconcile_run.py``) -- the pure-code step that runs
*after* both LLM steps (ANALYST, VERIFIER) in ``analyze.yml``, per the
approved build plan's Phase 2 section.

Given ``data/run_plan.json`` (``scripts/plan_run.py``'s own output: the
cluster subset this run attempted, each with its pre-computed
``proposed_card_id`` so the analyst never invents or collides an id) and
this run's resulting cards on disk (or the lack thereof, for a cluster the
verifier dropped), this module:

1. **Finalizes ledger entries** (:func:`reconcile_ledger`): a cluster whose
   ``content/cards/<proposed_card_id>.json`` actually exists was published
   -- its ledger entry gets ``card_id`` set and ``status: "published"``. A
   cluster with no such file was dropped (by the verifier, or never
   written by the analyst at all) -- its ledger entry gets ``status:
   "dropped"``, ``card_id`` stays/becomes ``null`` (schema-enforced to stay
   null permanently for that exact ``cluster_hash`` by ``schemas/ledger.
   schema.json``'s own ``if``/``then``), and a ``verifier_outcome`` record.
2. **Regenerates the card index** by calling ``scripts/update_card_index.
   write_card_index`` -- never a second, parallel index-builder here.
3. **Appends one row to ``data/verifier_stats.json``**
   (:func:`compute_verifier_stats_row`): ``{date, cards_drafted, confirmed,
   reported, dropped, pass_rate}`` for this run.

A cluster_hash a run plan names but that has no existing ledger entry at
all is a defensive, shouldn't-happen case in the real pipeline (every
queued cluster is upserted into the ledger by ``watch.yml`` before
``analyze.yml`` ever runs) -- see :func:`reconcile_ledger`'s own docstring
for how it's handled (skipped with a warning, not a crash).

Also provides :func:`rolling_pass_rate`, a small pure helper over
``verifier_stats.json``'s own ``runs[]`` history that pools
``(confirmed + reported) / cards_drafted`` across every run whose ``date``
falls in a trailing window ending on an explicit ``as_of`` date. This
directly pre-stages the data shape Phase 5's weekly ``audit.yml`` will need
for its own "verifier pass-rate trend (rolling 7d/30d)" check (CLAUDE.md's
daily-loop diagram) -- the auditor itself is out of this turn's scope, but
the helper operates purely on this module's own ``verifier_stats.json``
shape, so it lives here rather than duplicating that shape's field names a
second time in a not-yet-built module. Logged in IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Allow running as `python scripts/reconcile_run.py` (no package install /
# no `-m` needed) -- same sys.path trick every other script in this repo
# uses (scripts/plan_run.py, scripts/update_card_index.py, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.update_card_index import CARDS_DIR, write_card_index  # noqa: E402
from watcher.config import REPO_ROOT  # noqa: E402
from watcher.ledger import LEDGER_PATH, load_ledger, save_ledger  # noqa: E402
from watcher.schema_validate import validate  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = [
    "VERIFIER_STATS_PATH",
    "VERIFIER_STATS_VERSION",
    "DROPPED_REASON_NO_CARD",
    "empty_verifier_stats",
    "load_cards_by_cluster",
    "reconcile_ledger",
    "compute_verifier_stats_row",
    "load_verifier_stats",
    "append_verifier_stats_row",
    "save_verifier_stats",
    "rolling_pass_rate",
    "reconcile_run",
    "main",
]

VERIFIER_STATS_PATH = REPO_ROOT / "data" / "verifier_stats.json"
VERIFIER_STATS_VERSION = 1

# Spec-silent (there is no per-cluster "why did the verifier drop this"
# data channel persisted anywhere -- that reasoning lives only inside the
# verifier's own conversation transcript, never structurally captured);
# the simplest reasonable fixed reason string, logged in
# IMPROVEMENT_BACKLOG.md. A future phase could thread a real per-cluster
# reason through data/run_plan.json or a sibling file if the auditor ever
# needs finer granularity than "no card resulted."
DROPPED_REASON_NO_CARD = (
    "no content/cards/<id>.json was published for this cluster this run "
    "(verifier drop or analyst skip)"
)


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Step 1: load whichever proposed cards actually got written this run.
# --------------------------------------------------------------------------


def load_cards_by_cluster(
    clusters: list[dict[str, Any]], cards_dir: Path | str = CARDS_DIR
) -> dict[str, dict[str, Any]]:
    """For each ``run_plan.json`` cluster entry (``{cluster_hash,
    proposed_card_id, rank}``), load + schema-validate ``content/cards/
    <proposed_card_id>.json`` if it exists, keyed by ``cluster_hash``.

    A cluster whose card file is absent is simply omitted from the
    returned mapping -- that absence *is* "dropped," per this module's own
    reconciliation rule (:func:`reconcile_ledger`,
    :func:`compute_verifier_stats_row`); it is not an error condition
    here.
    """
    cards_dir = Path(cards_dir)
    result: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        card_path = cards_dir / f"{cluster['proposed_card_id']}.json"
        if not card_path.is_file():
            continue
        with card_path.open("r", encoding="utf-8") as f:
            card = json.load(f)
        validate(card, "card")
        result[cluster["cluster_hash"]] = card
    return result


# --------------------------------------------------------------------------
# Step 2: finalize ledger status/card_id for every cluster this run
# attempted.
# --------------------------------------------------------------------------


def reconcile_ledger(
    clusters: list[dict[str, Any]],
    ledger: dict[str, Any],
    cards_by_cluster: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Return a *new* ledger dict (never mutates ``ledger`` in place --
    same convention as ``watcher/ledger.py``'s own ``upsert_entries``)
    with one entry finalized per ``clusters`` entry:

    - **Published** (a card exists for this ``cluster_hash``): ``card_id``
      is set to the card's own ``id``, ``status`` becomes ``"published"``.
    - **Dropped** (no card exists): ``card_id`` stays/becomes ``null``,
      ``status`` becomes ``"dropped"``, and ``verifier_outcome`` records
      ``last_attempted_at`` + ``dropped_reason``. ``schemas/ledger.
      schema.json``'s own ``if``/``then`` enforces that ``card_id`` must
      be ``null`` whenever ``status`` is ``"dropped"``, so this can never
      drift -- and calling this function again later for the same
      ``cluster_hash`` while it still has no card simply re-affirms the
      same dropped state, so ``card_id`` stays permanently ``null`` for
      that exact hash, matching the plan's own stated invariant (a later
      run with genuinely new corroborating evidence produces a
      *different* ``cluster_hash`` -- per ``watcher/clustering.py``'s own
      hash formula over member URLs -- so it is a fresh ledger entry, not
      a revived stuck one).

    A ``cluster_hash`` this run plan names but that has no existing
    ledger entry at all (shouldn't happen in the real pipeline -- every
    queued cluster is upserted into the ledger by ``watch.yml`` before
    ``analyze.yml`` ever runs) is defensively skipped with a warning
    rather than raising: there is no ``first_seen``/``member_urls`` on
    hand here to synthesize a schema-valid entry from scratch, and a
    partial/corrupted run plan shouldn't crash the reconciler.
    """
    entries = dict(ledger.get("entries", {}))
    last_attempted_at = _iso_now(now)

    for cluster in clusters:
        cluster_hash = cluster["cluster_hash"]
        existing = entries.get(cluster_hash)
        if existing is None:
            logger.warning(
                "reconcile_ledger: cluster_hash %s from run_plan has no "
                "existing ledger entry -- skipping (shouldn't happen in "
                "the real pipeline, where watch.yml upserts every queued "
                "cluster before analyze.yml runs)",
                cluster_hash,
            )
            continue

        card = cards_by_cluster.get(cluster_hash)
        updated = dict(existing)
        if card is not None:
            updated["card_id"] = card["id"]
            updated["status"] = "published"
        else:
            updated["card_id"] = None
            updated["status"] = "dropped"
            updated["verifier_outcome"] = {
                "last_attempted_at": last_attempted_at,
                "dropped_reason": DROPPED_REASON_NO_CARD,
            }
        entries[cluster_hash] = updated

    return {"version": ledger.get("version", 1), "entries": entries}


# --------------------------------------------------------------------------
# Step 3: one data/verifier_stats.json row for this run.
# --------------------------------------------------------------------------


def compute_verifier_stats_row(
    clusters: list[dict[str, Any]],
    cards_by_cluster: dict[str, dict[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """One ``verifier_stats.schema.json`` ``runs[]`` row for this run.

    ``cards_drafted`` = every cluster the plan attempted this run (``0``
    for a "skip" run's empty ``clusters[]``). ``confirmed``/``reported`` =
    how many of the resulting cards finished at each status (a
    freshly-reconciled card is never ``"corrected"`` -- that status only
    ever arises later, via the separate corrections workflow acting on an
    already-published card; any status other than ``"confirmed"`` on this
    defensive path still counts as ``reported`` rather than being dropped
    from the tally, so ``confirmed + reported + dropped`` always sums to
    ``cards_drafted``). ``dropped`` = clusters with no resulting card at
    all. ``pass_rate`` = ``(confirmed + reported) / cards_drafted``, or
    ``0.0`` when ``cards_drafted`` is ``0`` -- matches the schema's own
    field description verbatim (a schema-required ``number`` field can't
    be null, so an empty/skip run's row reports ``0.0``, not "no data" --
    contrast :func:`rolling_pass_rate` below, which *can* return ``None``
    for an all-quiet window since it isn't schema-bound).
    """
    cards_drafted = len(clusters)
    confirmed = 0
    reported = 0
    dropped = 0

    for cluster in clusters:
        card = cards_by_cluster.get(cluster["cluster_hash"])
        if card is None:
            dropped += 1
        elif card["status"] == "confirmed":
            confirmed += 1
        else:
            reported += 1

    pass_rate = (confirmed + reported) / cards_drafted if cards_drafted else 0.0

    return {
        "date": now.date().isoformat(),
        "cards_drafted": cards_drafted,
        "confirmed": confirmed,
        "reported": reported,
        "dropped": dropped,
        "pass_rate": pass_rate,
    }


# --------------------------------------------------------------------------
# load / save / append -- schema-valid round trip (same pattern as
# watcher/ledger.py's save_ledger/load_ledger).
# --------------------------------------------------------------------------


def empty_verifier_stats() -> dict[str, Any]:
    """The seed shape Phase 2 ships as ``data/verifier_stats.json``:
    version 1, no runs yet."""
    return {"version": VERIFIER_STATS_VERSION, "runs": []}


def load_verifier_stats(path: Path | str = VERIFIER_STATS_PATH) -> dict[str, Any]:
    """Load and schema-validate ``data/verifier_stats.json`` at ``path``.

    A missing file returns :func:`empty_verifier_stats` -- the very first
    reconcile run against a fresh checkout has no stats file yet, and that
    is not an error.
    """
    path = Path(path)
    if not path.is_file():
        return empty_verifier_stats()
    with path.open("r", encoding="utf-8") as f:
        stats = json.load(f)
    validate(stats, "verifier_stats")
    return stats


def append_verifier_stats_row(
    stats: dict[str, Any], row: dict[str, Any]
) -> dict[str, Any]:
    """Return a *new* verifier_stats dict with ``row`` appended to
    ``runs`` (oldest-first, per the schema's own convention) -- never
    mutates ``stats`` in place, matching ``watcher/ledger.py``'s own
    upsert convention.
    """
    return {
        "version": stats.get("version", VERIFIER_STATS_VERSION),
        "runs": list(stats.get("runs", [])) + [row],
    }


def save_verifier_stats(
    stats: dict[str, Any], path: Path | str = VERIFIER_STATS_PATH
) -> None:
    """Schema-validate then write ``stats`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting every other writer in
    this pipeline uses. Validating *before* writing means a malformed
    stats file is never persisted.
    """
    validate(stats, "verifier_stats")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
        f.write("\n")


# --------------------------------------------------------------------------
# Rolling pass-rate: a small, self-contained helper pre-staging Phase 5's
# audit.yml "verifier pass-rate trend (rolling 7d/30d)" check. Pure
# function of an explicit runs[] history + an explicit as_of date -- never
# reads verifier_stats.json or the real clock itself, so it is fully
# testable against a synthetic, hand-built history list.
# --------------------------------------------------------------------------


def rolling_pass_rate(
    runs: list[dict[str, Any]], *, window_days: int, as_of: date
) -> float | None:
    """The pooled ``(confirmed + reported) / cards_drafted`` pass rate
    across every ``runs[]`` row (``verifier_stats.schema.json``-shaped
    dicts) whose ``date`` falls within the trailing ``window_days`` window
    ending on ``as_of`` (inclusive of both ends) -- e.g. ``window_days=7``
    for a rolling 7-day rate, ``30`` for a rolling 30-day rate.

    Pooled across cards, not averaged across days: a day that drafted 8
    cards counts 8x as much as a day that drafted 1, which is the more
    meaningful "how often does a published card actually hold up" reading
    than a naive mean-of-daily-rates would give. A day with
    ``cards_drafted == 0`` (a "skip" run) contributes ``0`` to both the
    numerator and denominator, so it neither inflates nor deflates the
    rate -- it is correctly weightless, not simply absent from the
    window.

    Returns ``None`` if no run in the window drafted any cards at all (an
    all-quiet window, or a window before any run exists) -- this
    deliberately does *not* return ``0.0`` in that case, since ``0.0``
    would misleadingly read as "every card failed" rather than "nothing
    happened to measure."
    """
    window_start = as_of - timedelta(days=window_days - 1)
    total_drafted = 0
    total_pass = 0
    for run in runs:
        run_date = date.fromisoformat(run["date"])
        if window_start <= run_date <= as_of:
            total_drafted += run["cards_drafted"]
            total_pass += run["confirmed"] + run["reported"]

    if total_drafted == 0:
        return None
    return total_pass / total_drafted


# --------------------------------------------------------------------------
# Compose everything: the single call analyze.yml's post-LLM-steps stage
# makes.
# --------------------------------------------------------------------------


def reconcile_run(
    run_plan: dict[str, Any],
    ledger: dict[str, Any],
    verifier_stats: dict[str, Any],
    *,
    cards_dir: Path | str = CARDS_DIR,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The full post-LLM-steps reconciliation for one ``analyze.yml`` run.

    Returns ``(new_ledger, new_verifier_stats, card_index)``. Neither
    ``ledger`` nor ``verifier_stats`` is mutated in place (this function's
    own return values are what a caller persists via
    :func:`watcher.ledger.save_ledger`/:func:`save_verifier_stats`).

    Regenerating ``content/cards/index.json`` is the one disk write this
    function *does* perform as a side effect (via ``scripts.
    update_card_index.write_card_index``, which already owns
    validate-then-write for that artifact -- there is no benefit to a
    second, parallel in-memory-only card-index builder here). The index is
    always written to ``<cards_dir>/index.json`` -- deliberately derived
    from the caller's own ``cards_dir`` rather than defaulting to the
    module-level ``CARD_INDEX_PATH`` constant, so that passing a
    non-default ``cards_dir`` (every test in this repo's suite does,
    against a ``tmp_path``) can never fall through to writing the real
    ``content/cards/index.json`` by accident. A caller that wants a
    side-effect-free preview of the index can call ``scripts.
    update_card_index.build_card_index`` directly instead.
    """
    cards_dir = Path(cards_dir)
    clusters = run_plan.get("clusters", [])
    cards_by_cluster = load_cards_by_cluster(clusters, cards_dir)

    new_ledger = reconcile_ledger(clusters, ledger, cards_by_cluster, now=now)
    card_index = write_card_index(cards_dir, cards_dir / "index.json")
    stats_row = compute_verifier_stats_row(clusters, cards_by_cluster, now=now)
    new_verifier_stats = append_verifier_stats_row(verifier_stats, stats_row)

    return new_ledger, new_verifier_stats, card_index


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    # Local import (rather than a hard module-level dependency): this
    # module's own pure functions above only need run_plan.json's already-
    # loaded *contents*, never scripts.plan_run's loader/path constants
    # directly -- keeping the import here means a test that only exercises
    # reconcile_ledger/compute_verifier_stats_row/rolling_pass_rate never
    # needs scripts/plan_run.py importable at all.
    from scripts.plan_run import RUN_PLAN_PATH, load_run_plan  # noqa: E402

    run_plan = load_run_plan(RUN_PLAN_PATH)
    if run_plan is None:
        print("data/run_plan.json not found -- nothing to reconcile.")
        return 0

    ledger = load_ledger(LEDGER_PATH)
    verifier_stats = load_verifier_stats(VERIFIER_STATS_PATH)
    now = datetime.now(timezone.utc)

    new_ledger, new_verifier_stats, card_index = reconcile_run(
        run_plan, ledger, verifier_stats, now=now
    )

    save_ledger(new_ledger, LEDGER_PATH)
    save_verifier_stats(new_verifier_stats, VERIFIER_STATS_PATH)

    latest_row = new_verifier_stats["runs"][-1]
    print(
        f"reconcile_run: {len(card_index['cards'])} cards indexed; "
        f"verifier_stats row appended for {latest_row['date']} "
        f"(cards_drafted={latest_row['cards_drafted']} "
        f"pass_rate={latest_row['pass_rate']:.3f})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
