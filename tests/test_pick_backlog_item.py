"""Tests for `scripts/pick_backlog_item.py` -- the fortnightly improve
loop's backlog-item selection rule.

Exercises the pure `parse_backlog_items` / `pick_next_item` functions
directly against small, hand-built fixture Markdown texts (mixed
checked/unchecked checkbox lines, mixed severities, section-header dates,
a header-less checkbox line, an unrelated `##` heading resetting context,
and -- critically -- real-shaped pre-existing plain-bullet decision-log
entries proving those are never selectable), then `pick_backlog_item`'s
disk-reading behavior against `tmp_path` fixture files, `main()`'s CLI
plumbing, and one integration smoke test against this repo's real,
current `IMPROVEMENT_BACKLOG.md` (which has zero checkbox lines today --
no real audit run has happened yet).
"""
from __future__ import annotations

from pathlib import Path

import scripts.append_backlog_findings as backlog_mod
import scripts.pick_backlog_item as mod
from scripts.pick_backlog_item import (
    BacklogItem,
    parse_backlog_items,
    pick_backlog_item,
    pick_next_item,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Reuse-by-identity: severity vocabulary and the real backlog path both come
# straight from scripts.append_backlog_findings, never a second,
# independently-maintained copy.
# ---------------------------------------------------------------------------


def test_severity_labels_reused_by_identity_not_copied():
    assert mod.SEVERITY_LABELS is backlog_mod.SEVERITY_LABELS


def test_backlog_path_reused_by_identity_not_copied():
    assert mod.BACKLOG_PATH is backlog_mod.BACKLOG_PATH


def test_severity_rank_derived_from_severity_labels_order():
    assert mod.SEVERITY_RANK == {"high": 3, "medium": 2, "low": 1}
    assert mod.SEVERITY_RANK["high"] > mod.SEVERITY_RANK["medium"] > mod.SEVERITY_RANK["low"]


# ---------------------------------------------------------------------------
# parse_backlog_items -- basic shapes
# ---------------------------------------------------------------------------


def test_parse_empty_text_yields_no_items():
    assert parse_backlog_items("") == []


def test_parse_ignores_pre_existing_plain_bullet_decision_log_entries():
    """The real, current `IMPROVEMENT_BACKLOG.md` is almost entirely this
    shape -- `"- **DATE -- decision text.**"`, no `[ ]`/`[x]` prefix at
    all. This is the scope decision's own proof: these lines must
    contribute zero parsed items, with no special-case exclusion logic
    needed -- the checkbox regex simply never matches them."""
    text = (
        "## Decisions (spec-silent judgment calls)\n\n"
        "- **2026-07-09 -- Unpinned dependency versions in "
        "`requirements.txt`.** Kept minimal and unpinned for now.\n"
        "- **2026-07-09 -- Another old-style decision entry.** More decision "
        "prose that happens to mention severity and audit in passing.\n"
    )
    assert parse_backlog_items(text) == []


def test_parse_a_single_audit_findings_section():
    text = (
        "## Audit findings -- audit-20260701T000000Z (2026-07-01T00:00:00Z)\n"
        "\n"
        "- [ ] **[LOW]** Dead citation link (HTTP 404): https://example.com/old\n"
        "- [x] **[MEDIUM]** Already-addressed duplicate finding.\n"
    )
    items = parse_backlog_items(text)
    assert len(items) == 2

    first, second = items
    assert first == BacklogItem(
        severity="low",
        severity_label="LOW",
        checked=False,
        summary="Dead citation link (HTTP 404): https://example.com/old",
        run_id="audit-20260701T000000Z",
        generated_at="2026-07-01T00:00:00Z",
        line_no=3,
    )
    assert second.severity == "medium"
    assert second.checked is True
    assert second.summary == "Already-addressed duplicate finding."


def test_parse_checked_box_accepts_lowercase_and_uppercase_x():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [x] **[LOW]** lowercase x is checked\n"
        "- [X] **[LOW]** uppercase X is checked\n"
        "- [ ] **[LOW]** unchecked\n"
    )
    items = parse_backlog_items(text)
    assert [item.checked for item in items] == [True, True, False]


def test_parse_unrecognized_severity_label_still_parses():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[WEIRD]** an unrecognized severity label\n"
    )
    items = parse_backlog_items(text)
    assert len(items) == 1
    assert items[0].severity == "weird"
    assert items[0].severity_label == "WEIRD"


def test_parse_checkbox_line_with_no_preceding_header_has_no_date():
    text = "- [ ] **[LOW]** a checkbox line before any section header at all\n"
    items = parse_backlog_items(text)
    assert len(items) == 1
    assert items[0].run_id is None
    assert items[0].generated_at is None


