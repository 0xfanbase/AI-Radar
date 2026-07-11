"""Duplicate-topic detection (`audit.yml`'s weekly, pure-code, no-LLM pass).

Per CLAUDE.md's "daily self-learning loop" / `audit.yml` description and
the approved build plan's section 6 (Phase 5): "duplicate-topic detection
(pairwise Jaccard on published titles/topics)". This module implements
exactly that check.

**Reuse, not reimplementation, per this turn's own explicit instruction:**
the actual similarity computation is `watcher.clustering._jaccard` --
imported and called directly, not reimplemented here -- applied to the
same `watcher.models.tokenize_title` token sets `watcher/clustering.py`
itself builds from a title/headline. This is the identical mechanism
Phase 1's own clustering pass already uses to decide whether two *source
items* are "the same underlying story" before a card is ever written;
this module asks the same question a second time, after the fact, across
*published cards'* own headlines -- catching a case where two clusters
that should have merged (or a genuine second cluster on the same story
that arrived on a later day) both slipped through as separate cards.

**Threshold: `watcher.config.JACCARD_SIMILARITY_THRESHOLD` (0.35), the
general bar -- not `LAB_LAB_JACCARD_SIMILARITY_THRESHOLD` (0.65).**
Spec-silent choice (the task names 0.35 as the default, with a documented
alternative allowed); logged in full in `IMPROVEMENT_BACKLOG.md`. Short
version: the 0.65 lab-lab bar exists specifically because raw lab RSS
titles are short, heavily templated marketing copy ("Introducing
GPT-5.4", "Introducing GPT-5.5") where a couple of shared boilerplate
tokens alone can clear 0.35 -- see `watcher/clustering.py`'s own
`_merge_threshold` docstring. Published card *headlines* are the
analyst's own original prose, not raw lab boilerplate, so that specific
false-positive risk doesn't transfer here; the general, cross-source
0.35 bar (the same one clustering.py applies to every non-lab-lab
comparison, including the analyst's own eventual card-vs-card use case if
it ever existed) is the more appropriate constant to reuse.

**The follow-up-link exemption.** CLAUDE.md's corroboration procedure,
step 6 ("Follow-up-story linking convention"), has the analyst write a
fixed, greppable sentence into a follow-up card's own `what_happened` (or
`why_it_matters`) whenever it's a genuine continuation of an earlier
story: literally `(follow-up to "<prior headline>", card <prior_id>)`.
A pair of published cards whose headlines are similar *because* one is
an acknowledged, cited follow-up of the other is not a true duplicate --
it's the corroboration procedure working as designed -- so any pair
carrying that pattern (checked against the specific earlier card's own
id, not just any follow-up sentence at all) is exempted from being
flagged. See :func:`is_acknowledged_followup` for the exact matching
rule and the reasoning for anchoring on `prior_id` rather than requiring
an exact literal match of the quoted prior headline text too.

This module is pure, filesystem-free logic for its core functions (same
convention `auditor/lexicon_audit.py` already established): every
function takes already-loaded Python lists/dicts and returns plain data
structures, so it's trivially unit-testable against small fixture cards.
:func:`audit_duplicates` is the one exception -- like
`auditor.linkrot.audit_link_rot`, it accepts `cards=None` and, in that
case only, loads real published cards from disk via
`auditor.linkrot.load_cards` (reused directly rather than a third,
independently-written "walk content/cards/, skip index.json" loader).
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence

from auditor.linkrot import load_cards
from watcher.clustering import _jaccard
from watcher.config import JACCARD_SIMILARITY_THRESHOLD
from watcher.models import tokenize_title

# Reused directly from watcher/config.py, per this turn's own instruction
# and the module docstring above -- not a second, independently-tuned
# constant. Exposed under this module's own name so a caller/test doesn't
# need to know it happens to live in `watcher.config` today.
DUPLICATE_JACCARD_THRESHOLD = JACCARD_SIMILARITY_THRESHOLD

# The card.schema.json prose fields CLAUDE.md's follow-up-linking
# convention (corroboration procedure step 6) says the fixed sentence may
# be appended to -- "as one sentence appended to `what_happened` (or
# `why_it_matters` if it reads more naturally there)". Both are scanned;
# `headline` and `one_liner` are deliberately not, since the convention
# never names them as the sentence's home.
FOLLOWUP_SCAN_FIELDS: tuple[str, ...] = ("what_happened", "why_it_matters")


def _followup_pattern(prior_id: str) -> "re.Pattern[str]":
    """Compile the greppable-pattern regex anchored on one specific prior
    card's id.

    Matches CLAUDE.md's fixed form ``(follow-up to "<prior headline>",
    card <prior_id>)`` with the quoted headline text treated as a
    wildcard (``[^"]+``) and only ``prior_id`` held literal
    (``re.escape``d). Anchoring on the id rather than also requiring an
    exact literal match of the quoted prior-headline substring is a
    deliberate, logged judgment call: `prior_id` is the single
    unambiguous identifier the convention itself exists to make
    greppable (CLAUDE.md's own note that `content/cards/index.json`
    already gives the `id -> date/headline` lookup a future site
    enhancement would need to promote this into a real hyperlink), while
    requiring the quoted headline text to match byte-for-byte would make
    the exemption fragile to trivial, meaning-preserving textual drift
    (a stray extra space, a smart vs. straight quote) despite the
    sentence unambiguously naming the correct prior card either way.
    """
    return re.compile(r'\(follow-up to "[^"]+", card ' + re.escape(prior_id) + r"\)")


def _followup_scan_text(card: Mapping[str, object]) -> str:
    """Concatenate `card`'s own follow-up-eligible prose fields (see
    `FOLLOWUP_SCAN_FIELDS`) into one string to grep. Missing/`None`
    fields contribute nothing rather than raising -- a fixture (or a
    real card missing an optional-in-practice field) is handled
    gracefully, matching `auditor/lexicon_audit.py::_card_prose_text`'s
    own convention.
    """
    parts = []
    for field_name in FOLLOWUP_SCAN_FIELDS:
        value = card.get(field_name)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def is_acknowledged_followup(later_card: Mapping[str, object], prior_id: str) -> bool:
    """Does `later_card`'s own prose already carry CLAUDE.md's fixed
    follow-up-link sentence naming `prior_id` specifically?

    True means this pair is an acknowledged continuation of the same
    story, not a true duplicate -- :func:`find_duplicate_pairs` exempts
    it from being flagged. See :func:`_followup_pattern` for the exact
    matching rule.
    """
    return bool(_followup_pattern(prior_id).search(_followup_scan_text(later_card)))


def title_similarity(card_a: Mapping[str, object], card_b: Mapping[str, object]) -> float:
    """Jaccard similarity of two cards' own `headline` fields.

    Tokenizes each headline with `watcher.models.tokenize_title` (the
    exact function `watcher/clustering.py` itself uses on a title) and
    hands the two resulting token sets straight to
    `watcher.clustering._jaccard` -- reused directly, per this turn's own
    instruction, not reimplemented. A missing/empty `headline` tokenizes
    to an empty set, which `_jaccard` itself already defines as 0.0
    similarity against anything (see its own docstring), so this never
    raises on a sparse fixture.
    """
    tokens_a = tokenize_title(str(card_a.get("headline") or ""))
    tokens_b = tokenize_title(str(card_b.get("headline") or ""))
    return _jaccard(tokens_a, tokens_b)


def shared_topics(card_a: Mapping[str, object], card_b: Mapping[str, object]) -> list[str]:
    """The sorted intersection of two cards' own `topics[]` arrays.

    Informational context attached to a flagged pair's finding, not a
    second independent threshold gate -- see the module docstring for
    why a card's `topics[]` (a small, closed 9-value enum per
    `card.schema.json`, not free prose) isn't run through the same
    word-tokenization Jaccard machinery `title_similarity` uses on free
    headline text. Missing/absent `topics` on either card contributes an
    empty set, never a `KeyError`.
    """
    topics_a = set(card_a.get("topics") or [])
    topics_b = set(card_b.get("topics") or [])
    return sorted(topics_a & topics_b)


def _sort_key(card: Mapping[str, object]) -> tuple[str, str]:
    """`(date, id)` -- the deterministic order `find_duplicate_pairs` sorts
    every card into before comparing pairs, so which card in a flagged
    pair counts as "earlier" (the one a follow-up sentence would name as
    `prior_id`) vs. "later" (the one whose own prose is scanned for that
    sentence) never depends on the order `cards` happened to be passed
    in -- the same determinism discipline `watcher/clustering.py` already
    applies to its own item-sort step, for the same reason (a same-input
    re-run must always produce the same result). `id` is the tie-break
    for same-day cards, matching every other id-bearing sort in this
    repo (e.g. `scripts/plan_run.py`'s own tie-break conventions).
    """
    return (str(card.get("date") or ""), str(card.get("id") or ""))


def find_duplicate_pairs(
    cards: Sequence[Mapping[str, object]],
    *,
    threshold: float = DUPLICATE_JACCARD_THRESHOLD,
) -> list[dict]:
    """Pairwise-compare every published card's headline against every
    other's, flagging every pair whose :func:`title_similarity` clears
    `threshold` (default `DUPLICATE_JACCARD_THRESHOLD`, i.e. `>=`, matching
    `watcher/clustering.py`'s own `>=` comparison) -- unless the later
    card's own prose already carries an acknowledged follow-up-link
    sentence naming the earlier card (see :func:`is_acknowledged_followup`),
    in which case the pair is skipped entirely, not merely flagged with a
    caveat: an acknowledged follow-up is the corroboration procedure
    working as designed, not a finding.

    `cards` is sorted into a fixed `(date, id)` order first (see
    :func:`_sort_key`) so the "earlier"/"later" role in each compared
    pair -- and therefore which card's id the follow-up-link check looks
    for -- never depends on the order `cards` was passed in. Every
    `i < j` pair (over the sorted list) is compared exactly once; a card
    is never compared against itself.

    Returns one finding dict per flagged pair, in the same deterministic
    `(i, j)` scan order: `{"card_a": <earlier id>, "card_b": <later id>,
    "headline_a": ..., "headline_b": ..., "similarity": <float>,
    "shared_topics": [...]}`. An empty return means a clean run (no
    above-threshold, unacknowledged pair found), not "nothing was
    checked".
    """
    ordered = sorted(cards, key=_sort_key)
    findings: list[dict] = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            earlier, later = ordered[i], ordered[j]
            similarity = title_similarity(earlier, later)
            if similarity < threshold:
                continue
            earlier_id = str(earlier.get("id") or "")
            if is_acknowledged_followup(later, earlier_id):
                continue
            findings.append(
                {
                    "card_a": earlier_id,
                    "card_b": str(later.get("id") or ""),
                    "headline_a": earlier.get("headline"),
                    "headline_b": later.get("headline"),
                    "similarity": similarity,
                    "shared_topics": shared_topics(earlier, later),
                }
            )
    return findings


def audit_duplicates(
    cards: list[dict] | None = None,
    *,
    threshold: float = DUPLICATE_JACCARD_THRESHOLD,
) -> dict:
    """Run the full duplicate-topic check and return a summary dict.

    `cards` lets a caller (or a test) pass an explicit list of card
    dicts directly, for testability without needing real files on disk --
    per this turn's own instruction, since `content/cards/` is empty in
    this repo today. When `cards` is omitted (`None`), published cards
    are loaded from disk via `auditor.linkrot.load_cards` (reused
    directly, not reimplemented -- see module docstring).

    Returns `{"duplicate_pairs": [...]}` -- `find_duplicate_pairs`'s own
    return value under one key, matching the same "provisional shape for
    a future `schemas/audit.schema.json`, not yet a locked contract"
    convention `auditor.linkrot.audit_link_rot` and
    `auditor.lexicon_audit.audit_lexicon` already establish for their own
    summary dicts.
    """
    if cards is None:
        cards = load_cards()
    return {"duplicate_pairs": find_duplicate_pairs(cards, threshold=threshold)}
