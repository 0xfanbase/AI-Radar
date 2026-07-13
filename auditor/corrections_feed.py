"""Phase 9: turn profile-targeting audit findings into
`data/pending_corrections.json` `target_type: "company"` candidates.

Per this phase's own build brief: "both [checks] capable of feeding
`data/pending_corrections.json` with `target_type: "company"` entries
(the schema support for this was added in Phase 6 -- confirm it's
actually wired end to end, don't just assume)".

**What "confirm it's actually wired end to end" turned up.** Phase 6 added
`target_type`/`target_id` to `schemas/pending_corrections.schema.json` as
a "forward-looking hook," and Phase 8's PROFILER prompt text
(`.github/workflows/analyze.yml`) is a real, documented *consumer* of
`target_type: "company"` entries (its own Step 0 drains them). But no
*producer* of one had ever actually run end to end -- and when this
module became the first real one, `schemas/pending_corrections.schema.json`
itself turned out not to validate a genuine company-only candidate at
all: `card_id` was unconditionally required (`"required": [..., "card_id",
...]`), so a `target_type: "company"` entry with no card to name would
either fail schema validation or force this module to fabricate a
`card_id` value just to satisfy the schema -- a direct violation of this
project's own "no fabrication, ever" rule. Fixed at the schema
(`schemas/pending_corrections.schema.json` now makes `card_id` required
only when `target_type` is absent or `"card"`, via an `if`/`then`/`else`
conditional -- every existing card-only shape is completely unchanged),
not worked around here. See that schema's own updated description and
`IMPROVEMENT_BACKLOG.md` for the full account.

Two builders, one appender, mirroring `auditor.report`'s own
"assemble the checkers' own already-computed dicts, don't recompute
anything" separation:

- :func:`build_staleness_candidates` -- one candidate per
  `auditor.profile_staleness.audit_profile_staleness` result with
  `stale: true`. `evidence_url` points at the profile's own existing
  `profile.overview.citations[0].url` (the most naturally "please
  re-check this profile against its own sourcing" evidence a
  staleness finding can offer -- there is no *new*, external evidence a
  staleness check could point at, unlike a hijacked-citation finding,
  which has a concrete broken URL to hand the analyst) -- falling back to
  `https://<official_domains[0]>/` only if a profile is missing its
  overview citation entirely (defensive; every real seeded profile has
  one, per `schemas/company.schema.json`'s own `minItems: 1` on
  `citedText.citations`).
- :func:`build_hijack_candidates` -- one candidate per
  `auditor.linkrot.audit_company_hijacked_links` result with
  `status == "hijacked"`. `evidence_url` is the hijacked citation's own
  original URL (not `final_url` -- the analyst needs to re-fetch the
  citation exactly as the profile currently cites it to see the same
  hijack this check saw).
- :func:`feed_pending_corrections` -- the one function that actually
  touches `data/pending_corrections.json` (load, skip any candidate whose
  `id` already exists -- idempotent re-running within the same day/run,
  matching `scripts.pending_corrections.drain_pending_correction`'s own
  "already absent/present is a no-op, never an error" discipline --
  append every new one, save once). Every candidate id is
  deterministic and date-stamped (`audit-<category>-<company_id>[-<url
  hash>]-<date>`), so a re-run against an *unchanged* finding on the same
  day never double-appends; a finding that is still true a week later
  (the company still hasn't been re-verified, or the citation is still
  hijacked) *does* get a fresh dated id and therefore a fresh pending
  entry next week if the prior one was never drained -- the same
  "actionable every week until it's actually fixed" behavior
  `scripts.append_backlog_findings`'s own dead-link/verifier-trend
  findings already have for `IMPROVEMENT_BACKLOG.md`, not a new problem
  unique to this module. Logged in `IMPROVEMENT_BACKLOG.md`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.pending_corrections import (
    PENDING_CORRECTIONS_PATH,
    append_pending_correction,
    find_pending_correction,
    load_pending_corrections,
    save_pending_corrections,
)

__all__ = [
    "build_staleness_candidates",
    "build_hijack_candidates",
    "feed_pending_corrections",
]


def _date_part(flagged_at: str) -> str:
    """The `YYYY-MM-DD` prefix of an ISO-8601 `flagged_at` timestamp, used
    to make each candidate's own `id` unique per calendar day this audit
    runs (see module docstring's own note on why a still-true finding
    gets a fresh id, and therefore a fresh pending entry, on a later run)."""
    return flagged_at[:10]


def _company_evidence_url(company: Mapping[str, Any] | None) -> str:
    """The best available "please re-check this against its own sourcing"
    URL for a staleness candidate -- see module docstring for why this
    differs from the hijack candidate's own `evidence_url` choice."""
    company = company or {}
    profile = company.get("profile") or {}
    overview = profile.get("overview") or {}
    citations = overview.get("citations") or []
    if citations and citations[0].get("url"):
        return str(citations[0]["url"])
    domains = company.get("official_domains") or []
    if domains:
        return f"https://{domains[0]}/"
    # Defensive only -- schemas/company.schema.json requires both
    # profile.overview.citations (minItems 1) and official_domains
    # (minItems 1), so a real seeded profile always hits one of the two
    # branches above. Never reached against real data; exists so this
    # function still returns a schema-valid (format: uri) string rather
    # than raising against a malformed test fixture.
    return "https://example.invalid/"


