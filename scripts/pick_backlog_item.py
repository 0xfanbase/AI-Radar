#!/usr/bin/env python3
"""Fortnightly-improve-loop backlog-item picker (``scripts/pick_backlog_item.py``).

Per the approved build plan's Phase 5 section ("`improve.yml` ...
`scripts/pick_backlog_item.py` selects the highest-severity unaddressed
backlog item (tie-break: oldest) -- a deterministic, testable rule, not
left to the LLM's discretion") and CLAUDE.md's own `IMPROVEMENT_BACKLOG.md`
job #2 ("The fortnightly `improve.yml` picks the highest-severity
unaddressed item from this file (oldest first on ties) as its next
self-improvement target"). This module implements exactly that selection
rule as pure, testable Python -- no LLM anywhere in this file.

**This turn builds and documents the picker only.** Per this turn's own
explicit scope, no `improve.yml` trigger/Routine is created or activated --
a live, scheduled process that can touch arbitrary files (including code
and workflows, unlike the daily analyst/verifier's content/+data/-only
allowlist) is a materially different authorization than the one the user
has already granted for the narrower daily analyst refresh. Wiring this
picker's output into a real `improve.yml` step (or a Routine reading it)
is left as an explicit future activation step for the user, exactly as
CLAUDE.md's own architecture note already treats the daily Routine's own
activation.

## The real format this module parses

Read directly out of the actual, shipped
``scripts/append_backlog_findings.py`` (``format_finding_line`` /
``append_findings_to_backlog``), not assumed or guessed: every audit run
that finds at least one actionable finding appends one dated section to
``IMPROVEMENT_BACKLOG.md`` shaped exactly like this::

    ## Audit findings -- audit-20260711T233000Z (2026-07-11T23:30:00Z)

    - [ ] **[HIGH]** Verifier pass-rate trend is falling: ...
    - [ ] **[MEDIUM]** Missed story: "..." (https://...) -- not covered ...
    - [ ] **[LOW]** Dead citation link (HTTP 404): https://...

**Correction of this turn's own task-text example, logged plainly rather
than silently reconciled:** this turn's own instructions described the
target line shape as e.g. ``"- [ ] [audit:DATE][severity:high] ..."`` --
that exact shape does not appear anywhere in this repo's real, shipped
``scripts/append_backlog_findings.py`` (confirmed by reading that file's
``format_finding_line``/``append_findings_to_backlog`` in full, not
assumed from the task text). The real, already-committed format is
``"- [ ] **[SEVERITY]** <summary>"`` grouped under a
``"## Audit findings -- <run_id> (<generated_at>)"`` section header that
carries the shared timestamp for every finding appended in that one run
(no per-line date field exists at all). Since the task instruction itself
says to parse "the format `scripts/append_backlog_findings.py` writes,"
the authoritative source is that module's own real, tested code -- not
the task text's illustrative shorthand -- so this module parses the real
shape above. Severity labels/order are imported directly from
``scripts.append_backlog_findings.SEVERITY_LABELS`` (reuse, not a second,
independently-maintained severity vocabulary that could silently drift
from what that module actually emits).

## Selection rule

1. Only lines matching the real checkbox+severity shape above are
   candidates at all -- see "Scope decision" below for why every other
   line in the file (including every pre-existing plain-bullet decision-log
   entry) is structurally never a candidate, without this module needing
   any special-case logic to exclude them.
2. A checked box (``- [x]`` / ``- [X]``) is never picked -- it has already
   been addressed.
3. Among unchecked candidates, the highest-severity one wins
   (``high`` > ``medium`` > ``low``, per
   ``scripts.append_backlog_findings.SEVERITY_LABELS``'s own order). An
   unrecognized severity label (should not occur against this repo's real
   generator, but a hand-edited or future-format line could carry one)
   ranks below every recognized label rather than raising -- logged as a
   defensive, spec-silent choice.
4. Ties are broken by **oldest date** -- each finding line has no date of
   its own, so "its date" is its enclosing ``"## Audit findings -- ...
   (<generated_at>)"`` section header's timestamp (every finding appended
   in the same audit run shares that one timestamp, which is exactly the
   granularity a weekly audit run actually produces). A checkbox line with
   no enclosing section header at all (a malformed/hand-edited file, never
   produced by the real generator) still parses as a valid candidate but
   is treated as having no date, which sorts *after* every dated
   candidate at the same severity -- it should never win a tie purely
   because its own metadata is missing.
5. A final, fully deterministic tie-break -- same severity, same section
   date -- is the line's own position in the file (earlier wins), so the
   selection is single-valued even for two findings appended in the very
   same audit run.

## Scope decision, logged in full in IMPROVEMENT_BACKLOG.md

``IMPROVEMENT_BACKLOG.md`` predates this checkbox format entirely: its
"Decisions (spec-silent judgment calls)" section (and every phase-specific
sub-section) is one plain Markdown bullet per entry, e.g.
``"- **2026-07-09 -- Unpinned dependency versions ...**"`` -- no
``[ ]``/``[x]`` checkbox prefix at all. Those entries are historical
decision-log prose, never a to-do item in the sense the fortnightly
improve loop is meant to act on, and this module's checkbox regex simply
never matches a line that doesn't start with ``"- [ ]"`` or ``"- [x]"`` --
so every one of those older entries is structurally invisible to this
picker, with no separate "is this an old-style entry" check needed. This
is a deliberate scope choice (this turn's own explicit instruction), not
an oversight -- proven, not merely asserted, by
``tests/test_pick_backlog_item.py``'s fixture, which includes real-shaped
plain-bullet lines alongside checkbox lines and asserts the plain ones
contribute zero parsed items.

Every function here is pure except :func:`pick_backlog_item` (the one
disk-reading entry point) and :func:`main` -- matching every sibling
``auditor.*``/``scripts.*`` module's own "pure-core, one disk-touching
entry point" convention.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow running as `python scripts/pick_backlog_item.py` (no package
# install / no `-m` needed) -- same sys.path trick every other script in
# this repo uses (scripts/plan_run.py, scripts/reconcile_run.py,
# scripts/append_backlog_findings.py).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.append_backlog_findings import (  # noqa: E402
    BACKLOG_PATH,
    SEVERITY_LABELS,
)

__all__ = [
    "BACKLOG_PATH",
    "BacklogItem",
    "SEVERITY_RANK",
    "SECTION_HEADER_RE",
    "CHECKBOX_RE",
    "parse_backlog_items",
    "pick_next_item",
    "pick_backlog_item",
    "main",
]

# Reuse scripts.append_backlog_findings's own severity vocabulary/order
# rather than a second, independently-maintained list that could silently
# drift from what that module actually emits. SEVERITY_LABELS is
# `{"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}`, insertion-ordered
# highest-severity-first -- rank each key by its (reverse) position, so
# "high" always ranks strictly above "medium" strictly above "low"
# regardless of how many severities that module ever defines.
SEVERITY_RANK: dict[str, int] = {
    severity: len(SEVERITY_LABELS) - index
    for index, severity in enumerate(SEVERITY_LABELS)
}
# Rank floor for a severity label this module doesn't recognize at all
# (e.g. a hand-edited line, or a future label scripts.append_backlog_findings
# grows that this module hasn't been updated for yet) -- always below every
# known severity, never raises. Spec-silent, logged in IMPROVEMENT_BACKLOG.md.
UNKNOWN_SEVERITY_RANK = 0

# `{"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}` -- the bracket text
# format_finding_line() actually writes (SEVERITY_LABELS' own values) maps
# back to the lowercase severity key used for ranking/lookup.
_LABEL_TO_SEVERITY = {label: severity for severity, label in SEVERITY_LABELS.items()}

# Matches a section header scripts.append_backlog_findings.
# append_findings_to_backlog actually writes:
#   "## Audit findings -- audit-20260711T233000Z (2026-07-11T23:30:00Z)"
SECTION_HEADER_RE = re.compile(
    r"^## Audit findings -- (?P<run_id>\S+) \((?P<generated_at>.*)\)\s*$"
)

# Matches a finding line scripts.append_backlog_findings.format_finding_line
# actually writes: "- [ ] **[HIGH]** <summary>" (or "- [x]"/"- [X]" once
# addressed). The severity label is captured as any run of letters/digits/
# underscores so an unrecognized future label still parses as a candidate
# (see UNKNOWN_SEVERITY_RANK) rather than silently vanishing from the file
# entirely.
CHECKBOX_RE = re.compile(
    r"^- \[(?P<mark>[ xX])\] \*\*\[(?P<severity_label>[A-Za-z0-9_]+)\]\*\* "
    r"(?P<summary>.+?)\s*$"
)

# The exact strftime/strptime shape auditor.report._iso_utc /
# scripts.append_backlog_findings.append_findings_to_backlog's own
# `generated_at` argument both use -- e.g. "2026-07-11T23:30:00Z".
_GENERATED_AT_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Sort placeholder for "no known date" -- deliberately *after* every real,
# parseable date (see module docstring point 4): a candidate with unknown
# provenance must never win an "oldest first" tie against one with a real,
# known timestamp just because its own metadata happens to be missing.
_UNKNOWN_DATE_SORT_KEY = datetime.max


@dataclass(frozen=True)
class BacklogItem:
    """One parsed ``"- [ ] **[SEVERITY]** <summary>"`` line.

    ``severity`` is the normalized lowercase key (``"high"``/``"medium"``/
    ``"low"``, or the raw lowercased label verbatim if unrecognized).
    ``severity_label`` is the original bracket text exactly as written in
    the file (e.g. ``"HIGH"``). ``run_id``/``generated_at`` come from the
    nearest enclosing ``"## Audit findings -- ..."`` section header found
    above this line -- both ``None`` if no such header precedes it (a
    malformed/hand-edited file; never produced by the real generator).
    ``line_no`` is this line's 1-indexed position in the source text, used
    only as the final, fully deterministic tie-break.
    """

    severity: str
    severity_label: str
    checked: bool
    summary: str
    run_id: Optional[str]
    generated_at: Optional[str]
    line_no: int


def _parse_generated_at(raw: Optional[str]) -> Optional[datetime]:
    """Parse a section header's `generated_at` text (e.g.
    `"2026-07-11T23:30:00Z"`) into a naive `datetime` for ordering
    purposes only -- returns `None` for a missing or unparseable value
    (never raises), which callers then treat as "unknown, sorts last."
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _GENERATED_AT_FMT)
    except ValueError:
        return None


