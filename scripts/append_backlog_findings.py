#!/usr/bin/env python3
"""Promote Phase 5 audit findings into `IMPROVEMENT_BACKLOG.md` checkbox
lines (`scripts/append_backlog_findings.py`).

Per CLAUDE.md's "daily self-learning loop" / `audit.yml` description
("... findings auto-append IMPROVEMENT_BACKLOG.md") and
`IMPROVEMENT_BACKLOG.md`'s own stated job #2 ("Once the weekly `audit.yml`
exists, it auto-appends its findings here ... The fortnightly `improve.yml`
picks the highest-severity unaddressed item from this file"), this module
is the one piece that turns the five checkers' own raw output dicts (from
`auditor.linkrot`/`auditor.lexicon_audit`/`auditor.trend`/`auditor.
missed_story`/`auditor.duplicates`, exactly as `auditor.report.build_report`
also consumes them) into a flat list of `{severity, category, summary}`
finding dicts, then appends one Markdown checkbox line per finding to
`IMPROVEMENT_BACKLOG.md`.

**Severity mapping (this turn's own explicit instruction, verbatim) -- a
spec-silent default, since neither CLAUDE.md nor the approved build plan
assigns severities to any of these checks. Documented here AND in
`IMPROVEMENT_BACKLOG.md`'s own decision log, per this turn's own
instruction:**

| Category                                                   | Severity |
|-------------------------------------------------------------|----------|
| Declining verifier pass-rate trend (`verifier_trend.trend == "falling"`) | **high** |
| Hijacked card citation (`hijacked_links.results[].status == "hijacked"`) | **high** -- Phase 9 addition, not part of this turn's original given mapping; a citation that was trusted at publish time and now redirects off the allowlist is a live security-relevant finding, the same severity class as a falling verifier-trend, not a mere content-quality nit. |
| Hijacked company-profile citation (`company_hijacked_links.results[].status == "hijacked"`) | **high** -- Phase 9 addition, same rationale as the card-citation case above. |
| Missed story (`missed_stories.missed_stories[]`)             | **medium** |
| Duplicate-topic pair (`duplicates.duplicate_pairs[]`)        | **medium** |
| Dead citation link (`link_rot.results[].status == "dead"`)   | **low** |
| Lexicon orphan (`lexicon.orphans[]`)                         | **low** |
| Lexicon coverage gap (`lexicon.coverage_gaps[]`)             | **low** -- not explicitly named by this turn's own instruction (only "orphans" is); grouped under the same "lexicon" category/severity as the sibling check it lives alongside in `auditor.lexicon_audit`'s own combined `audit_lexicon()` return. Logged as this module's own extension of the given mapping, not a deviation from it. |
| Stale company profile (`profile_staleness.results[].stale == true`) | **low** -- Phase 9 addition; informational (the audit report already surfaces it, and Phase 9's `auditor.corrections_feed` separately queues it for the PROFILER via `data/pending_corrections.json`), not itself a content error the way a dead link or hijack is. |

This mapping is *this module's own default*, not the fortnightly
`improve.yml`'s picker logic (`scripts/pick_backlog_item.py`, out of this
turn's scope) -- it only decides what severity tag gets written on each
line; picking the next self-improvement target from the accumulated,
severity-tagged checkboxes is a separate, not-yet-built script's job.

**What counts as "actionable" (i.e., what actually gets promoted to a
line) per category:**

- **link rot** -- only `status == "dead"` (404/410, a server-confirmed
  "this is gone"). `unreachable` results are deliberately *not* promoted:
  `auditor.linkrot`'s own docstring already treats `unreachable` as
  "retry next week, not a confirmed problem yet" (a 403/5xx/timeout may
  well be transient bot-management noise, per this project's own
  documented real fetch history -- see `IMPROVEMENT_BACKLOG.md`'s Phase 3
  Frontier Board entries), so flagging every `unreachable` URL as
  actionable every single week would be noise, not signal.
- **lexicon coverage gaps / orphans** -- every entry in each list, always
  -- except see the "zero published cards" guard below for orphans.
- **verifier trend** -- only when `trend == "falling"`; `rising`/`flat`
  need no action, and `insufficient_data` means there's nothing to act on
  yet either.
- **missed stories** -- every entry in `missed_stories.missed_stories[]`
  (never `seen_but_dropped_stories[]` -- CLAUDE.md's own `audit.yml`
  bullet is explicit these two outcomes are "distinguished," and a
  correctly-declined story is the corroboration rule working as intended,
  not a finding).
- **duplicates** -- every entry in `duplicates.duplicate_pairs[]`.
- **hijacked_links / company_hijacked_links** -- only `status ==
  "hijacked"`, same "unreachable is retry-next-week, not confirmed" logic
  as link rot above (`auditor.linkrot.check_hijack`'s own docstring makes
  the identical distinction for its own `"unreachable"` bucket); `trusted`
  needs no action.
- **profile_staleness** -- every entry in `profile_staleness.results[]`
  with `stale == true`.

**Spec-silent guard: lexicon-orphan findings are suppressed (not merely
demoted) whenever `has_cards` is `False` (zero published cards).** Logged
in full here since this is a real, deliberate judgment call, not an
oversight: this repo's own real state today is exactly this case --
`content/cards/` doesn't exist yet (no analyst run has happened for real)
and all 30 real `content/lexicon.json` entries have `seen_in: []`, per
Phase 3's seed content. Running this module unconditionally against that
real state would promote all 30 seed lexicon terms to individual "orphan"
checkbox findings on literally the *first* audit run ever -- not a real
signal that the analyst's own lexicon auto-growth rule (CLAUDE.md's
corroboration procedure step 7) is failing to reference terms, just the
expected, uninteresting fact that publishing hasn't started yet.
`auditor.lexicon_audit.find_orphans` itself is intentionally left
unchanged -- it still reports the full, honest list in
`data/audit/latest.json`'s own `lexicon.orphans` field regardless of
`has_cards`, so the raw check result stays complete; this guard only
affects what gets promoted into `IMPROVEMENT_BACKLOG.md` as an actionable
checkbox. Coverage gaps need no equivalent guard: they are already
structurally empty whenever `cards` is empty (nothing to scan for a gap),
so there is nothing to suppress there.

Every function here is pure except :func:`append_findings_to_backlog`,
which is the one function that actually opens and appends to
`IMPROVEMENT_BACKLOG.md` (or, for a test/dry run, any other path passed
explicitly) -- matching every sibling `auditor.*` module's own
"pure-core, one impure disk-touching entry point" convention.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Allow running as `python scripts/append_backlog_findings.py` (no package
# install / no `-m` needed) -- same sys.path trick every other script in
# this repo uses (scripts/plan_run.py, scripts/reconcile_run.py, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher.config import REPO_ROOT  # noqa: E402

__all__ = [
    "BACKLOG_PATH",
    "SEVERITY_LABELS",
    "derive_findings",
    "format_finding_line",
    "append_findings_to_backlog",
]

BACKLOG_PATH = REPO_ROOT / "IMPROVEMENT_BACKLOG.md"

SEVERITY_LABELS = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def _fmt_pct(value: float | None) -> str:
    """`0.823` -> `"82.3%"`; `None` -> `"n/a"` (an all-quiet
    `rolling_pass_rate` window -- see `auditor.trend`'s own docstring for
    why that's `None`, not a fabricated `0.0`).
    """
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def derive_findings(
    *,
    link_rot: dict[str, Any],
    lexicon: dict[str, Any],
    verifier_trend: dict[str, Any],
    missed_stories: dict[str, Any],
    duplicates: dict[str, Any],
    hijacked_links: dict[str, Any] | None = None,
    company_hijacked_links: dict[str, Any] | None = None,
    profile_staleness: dict[str, Any] | None = None,
    has_cards: bool,
) -> list[dict[str, str]]:
    """Flatten the eight checkers' own raw output dicts (exactly as
    `auditor.report.build_report` also consumes them) into one ordered
    list of `{"severity", "category", "summary"}` finding dicts, per the
    severity mapping and "what's actionable" rules in the module
    docstring.

    `hijacked_links`/`company_hijacked_links`/`profile_staleness` (Phase 9)
    default to `None`, treated as `{}` (contributing no findings) -- unlike
    the original five, which stay required kwargs, unchanged, so no
    existing call site of this function needs updating just to keep
    working. Every real caller (`auditor.cli.run_audit`) passes all eight
    explicitly.

    Order is deterministic: link rot, then hijacked links, then
    company-hijacked links, then lexicon coverage gaps, then lexicon
    orphans, then verifier trend, then missed stories, then duplicates,
    then profile staleness -- the same field order
    `auditor.report.build_report` itself assembles its own report in. An
    empty return means a completely clean run (nothing actionable this
    week), not "nothing was checked."
    """
    findings: list[dict[str, str]] = []

    for result in link_rot.get("results", []) or []:
        if result.get("status") != "dead":
            continue
        findings.append(
            {
                "severity": "low",
                "category": "link_rot",
                "summary": (
                    f"Dead citation link (HTTP {result.get('http_status')}): "
                    f"{result.get('url')}"
                ),
            }
        )

    for result in (hijacked_links or {}).get("results", []) or []:
        if result.get("status") != "hijacked":
            continue
        findings.append(
            {
                "severity": "high",
                "category": "hijacked_citation",
                "summary": (
                    f"Card citation {result.get('url')} now redirects to "
                    f"{result.get('final_url')}, which fails the outbound-link "
                    "allowlist (data/trusted_domains.json)."
                ),
            }
        )

    for result in (company_hijacked_links or {}).get("results", []) or []:
        if result.get("status") != "hijacked":
            continue
        findings.append(
            {
                "severity": "high",
                "category": "hijacked_company_citation",
                "summary": (
                    f"Company profile '{result.get('company_id')}' citation "
                    f"{result.get('url')} now redirects to "
                    f"{result.get('final_url')}, which fails the outbound-link "
                    "allowlist (data/trusted_domains.json)."
                ),
            }
        )

    for gap in lexicon.get("coverage_gaps", []) or []:
        terms = ", ".join(gap.get("missing_terms", []) or [])
        findings.append(
            {
                "severity": "low",
                "category": "lexicon_coverage_gap",
                "summary": (
                    f"Card {gap.get('card_id')} uses lexicon term(s) {terms} "
                    "not listed in its own lexicon_terms[]."
                ),
            }
        )

    if has_cards:
        for term in lexicon.get("orphans", []) or []:
            findings.append(
                {
                    "severity": "low",
                    "category": "lexicon_orphan",
                    "summary": (
                        f'Lexicon term "{term}" has no seen_in[] entries and is '
                        "not referenced in any current card -- possible orphan."
                    ),
                }
            )

    if verifier_trend.get("trend") == "falling":
        findings.append(
            {
                "severity": "high",
                "category": "verifier_trend",
                "summary": (
                    "Verifier pass-rate trend is falling: rolling 7d "
                    f"{_fmt_pct(verifier_trend.get('rolling_7d_pass_rate'))} vs. "
                    f"prior week {_fmt_pct(verifier_trend.get('prior_week_pass_rate'))} "
                    f"(as of {verifier_trend.get('as_of')})."
                ),
            }
        )

    for story in missed_stories.get("missed_stories", []) or []:
        findings.append(
            {
                "severity": "medium",
                "category": "missed_story",
                "summary": (
                    f'Missed story: "{story.get("title")}" ({story.get("url")}) -- '
                    "not covered by any published card or ledger entry."
                ),
            }
        )

    for pair in duplicates.get("duplicate_pairs", []) or []:
        topics = pair.get("shared_topics") or []
        topics_note = f" (shared topics: {', '.join(topics)})" if topics else ""
        similarity = pair.get("similarity")
        similarity_note = f"{similarity:.2f}" if similarity is not None else "n/a"
        findings.append(
            {
                "severity": "medium",
                "category": "duplicate_topic",
                "summary": (
                    f"Possible duplicate: card {pair.get('card_a')} and "
                    f"{pair.get('card_b')} share headline similarity "
                    f"{similarity_note}{topics_note}."
                ),
            }
        )

    for result in (profile_staleness or {}).get("results", []) or []:
        if not result.get("stale"):
            continue
        findings.append(
            {
                "severity": "low",
                "category": "profile_staleness",
                "summary": (
                    f"Company profile '{result.get('company_id')}' last "
                    f"verified {result.get('last_verified')} -- "
                    f"{result.get('days_stale')} day(s) ago, past the freshness "
                    "floor."
                ),
            }
        )

    return findings


def format_finding_line(finding: dict[str, str]) -> str:
    """One Markdown checkbox line: `- [ ] **[SEVERITY]** <summary>`."""
    label = SEVERITY_LABELS.get(finding["severity"], finding["severity"].upper())
    return f"- [ ] **[{label}]** {finding['summary']}"


def append_findings_to_backlog(
    findings: list[dict[str, str]],
    *,
    run_id: str,
    generated_at: str,
    path: Path | str = BACKLOG_PATH,
) -> int:
    """Append one `"## Audit findings -- <run_id> (<generated_at>)"`
    section, followed by one :func:`format_finding_line` per finding, to
    the end of `path` (default the real `IMPROVEMENT_BACKLOG.md`).

    Does nothing at all -- not even writing an empty header -- when
    `findings` is empty, and returns `0`: a clean week with nothing
    actionable shouldn't leave a trace of empty bureaucracy in the backlog
    file. Otherwise returns `len(findings)` (every finding passed in is
    always written; this function never itself filters -- see
    :func:`derive_findings` for the actionable-vs-not decisions).

    Appends (never rewrites or reorders any existing content), matching
    `IMPROVEMENT_BACKLOG.md`'s own stated convention ("Newest entries at
    the bottom of each section, in commit order").
    """
    if not findings:
        return 0

    path = Path(path)
    lines = [f"## Audit findings -- {run_id} ({generated_at})", ""]
    lines.extend(format_finding_line(f) for f in findings)
    lines.append("")
    block = "\n".join(lines) + "\n"

    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + block)

    return len(findings)
