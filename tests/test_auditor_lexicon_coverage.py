"""Tests for auditor/lexicon_audit.py (Phase 5's lexicon coverage/orphan
checker -- see CLAUDE.md's "audit.yml -- weekly" bullet: "lexicon orphan/
coverage check (terms used vs defined)").

`content/cards/` is currently empty (no analyst run has happened for
real yet), so -- per this turn's explicit instruction -- these tests
exercise the pure `find_coverage_gaps` / `audit_coverage` / `find_orphans`
/ `audit_lexicon` functions directly against small fixture cards and a
small fixture lexicon, rather than the real 30-entry
`content/lexicon.json` (which is separately exercised below by one
integration-flavored smoke test to confirm the real file at least loads
and scans without raising).

Imported the same way `tests/test_check_path_allowlist.py` already
imports `scripts/check_path_allowlist.py` -- `auditor/`, like `scripts/`,
has no `__init__.py` (an implicit namespace package is enough; no other
module here needs `auditor` importable as a real package yet), so the
directory is added to `sys.path` and the module imported by its bare
name, rather than loaded via `importlib.util.spec_from_file_location`
(that heavier approach is what `site/tests` and `tests/test_linkify.py`
use instead, specifically because `site` collides with a stdlib module
name -- `auditor` has no such collision).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "auditor"))

import lexicon_audit as mod  # noqa: E402
from lexicon_audit import (  # noqa: E402
    audit_coverage,
    audit_lexicon,
    find_coverage_gaps,
    find_orphans,
)


def _lexicon_entry(term: str, seen_in: list[str] | None = None) -> dict:
    """Build a minimal, schema-shaped lexicon entry fixture (every
    `lexicon.schema.json`-required field present, since a stray
    `KeyError`/`AttributeError` on a fixture that skipped a required
    field would be a fixture bug, not a real finding).
    """
    return {
        "term": term,
        "one_liner": f"{term} one-liner.",
        "deeper": f"{term} deeper cut.",
        "related": [],
        "seen_in": seen_in or [],
    }


def _card(card_id: str, **prose: str) -> dict:
    """Build a minimal card fixture. Only the fields a test actually sets
    are populated (plus `id`) -- `find_coverage_gaps`/`find_orphans` only
    ever read `CARD_PROSE_FIELDS` and `lexicon_terms`, so a fixture never
    needs every `card.schema.json`-required field to exercise this
    module.
    """
    card = {"id": card_id}
    card.update(prose)
    return card


# ---------------------------------------------------------------------------
# find_coverage_gaps
# ---------------------------------------------------------------------------


def test_genuine_coverage_gap_is_detected():
    """A term used in the card's prose but missing from its own
    lexicon_terms[] is reported as a gap."""
    card = _card(
        "card-a",
        headline="Lab ships new agent framework",
        what_happened=(
            "The lab's fine-tuning process now incorporates rag for "
            "better retrieval over long documents."
        ),
        why_it_matters="Newcomers can now build grounded assistants more easily.",
        one_liner="A new way to fine-tune agents with retrieval.",
        lexicon_terms=["fine-tuning"],  # RAG used above but not listed
    )
    gaps = find_coverage_gaps(card, ["RAG", "fine-tuning"])
    assert gaps == ["RAG"]


def test_correctly_listed_term_is_not_a_gap():
    """A term that IS listed in the card's own lexicon_terms[] is never
    reported as a gap, even though it's also genuinely used in the
    prose."""
    card = _card(
        "card-a",
        headline="Lab ships new agent framework",
        what_happened=(
            "The lab's fine-tuning process now incorporates rag for "
            "better retrieval over long documents."
        ),
        why_it_matters="Newcomers can now build grounded assistants more easily.",
        one_liner="A new way to fine-tune agents with retrieval.",
        lexicon_terms=["fine-tuning"],
    )
    gaps = find_coverage_gaps(card, ["fine-tuning"])
    assert gaps == []


def test_listed_term_comparison_is_case_insensitive():
    """A card that lists "rag" (lowercase) in lexicon_terms[] is treated
    as already covering the canonical lexicon term "RAG" -- the
    already-listed check is case-insensitive, not just the prose scan."""
    card = _card(
        "card-d",
        headline="Retrieval gets an upgrade",
        what_happened="The system now uses RAG to ground its answers.",
        why_it_matters="Reduces hallucination in long-document Q&A.",
        one_liner="Retrieval, upgraded.",
        lexicon_terms=["rag"],
    )
    gaps = find_coverage_gaps(card, ["RAG"])
    assert gaps == []


def test_word_boundary_prevents_false_positive_inside_longer_word():
    """"RAG" must not match as a substring inside "storage" or "average"
    -- the core case-sensitivity/word-boundary edge case this module
    exists to get right."""
    card = _card(
        "card-b",
        headline="Cloud storage costs keep rising",
        what_happened=(
            "Average storage spend increased across providers this "
            "quarter, driven by larger backup archives."
        ),
        why_it_matters="Storage budgets are a growing line item for AI labs.",
        one_liner="Storage keeps getting pricier.",
        lexicon_terms=[],
    )
    gaps = find_coverage_gaps(card, ["RAG"])
    assert gaps == []


def test_word_boundary_edge_case_is_not_defeated_by_punctuation_either():
    """A term at a sentence boundary (comma/period-adjacent) still counts
    as a genuine, standalone use -- `\\b` matches at punctuation as well
    as whitespace."""
    card = _card(
        "card-e",
        headline="New pipeline for grounded answers",
        what_happened="It relies on RAG, not on a larger context window.",
        why_it_matters="Cheaper than scaling context length alone.",
        one_liner="Retrieval beats brute-force context.",
        lexicon_terms=[],
    )
    gaps = find_coverage_gaps(card, ["RAG"])
    assert gaps == ["RAG"]


def test_missing_prose_fields_do_not_raise():
    """A fixture (or a real card missing an optional-in-practice field)
    that omits some CARD_PROSE_FIELDS entries is handled gracefully, not
    a KeyError."""
    card = _card("card-f", headline="Only a headline, nothing else")
    gaps = find_coverage_gaps(card, ["RAG", "fine-tuning"])
    assert gaps == []


# ---------------------------------------------------------------------------
# audit_coverage (multi-card wrapper)
# ---------------------------------------------------------------------------


def test_audit_coverage_reports_only_cards_with_gaps():
    lexicon = [_lexicon_entry("RAG"), _lexicon_entry("fine-tuning")]
    gap_card = _card(
        "card-a",
        headline="Lab ships new agent framework",
        what_happened="The lab's fine-tuning process now incorporates rag.",
        why_it_matters="Matters.",
        one_liner="One liner.",
        lexicon_terms=["fine-tuning"],
    )
    clean_card = _card(
        "card-b",
        headline="Cloud storage costs keep rising",
        what_happened="Average storage spend increased across providers.",
        why_it_matters="Matters.",
        one_liner="One liner.",
        lexicon_terms=[],
    )
    findings = audit_coverage([gap_card, clean_card], lexicon)
    assert findings == [{"card_id": "card-a", "missing_terms": ["RAG"]}]


def test_audit_coverage_returns_empty_list_when_nothing_to_report():
    lexicon = [_lexicon_entry("RAG")]
    clean_card = _card(
        "card-b",
        headline="Cloud storage costs keep rising",
        what_happened="Average storage spend increased across providers.",
        why_it_matters="Matters.",
        one_liner="One liner.",
        lexicon_terms=[],
    )
    assert audit_coverage([clean_card], lexicon) == []


# ---------------------------------------------------------------------------
# find_orphans
# ---------------------------------------------------------------------------


def test_orphan_is_detected_when_seen_in_empty_and_never_mentioned():
    lexicon = [_lexicon_entry("hallucination", seen_in=[])]
    cards = [
        _card(
            "card-a",
            headline="Lab ships new agent framework",
            what_happened="The lab's fine-tuning process now incorporates rag.",
            why_it_matters="Matters.",
            one_liner="One liner.",
            lexicon_terms=["fine-tuning"],
        ),
    ]
    assert find_orphans(lexicon, cards) == ["hallucination"]


def test_term_with_nonempty_seen_in_is_never_an_orphan():
    """Even if the passed-in `cards` subset doesn't itself mention the
    term, a non-empty seen_in[] is enough to prove it's not an orphan --
    seen_in[] is the analyst's own historical record, which may span more
    cards than whatever subset this particular audit run is given."""
    lexicon = [_lexicon_entry("scaling laws", seen_in=["2026-01-01-some-older-card"])]
    cards = [
        _card(
            "card-b",
            headline="Cloud storage costs keep rising",
            what_happened="Average storage spend increased across providers.",
            why_it_matters="Matters.",
            one_liner="One liner.",
            lexicon_terms=[],
        ),
    ]
    assert find_orphans(lexicon, cards) == []


def test_term_referenced_in_prose_is_not_an_orphan_even_with_empty_seen_in():
    """A term genuinely used in a card's prose is not an orphan even
    before the analyst's own auto-growth rule has back-filled seen_in[]
    for it -- this is the "used but not (yet) recorded" case, distinct
    from a true orphan."""
    lexicon = [_lexicon_entry("RAG", seen_in=[])]
    cards = [
        _card(
            "card-a",
            headline="Lab ships new agent framework",
            what_happened="The lab's fine-tuning process now incorporates rag.",
            why_it_matters="Matters.",
            one_liner="One liner.",
            lexicon_terms=["fine-tuning"],
        ),
    ]
    assert find_orphans(lexicon, cards) == []


def test_orphan_word_boundary_edge_case_storage_average_do_not_count_as_mention():
    """A lexicon entry "RAG" with empty seen_in[] IS an orphan when the
    only cards around merely contain "storage"/"average" (which embed the
    literal substring "rag" but not the standalone word) -- the same
    word-boundary discipline applies on the orphan side, not just the
    coverage-gap side."""
    lexicon = [_lexicon_entry("RAG", seen_in=[])]
    cards = [
        _card(
            "card-b",
            headline="Cloud storage costs keep rising",
            what_happened="Average storage spend increased across providers.",
            why_it_matters="Storage budgets are a growing line item for AI labs.",
            one_liner="Storage keeps getting pricier.",
            lexicon_terms=[],
        ),
    ]
    assert find_orphans(lexicon, cards) == ["RAG"]


def test_find_orphans_with_no_cards_at_all_relies_only_on_seen_in():
    lexicon = [
        _lexicon_entry("hallucination", seen_in=[]),
        _lexicon_entry("scaling laws", seen_in=["2026-01-01-some-older-card"]),
    ]
    assert find_orphans(lexicon, []) == ["hallucination"]


# ---------------------------------------------------------------------------
# audit_lexicon (combined wrapper)
# ---------------------------------------------------------------------------


def test_audit_lexicon_combines_both_checks():
    lexicon = [
        _lexicon_entry("RAG", seen_in=[]),
        _lexicon_entry("fine-tuning", seen_in=["card-a"]),
        _lexicon_entry("hallucination", seen_in=[]),
    ]
    card_a = _card(
        "card-a",
        headline="Lab ships new agent framework",
        what_happened="The lab's fine-tuning process now incorporates rag.",
        why_it_matters="Matters.",
        one_liner="One liner.",
        lexicon_terms=["fine-tuning"],  # RAG used but not listed -> gap
    )
    card_b = _card(
        "card-b",
        headline="Cloud storage costs keep rising",
        what_happened="Average storage spend increased across providers.",
        why_it_matters="Storage budgets are a growing line item.",
        one_liner="Storage keeps getting pricier.",
        lexicon_terms=[],
    )
    result = audit_lexicon([card_a, card_b], lexicon)
    assert result == {
        "coverage_gaps": [{"card_id": "card-a", "missing_terms": ["RAG"]}],
        "orphans": ["hallucination"],
    }


def test_audit_lexicon_clean_state_reports_nothing():
    lexicon = [_lexicon_entry("fine-tuning", seen_in=["card-a"])]
    card_a = _card(
        "card-a",
        headline="Lab ships new agent framework",
        what_happened="The lab's fine-tuning process is the whole story.",
        why_it_matters="Matters.",
        one_liner="One liner.",
        lexicon_terms=["fine-tuning"],
    )
    result = audit_lexicon([card_a], lexicon)
    assert result == {"coverage_gaps": [], "orphans": []}


# ---------------------------------------------------------------------------
# Real content/lexicon.json smoke test
# ---------------------------------------------------------------------------


def test_real_lexicon_json_loads_and_scans_without_raising():
    """`content/lexicon.json` (the real, seeded 30-entry file) at least
    loads and runs through every function in this module without
    raising, against a couple of synthetic cards -- a defensive
    integration check distinct from the fixture-only unit tests above,
    which are what actually exercise the coverage-gap/orphan logic in
    detail per this turn's explicit fixture-based scope (`content/cards/`
    itself is empty; there are no real cards to check against yet)."""
    lexicon_path = REPO_ROOT / "content" / "lexicon.json"
    lexicon_entries = json.loads(lexicon_path.read_text())
    assert len(lexicon_entries) == 30

    synthetic_cards = [
        _card(
            "smoke-card-1",
            headline="A synthetic headline mentioning RAG and fine-tuning",
            what_happened="Describes RAG and fine-tuning in the same sentence.",
            why_it_matters="Exercises the real lexicon without any real cards existing yet.",
            one_liner="A smoke test, not a real story.",
            lexicon_terms=["RAG"],
        ),
    ]

    result = audit_lexicon(synthetic_cards, lexicon_entries)
    assert isinstance(result["coverage_gaps"], list)
    assert isinstance(result["orphans"], list)
    # Every real entry has an empty seen_in[] today (no analyst run has
    # ever happened), so every term not mentioned in the one synthetic
    # card above is expected to be reported as an orphan -- sanity-check
    # that at least the vast majority of the 30 terms show up (only
    # "RAG" and "fine-tuning" are mentioned in the synthetic prose).
    assert len(result["orphans"]) >= 27
