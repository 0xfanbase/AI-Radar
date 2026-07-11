"""Lexicon coverage/orphan checker (Phase 5, `audit.yml`'s weekly pure-code
pass -- see CLAUDE.md's "audit.yml -- weekly" bullet: "lexicon orphan/
coverage check (terms used vs defined)").

This module does two related but distinct word-boundary, case-insensitive
scans over a set of cards' prose against `content/lexicon.json`'s real 30
entries:

1. **Coverage gap** (:func:`find_coverage_gaps` / :func:`audit_coverage`):
   a card's prose (`headline`, `what_happened`, `why_it_matters`,
   `one_liner` -- `card.schema.json`'s own prose fields) uses a real
   lexicon term that the card's own `lexicon_terms[]` doesn't list. This
   is exactly the failure mode CLAUDE.md's lexicon auto-growth rule
   (spec step 7) is supposed to prevent every analyst run -- catching a
   miss here is a finding for `IMPROVEMENT_BACKLOG.md`, not a schema
   violation (the card is still schema-valid either way).
2. **Orphan** (:func:`find_orphans`): a lexicon entry whose `seen_in[]`
   is empty AND whose term is never referenced in any card's prose
   either. A term with a non-empty `seen_in[]` is never an orphan, even
   if the particular `cards` list passed to a given audit run happens
   not to include the referencing card(s) -- `seen_in[]` is the
   analyst's own auto-grown historical record (CLAUDE.md step 7), which
   may span more cards than whatever subset a caller passes in here.

Both checks share one word-boundary-matching primitive (`_term_pattern`)
matching the same technique already established elsewhere in this repo
(`watcher/sources/hn.py`'s `HN_KEYWORDS` whole-word matching,
`site/lib/linkify.py`'s own term-matching regex) specifically so a short
term like "RAG" matches only as a standalone word, never as a false-positive
substring inside an unrelated longer word such as "storage" or "average"
(both literally contain the three characters "r", "a", "g" in sequence).

This module is pure, filesystem-free, dependency-free logic: every
function takes already-loaded Python lists/dicts (a card is whatever
`card.schema.json` describes, loaded as a plain dict; a lexicon entry is
whatever `lexicon.schema.json` describes) and returns plain data
structures. Callers (the not-yet-built `auditor/cli.py` /
`scripts/append_backlog_findings.py`) are responsible for the actual
`content/lexicon.json` / `content/cards/*.json` file I/O -- kept out of
this module so it stays trivially unit-testable against small fixture
lists, per this turn's explicit instruction (`content/cards/` is
currently empty; no real cards exist to load yet).
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

# The card.schema.json prose fields a newcomer would actually read --
# deliberately excludes `topics[]` (a closed enum, not free prose),
# `citations[].quote` (verbatim source text, not the analyst's own
# words, so a lexicon term appearing only inside a quote was never
# "used" by the card's own prose in the sense the auto-growth rule
# means), and `correction_note` (a short pointer string, not the
# card's substantive body).
CARD_PROSE_FIELDS: tuple[str, ...] = (
    "headline",
    "what_happened",
    "why_it_matters",
    "one_liner",
)


def _term_pattern(term: str) -> "re.Pattern[str]":
    """Compile a case-insensitive, word-boundary regex for one lexicon
    term.

    `\\b` boundaries are what make this safe against false positives on
    short terms: "RAG" (`\\bRAG\\b`, case-insensitive) matches the
    standalone word "RAG"/"rag" but not the "rag" substring embedded in
    "storage" or "average", since neither substring occurrence sits at a
    word boundary on both sides.
    """
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def _card_prose_text(card: Mapping[str, object]) -> str:
    """Concatenate a card's prose fields (see `CARD_PROSE_FIELDS`) into
    one string to scan. Missing/`None` fields contribute nothing rather
    than raising or literally inserting the string "None" -- a caller may
    reasonably pass a partial fixture dict that omits some prose fields.
    """
    parts = []
    for field_name in CARD_PROSE_FIELDS:
        value = card.get(field_name)
        if value:
            parts.append(str(value))
    return " ".join(parts)


def find_coverage_gaps(
    card: Mapping[str, object], lexicon_terms: Iterable[str]
) -> list[str]:
    """Return every term from `lexicon_terms` that is used (word-boundary,
    case-insensitive) somewhere in `card`'s prose fields but is missing
    from the card's own `lexicon_terms[]` list -- a coverage gap.

    The "already listed" check is itself case-insensitive: a card listing
    "rag" in its own `lexicon_terms[]` is treated as already covering the
    canonical lexicon term "RAG", since `lexicon_terms[]` is meant to name
    a real lexicon entry, not necessarily reproduce its exact casing.

    Terms are returned in the same order as `lexicon_terms` (typically
    `content/lexicon.json`'s own entry order), not the order they occur
    in the prose.
    """
    listed_lower = {str(t).lower() for t in (card.get("lexicon_terms") or [])}
    text = _card_prose_text(card)
    gaps: list[str] = []
    for term in lexicon_terms:
        if term.lower() in listed_lower:
            continue
        if _term_pattern(term).search(text):
            gaps.append(term)
    return gaps


def audit_coverage(
    cards: Iterable[Mapping[str, object]],
    lexicon_entries: Iterable[Mapping[str, object]],
) -> list[dict]:
    """Run :func:`find_coverage_gaps` across every card in `cards` against
    every term in `lexicon_entries`.

    Returns one finding dict per card that has at least one gap,
    `{"card_id": <card's id>, "missing_terms": [...]}`; cards with no gaps
    are omitted entirely (an empty return means a clean run, not "no
    cards were checked").
    """
    terms = [entry["term"] for entry in lexicon_entries]
    findings: list[dict] = []
    for card in cards:
        gaps = find_coverage_gaps(card, terms)
        if gaps:
            findings.append({"card_id": card.get("id"), "missing_terms": gaps})
    return findings


def find_orphans(
    lexicon_entries: Iterable[Mapping[str, object]],
    cards: Iterable[Mapping[str, object]],
) -> list[str]:
    """Return the `term` of every lexicon entry that is an orphan: its
    `seen_in[]` is empty AND its term is never referenced (word-boundary,
    case-insensitive) in any card's prose fields.

    An entry with any `seen_in[]` entries at all is never considered an
    orphan by this check, regardless of whether `cards` happens to
    include the referencing card -- `seen_in[]` already is the auto-grown
    record of every card that used the term (CLAUDE.md step 7), so it can
    correctly attest coverage the passed-in `cards` subset doesn't itself
    demonstrate.
    """
    texts = [_card_prose_text(card) for card in cards]
    orphans: list[str] = []
    for entry in lexicon_entries:
        if entry.get("seen_in"):
            continue
        pattern = _term_pattern(entry["term"])
        if any(pattern.search(text) for text in texts):
            continue
        orphans.append(entry["term"])
    return orphans


def audit_lexicon(
    cards: Sequence[Mapping[str, object]],
    lexicon_entries: Sequence[Mapping[str, object]],
) -> dict:
    """Convenience wrapper combining both checks into the one shape a
    future `audit.yml` findings-writer most naturally consumes:
    `{"coverage_gaps": [...], "orphans": [...]}`, where `coverage_gaps` is
    :func:`audit_coverage`'s own return value and `orphans` is
    :func:`find_orphans`'s own return value.

    Does no filesystem I/O itself -- see module docstring.
    """
    return {
        "coverage_gaps": audit_coverage(cards, lexicon_entries),
        "orphans": find_orphans(lexicon_entries, cards),
    }
