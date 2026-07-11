"""Tests for auditor/duplicates.py -- the weekly duplicate-topic check (see
CLAUDE.md's "audit.yml -- weekly" bullet / the approved plan's Phase 5
section: "duplicate-topic detection (pairwise Jaccard on published titles/
topics)").

`content/cards/` is currently empty (no analyst run has happened for real
yet), so -- per this turn's explicit instruction -- these tests exercise
the pure `title_similarity` / `shared_topics` / `is_acknowledged_followup`
/ `find_duplicate_pairs` / `audit_duplicates` functions directly against
small fixture cards at varying title similarity, rather than the (empty)
real `content/cards/*.json`. One integration-flavored smoke test at the
bottom confirms `audit_duplicates()` still runs cleanly against the real,
currently-empty `content/cards/` directory via its own default
`auditor.linkrot.load_cards` loading path.

Imported the same way `tests/test_auditor_linkrot.py` already imports
`auditor/linkrot.py` -- `auditor/` has no `__init__.py` (an implicit
namespace package is enough), so `from auditor import duplicates` /
`from auditor.duplicates import ...` resolve directly with no
`sys.path` manipulation, given `python -m pytest` is run from the repo
root (this repo's own established convention).
"""
from __future__ import annotations

import watcher.clustering as clustering_mod
import watcher.config as config_mod
import watcher.models as models_mod
from auditor import duplicates as mod
from auditor.duplicates import (
    DUPLICATE_JACCARD_THRESHOLD,
    audit_duplicates,
    find_duplicate_pairs,
    is_acknowledged_followup,
    shared_topics,
    title_similarity,
)


def _card(card_id: str, date: str, headline: str, **rest) -> dict:
    """Build a minimal card fixture. Only the fields a given test actually
    needs are populated (plus `id`/`date`/`headline`) -- this module's own
    functions only ever read `id`, `date`, `headline`, `topics`,
    `what_happened`, and `why_it_matters`, so a fixture never needs every
    `card.schema.json`-required field to exercise it.
    """
    card = {"id": card_id, "date": date, "headline": headline}
    card.update(rest)
    return card


# ---------------------------------------------------------------------------
# Reuse, not reimplementation -- the core instruction this turn's task gave.
# ---------------------------------------------------------------------------


def test_duplicates_reuses_watcher_clustering_jaccard_by_identity():
    """`duplicates._jaccard` must be the *exact same function object* as
    `watcher.clustering._jaccard` -- proof this module imports and calls
    the existing implementation directly rather than reimplementing its
    own copy, per this turn's own explicit instruction."""
    assert mod._jaccard is clustering_mod._jaccard


def test_duplicates_reuses_watcher_models_tokenize_title_by_identity():
    """Same identity proof for the tokenizer clustering.py itself uses."""
    assert mod.tokenize_title is models_mod.tokenize_title


def test_duplicate_threshold_constant_is_the_real_config_value_not_a_new_one():
    """`DUPLICATE_JACCARD_THRESHOLD` must be `watcher.config`'s own general
    `JACCARD_SIMILARITY_THRESHOLD` (0.35) -- the same constant
    `watcher/clustering.py` applies to every non-lab-lab comparison --
    reused, not a second, independently-chosen value."""
    assert DUPLICATE_JACCARD_THRESHOLD == config_mod.JACCARD_SIMILARITY_THRESHOLD == 0.35


def test_title_similarity_matches_manual_jaccard_of_tokenized_headlines():
    """`title_similarity` must equal calling `_jaccard(tokenize_title(a),
    tokenize_title(b))` directly -- the exact composition the module
    docstring claims, checked here against real computed values rather
    than merely trusted."""
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    card_b = _card("b", "2026-07-02", "OpenAI ships GPT-5.5 with major upgrades")
    expected = clustering_mod._jaccard(
        models_mod.tokenize_title(card_a["headline"]),
        models_mod.tokenize_title(card_b["headline"]),
    )
    assert title_similarity(card_a, card_b) == expected
    assert expected > 0.35  # sanity: this fixture pair is a genuine near-duplicate


def test_title_similarity_is_zero_for_unrelated_headlines():
    card_a = _card("a", "2026-07-01", "DeepSeek launches new open-weights model")
    card_b = _card("b", "2026-07-02", "Cloud storage costs keep rising this quarter")
    assert title_similarity(card_a, card_b) == 0.0


def test_title_similarity_handles_missing_headline_gracefully():
    card_a = _card("a", "2026-07-01", "")
    card_b = {"id": "b", "date": "2026-07-02"}  # no headline key at all
    assert title_similarity(card_a, card_b) == 0.0


# ---------------------------------------------------------------------------
# shared_topics
# ---------------------------------------------------------------------------


