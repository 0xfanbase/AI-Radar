"""Lexicon auto-linking for card prose (Phase 4, build-plan section 5).

The build plan's own words for this module: "``linkify.py`` substitutes
the first occurrence of each ``lexicon_terms[]`` entry into an ``<a>``,
with a resilient fallback (a 'Terms:' chip row at card footer) if
substring matching fails -- this makes the P4 acceptance bar ('define any
linked term in one tap') hold unconditionally rather than depending on
prose-matching luck."

Two-step build usage:

1. Call :func:`build_slug_map` **once per build** against the loaded
   ``content/lexicon.json`` array, producing a ``{lowercased term: slug}``
   dict that every card reuses.
2. For each card, call :func:`linkify` with its ``lexicon_terms`` list and
   the raw prose field being rendered (``what_happened``,
   ``why_it_matters``, etc.). It HTML-escapes the prose itself, splices in
   real ``<a href="/lexicon/<slug>/">`` anchors for every term it can find
   verbatim (case-insensitively, word-boundary matched, first occurrence
   only), and returns a :class:`LinkifyResult` whose ``.html`` is
   pre-escaped and marked ``Markup``-safe (so Jinja2's autoescape does not
   double-escape the anchor tags this module already produced) and whose
   ``.unmatched_terms`` lists every term the caller still owes a mention
   -- via the card-footer "Terms:" chip row fallback -- because it either
   is not present in ``content/lexicon.json`` at all, or is not found
   verbatim in this particular prose string. Callers must not treat
   ``unmatched_terms`` as an error: an empty ``content/cards/`` directory
   today means this path is only exercised against synthetic fixtures
   (see ``site/tests/test_linkify.py``) until the real Wire builder runs;
   real analyst-written prose is expected to hit the fallback path
   occasionally and that is by design, not a bug.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from markupsafe import Markup

LEXICON_TERM_URL_TEMPLATE = "/lexicon/{slug}/"


def slugify(term: str) -> str:
    """Lowercase + hyphenate a lexicon term into its URL slug form, e.g.
    "foundation model" -> "foundation-model", "RLHF" -> "rlhf".

    This intentionally matches the transform
    ``tests/test_seed_content.py``'s own ``_slugify`` helper already uses
    to resolve ``content/primer.json``'s slugs against
    ``content/lexicon.json`` entries (``term.lower().replace(" ", "-")``)
    -- that test file's docstring flagged "site/lib's own slugifier
    (Phase 4, not yet built) should match this convention" as an open
    item; this closes it. See IMPROVEMENT_BACKLOG.md.
    """
    return term.lower().replace(" ", "-")


def build_slug_map(lexicon_entries: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Build the lowercased-term -> slug map from a loaded
    ``content/lexicon.json`` array. Intended to be called once per build
    and passed into every :func:`linkify` call, rather than recomputed
    per card.
    """
    slug_map: dict[str, str] = {}
    for entry in lexicon_entries:
        term = str(entry["term"])
        slug_map[term.lower()] = slugify(term)
    return slug_map


@dataclass
class LinkifyResult:
    """Result of :func:`linkify`.

    ``html`` is the fully HTML-escaped prose with ``<a>`` anchors spliced
    in for every matched term, wrapped in ``markupsafe.Markup`` so it
    renders unescaped in a Jinja2 autoescape-on template -- safe to mark
    because every character of the surrounding prose was run through
    ``html.escape`` before any anchor was inserted, and the only
    unescaped markup added afterward is the anchor tags this module
    itself constructs (no untrusted input reaches the tag construction
    unescaped).

    ``unmatched_terms`` lists, in the order given, every term from the
    input ``lexicon_terms`` that could not be linked -- either because it
    has no entry in the lexicon slug map, or because it (or all of its
    occurrences) could not be found verbatim in this prose string.
    Callers render these as a fallback "Terms:" chip row rather than
    dropping them silently.
    """

    html: Markup
    unmatched_terms: list[str] = field(default_factory=list)


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def linkify(
    prose: str,
    lexicon_terms: Iterable[str],
    slug_map: Mapping[str, str],
) -> LinkifyResult:
    """HTML-escape ``prose``, then splice a real ``<a>`` anchor around the
    first (non-overlapping) verbatim occurrence of each term in
    ``lexicon_terms``, case-insensitively, word-boundary matched.

    Matching is performed against the already-escaped prose so a term
    containing HTML-special characters is matched against its escaped
    form too (``html.escape`` is applied to the term for pattern
    construction as well) -- if that still doesn't find a verbatim match,
    the term is reported unmatched rather than raising, per this module's
    resilience requirement: an unmatched term is never a build failure or
    a silent drop.

    Terms are processed in the order given. If two terms' matches would
    overlap in the text (e.g. one term is a substring of another's
    matched span), the earlier-listed term claims the span and the later
    one keeps searching for its own next non-overlapping occurrence (or
    is reported unmatched if none exists) -- this avoids ever producing
    nested/overlapping anchor tags. Spec-silent judgment call, logged in
    IMPROVEMENT_BACKLOG.md.
    """
    escaped = html.escape(prose)
    claimed_spans: list[tuple[int, int]] = []
    claims: list[tuple[int, int, str]] = []
    unmatched: list[str] = []

    for term in lexicon_terms:
        slug = slug_map.get(term.lower())
        if slug is None:
            unmatched.append(term)
            continue

        pattern = re.compile(r"\b" + re.escape(html.escape(term)) + r"\b", re.IGNORECASE)
        found_span: tuple[int, int] | None = None
        for m in pattern.finditer(escaped):
            span = m.span()
            if not any(_spans_overlap(span, claimed) for claimed in claimed_spans):
                found_span = span
                break

        if found_span is None:
            unmatched.append(term)
            continue

        claimed_spans.append(found_span)
        claims.append((found_span[0], found_span[1], slug))

    # Splice right-to-left so earlier spans' indices stay valid as later
    # (further-right) insertions grow the string.
    result = escaped
    for start, end, slug in sorted(claims, key=lambda c: c[0], reverse=True):
        matched_text = result[start:end]
        href = LEXICON_TERM_URL_TEMPLATE.format(slug=html.escape(slug, quote=True))
        anchor = f'<a href="{href}">{matched_text}</a>'
        result = result[:start] + anchor + result[end:]

    return LinkifyResult(html=Markup(result), unmatched_terms=unmatched)