def test_parse_unrelated_heading_resets_section_context():
    """A `## `-level heading that isn't an "Audit findings" section (e.g.
    a pre-existing decision-log sub-heading) must reset the current
    section context -- a checkbox line appearing after it must not
    silently inherit a stale, unrelated audit run's timestamp."""
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "## Some Unrelated Heading\n\n"
        "- [ ] **[LOW]** must not inherit audit-1's timestamp\n"
    )
    items = parse_backlog_items(text)
    assert len(items) == 1
    assert items[0].run_id is None
    assert items[0].generated_at is None


def test_parse_multiple_sections_each_keep_their_own_date():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[LOW]** from the first run\n\n"
        "## Audit findings -- audit-2 (2026-07-08T00:00:00Z)\n\n"
        "- [ ] **[LOW]** from the second run\n"
    )
    items = parse_backlog_items(text)
    assert [item.generated_at for item in items] == [
        "2026-07-01T00:00:00Z",
        "2026-07-08T00:00:00Z",
    ]


# ---------------------------------------------------------------------------
# pick_next_item -- the selection rule itself
# ---------------------------------------------------------------------------


def test_pick_next_item_empty_list_is_none():
    assert pick_next_item([]) is None


def test_pick_next_item_all_checked_is_none():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [x] **[HIGH]** already handled\n"
        "- [X] **[LOW]** also already handled\n"
    )
    assert pick_next_item(parse_backlog_items(text)) is None


def test_pick_next_item_prefers_highest_severity():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[LOW]** a low-severity finding\n"
        "- [ ] **[MEDIUM]** a medium-severity finding\n"
        "- [ ] **[HIGH]** a high-severity finding\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.severity == "high"
    assert picked.summary == "a high-severity finding"


def test_pick_next_item_skips_checked_higher_severity_item():
    """A checked HIGH must lose to an unchecked MEDIUM -- checked status
    always wins over severity."""
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [x] **[HIGH]** already handled\n"
        "- [ ] **[MEDIUM]** still open\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.severity == "medium"
    assert picked.summary == "still open"