def test_shared_topics_returns_sorted_intersection():
    card_a = _card("a", "2026-07-01", "x", topics=["models", "China", "policy"])
    card_b = _card("b", "2026-07-02", "y", topics=["policy", "models", "funding"])
    assert shared_topics(card_a, card_b) == ["models", "policy"]


def test_shared_topics_handles_missing_topics_gracefully():
    card_a = _card("a", "2026-07-01", "x")
    card_b = _card("b", "2026-07-02", "y", topics=["models"])
    assert shared_topics(card_a, card_b) == []


# ---------------------------------------------------------------------------
# is_acknowledged_followup
# ---------------------------------------------------------------------------


def test_followup_sentence_in_what_happened_is_recognized():
    later = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        what_happened=(
            'Anthropic pushed a follow-on update. (follow-up to "Anthropic '
            'ships Claude Fable 5 today", card earlier-card)'
        ),
    )
    assert is_acknowledged_followup(later, "earlier-card") is True


def test_followup_sentence_in_why_it_matters_is_also_recognized():
    """CLAUDE.md's own convention allows the sentence in `why_it_matters`
    'if it reads more naturally there' -- both fields must be scanned."""
    later = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        why_it_matters=(
            'This matters because it extends the prior release. (follow-up '
            'to "Anthropic ships Claude Fable 5 today", card earlier-card)'
        ),
    )
    assert is_acknowledged_followup(later, "earlier-card") is True


def test_followup_sentence_naming_a_different_id_does_not_exempt():
    """The pattern must name *this specific* earlier card's id -- a
    follow-up sentence pointing at some other, unrelated prior card does
    not exempt this pair."""
    later = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        what_happened=(
            'Unrelated. (follow-up to "Some other story", card '
            'some-other-card-id)'
        ),
    )
    assert is_acknowledged_followup(later, "earlier-card") is False


def test_no_followup_sentence_at_all_is_not_exempt():
    later = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        what_happened="Just an ordinary update with nothing special about it.",
    )
    assert is_acknowledged_followup(later, "earlier-card") is False


def test_is_acknowledged_followup_handles_missing_prose_fields_gracefully():
    later = {"id": "later-card", "date": "2026-07-05", "headline": "x"}
    assert is_acknowledged_followup(later, "earlier-card") is False


# ---------------------------------------------------------------------------
# find_duplicate_pairs
# ---------------------------------------------------------------------------


def test_above_threshold_pair_with_no_followup_link_is_flagged():
    card_a = _card(
        "2026-07-01-openai-gpt-5-5",
        "2026-07-01",
        "OpenAI releases GPT-5.5 with major upgrades",
        topics=["models"],
        what_happened="OpenAI shipped GPT-5.5 today with broad upgrades.",
    )
    card_b = _card(
        "2026-07-03-openai-gpt-5-5-again",
        "2026-07-03",
        "OpenAI ships GPT-5.5 with major upgrades",
        topics=["models", "products"],
        what_happened="A second, unrelated write-up covering the same launch.",
    )
    findings = find_duplicate_pairs([card_a, card_b])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["card_a"] == "2026-07-01-openai-gpt-5-5"
    assert finding["card_b"] == "2026-07-03-openai-gpt-5-5-again"
    assert finding["headline_a"] == "OpenAI releases GPT-5.5 with major upgrades"
    assert finding["headline_b"] == "OpenAI ships GPT-5.5 with major upgrades"
    assert finding["similarity"] == title_similarity(card_a, card_b)
    assert finding["shared_topics"] == ["models"]


def test_below_threshold_pair_is_not_flagged():
    card_a = _card("a", "2026-07-01", "DeepSeek launches new open-weights model")
    card_b = _card("b", "2026-07-02", "Cloud storage costs keep rising this quarter")
    assert find_duplicate_pairs([card_a, card_b]) == []


def test_threshold_boundary_is_inclusive_ge_not_strict_gt():
    """Two headlines with a real, exactly-representable Jaccard of 0.5
    (3 shared tokens / 6 union tokens -- both exact in binary floating
    point, so this boundary check is never at the mercy of float rounding)
    prove the `>=` comparison `watcher/clustering.py` itself uses is
    matched here: flagged when threshold == similarity, not flagged when
    threshold is set just above it."""
    card_a = _card("a", "2026-07-01", "Anthropic ships Claude Fable 5 today")
    card_b = _card("b", "2026-07-02", "Anthropic launches Claude Fable 5 update")
    similarity = title_similarity(card_a, card_b)
    assert similarity == 0.5

    flagged_at_boundary = find_duplicate_pairs([card_a, card_b], threshold=0.5)
    assert len(flagged_at_boundary) == 1

    not_flagged_just_above = find_duplicate_pairs([card_a, card_b], threshold=0.51)
    assert not_flagged_just_above == []


