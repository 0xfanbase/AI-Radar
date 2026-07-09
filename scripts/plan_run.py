#!/usr/bin/env python3
"""Run planner (``scripts/plan_run.py``) -- the quota-degradation ladder.

Per the approved build plan's Phase 2 section: this is the pure-code step
that runs *before* the analyst in ``analyze.yml``. It reads
``data/queue.json`` (this run's ranked, ready-to-write cluster subset --
``watcher/queue_writer.py``'s own output, never re-derived here) and the
owner-controlled quota-degradation level, and writes ``data/run_plan.json``
(``schemas/run_plan.schema.json``): today's cluster subset, a pre-computed
``proposed_card_id`` per cluster (so the analyst never invents or collides
IDs -- CLAUDE.md's own stated reason), and the degradation-ladder decision
for this run.

**Degradation ladder** (CLAUDE.md's "Quota degradation ladder" section,
``degradation_level`` 0-3):

0. Normal -- up to :data:`NORMAL_CARDS_CAP` (8) clusters.
1. Capped -- top :data:`CAPPED_CARDS_CAP` (5) ranked clusters only.
2. Every-other-day -- a day-of-year parity check on the passed-in ``today``
   date decides whether this is a "run" day (capped top-5, same as level 1)
   or a "skip" day. Deliberately day-of-year parity, not wall-clock time or
   an ordinal-date parity, per this turn's own instruction, so the decision
   is a pure function of a passed-in date and stays unit-testable without
   freezing the real clock. Logged in IMPROVEMENT_BACKLOG.md, including the
   known quirk this produces at a year boundary (see
   :func:`decide_run_mode`'s docstring).
3. Weekly digest -- only on the designated digest weekday
   (:data:`DIGEST_WEEKDAY`, Monday), bundling *every* queued cluster into
   the ``clusters`` list for a single summary card (``run_mode: "digest"``,
   ``cards_cap: 1``) rather than one card each; any other weekday is a
   "skip" day under level 3.

**Unconditional rule, independent of the ladder (structural, not just
convention):** if ``data/queue.json`` is empty, ``run_mode`` is *always*
``"skip"`` -- checked first in :func:`decide_run_mode`, before any
``degradation_level`` branch is even reached, so no level can ever turn an
empty queue into a "run" decision. This is CLAUDE.md's absolute "empty
queue = exit without inventing news" rule, not one rung of the ladder.

This module follows the same pure-compute / validate-then-write / CLI-glue
three-layer shape already established by ``watcher/queue_writer.py`` and
``watcher/velocity.py``: :func:`compute_run_plan` takes an explicit ``now``
(never calls ``datetime.now()`` internally) and returns a plain dict;
:func:`save_run_plan` schema-validates then writes it; :func:`write_run_plan`
composes the two. :func:`main` is the only piece that reads the real clock
and the real ``QUOTA_DEGRADATION_LEVEL`` environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as `python scripts/plan_run.py` (no package install / no -m
# needed), same trick scripts/run_watcher_live.py already uses -- put the
# repo root on sys.path before importing the watcher package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher.config import REPO_ROOT  # noqa: E402
from watcher.queue_writer import QUEUE_PATH, load_queue  # noqa: E402
from watcher.schema_validate import validate  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = [
    "RUN_PLAN_PATH",
    "RUN_PLAN_VERSION",
    "NORMAL_CARDS_CAP",
    "CAPPED_CARDS_CAP",
    "DIGEST_CARDS_CAP",
    "DIGEST_WEEKDAY",
    "DEFAULT_DEGRADATION_LEVEL",
    "kebab_slug",
    "compute_proposed_card_id",
    "decide_run_mode",
    "compute_run_plan",
    "load_run_plan",
    "save_run_plan",
    "write_run_plan",
    "read_degradation_level",
    "main",
]

RUN_PLAN_PATH = REPO_ROOT / "data" / "run_plan.json"
RUN_PLAN_VERSION = 1

# --------------------------------------------------------------------------
# Ladder constants (CLAUDE.md's "Quota degradation ladder" section, spec-
# silent exact numbers already fixed by schemas/run_plan.schema.json's own
# description: "e.g. 8 normal, 5 capped, 1 digest").
# --------------------------------------------------------------------------

NORMAL_CARDS_CAP = 8
CAPPED_CARDS_CAP = 5
DIGEST_CARDS_CAP = 1

# date.weekday(): 0=Monday ... 6=Sunday. Spec names "e.g. Monday" for the
# level-3 digest day; Monday is adopted verbatim (not some other weekday),
# logged in IMPROVEMENT_BACKLOG.md.
DIGEST_WEEKDAY = 0  # Monday

DEFAULT_DEGRADATION_LEVEL = 0


# --------------------------------------------------------------------------
# proposed_card_id: date + kebab-slug(title) + "-" + cluster_hash[:6]
# --------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

# Simplest reasonable cap so a long real-world headline can't produce an
# absurdly long content/cards/<id>.json filename; spec-silent, logged in
# IMPROVEMENT_BACKLOG.md.
_MAX_SLUG_LENGTH = 60


def kebab_slug(text: str) -> str:
    """Lowercase, ASCII-alnum-only, hyphen-joined slug of ``text``.

    Any run of characters outside ``[a-z0-9]`` (after lowercasing) becomes
    a single hyphen; leading/trailing hyphens are stripped. Falls back to
    ``"untitled"`` for a title that yields no alnum characters at all
    (e.g. an empty or purely-punctuation title), so a proposed_card_id is
    always non-empty even in that degenerate case.
    """
    slug = _SLUG_STRIP_RE.sub("-", text.lower()).strip("-")
    if len(slug) > _MAX_SLUG_LENGTH:
        slug = slug[:_MAX_SLUG_LENGTH].rstrip("-")
    return slug or "untitled"


def _entry_title(entry: dict[str, Any]) -> str:
    """The representative title for one ``queue.schema.json`` cluster
    entry -- the first ``sources[]`` member's title, in the cluster's own
    deterministic member order (``watcher/queue_writer.py``'s own
    ordering). Falls back to ``""`` (-> ``kebab_slug`` -> ``"untitled"``)
    if a synthetic/malformed entry carries no sources at all.
    """
    sources = entry.get("sources") or []
    if not sources:
        return ""
    return sources[0].get("title", "") or ""


def compute_proposed_card_id(today: date, title: str, cluster_hash: str) -> str:
    """``<today>-<kebab-slug(title)>-<cluster_hash[:6]>`` -- pre-assigned
    so the downstream analyst never invents or collides a
    ``content/cards/<id>.json`` slug (this turn's own instruction; matches
    ``schemas/card.schema.json``'s own example id shape,
    ``'2026-07-09-gpt-5-5-release'``). The trailing ``cluster_hash[:6]``
    keeps two same-titled clusters (or a slug collapsing to
    ``"untitled"``) from ever colliding, since ``cluster_hash`` is itself a
    sha256 digest already unique per distinct cluster membership.
    """
    return f"{today.isoformat()}-{kebab_slug(title)}-{cluster_hash[:6]}"


# --------------------------------------------------------------------------
# decide_run_mode -- the degradation ladder itself
# --------------------------------------------------------------------------


def decide_run_mode(
    queue: list[dict[str, Any]], degradation_level: int, today: date
) -> tuple[str, int | None, list[dict[str, Any]], str]:
    """Return ``(run_mode, cards_cap, selected_entries, reason)`` for one
    run, given the full ``queue`` (already-ranked, already-capped-at-8
    ``queue.schema.json`` entries), the owner-set ``degradation_level``
    (0-3), and the passed-in ``today`` date (never read from the wall
    clock here -- see :func:`compute_run_plan`, the only caller, for where
    ``today`` actually comes from).

    **Unconditional empty-queue rule, checked first:** an empty ``queue``
    always returns ``("skip", None, [], ...)`` regardless of
    ``degradation_level`` -- this is CLAUDE.md's absolute "empty queue =
    exit without inventing news" rule, not one rung of the ladder, so it
    is structurally impossible for any level's branch below to override
    it (the level branches are simply never reached).

    **Level 2's day-of-year parity is a known, deliberate quirk at a year
    boundary.** ``today.timetuple().tm_yday`` resets to 1 every January
    1st, so on a year with an odd length (365 days, the common case),
    December 31 (day 365, odd) and the following January 1 (day 1, odd)
    land on the *same* parity -- two "run" days in a row rather than a
    strict alternation, instead of the single skipped alternation a
    wall-clock-continuous (e.g. ``today.toordinal()``) parity would give.
    This turn's own instruction is explicit that the check must be
    "a day-of-year parity check on a passed-in 'today' date," not an
    ordinal-date parity, so this is accepted as-is rather than
    "corrected" to ordinal parity; logged in IMPROVEMENT_BACKLOG.md.
    Odd day-of-year is arbitrarily chosen as the "run" parity (so day 1 of
    any year is always a run day) -- also spec-silent, also logged.
    """
    if not queue:
        return (
            "skip",
            None,
            [],
            "empty queue: exit without inventing news -- unconditional, "
            "independent of degradation_level (CLAUDE.md's absolute rule, "
            "not part of the degradation ladder)",
        )

    if degradation_level == 0:
        selected = queue[:NORMAL_CARDS_CAP]
        return (
            "normal",
            NORMAL_CARDS_CAP,
            selected,
            f"degradation_level=0: normal run, up to {NORMAL_CARDS_CAP} "
            f"clusters ({len(selected)} available)",
        )

    if degradation_level == 1:
        selected = queue[:CAPPED_CARDS_CAP]
        return (
            "capped",
            CAPPED_CARDS_CAP,
            selected,
            f"degradation_level=1: capped to top {CAPPED_CARDS_CAP} "
            f"({len(selected)} available)",
        )

    if degradation_level == 2:
        day_of_year = today.timetuple().tm_yday
        if day_of_year % 2 == 1:
            selected = queue[:CAPPED_CARDS_CAP]
            return (
                "capped",
                CAPPED_CARDS_CAP,
                selected,
                f"degradation_level=2: every-other-day, day-of-year "
                f"{day_of_year} is odd -> capped top-{CAPPED_CARDS_CAP} run",
            )
        return (
            "skip",
            None,
            [],
            f"degradation_level=2: every-other-day, day-of-year "
            f"{day_of_year} is even -> skip",
        )

    if degradation_level == 3:
        if today.weekday() == DIGEST_WEEKDAY:
            selected = list(queue)
            return (
                "digest",
                DIGEST_CARDS_CAP,
                selected,
                f"degradation_level=3: weekly digest day (Monday), "
                f"bundling all {len(selected)} queued clusters into one "
                f"summary card",
            )
        return (
            "skip",
            None,
            [],
            "degradation_level=3: weekly digest mode, today is not the "
            "designated digest weekday (Monday) -> skip",
        )

    raise ValueError(f"degradation_level must be 0-3, got {degradation_level!r}")


# --------------------------------------------------------------------------
# compute_run_plan -- pure computation, no disk I/O (mirrors
# watcher/velocity.py's compute_whats_moving: takes an explicit `now`,
# never calls datetime.now() itself).
# --------------------------------------------------------------------------


def compute_run_plan(
    queue: list[dict[str, Any]], degradation_level: int, *, now: datetime
) -> dict[str, Any]:
    """Build the ``run_plan.schema.json``-shaped payload for one run.

    ``today`` (used for the level-2 parity check, the level-3 weekday
    check, and the ``proposed_card_id`` date component) is derived as
    ``now.date()`` -- the *only* place this module reads a clock-like
    value from, and even that is caller-supplied, never
    ``datetime.now()`` called internally. This is what keeps the whole
    degradation-ladder decision unit-testable without freezing the real
    clock: every test in ``tests/test_degradation_ladder.py`` passes its
    own fixed ``now``.
    """
    today = now.date()
    run_mode, cards_cap, selected, reason = decide_run_mode(
        queue, degradation_level, today
    )

    clusters = [
        {
            "cluster_hash": entry["cluster_hash"],
            "proposed_card_id": compute_proposed_card_id(
                today, _entry_title(entry), entry["cluster_hash"]
            ),
            "rank": entry["rank"],
        }
        for entry in selected
    ]

    return {
        "version": RUN_PLAN_VERSION,
        "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "degradation_level": degradation_level,
        "run_mode": run_mode,
        "cards_cap": cards_cap,
        "clusters": clusters,
        "reason": reason,
    }


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as
# watcher/ledger.py, watcher/queue_writer.py, watcher/velocity.py).
# --------------------------------------------------------------------------


def load_run_plan(path: Path | str = RUN_PLAN_PATH) -> dict[str, Any] | None:
    """Load and schema-validate ``run_plan.json`` at ``path``.

    Returns ``None`` if the file doesn't exist yet, matching
    ``watcher/velocity.py``'s ``load_whats_moving`` convention for a
    same-shaped "not yet generated" artifact -- not an error.
    """
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        plan = json.load(f)
    validate(plan, "run_plan")
    return plan


def save_run_plan(plan: dict[str, Any], path: Path | str = RUN_PLAN_PATH) -> None:
    """Schema-validate then write ``plan`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting every other writer in
    this pipeline uses. Validating first means a malformed plan is never
    persisted.
    """
    validate(plan, "run_plan")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, sort_keys=True)
        f.write("\n")


def write_run_plan(
    queue: list[dict[str, Any]],
    degradation_level: int,
    *,
    now: datetime,
    path: Path | str = RUN_PLAN_PATH,
) -> dict[str, Any]:
    """Compose :func:`compute_run_plan` + :func:`save_run_plan`: the
    single call ``main`` (and any future ``analyze.yml`` step) makes to
    compute, validate, and persist ``data/run_plan.json`` for one run.
    Returns the written payload.
    """
    plan = compute_run_plan(queue, degradation_level, now=now)
    save_run_plan(plan, path)
    return plan


# --------------------------------------------------------------------------
# QUOTA_DEGRADATION_LEVEL environment variable (owner-controlled, manual
# `gh variable set` toggle per the approved plan -- CLAUDE.md's own
# deferred-decision note on the exact mechanism). Read here, not baked
# into compute_run_plan/decide_run_mode, so the pure ladder logic stays
# testable with a plain int and no environment stubbing.
# --------------------------------------------------------------------------


def read_degradation_level(env: dict[str, str] | None = None) -> int:
    """Read and sanitize ``QUOTA_DEGRADATION_LEVEL`` from ``env``
    (defaults to the real ``os.environ``).

    Unset or blank -> :data:`DEFAULT_DEGRADATION_LEVEL` (0), per this
    turn's own instruction ("defaulting to 0 if unset, to keep this pure
    and testable without needing real GitHub Actions vars"). An
    unparseable value also defaults to 0 (logged via a warning) rather
    than raising -- a malformed owner-set variable should degrade to the
    safe default, not crash the run planner. An out-of-range integer
    (outside 0-3) is clamped into range (also logged) rather than raising
    -- simplest reasonable handling for a value this script does not
    itself validate against the schema until later; both choices are
    spec-silent and logged in IMPROVEMENT_BACKLOG.md.
    """
    env = os.environ if env is None else env
    raw = env.get("QUOTA_DEGRADATION_LEVEL")
    if raw is None or not raw.strip():
        return DEFAULT_DEGRADATION_LEVEL

    try:
        level = int(raw.strip())
    except ValueError:
        logger.warning(
            "QUOTA_DEGRADATION_LEVEL=%r is not an integer; defaulting to %d",
            raw,
            DEFAULT_DEGRADATION_LEVEL,
        )
        return DEFAULT_DEGRADATION_LEVEL

    clamped = max(0, min(3, level))
    if clamped != level:
        logger.warning(
            "QUOTA_DEGRADATION_LEVEL=%d is out of the valid 0-3 range; "
            "clamped to %d",
            level,
            clamped,
        )
    return clamped


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    degradation_level = read_degradation_level()
    queue = load_queue(QUEUE_PATH)
    now = datetime.now(timezone.utc)

    plan = write_run_plan(queue, degradation_level, now=now)

    print(
        f"data/run_plan.json written: run_mode={plan['run_mode']} "
        f"degradation_level={plan['degradation_level']} "
        f"clusters={len(plan['clusters'])} cards_cap={plan['cards_cap']}"
    )
    print(f"reason: {plan['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