def build_staleness_candidates(
    staleness_report: Mapping[str, Any],
    companies: Sequence[Mapping[str, Any]],
    *,
    flagged_at: str,
) -> list[dict[str, Any]]:
    """Turn `auditor.profile_staleness.audit_profile_staleness`'s own
    `results[]` into `target_type: "company"` pending-correction
    candidates -- one per company flagged `stale: true`. Every candidate
    omits `card_id` entirely (never fabricates one -- see module
    docstring), which `schemas/pending_corrections.schema.json`'s Phase 9
    fix is what makes schema-valid at all.
    """
    by_id = {str(c.get("id") or ""): c for c in companies}
    date_part = _date_part(flagged_at)
    candidates: list[dict[str, Any]] = []
    for result in staleness_report.get("results", []) or []:
        if not result.get("stale"):
            continue
        company_id = str(result.get("company_id") or "")
        company = by_id.get(company_id)
        candidates.append(
            {
                "id": f"audit-profile-staleness-{company_id}-{date_part}",
                "target_type": "company",
                "target_id": company_id,
                "issue_description": (
                    f"Company profile '{company_id}' has not been "
                    f"re-verified in {result.get('days_stale')} day(s) "
                    f"(last_verified {result.get('last_verified')}), past "
                    f"the {staleness_report.get('stale_days_threshold')}-day "
                    "freshness floor."
                ),
                "evidence_url": _company_evidence_url(company),
                "flagged_at": flagged_at,
                "source": "audit",
            }
        )
    return candidates


def build_hijack_candidates(
    hijack_report: Mapping[str, Any],
    *,
    flagged_at: str,
) -> list[dict[str, Any]]:
    """Turn `auditor.linkrot.audit_company_hijacked_links`'s own
    `results[]` into `target_type: "company"` pending-correction
    candidates -- one per result with `status == "hijacked"`. Every
    candidate omits `card_id` entirely, same as
    :func:`build_staleness_candidates` -- see module docstring.
    """
    date_part = _date_part(flagged_at)
    candidates: list[dict[str, Any]] = []
    for result in hijack_report.get("results", []) or []:
        if result.get("status") != "hijacked":
            continue
        company_id = str(result.get("company_id") or "")
        url = str(result.get("url") or "")
        url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
        candidates.append(
            {
                "id": (
                    f"audit-hijacked-citation-{company_id}-{url_hash}-{date_part}"
                ),
                "target_type": "company",
                "target_id": company_id,
                "issue_description": (
                    f"Citation {url} in company profile '{company_id}' "
                    f"currently resolves to {result.get('final_url')}, which "
                    "fails the outbound-link allowlist "
                    "(data/trusted_domains.json). This may be a genuine "
                    "post-publication redirect hijack (the domain was "
                    "trusted when cited and has since started redirecting "
                    "elsewhere), or the destination may simply never have "
                    "been added to the allowlist -- either way, this "
                    "citation's current target needs a human/analyst look."
                ),
                "evidence_url": url,
                "flagged_at": flagged_at,
                "source": "audit",
            }
        )
    return candidates


def feed_pending_corrections(
    candidates: Sequence[Mapping[str, Any]],
    *,
    path: Path | str = PENDING_CORRECTIONS_PATH,
) -> int:
    """Append every candidate in `candidates` to `data/pending_corrections
    .json`'s own `pending[]`, skipping any candidate whose `id` already
    exists there (idempotent -- see module docstring). Returns the number
    of candidates actually appended (0 for an empty `candidates`, or when
    every candidate's id is already present).

    Loads once, appends every new candidate via
    `scripts.pending_corrections.append_pending_correction` (reused, not
    reimplemented -- validation happens once, at the single
    `save_pending_corrections` call at the end, matching that module's
    own "pure in-memory helpers never validate themselves" division of
    responsibility), and saves at most once -- never touches disk at all
    if nothing new was appended.
    """
    if not candidates:
        return 0

    data = load_pending_corrections(path)
    added = 0
    for candidate in candidates:
        if find_pending_correction(data, str(candidate["id"])) is not None:
            continue
        data = append_pending_correction(data, dict(candidate))
        added += 1

    if added:
        save_pending_corrections(data, path)
    return added