def test_pick_next_item_ties_broken_by_oldest_date():
    text = (
        "## Audit findings -- audit-old (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[HIGH]** the older high-severity finding\n\n"
        "## Audit findings -- audit-new (2026-07-08T00:00:00Z)\n\n"
        "- [ ] **[HIGH]** the newer high-severity finding\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.summary == "the older high-severity finding"
    assert picked.run_id == "audit-old"


def test_pick_next_item_ties_broken_by_line_number_when_severity_and_date_match():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[LOW]** appears first in the file\n"
        "- [ ] **[LOW]** appears second in the file\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.summary == "appears first in the file"


def test_pick_next_item_header_less_item_never_wins_a_tie_against_a_dated_one():
    text = (
        "- [ ] **[LOW]** a checkbox line before any section header at all\n\n"
        "## Audit findings -- audit-1 (2026-07-05T00:00:00Z)\n\n"
        "- [ ] **[LOW]** a properly dated low-severity finding\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.summary == "a properly dated low-severity finding"


def test_pick_next_item_unrecognized_severity_ranks_below_every_known_one():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[WEIRD]** an unrecognized severity label\n"
        "- [ ] **[LOW]** a real, recognized low-severity finding\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked.severity == "low"


def test_pick_next_item_unrecognized_severity_still_selectable_if_alone():
    text = (
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[WEIRD]** the only unchecked item in the file\n"
    )
    picked = pick_next_item(parse_backlog_items(text))
    assert picked is not None
    assert picked.severity == "weird"


def test_pick_next_item_mixed_realistic_fixture_end_to_end():
    """A fixture mixing old-style plain bullets, multiple audit-findings
    sections, mixed severities, and mixed checked/unchecked status --
    proving the single correct item is selected out of all of it."""
    text = (
        "# IMPROVEMENT_BACKLOG.md\n\n"
        "This file has two jobs...\n\n"
        "---\n\n"
        "## Decisions (spec-silent judgment calls)\n\n"
        "- **2026-07-09 -- Unpinned dependency versions.** Some decision "
        "prose, never a checkbox.\n"
        "- **2026-07-09 -- Another historical decision.** More prose.\n\n"
        "## Phase 2, commit 12: ledger extension (2026-07-09)\n\n"
        "- **A phase-specific historical decision entry, also plain-bulleted.**\n\n"
        "## Audit findings -- audit-20260701T000000Z (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[LOW]** Dead citation link (HTTP 404): https://example.com/old-story\n"
        "- [x] **[MEDIUM]** Already-addressed duplicate finding from the earliest run.\n\n"
        "## Audit findings -- audit-20260708T000000Z (2026-07-08T00:00:00Z)\n\n"
        "- [ ] **[HIGH]** Verifier pass-rate trend is falling: rolling 7d "
        "40.0% vs. prior week 70.0% (as of 2026-07-08).\n"
        "- [ ] **[MEDIUM]** Missed story: \"Some AI Story\" "
        "(https://example.com/story) -- not covered by any published card "
        "or ledger entry.\n"
        "- [ ] **[LOW]** Lexicon term \"orphaned-term\" has no seen_in[] "
        "entries and is not referenced in any current card -- possible orphan.\n\n"
        "## Audit findings -- audit-20260711T233000Z (2026-07-11T23:30:00Z)\n\n"
        "- [ ] **[HIGH]** Another high-severity finding from the newest "
        "run, should lose the tie to the 2026-07-08 one above.\n"
        "- [ ] **[WEIRD]** An unrecognized severity label, should never "
        "outrank a real one.\n"
    )
    items = parse_backlog_items(text)
    # Exactly the 7 checkbox-shaped lines -- the plain-bullet decision-log
    # entries above them contribute nothing.
    assert len(items) == 7

    picked = pick_next_item(items)
    assert picked.severity == "high"
    assert picked.run_id == "audit-20260708T000000Z"
    assert picked.summary.startswith("Verifier pass-rate trend is falling")


# ---------------------------------------------------------------------------
# pick_backlog_item -- disk-reading entry point
# ---------------------------------------------------------------------------


def test_pick_backlog_item_missing_file_returns_none(tmp_path):
    assert pick_backlog_item(tmp_path / "does-not-exist.md") is None


def test_pick_backlog_item_reads_and_selects_from_a_real_file(tmp_path):
    fixture = tmp_path / "BACKLOG.md"
    fixture.write_text(
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[LOW]** a low finding\n"
        "- [ ] **[HIGH]** a high finding\n",
        encoding="utf-8",
    )
    picked = pick_backlog_item(fixture)
    assert picked.severity == "high"
    assert picked.summary == "a high finding"


def test_pick_backlog_item_no_checkbox_lines_returns_none(tmp_path):
    fixture = tmp_path / "BACKLOG.md"
    fixture.write_text(
        "## Decisions (spec-silent judgment calls)\n\n"
        "- **2026-07-09 -- an old-style decision, never a checkbox.**\n",
        encoding="utf-8",
    )
    assert pick_backlog_item(fixture) is None


# ---------------------------------------------------------------------------
# main() -- CLI plumbing
# ---------------------------------------------------------------------------


def test_main_prints_the_picked_item_and_returns_zero(tmp_path, capsys):
    fixture = tmp_path / "BACKLOG.md"
    fixture.write_text(
        "## Audit findings -- audit-1 (2026-07-01T00:00:00Z)\n\n"
        "- [ ] **[HIGH]** the one to pick\n",
        encoding="utf-8",
    )
    exit_code = mod.main(["--path", str(fixture)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[HIGH]" in out
    assert "the one to pick" in out


def test_main_reports_no_item_found_and_returns_zero(tmp_path, capsys):
    fixture = tmp_path / "BACKLOG.md"
    fixture.write_text(
        "## Decisions (spec-silent judgment calls)\n\n"
        "- **2026-07-09 -- an old-style decision, never a checkbox.**\n",
        encoding="utf-8",
    )
    exit_code = mod.main(["--path", str(fixture)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "No unaddressed backlog item found" in out


def test_main_defaults_to_the_real_backlog_path_when_no_path_given(capsys):
    """No `--path` given -> reads the real, current `IMPROVEMENT_BACKLOG.md`.
    Its checkbox contents churn as `audit.yml` appends findings over time, so
    this only asserts the CLI runs cleanly and reports zero exit -- not which
    branch (item found vs. none) it lands on. See the integration smoke test
    below for the same live-file caveat."""
    exit_code = mod.main([])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert out.strip() != ""


# ---------------------------------------------------------------------------
# Integration smoke test against the real, current IMPROVEMENT_BACKLOG.md
# ---------------------------------------------------------------------------


def test_real_improvement_backlog_parses_cleanly():
    """This repo's own real `IMPROVEMENT_BACKLOG.md` gains new checkbox
    sections over time as `audit.yml` appends findings, so this deliberately
    does not assert a specific item count or emptiness (that would be a
    time-bomb against the file's own designed mutation). It only confirms
    the real parser runs cleanly (no exception) against the real, current
    file and returns a well-formed result."""
    real_path = REPO_ROOT / "IMPROVEMENT_BACKLOG.md"
    assert real_path.is_file()
    text = real_path.read_text(encoding="utf-8")

    items = parse_backlog_items(text)
    assert isinstance(items, list)
    assert all(isinstance(item, BacklogItem) for item in items)

    picked = pick_backlog_item(real_path)
    assert picked is None or isinstance(picked, BacklogItem)
