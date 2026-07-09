"""Validation tests for Phase 3's one-time seed content
(``content/frontier_board.json``, ``content/lexicon.json``,
``content/primer.json``).

Unlike ``tests/test_schemas.py``/``tests/test_p2_schemas.py`` (which check
small synthetic fixtures under ``fixtures/schema_examples/``), this module
validates the *real, committed* Phase 3 seed artifacts directly -- the
actual content this backfill wrote, not a stand-in.

Per this turn's task scope, the acceptance bar for each file is "meets the
target, or documents why not" rather than a hard failure on a shortfall:
this backfill's own ``IMPROVEMENT_BACKLOG.md``/``PROGRESS.md`` entries
already log that only 4 of the intended >=12 Frontier Board rows were
verified this run (see "Phase 3 seed-content backfill" in both files). The
tests below therefore assert a floor matching what was actually shipped
(so a real regression -- e.g. a future edit that empties the file --still
fails loudly) plus one explicit, non-blocking ``xfail`` that tracks the
>=12 target so it's visible in test output without turning the suite red,
matching this repo's existing convention of asserting a floor rather than
an exact count against real (organically-growing) data
(``tests/test_p2_schemas.py``'s ``len(real_ledger["entries"]) > 0``).
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

# Phase 3's stated targets (approved build plan, section 4) vs. what this
# backfill run actually verified live -- see IMPROVEMENT_BACKLOG.md for the
# full shortfall explanation.
FRONTIER_BOARD_TARGET_MIN_ROWS = 12
FRONTIER_BOARD_ACTUAL_ROWS = 4
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


@pytest.mark.xfail(
    reason=(
        "Phase 3 target is >=12 Frontier Board rows spanning US/China/"
        "open-weights; this backfill run only live-verified 4 distinct "
        "(lab, model) rows before running out of scope for this turn. "
        "Logged in IMPROVEMENT_BACKLOG.md with the specific labs/models "
        "still needed to close the gap. Not marked strict: if a future "
        "analyst run or backfill grows content/frontier_board.json past "
        "12 rows, this test starts passing (reported as XPASS) instead of "
        "needing to be deleted."
    ),
    strict=False,
)
def test_frontier_board_meets_phase_3_target_row_count():
    board = _load(FRONTIER_BOARD_PATH)
    assert len(board) >= FRONTIER_BOARD_TARGET_MIN_ROWS


def test_frontier_board_rows_span_more_than_one_region():
    # Not every region needs equal representation, but a "Board" spanning
    # "US/China/open-weights" per CLAUDE.md's purpose statement shouldn't
    # collapse to a single region.
    board = _load(FRONTIER_BOARD_PATH)
    regions = {row["region"] for row in board}
    assert len(regions) > 1


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
