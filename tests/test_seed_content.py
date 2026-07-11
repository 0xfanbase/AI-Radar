"""Validation tests for Phase 3's one-time seed content
(``content/frontier_board.json``, ``content/lexicon.json``,
``content/primer.json``).

Unlike ``tests/test_schemas.py``/``tests/test_p2_schemas.py`` (which check
small synthetic fixtures under ``fixtures/schema_examples/``), this module
validates the *real, committed* Phase 3 seed artifacts directly -- the
actual content this backfill wrote, not a stand-in.

**Frontier Board row-count history:** the first backfill pass shipped only
4 of the intended >=12 rows and this file originally encoded that shortfall
as a non-blocking, non-strict ``xfail`` on
``test_frontier_board_meets_phase_3_target_row_count`` (see git history and
``IMPROVEMENT_BACKLOG.md``'s "Phase 3: seed-content backfill" entry for the
full account). A follow-up backfill turn independently live-fetched and
verified 9 more rows -- OpenAI, Google DeepMind, Meta, xAI, Mistral (US
section, per the plan's own logged Mistral-bucketing quirk), Alibaba Qwen,
Moonshot AI, Zhipu AI, and ByteDance -- bringing the real total to 13,
past the >=12 target and giving China 5 rows instead of 1. The former
``xfail`` is now a hard assertion (see
"Phase 3 PM checkpoint round 2" in ``PROGRESS.md``/``IMPROVEMENT_BACKLOG.md``
for the closure record); a future accidental regression back below 12 rows
must fail the suite, not quietly report XFAIL/XPASS.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from jsonschema import ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = REPO_ROOT / "content"

FRONTIER_BOARD_PATH = CONTENT_DIR / "frontier_board.json"
LEXICON_PATH = CONTENT_DIR / "lexicon.json"
PRIMER_PATH = CONTENT_DIR / "primer.json"

# Phase 3's stated target (approved build plan, section 4), now met -- see
# IMPROVEMENT_BACKLOG.md/PROGRESS.md's "Phase 3 PM checkpoint round 2" entries
# for the closure record. FRONTIER_BOARD_ACTUAL_ROWS is a floor at the real
# shipped count (not the bare >=12 target) so a future accidental deletion of
# rows is still caught even if it doesn't drop all the way below 12.
FRONTIER_BOARD_TARGET_MIN_ROWS = 12
FRONTIER_BOARD_ACTUAL_ROWS = 13
LEXICON_TARGET_COUNT = 30

_HREF_RE = re.compile(r"<a\s+href=", re.IGNORECASE)


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# content/frontier_board.json
# ---------------------------------------------------------------------------


def test_frontier_board_validates_against_schema():
    board = _load(FRONTIER_BOARD_PATH)
    validate(board, "frontier_board")  # must not raise


def test_frontier_board_has_at_least_the_rows_this_backfill_actually_shipped():
    board = _load(FRONTIER_BOARD_PATH)
    assert len(board) >= FRONTIER_BOARD_ACTUAL_ROWS


def test_frontier_board_meets_phase_3_target_row_count():
    # No longer xfail: the follow-up backfill turn closed the gap (see
    # module docstring). A regression below 12 rows is now a real failure.
    board = _load(FRONTIER_BOARD_PATH)
    assert len(board) >= FRONTIER_BOARD_TARGET_MIN_ROWS


def test_frontier_board_rows_span_more_than_one_region():
    # Not every region needs equal representation, but a "Board" spanning
    # "US/China/open-weights" per CLAUDE.md's purpose statement shouldn't
    # collapse to a single region.
    board = _load(FRONTIER_BOARD_PATH)
    regions = {row["region"] for row in board}
    assert len(regions) > 1


def test_frontier_board_china_region_has_more_than_one_row():
    # The first backfill pass left China represented by a single row
    # (DeepSeek only) -- a real neutrality-adjacent gap (CLAUDE.md's Hard
    # Rule 4 requires the same evidentiary bar and, implicitly, the same
    # coverage effort for Chinese labs as US ones). The follow-up backfill
    # turn added Alibaba Qwen, Moonshot AI, Zhipu AI, and ByteDance. This
    # regression test locks that in going forward.
    board = _load(FRONTIER_BOARD_PATH)
    china_rows = [row for row in board if row["region"] == "China"]
    assert len(china_rows) > 1, "China region collapsed back to a single row"


def test_frontier_board_has_no_duplicate_lab_model_pairs():
    # CLAUDE.md's Frontier Board upsert rule keys rows on exact (lab,
    # model) -- one row per model, refreshed in place, never duplicated.
    board = _load(FRONTIER_BOARD_PATH)
    pairs = [(row["lab"], row["model"]) for row in board]
    assert len(pairs) == len(set(pairs))


# ---------------------------------------------------------------------------
# content/lexicon.json
# ---------------------------------------------------------------------------


def test_lexicon_validates_against_schema():
    lexicon = _load(LEXICON_PATH)
    validate(lexicon, "lexicon")  # must not raise


def test_lexicon_has_exactly_30_entries():
    lexicon = _load(LEXICON_PATH)
    assert len(lexicon) == LEXICON_TARGET_COUNT


def test_lexicon_terms_are_unique():
    lexicon = _load(LEXICON_PATH)
    terms = [e["term"] for e in lexicon]
    assert len(terms) == len(set(terms))


def test_every_lexicon_entry_deeper_field_has_at_least_one_href():
    # lexicon.schema.json has no dedicated source_url field; Phase 3's
    # decision (logged in IMPROVEMENT_BACKLOG.md) is that each entry's live
    # citation is embedded as an inline <a href="..."> anchor inside
    # `deeper` instead. This is the acceptance check for that convention.
    lexicon = _load(LEXICON_PATH)
    offenders = [e["term"] for e in lexicon if not _HREF_RE.search(e["deeper"])]
    assert offenders == [], f"entries missing an <a href> citation in deeper: {offenders}"


def test_every_lexicon_related_term_resolves_within_the_same_file():
    lexicon = _load(LEXICON_PATH)
    terms = {e["term"] for e in lexicon}
    unresolved = [
        (e["term"], related)
        for e in lexicon
        for related in e["related"]
        if related not in terms
    ]
    assert unresolved == [], f"related[] entries with no matching term: {unresolved}"


# ---------------------------------------------------------------------------
# content/primer.json
# ---------------------------------------------------------------------------


def _slugify(term: str) -> str:
    """Lowercase + hyphenate a lexicon term into its primer slug form --
    e.g. "foundation model" -> "foundation-model", "RLHF" -> "rlhf".
    Matches the transform this backfill's task instructions specified for
    deriving PRIMER_ORDER's 10 slugs; site/lib's own slugifier (Phase 4,
    not yet built) should match this convention for lexicon terms to stay
    linkable from the primer -- flagged in IMPROVEMENT_BACKLOG.md."""
    return term.lower().replace(" ", "-")


def test_primer_has_generated_at_and_terms_fields():
    primer = _load(PRIMER_PATH)
    assert "generated_at" in primer
    assert "terms" in primer
    assert isinstance(primer["terms"], list)


def test_primer_terms_all_resolve_to_real_lexicon_entries():
    primer = _load(PRIMER_PATH)
    lexicon = _load(LEXICON_PATH)
    slug_to_term = {_slugify(e["term"]): e["term"] for e in lexicon}
    unresolved = [slug for slug in primer["terms"] if slug not in slug_to_term]
    assert unresolved == [], f"primer slugs with no matching lexicon entry: {unresolved}"


def test_primer_has_all_10_intended_dependency_ordered_terms():
    # Fixed dependency order per the approved build plan's Phase 3 section
    # (and this backfill's own PRIMER_ORDER input) -- logged as a hard
    # requirement in IMPROVEMENT_BACKLOG.md if any of the 10 didn't make it.
    expected = [
        "foundation-model",
        "transformer",
        "attention",
        "parameter-count",
        "context-window",
        "pretraining",
        "fine-tuning",
        "rlhf",
        "hallucination",
        "open-weights",
    ]
    primer = _load(PRIMER_PATH)
    assert primer["terms"] == expected


# ---------------------------------------------------------------------------
# Fixed-fixture invalid-instance checks (schema still rejects malformed
# data even though the tests above exercise the real committed content).
# ---------------------------------------------------------------------------


def test_frontier_board_schema_rejects_a_row_missing_a_required_field():
    board = _load(FRONTIER_BOARD_PATH)
    broken = [dict(board[0])]
    del broken[0]["source_url"]
    with pytest.raises(ValidationError):
        validate(broken, "frontier_board")


def test_lexicon_schema_rejects_an_entry_missing_a_required_field():
    lexicon = _load(LEXICON_PATH)
    broken = [dict(lexicon[0])]
    del broken[0]["related"]
    with pytest.raises(ValidationError):
        validate(broken, "lexicon")