def test_acknowledged_followup_pair_is_exempted_not_flagged():
    card_a = _card(
        "earlier-card",
        "2026-07-01",
        "Anthropic ships Claude Fable 5 today",
    )
    card_b = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        what_happened=(
            'Anthropic shipped a follow-on update to the same model. '
            '(follow-up to "Anthropic ships Claude Fable 5 today", card '
            'earlier-card)'
        ),
    )
    assert title_similarity(card_a, card_b) >= DUPLICATE_JACCARD_THRESHOLD
    assert find_duplicate_pairs([card_a, card_b]) == []


def test_similar_pair_without_the_specific_followup_link_is_still_flagged():
    """Similar headlines plus *some* follow-up-shaped sentence that names
    the wrong id must still be flagged -- the exemption is specific, not a
    blanket "any follow-up text present" pass."""
    card_a = _card("earlier-card", "2026-07-01", "Anthropic ships Claude Fable 5 today")
    card_b = _card(
        "later-card",
        "2026-07-05",
        "Anthropic launches Claude Fable 5 update",
        what_happened=(
            'Unrelated. (follow-up to "Some other story", card wrong-id)'
        ),
    )
    findings = find_duplicate_pairs([card_a, card_b])
    assert len(findings) == 1
    assert findings[0]["card_a"] == "earlier-card"
    assert findings[0]["card_b"] == "later-card"


def test_deterministic_regardless_of_input_order():
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    card_b = _card("b", "2026-07-03", "OpenAI ships GPT-5.5 with major upgrades")
    forward = find_duplicate_pairs([card_a, card_b])
    reversed_input = find_duplicate_pairs([card_b, card_a])
    assert forward == reversed_input
    assert forward[0]["card_a"] == "a"  # the earlier-dated card, regardless of input order
    assert forward[0]["card_b"] == "b"


def test_empty_card_list_returns_no_findings():
    assert find_duplicate_pairs([]) == []


def test_single_card_returns_no_findings():
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    assert find_duplicate_pairs([card_a]) == []


def test_multiple_cards_reports_only_the_flagged_pairs():
    """Three cards: (a, b) are a near-duplicate pair, (a, c) and (b, c)
    are both unrelated to "c" -- only the one true duplicate pair should
    be reported, and no card is ever compared against itself."""
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    card_b = _card("b", "2026-07-02", "OpenAI ships GPT-5.5 with major upgrades")
    card_c = _card("c", "2026-07-03", "Cloud storage costs keep rising this quarter")
    findings = find_duplicate_pairs([card_a, card_b, card_c])
    assert len(findings) == 1
    assert (findings[0]["card_a"], findings[0]["card_b"]) == ("a", "b")


# ---------------------------------------------------------------------------
# audit_duplicates
# ---------------------------------------------------------------------------


def test_audit_duplicates_with_explicit_cards_wraps_find_duplicate_pairs():
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    card_b = _card("b", "2026-07-02", "OpenAI ships GPT-5.5 with major upgrades")
    result = audit_duplicates([card_a, card_b])
    assert result == {"duplicate_pairs": find_duplicate_pairs([card_a, card_b])}
    assert len(result["duplicate_pairs"]) == 1


def test_audit_duplicates_clean_state_reports_empty_list():
    card_a = _card("a", "2026-07-01", "DeepSeek launches new open-weights model")
    card_b = _card("b", "2026-07-02", "Cloud storage costs keep rising this quarter")
    assert audit_duplicates([card_a, card_b]) == {"duplicate_pairs": []}


def test_audit_duplicates_defaults_to_loading_via_auditor_linkrot_load_cards(monkeypatch):
    """`cards=None` must load through `auditor.linkrot.load_cards` --
    reused directly, per the module docstring -- not some independent
    disk-walking logic. Proven by monkeypatching the exact bound name
    `duplicates.load_cards` and confirming it's what actually supplies
    the cards."""
    card_a = _card("a", "2026-07-01", "OpenAI releases GPT-5.5 with major upgrades")
    card_b = _card("b", "2026-07-02", "OpenAI ships GPT-5.5 with major upgrades")
    monkeypatch.setattr(mod, "load_cards", lambda: [card_a, card_b])
    result = audit_duplicates()
    assert len(result["duplicate_pairs"]) == 1


def test_audit_duplicates_against_the_real_currently_empty_content_cards_dir():
    """Integration-flavored smoke test: `content/cards/` really is empty
    in this repo today (no analyst run has happened for real yet), so
    the real default `auditor.linkrot.load_cards()` path must return `[]`
    and `audit_duplicates()` must complete cleanly with no findings,
    rather than raising."""
    result = audit_duplicates()
    assert result == {"duplicate_pairs": []}
