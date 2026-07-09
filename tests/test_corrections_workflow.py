"""Integration test: a synthetic status="corrected" card and a matching
content/corrections.json entry validate together.

This is a confirmation test, not a new discovery: Phase 1 already fixed
schemas/card.schema.json's `status` enum as `confirmed|reported|corrected`
(see IMPROVEMENT_BACKLOG.md's card-schema entries), and
schemas/corrections.schema.json's minimal shape
`{id, card_id, original_claim, corrected_claim, reason, source_url,
corrected_at}` is a direct transcription of the approved build plan's
Phase 2 section -- both already independently schema-tested
(tests/test_schemas.py, tests/test_p2_schemas.py). What is not exercised
anywhere else is the two artifacts *together*, the way CLAUDE.md's
corrections workflow actually produces them: one card whose `status` is
`"corrected"` and carries a `correction_note` pointer, plus one
content/corrections.json entry whose `card_id` names that same card,
exactly as the analyst is instructed to write them in the same drain step
(analyze.yml's ANALYST prompt, "Step 0"). This suite builds that pair by
hand and checks: each half validates against its own schema, the two link
up correctly (`corrections[].card_id == card["id"]`), and the "corrected"
convention itself does not silently drift apart from
card.schema.json's status enum in the future (a targeted regression guard,
not a duplicate of the existing enum-value test already in
tests/test_schemas.py / tests/test_p2_schemas.py).
"""
from __future__ import annotations

from typing import Any

from watcher.schema_validate import validate

CARD_ID = "2026-07-08-example-benchmark-paper"
CORRECTION_ID = "2026-07-09-correction-1"


def _corrected_card(
    *,
    card_id: str = CARD_ID,
    correction_note: str | None = f"See correction {CORRECTION_ID}: benchmark figure corrected.",
) -> dict[str, Any]:
    """A minimal, fully card.schema.json-valid card whose status is
    "corrected" -- the same shape the analyst writes when it drains
    data/pending_corrections.json and confirms an error (CLAUDE.md's
    "Corrections workflow" section, step 2)."""
    return {
        "id": card_id,
        "date": "2026-07-08",
        "headline": "Example Lab Publishes Benchmark Paper",
        "what_happened": (
            "Example Lab published a paper reporting benchmark results for "
            "its latest model, later found to misstate one figure."
        ),
        "why_it_matters": "The corrected figure changes how the result compares to rivals.",
        "one_liner": "A benchmark claim in an earlier card was wrong and has been fixed.",
        "topics": ["research"],
        "status": "corrected",
        "citations": [
            {
                "url": "https://example-lab.test/papers/benchmark-paper",
                "outlet": "Example Lab",
                "quote": "our model reaches a new state of the art",
            }
        ],
        "lexicon_terms": ["benchmark"],
        "generated_at": "2026-07-08T07:15:00Z",
        "model": "claude-sonnet-4-5",
        "correction_note": correction_note,
    }


def _corrections_entry(
    *, card_id: str = CARD_ID, correction_id: str = CORRECTION_ID
) -> dict[str, Any]:
    """A minimal, fully corrections.schema.json-valid content/corrections.json
    entry targeting the same card -- the counterpart the analyst appends in
    the same drain step (CLAUDE.md's "Corrections workflow" section, step
    2: "append one entry to content/corrections.json ... and set the
    affected card's status: 'corrected' plus a short correction_note
    pointing at the correction")."""
    return {
        "id": correction_id,
        "card_id": card_id,
        "original_claim": "The model reaches 92% on the benchmark.",
        "corrected_claim": "The model reaches 82% on the benchmark, per the paper's own errata.",
        "reason": "The original figure was a transcription error later corrected by the authors.",
        "source_url": "https://example-lab.test/papers/benchmark-paper/errata",
        "corrected_at": "2026-07-09T08:00:00Z",
    }


# --------------------------------------------------------------------------
# each half validates independently against its own schema
# --------------------------------------------------------------------------


def test_corrected_card_validates_against_card_schema():
    validate(_corrected_card(), "card")


def test_corrections_entry_validates_against_corrections_schema():
    validate([_corrections_entry()], "corrections")


# --------------------------------------------------------------------------
# the two artifacts, together, form a consistent corrected-card record
# --------------------------------------------------------------------------


def test_corrected_card_and_matching_corrections_entry_validate_together():
    card = _corrected_card()
    corrections = [_corrections_entry()]

    validate(card, "card")
    validate(corrections, "corrections")

    # The link the analyst is responsible for maintaining: the corrections
    # entry names the same card_id the corrected card itself carries.
    assert corrections[0]["card_id"] == card["id"]
    # And the card's own correction_note names that same correction id --
    # informal (schema-unenforced) but the convention CLAUDE.md's
    # corrections workflow describes ("a short correction_note pointing at
    # the correction").
    assert corrections[0]["id"] in card["correction_note"]


def test_a_card_status_confirmed_or_reported_and_corrected_json_can_coexist():
    """content/corrections.json is a standing public log -- it is never
    emptied just because a particular card has since moved on (e.g. a
    second, unrelated correction was filed against a different card, or the
    original card's status was itself later revised again). This checks a
    corrections.json with entries for two different cards, only one of
    which is itself currently "corrected", still validates as a whole --
    the two schemas don't require the corrections log to be a 1:1 mirror
    of "corrected" cards on disk this moment."""
    corrected_card = _corrected_card()
    other_card = _corrected_card(card_id="2026-07-05-another-example")
    other_card["status"] = "confirmed"
    other_card["correction_note"] = None

    corrections = [
        _corrections_entry(),
        _corrections_entry(
            card_id="2026-07-05-another-example", correction_id="2026-07-06-correction-1"
        ),
    ]

    validate(corrected_card, "card")
    validate(other_card, "card")
    validate(corrections, "corrections")
    assert {c["card_id"] for c in corrections} == {corrected_card["id"], other_card["id"]}


# --------------------------------------------------------------------------
# regression guard: card.schema.json's status enum stays exactly
# confirmed|reported|corrected -- the convention this whole workflow leans
# on. Not a duplicate of tests/test_schemas.py's own enum coverage: this
# one is scoped specifically to the "corrected" workflow, so a future
# schema edit that silently drops/renames that value fails *this* test's
# own self-contained fixture, without needing to cross-reference the
# generic schema test suite.
# --------------------------------------------------------------------------


def test_card_schema_status_enum_still_includes_corrected():
    card = _corrected_card()
    assert card["status"] == "corrected"
    validate(card, "card")  # would raise if "corrected" were ever removed
