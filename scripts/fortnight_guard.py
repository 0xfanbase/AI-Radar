#!/usr/bin/env python3
"""Fortnightly-cadence guard (``scripts/fortnight_guard.py``).

Per the approved build plan's Phase 5 section: ``improve.yml`` (the
self-improvement loop) is conceptually **fortnightly**, but neither GitHub
Actions cron nor a Claude Code Remote Routine's ``cron_expression`` has a
native biweekly primitive -- both bottom out at "at least hourly," with no
"every 14 days" unit. The approved resolution (logged in
``IMPROVEMENT_BACKLOG.md``) is to let the real trigger fire **weekly** and
have this pure-code guard decide, on each firing, whether this is a "run"
week or a "skip" week -- alternating so the net effect approximates once
every two weeks.

**This module builds and documents the guard only.** Per this turn's own
explicit, binding scope, no live trigger of any kind (GitHub Actions
schedule, Claude Code Remote Routine) is created or activated this turn for
the fortnightly loop -- see ``improve.yml``'s own header comment and the
``IMPROVEMENT_BACKLOG.md`` entry logged alongside this file for the full
reasoning. This script is exactly what a future, owner-approved weekly
firing would call first, before ``scripts/pick_backlog_item.py``.

## Why ISO-week parity, not day-of-year parity

``scripts/plan_run.py``'s own degradation-ladder level 2 ("every-other-day"
from a *daily* firing) already solved the sibling problem -- approximating
a coarser cadence from a finer one -- using a **day-of-year** parity check
on an explicit ``today`` date, never the live clock. This module mirrors
that same shape (a pure function of an explicit date, never
``date.today()``/``datetime.now()`` internally) but deliberately uses
**ISO week number** parity instead of day-of-year parity, because the
firing cadence being approximated here is *weekly*, not *daily*: the
natural "half as often" split of a sequence of weekly firings is
alternating ISO week numbers, exactly as plan_run.py's level 2 alternates
calendar days. Using day-of-year parity here instead would not correctly
alternate a week-to-week firing sequence at all (seven consecutive days
share the same day-of-year parity pattern only by coincidence, not by
construction), so the two modules' choices are each the correct fit for
their own cadence-halving problem, not an inconsistency between them.

``RUN_PARITY = 1`` (odd ISO week number is a "run" week) is the direct
analogue of ``plan_run.py``'s own "day 1 of any year is always a run day"
convention (also spec-silent, also logged there): ISO week numbers always
start counting at 1 (an odd number) for every ISO year, so this convention
guarantees the very first ISO week of any year is always a "run" week,
exactly mirroring plan_run.py's own guarantee for its own ladder.

## The year-boundary quirk (known, deliberate, logged -- not a bug)

An ISO year has either 52 or 53 weeks (53 in years where 1 January falls on
a Thursday, or in a leap year where it falls on a Wednesday -- roughly one
year in five). Ordinarily, consecutive weekly firings alternate ISO week
parity cleanly across a year boundary too: week 52 of year Y (even) is
immediately followed by week 1 of year Y+1 (odd) -- a normal alternation,
confirmed for the real 2027-to-2028 boundary in
``tests/test_fortnight_guard.py``.

But in a **53-week** ISO year, the boundary instead runs
``... -> week 52 (even) -> week 53 (odd) -> week 1 of next year (odd)``:
two consecutive odd ("run") weeks in a row, rather than a strict
alternation -- one "skip" week that would otherwise have occurred is
effectively absorbed into that year's extra week. This is the direct
analogue of ``plan_run.py``'s own documented day-of-year-parity quirk
("two run days in a row" at a 365-day year boundary), the same class of
approximation artifact arising from the same kind of parity trick, and is
accepted as-is for exactly the same reason: the task instruction is
explicit that this must be "an ISO-week-parity check," not some
year-boundary-aware correction to it, and the quirk affects only the small
minority of year boundaries that land in a 53-week ISO year. The real
2026-to-2027 boundary is exactly such a case (2026 has ISO week 53) and is
used as the concrete, real-dated proof of this quirk in
``tests/test_fortnight_guard.py``, per this turn's own instruction to prove
"correct parity logic across a real year boundary" -- "correct" here means
"exactly the documented, deliberate behavior," including this quirk, not
"strict alternation at every boundary with no exception."

## No persisted data artifact

Unlike ``scripts/plan_run.py`` (which writes ``data/run_plan.json`` for the
downstream analyst prompt to read), this guard has no committed output
file of its own -- the approved plan names no such artifact, and a live
``improve.yml``/Routine firing only needs this run's yes/no decision to
gate its own next step, not a record for a separate downstream reader.
:func:`main` prints the decision to stdout and, when invoked from inside a
real GitHub Actions job (``GITHUB_OUTPUT`` set in the environment), also
appends ``mode``/``iso_year``/``iso_week``/``parity`` as step outputs via
:func:`write_github_output`, so a workflow step can gate subsequent steps
on ``steps.<id>.outputs.mode == 'run'`` without parsing stdout itself.

Every function here is pure and takes an explicit ``date`` -- never reads
the wall clock -- except :func:`_default_today` (used only by :func:`main`'s
own ``--date``-not-given fallback) and :func:`main` itself.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Allow running as `python scripts/fortnight_guard.py` (no package install /
# no `-m` needed) -- same sys.path trick every other script in this repo
# uses (scripts/plan_run.py, scripts/reconcile_run.py, scripts/
# pick_backlog_item.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

__all__ = [
    "RUN_PARITY",
    "iso_week_parity",
    "decide_fortnight_mode",
    "write_github_output",
    "main",
]

# Odd ISO week number => "run" fortnight. See the module docstring's "Why
# ISO-week parity" section for why this mirrors plan_run.py's own "day 1 of
# any year is always a run day" convention (spec-silent, logged in
# IMPROVEMENT_BACKLOG.md).
RUN_PARITY = 1


def iso_week_parity(today: date) -> int:
    """``today``'s ISO week number, mod 2 (``0`` even, ``1`` odd).

    Uses ``date.isocalendar()`` (ISO 8601 week numbering: weeks start
    Monday, week 1 is the week containing the year's first Thursday), not
    ``today.isocalendar()[1] % 7`` or any calendar-week-of-month notion --
    the only well-defined "week number" standard library ``date`` exposes
    directly.
    """
    return today.isocalendar()[1] % 2


def decide_fortnight_mode(today: date) -> dict[str, Any]:
    """Return this fortnight-guard's decision for ``today`` as a plain
    dict: ``{date, iso_year, iso_week, iso_weekday, parity, mode, reason}``.

    ``mode`` is ``"run"`` if ``today``'s ISO week parity matches
    :data:`RUN_PARITY`, else ``"skip"`` -- a pure function of the ISO
    calendar date passed in, with no other state and no clock access
    (:func:`main` is the only caller that ever supplies a live date).
    """
    iso_year, iso_week, iso_weekday = today.isocalendar()
    parity = iso_week % 2
    mode = "run" if parity == RUN_PARITY else "skip"
    reason = (
        f"{today.isoformat()} is ISO week {iso_week} of {iso_year} "
        f"({'odd' if parity else 'even'} parity={parity}); "
        f"RUN_PARITY={RUN_PARITY} -> {mode}"
    )
    return {
        "date": today.isoformat(),
        "iso_year": iso_year,
        "iso_week": iso_week,
        "iso_weekday": iso_weekday,
        "parity": parity,
        "mode": mode,
        "reason": reason,
    }


def write_github_output(decision: dict[str, Any], path: Path | str) -> None:
    """Append ``decision``'s ``mode``/``iso_year``/``iso_week``/``parity``
    fields to the GitHub Actions step-output file at ``path`` (the file
    ``$GITHUB_OUTPUT`` points at), one ``name=value`` line each, per
    GitHub's documented (non-deprecated) step-output mechanism. Plain
    ``name=value`` lines are safe here since none of these four values can
    ever contain a newline or ``=`` (an ISO date/int/fixed-vocabulary
    string), so the multiline ``name<<EOF`` heredoc form other steps in
    this repo's workflows use for free-text values is not needed.

    Appends (``"a"`` mode) rather than overwrites -- ``$GITHUB_OUTPUT`` is
    shared across every step in a job and each step's own writes must not
    clobber a previous step's, exactly like every real GitHub Actions
    runner's own convention.
    """
    path = Path(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"mode={decision['mode']}\n")
        f.write(f"iso_year={decision['iso_year']}\n")
        f.write(f"iso_week={decision['iso_week']}\n")
        f.write(f"parity={decision['parity']}\n")


def _default_today() -> date:
    """The real "today," derived from a UTC-aware instant (never a naive
    ``date.today()``, which would read the *server's local* timezone) --
    matching ``scripts/plan_run.py::compute_run_plan``'s own
    ``now.date()`` derivation. Isolated into its own function so
    ``tests/test_fortnight_guard.py`` can monkeypatch this one seam to
    prove :func:`main`'s no-``--date``-given path without depending on
    which real ISO week parity the test happens to run on.
    """
    return datetime.now(timezone.utc).date()


def _parse_date(raw: str) -> date:
    return date.fromisoformat(raw)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/fortnight_guard.py")
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "Override 'today' for this decision (ISO date). Defaults to "
            "the real current UTC date -- pass this for a dry run against "
            "a specific date without waiting for it to actually arrive."
        ),
    )
    args = parser.parse_args(argv)
    today = args.date if args.date is not None else _default_today()

    decision = decide_fortnight_mode(today)
    print(
        f"fortnight_guard: mode={decision['mode']} "
        f"iso_year={decision['iso_year']} iso_week={decision['iso_week']} "
        f"parity={decision['parity']}"
    )
    print(f"reason: {decision['reason']}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        write_github_output(decision, github_output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
