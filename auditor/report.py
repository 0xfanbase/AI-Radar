"""Weekly audit report assembler (`auditor/report.py`).

Per CLAUDE.md's "daily self-learning loop" / `audit.yml` description and
the approved build plan's section 6 (Phase 5): `audit.yml` "Writes
`data/audit/latest.json` (schema in `schemas/audit.schema.json`)". This
module owns that one artifact's shape: it takes the five sibling checkers'
own already-computed return dicts (`auditor.linkrot.audit_link_rot`,
`auditor.lexicon_audit.audit_lexicon`, `auditor.trend.audit_trend`,
`auditor.missed_story.audit_missed_stories`, `auditor.duplicates.
audit_duplicates`) verbatim -- this module never re-runs or re-derives any
of their own logic, only assembles their already-produced dicts into one
schema-valid envelope -- and returns/persists the combined report.

**Phase 9 addition -- three more checkers, same "assemble verbatim, never
recompute" contract.** `auditor.linkrot.audit_hijacked_links` (Phase 8's
own post-publication hijack re-check over card citations) was built but
explicitly left unwired into this module (see that function's own
docstring and `IMPROVEMENT_BACKLOG.md`'s Phase 8 entry naming this exact
wiring as "the concrete next step for whichever phase actually turns
`audit.yml` live"). This phase is that step, and adds two siblings this
phase itself introduces: `auditor.linkrot.audit_company_hijacked_links`
(the same hijack re-check over company-profile citations) and
`auditor.profile_staleness.audit_profile_staleness` (which
`content/companies/*.json` profiles are past the 45-day freshness floor).
All three are assembled verbatim, exactly like the original five, under
new required top-level keys `hijacked_links`, `company_hijacked_links`,
`profile_staleness`.

Top-level shape (`schemas/audit.schema.json`, this module's own
counterpart)::

    {
      "version": 1,
      "run_id": "audit-<compact UTC timestamp>",
      "generated_at": "<UTC ISO-8601 timestamp>",
      "window": {"days": 7, "start": "<date>", "end": "<date>"},
      "link_rot": <auditor.linkrot.audit_link_rot()'s own dict>,
      "lexicon": <auditor.lexicon_audit.audit_lexicon()'s own dict>,
      "verifier_trend": <auditor.trend.audit_trend()'s own dict>,
      "missed_stories": <auditor.missed_story.audit_missed_stories()'s own dict>,
      "duplicates": <auditor.duplicates.audit_duplicates()'s own dict>,
      "hijacked_links": <auditor.linkrot.audit_hijacked_links()'s own dict>,
      "company_hijacked_links": <auditor.linkrot.audit_company_hijacked_links()'s own dict>,
      "profile_staleness": <auditor.profile_staleness.audit_profile_staleness()'s own dict>,
      "findings_appended_to_backlog": <int>
    }

**`window` is this report's own top-level "what period does this audit
cover" annotation, not a replacement for each checker's own, different
internal window.** Spec-silent choice, logged in full in
`IMPROVEMENT_BACKLOG.md`: `link_rot`/`lexicon`/`duplicates` have no
inherent time window at all (they check the *current* state of citations/
the lexicon/published cards, whatever that is right now); `verifier_trend`
already carries its own `as_of` plus rolling 7d/30d figures internally, and
`missed_stories` already carries its own `window_hours` (168, i.e. 7 days)
internally. This module's own `window` field is a fixed `{"days": 7,
"start", "end"}` matching `audit.yml`'s own weekly cadence (and exactly the
7-day figure `missed_stories.window_hours` already uses) purely so a reader
of `data/audit/latest.json` has one quick "what period does this weekly
audit report cover" answer without inferring it from `missed_stories.
window_hours` alone -- it is never itself consulted by any of the five
checkers, all of which compute their own windows independently before this
module ever sees their output.

**`findings_appended_to_backlog` is a plain count, not a duplicated
list.** The five checkers' own dicts already carry every finding a
`scripts/append_backlog_findings.py` run derives from them (dead links in
`link_rot.results`, coverage gaps/orphans in `lexicon`, a falling trend in
`verifier_trend`, missed stories in `missed_stories.missed_stories`,
duplicate pairs in `duplicates.duplicate_pairs`) -- re-listing them a
second time under a sixth top-level key would just be the same data
duplicated. This field is the one thing the five checkers' own dicts
*can't* report about themselves: how many of their findings actually got
promoted into a checkbox line in `IMPROVEMENT_BACKLOG.md` this run (a
caller-supplied number -- see `auditor.cli.run_audit`, which computes it
via `scripts.append_backlog_findings.derive_findings`/
`append_findings_to_backlog` before calling :func:`build_report`).

`run_id` is derived from `generated_at` (`"audit-" + <compact UTC
timestamp>"`), not a random UUID -- this project uses no UUID dependency
anywhere else, and a timestamp-derived id is already unique per real
invocation (`audit.yml` runs at most once a week) while staying sortable
and human-legible, matching this repo's existing `cluster_hash`/`card_id`/
`proposed_card_id` convention of deriving ids from data rather than
generating opaque randomness.

Every function in this module is pure except :func:`save_report`/
:func:`load_report`, which are the only two that touch disk -- matching
every sibling `auditor.*` module's own "pure-core, one (or two) disk-
touching entry points" convention.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from watcher.config import REPO_ROOT
from watcher.schema_validate import validate

__all__ = [
    "AUDIT_DIR",
    "AUDIT_LATEST_PATH",
    "AUDIT_REPORT_VERSION",
    "AUDIT_WINDOW_DAYS",
    "make_run_id",
    "compute_window",
    "build_report",
    "save_report",
    "load_report",
]

AUDIT_DIR = REPO_ROOT / "data" / "audit"
AUDIT_LATEST_PATH = AUDIT_DIR / "latest.json"

AUDIT_REPORT_VERSION = 1

# The weekly cadence audit.yml itself runs on, and the same figure
# auditor.missed_story's own 7-day HN lookback already uses -- see module
# docstring for why this is a top-level annotation only, never itself fed
# into any of the five checkers.
AUDIT_WINDOW_DAYS = 7


def _iso_utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(now: datetime) -> str:
    """`"audit-<compact UTC timestamp>"`, e.g. `"audit-20260711T233000Z"`.

    Deterministic given `now` (never reads the real clock itself) so this
    -- and everything built from it -- stays fully unit-testable against
    an explicit, frozen instant, matching `auditor.trend`'s own "no live
    clock inside pure-compute logic" discipline.
    """
    return "audit-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def compute_window(now: datetime, *, days: int = AUDIT_WINDOW_DAYS) -> dict[str, Any]:
    """The report's own top-level `{"days", "start", "end"}` window,
    ending on `now`'s own UTC date (inclusive) and spanning `days` days
    back from there -- see module docstring for what this field is (and
    isn't) for.
    """
    end = now.astimezone(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    return {"days": days, "start": start.isoformat(), "end": end.isoformat()}


def build_report(
    *,
    link_rot: dict[str, Any],
    lexicon: dict[str, Any],
    verifier_trend: dict[str, Any],
    missed_stories: dict[str, Any],
    duplicates: dict[str, Any],
    hijacked_links: dict[str, Any],
    company_hijacked_links: dict[str, Any],
    profile_staleness: dict[str, Any],
    now: datetime,
    findings_appended_to_backlog: int = 0,
    window_days: int = AUDIT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Assemble the eight already-computed checker dicts into one
    `schemas/audit.schema.json`-shaped report -- pure, no filesystem I/O,
    no re-running of any checker (every one of `link_rot`/`lexicon`/
    `verifier_trend`/`missed_stories`/`duplicates`/`hijacked_links`/
    `company_hijacked_links`/`profile_staleness` is passed in exactly as
    its own `audit_*` function returned it -- see module docstring's
    "Phase 9 addition" section for the three added in this phase). `now`
    is always explicit, never a live clock call -- matching every sibling
    `auditor.*` module's own "no live clock inside pure-compute logic"
    convention.
    """
    return {
        "version": AUDIT_REPORT_VERSION,
        "run_id": make_run_id(now),
        "generated_at": _iso_utc(now),
        "window": compute_window(now, days=window_days),
        "link_rot": link_rot,
        "lexicon": lexicon,
        "verifier_trend": verifier_trend,
        "missed_stories": missed_stories,
        "duplicates": duplicates,
        "hijacked_links": hijacked_links,
        "company_hijacked_links": company_hijacked_links,
        "profile_staleness": profile_staleness,
        "findings_appended_to_backlog": findings_appended_to_backlog,
    }


def save_report(report: dict[str, Any], path: Path | str = AUDIT_LATEST_PATH) -> None:
    """Schema-validate then write `report` to `path` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- the same committed-artifact formatting `watcher/ledger.py`
    / `scripts/reconcile_run.py` already establish for every other
    persisted pipeline artifact. Validating before writing means a
    malformed report is never persisted.
    """
    validate(report, "audit")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


def load_report(path: Path | str = AUDIT_LATEST_PATH) -> dict[str, Any] | None:
    """Load and schema-validate `data/audit/latest.json` at `path`.

    Returns `None` if `path` doesn't exist -- there has been no real audit
    run yet, which is this repo's own real state today (`data/audit/`
    doesn't exist at all) -- rather than raising, matching every sibling
    loader's own "missing file -> graceful empty/None, not a crash"
    convention (`auditor.linkrot.load_cards`, `auditor.missed_story.
    load_ledger`, `scripts.reconcile_run.load_verifier_stats`).
    """
    path = Path(path)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        report = json.load(f)
    validate(report, "audit")
    return report