def parse_backlog_items(text: str) -> list[BacklogItem]:
    """Parse every ``"- [ ] **[SEVERITY]** <summary>"`` line in `text`
    into a :class:`BacklogItem`, tagging each with the `run_id`/
    `generated_at` of the nearest ``"## Audit findings -- ..."`` section
    header above it.

    Any other level-2 Markdown heading (``"## "`` that isn't an "Audit
    findings" section -- e.g. this file's own pre-existing
    ``"## Decisions (spec-silent judgment calls)"``/``"## Phase 2, commit
    12: ..."`` headings) resets the current section context to
    ``(None, None)`` rather than letting a stale audit-run timestamp leak
    into an unrelated later section's own checkbox-shaped lines (there are
    none today, but this keeps the parser's behavior well-defined if one
    is ever added under a different heading). A line outside any ``##``
    section at all (before the first heading) also starts with no
    context, i.e. ``(None, None)``.

    Every other line -- including every pre-existing plain-bullet
    decision-log entry, which never starts with ``"- [ ]"``/``"- [x]"`` at
    all -- is silently skipped; see the module docstring's "Scope
    decision" section for why this is deliberate, not a gap.
    """
    items: list[BacklogItem] = []
    current_run_id: Optional[str] = None
    current_generated_at: Optional[str] = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("## "):
            header_match = SECTION_HEADER_RE.match(line)
            if header_match:
                current_run_id = header_match.group("run_id")
                current_generated_at = header_match.group("generated_at")
            else:
                current_run_id = None
                current_generated_at = None
            continue

        checkbox_match = CHECKBOX_RE.match(line)
        if not checkbox_match:
            continue

        label = checkbox_match.group("severity_label")
        severity = _LABEL_TO_SEVERITY.get(label.upper(), label.lower())
        items.append(
            BacklogItem(
                severity=severity,
                severity_label=label,
                checked=checkbox_match.group("mark") in ("x", "X"),
                summary=checkbox_match.group("summary"),
                run_id=current_run_id,
                generated_at=current_generated_at,
                line_no=line_no,
            )
        )

    return items


