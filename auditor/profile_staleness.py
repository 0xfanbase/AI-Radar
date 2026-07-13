"""Company-profile staleness check (`audit.yml`'s weekly, pure-code pass --
Phase 9 addition).

Per this phase's own build brief: "a profile-staleness audit check (any
`content/companies/*.json` with `last_verified` older than the 45-day
floor used in Phase 8's profile-selection logic)". This module implements
exactly that check, reusing -- never reimplementing -- Phase 8's own
`scripts/plan_run.py` staleness primitives:

- :data:`PROFILE_STALE_THRESHOLD_DAYS` is `scripts.plan_run
  .PROFILE_STALE_THRESHOLD_DAYS` (45) re-exported under this module's own
  name, exactly `auditor.duplicates.DUPLICATE_JACCARD_THRESHOLD`'s own
  precedent for re-exporting a constant it imports rather than picking a
  second, independently-tuned number.
- :func:`load_company_registry` is `scripts.plan_run.load_company_registry`
  imported directly (the same "full per-company profiles, not the
  summary index, since only the full profile carries `last_verified`"
  loader Phase 8 already wrote for its own PROFILER-target selection).

**This check is deliberately broader than `scripts.plan_run
.find_stale_profile_candidate`, which it does NOT call.** That function
answers a different, narrower question -- "which *single* company should
this run's PROFILER refresh" (the oldest `last_verified`, and only if
that one company clears the 45-day floor) -- because a daily run can only
ever task the PROFILER with one company. This weekly audit check instead
answers "which companies, plural, are currently past the 45-day floor" --
a`audit.yml` finding is a report, not a same-run action queue with a
budget of one, so every stale company is surfaced here, not just the
single stalest one. :func:`find_stale_companies` is therefore its own
small function, not a thin wrapper -- but it reuses the exact same
threshold constant and the exact same `date.fromisoformat`
parse-defensively convention `find_stale_profile_candidate` already
established, so "stale" means the identical thing in both places.

Finding-emission pattern: matches every sibling `auditor.*` checker
(`auditor.linkrot.audit_link_rot`, `auditor.missed_story
.audit_missed_stories`, `auditor.duplicates.audit_duplicates`) --
`{checked_at, ...totals/threshold metadata..., counts, results}`, pure
core (:func:`find_stale_companies`) separated from the one disk-touching
entry point (:func:`audit_profile_staleness`, which accepts an explicit
`companies=` list for testability, exactly like `audit_link_rot` accepts
an explicit `cards=`).

Feeding `data/pending_corrections.json`: this module itself never writes
to that file -- see `auditor/corrections_feed.py`, which turns this
module's own `results` into `target_type: "company"` pending-correction
candidates and is the one place that actually appends them (mirroring
`auditor.report`'s own "assemble, don't reimplement" separation from the
checkers it assembles).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.plan_run import (
    COMPANIES_DIR,
    PROFILE_STALE_THRESHOLD_DAYS,
    load_company_registry,
)

__all__ = [
    "COMPANIES_DIR",
    "PROFILE_STALE_THRESHOLD_DAYS",
    "StaleProfileResult",
    "find_stale_companies",
    "audit_profile_staleness",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StaleProfileResult:
    """One company's own staleness classification."""

    company_id: str
    name: str
    last_verified: str | None
    days_stale: int | None
    stale: bool


def find_stale_companies(
    companies: Sequence[Mapping[str, Any]],
    today: date,
    *,
    stale_days: int = PROFILE_STALE_THRESHOLD_DAYS,
) -> list[StaleProfileResult]:
    """Classify every company in `companies` as stale/fresh against
    `today`, per the exact same "more than `stale_days` days before
    `today`" bar `scripts.plan_run.find_stale_profile_candidate` uses for
    its own single-company pick (`>`, not `>=` -- a profile exactly
    `stale_days` days old is not yet stale, matching that function's own
    comparison).

    A company with a missing or unparseable `last_verified` is reported
    with `days_stale: None, stale: False` rather than raising or being
    silently dropped -- defensive tolerance matching every sibling
    checker's "malformed input doesn't crash the weekly audit" discipline
    (see e.g. `auditor.linkrot.check_url`'s own never-raise contract),
    while still surfacing the company in `results` so a genuinely
    malformed registry entry isn't invisible to the audit either.

    Returns results sorted by `company_id` ascending -- deterministic
    regardless of `companies`' own on-disk iteration order (a plain
    `sorted(companies_dir.glob("*.json"))` already gives that order in
    practice, but this function doesn't rely on its caller having done
    so).
    """
    results: list[StaleProfileResult] = []
    for company in companies:
        company_id = str(company.get("id") or "")
        name = str(company.get("name") or company_id)
        raw = company.get("last_verified")
        if not raw:
            results.append(
                StaleProfileResult(company_id, name, None, None, False)
            )
            continue
        try:
            last_verified = date.fromisoformat(str(raw))
        except ValueError:
            results.append(
                StaleProfileResult(company_id, name, str(raw), None, False)
            )
            continue
        days_stale = (today - last_verified).days
        results.append(
            StaleProfileResult(
                company_id,
                name,
                last_verified.isoformat(),
                days_stale,
                days_stale > stale_days,
            )
        )
    return sorted(results, key=lambda r: r.company_id)


def audit_profile_staleness(
    companies: list[dict[str, Any]] | None = None,
    *,
    companies_dir: Path = COMPANIES_DIR,
    today: date | None = None,
    stale_days: int = PROFILE_STALE_THRESHOLD_DAYS,
) -> dict[str, Any]:
    """Run the full profile-staleness check and return a summary dict.

    `companies` lets a caller (or a test) pass an explicit list of
    company dicts directly, for testability without needing real files on
    disk, matching `auditor.linkrot.audit_link_rot`'s own `cards=`
    convention exactly. When omitted (`None`), the real registry is
    loaded from `companies_dir` (default `content/companies/`) via
    `scripts.plan_run.load_company_registry` (reused, not
    reimplemented).

    `today` defaults to the real UTC date when omitted (the one
    live-clock read in this module, matching `auditor.trend.audit_trend`'s
    own "explicit `today`, defaulting to the real clock only at this one
    call site" convention) -- a caller/test that wants a frozen date
    passes it explicitly.

    Returns `{checked_at, stale_days_threshold, total_companies, counts:
    {stale, fresh}, results: [...]}` -- each result is a
    :class:`StaleProfileResult` as a plain dict (via `dataclasses.asdict`).
    """
    if companies is None:
        companies = load_company_registry(companies_dir)
    if today is None:
        today = datetime.now(timezone.utc).date()

    classifications = find_stale_companies(companies, today, stale_days=stale_days)

    counts = {"stale": 0, "fresh": 0}
    for result in classifications:
        counts["stale" if result.stale else "fresh"] += 1

    return {
        "checked_at": _utcnow_iso(),
        "stale_days_threshold": stale_days,
        "total_companies": len(classifications),
        "counts": counts,
        "results": [asdict(r) for r in classifications],
    }