def _sort_key(item: BacklogItem) -> tuple[int, datetime, int]:
    """Ascending sort key implementing the module docstring's selection
    rule: highest severity first (negated rank, so `sorted`'s ascending
    order puts the highest-severity item first), then oldest
    `generated_at` first, then lowest `line_no` (earliest in the file)
    first.
    """
    rank = SEVERITY_RANK.get(item.severity, UNKNOWN_SEVERITY_RANK)
    dt = _parse_generated_at(item.generated_at) or _UNKNOWN_DATE_SORT_KEY
    return (-rank, dt, item.line_no)


def pick_next_item(items: list[BacklogItem]) -> Optional[BacklogItem]:
    """Select the single highest-severity unchecked item from `items` per
    the module docstring's selection rule, or `None` if every item is
    already checked (or `items` is empty) -- a clean "nothing left to
    improve" state, not an error.
    """
    candidates = [item for item in items if not item.checked]
    if not candidates:
        return None
    return min(candidates, key=_sort_key)


def pick_backlog_item(path: Path | str = BACKLOG_PATH) -> Optional[BacklogItem]:
    """Read, parse, and select the fortnightly improve loop's next target
    from the real (or caller-supplied) `IMPROVEMENT_BACKLOG.md`.

    Returns `None` if the file doesn't exist, has no checkbox-shaped lines
    at all (this repo's own real, current state -- no audit run has
    happened yet), or every checkbox-shaped line is already checked.
    """
    path = Path(path)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return pick_next_item(parse_backlog_items(text))


def _format_item(item: BacklogItem) -> str:
    section = item.run_id or "(no section header)"
    when = item.generated_at or "unknown"
    return (
        f"[{item.severity_label}] {item.summary}\n"
        f"  section={section} generated_at={when} line={item.line_no}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts/pick_backlog_item.py")
    parser.add_argument(
        "--path",
        type=Path,
        default=BACKLOG_PATH,
        help="IMPROVEMENT_BACKLOG.md path to read (default: the real repo file).",
    )
    args = parser.parse_args(argv)

    item = pick_backlog_item(args.path)
    if item is None:
        print(
            "No unaddressed backlog item found -- nothing for the "
            "fortnightly improve loop to select this run."
        )
        return 0

    print(_format_item(item))
    return 0


if __name__ == "__main__":
    sys.exit(main())
