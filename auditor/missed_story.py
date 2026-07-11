"""Weekly missed-story check (`audit.yml`'s pure-code, no-LLM pass).

Per CLAUDE.md's "audit.yml -- weekly" bullet ('"missed-story" check
(top-20 HN AI stories of the week vs cards; misses logged as findings,
not failures)') and the approved build plan's section 6 (Phase 5):
'missed-story check (HN top-20 AI stories of the week vs. published +
ledger-dropped clusters -- genuine misses vs. correctly-declined-
per-corroboration-rule are distinguished, both logged as *findings, not
failures*, per spec)'. This module implements exactly that check.

**Reuse, not reimplementation, per this turn's own explicit instruction:**

1. **HN fetching.** :func:`fetch_weekly_top_hn_stories` imports and calls
   ``watcher.sources.hn.fetch_hn_items`` directly -- the exact same
   broad-pool / keyword-filter / final-candidacy pipeline the daily
   watcher itself runs (see ``watcher/sources/hn.py``'s own module
   docstring) -- with only ``lookback_hours`` widened from the watcher's
   own 48h default to this module's own 168h (7-day) window, and the
   result sliced to the top ``top_n`` (default 20) items. `fetch_hn_items`
   already returns its items sorted ``(-points, url)`` descending (see its
   own docstring), so "top 20 by points" is nothing more than
   ``items[:20]`` on that existing sort -- no second sort, no
   reimplemented keyword filter, no reimplemented points/velocity
   candidacy logic.
2. **Similarity.** :func:`story_matches_card` reuses
   ``watcher.clustering._jaccard`` and ``watcher.models.tokenize_title``
   directly (the identical composition ``auditor/duplicates.py`` already
   established for its own card-vs-card check), and reuses
   ``watcher.models.normalize_url`` for the exact-URL tier -- literally
   the *other* half of ``watcher/clustering.py``'s own two-tier matching
   algorithm ("exact-URL-normalization match, else Jaccard >= 0.35 over
   stopword-stripped title tokens"), applied here a second time, after
   the fact, to decide whether a top-of-the-week HN story is the same
   underlying story as something the Wire already knows about.

**Why matching against `content/cards/*.json` and matching against
`data/ledger.json`'s entries use *different* tiers of that same two-tier
algorithm, not both tiers against both targets.** A published card
(`card.schema.json`) carries a `headline` (the analyst's own rewritten
prose) and a `citations[]` array of source URLs, but no single
"canonical source URL" field of its own -- so a card is matched against
an HN story two ways: (a) exact-URL tier, checking whether the story's
own (normalized) URL is among that card's own `citations[].url` (it was
literally one of the sources the analyst fetched and cited), or (b)
Jaccard tier, comparing the story's raw HN title against the card's
`headline`. A `data/ledger.json` entry (`ledger.schema.json`), by
contrast, carries `member_urls` (normalized member URLs the cluster_hash
was derived from) but no title text of any kind -- `ledger_entry` is
`additionalProperties: false` with no title/headline field, so there is
no text on the ledger side for a Jaccard comparison to even run against.
A ledger entry is therefore matched by the exact-URL tier only: is the
story's own normalized URL among that entry's `member_urls`? This is a
spec-silent judgment call (the task instruction says "Jaccard-match ...
against both published cards' titles AND data/ledger.json's entries",
but the ledger schema genuinely has no title text to Jaccard against);
logged in full in IMPROVEMENT_BACKLOG.md. Both tiers are still "reusing
watcher/clustering.py's similarity function" in the sense that matters --
the exact-URL tier is clustering.py's own first-choice mechanism
(`normalize_url` equality, checked before Jaccard is ever computed, per
its own module docstring), not a new invention for this module.

**Classification (three buckets, per this turn's own instruction):**

- **covered** -- the story matches a published card (via either tier
  above), OR matches a ledger entry whose `status` is not `"dropped"`
  (i.e. `"queued"` or `"published"` -- the pipeline already knows about
  this story and has not declined it). A card match always wins over a
  ledger match when both are present (see :func:`classify_story`) --
  "covered" is the strongest, least-interesting-to-report outcome, so any
  matching evidence for it takes priority over the `seen_but_dropped`
  bucket below.
- **seen_but_dropped** -- the story does *not* match any published card,
  but *does* match a ledger entry whose `status` is `"dropped"`: the
  pipeline saw this exact story, ran it through the corroboration rule
  (CLAUDE.md Hard Rule 1), and correctly declined to publish it. This is
  informational, not a miss -- CLAUDE.md's own audit.yml bullet is
  explicit that these two outcomes must be "distinguished," not
  conflated.
- **missed** -- the story matches neither a published card nor any ledger
  entry at all. This is a genuine gap: material the watcher's own daily
  pipeline never saw, or saw but never made it into `data/queue.json` in
  the first place (e.g. it fell outside `MAX_QUEUE_SIZE`, or clustering
  merged it into a cluster this specific check's matching logic doesn't
  reconstruct). Per CLAUDE.md, this is logged as a *finding*, not a test
  failure or an editorial embarrassment -- an 8-cards/day, quality-over-
  volume wire is expected to leave stories uncovered.

This module is pure, filesystem/network-free logic for its core
classification functions (:func:`story_matches_card`,
:func:`story_matches_ledger_entry`, :func:`classify_story`) -- every one
takes already-loaded Python data and returns plain values, so each is
trivially unit-testable against small fixture cards/ledgers/items, matching
the same convention `auditor/duplicates.py` and `auditor/linkrot.py`
already established. :func:`audit_missed_stories` is the impure entry
point that wires live HN fetching + disk-loaded cards/ledger together by
default, exactly like `auditor.linkrot.audit_link_rot` and
`auditor.duplicates.audit_duplicates` do for their own checks -- every one
of its live-touching defaults (`hn_items`, `cards`, `ledger`, `session`)
can be overridden by a caller/test with explicit in-memory values instead.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

import requests

from auditor.linkrot import load_cards
from watcher import http
from watcher.clustering import _jaccard
from watcher.config import CACHE_DIR, JACCARD_SIMILARITY_THRESHOLD, REPO_ROOT
from watcher.models import Item, normalize_url, tokenize_title
from watcher.sources import hn

LEDGER_PATH = REPO_ROOT / "data" / "ledger.json"

# "top-20 HN AI stories of the week" per CLAUDE.md's audit.yml bullet,
# verbatim -- 7 days (168h), widened from watcher/sources/hn.py's own 48h
# daily-watcher default via `fetch_hn_items`'s own `lookback_hours`
# parameter (per this turn's own instruction: "call it with a widened
# count/window rather than reimplementing HN fetching").
MISSED_STORY_HN_LOOKBACK_HOURS = 24 * 7
MISSED_STORY_TOP_N = 20

# Reused directly from watcher/config.py -- not a second, independently
# chosen bar. This is not merely "the same default duplicates.py picked
# for its own, different comparison" -- it is literally the exact
# threshold watcher/clustering.py's own daily clustering pass would apply
# to this very same HN Item's title when comparing it against any non-lab
# cluster seed (an HN item is never `source_type == "lab"`, so the
# stricter LAB_LAB_JACCARD_SIMILARITY_THRESHOLD never applies to it either
# way). This module is asking, after the fact, "would this story's title
# have clustered with an existing card/queued item" -- reusing the same
# constant that already answers that exact question for this exact item
# type is not a judgment call so much as using the one bar that was always
# the right one for this comparison.
MISSED_STORY_JACCARD_THRESHOLD = JACCARD_SIMILARITY_THRESHOLD


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HN fetch -- reuse, widened window/count, no reimplementation
# ---------------------------------------------------------------------------


def fetch_weekly_top_hn_stories(
    session: requests.Session,
    *,
    now: datetime | None = None,
    lookback_hours: int = MISSED_STORY_HN_LOOKBACK_HOURS,
    top_n: int = MISSED_STORY_TOP_N,
    cache_dir: Path = CACHE_DIR,
) -> list[Item]:
    """The top ``top_n`` AI-relevant HN stories of the trailing
    ``lookback_hours`` (default 7 days), by points.

    Delegates entirely to ``watcher.sources.hn.fetch_hn_items`` -- the
    exact same broad-pool/keyword-filter/final-candidacy pipeline the
    daily watcher itself runs -- passing only a widened ``lookback_hours``
    (this module's own 168h default vs. the watcher's own 48h default);
    every other fetch_hn_items parameter (points/velocity thresholds,
    keyword list, robots gate, ETag caching, windowed-query pagination)
    is left at its own real default, unchanged. `fetch_hn_items` already
    returns items sorted ``(-points, url)`` descending, so slicing to
    ``items[:top_n]`` is genuinely "top N by points," not a fresh sort.
    """
    items = hn.fetch_hn_items(
        session,
        now=now,
        lookback_hours=lookback_hours,
        cache_dir=cache_dir,
    )
    return items[:top_n]


# ---------------------------------------------------------------------------
# Ledger loading
# ---------------------------------------------------------------------------


def load_ledger(ledger_path: Path = LEDGER_PATH) -> dict:
    """Load ``data/ledger.json``.

    Returns the schema's own empty shape (``{"version": 1, "entries":
    {}}``) if ``ledger_path`` doesn't exist, matching
    ``auditor.linkrot.load_cards``'s own "missing directory -> empty,
    don't raise" convention for a fixture/test environment that hasn't
    seeded a real ledger file.
    """
    if not ledger_path.is_file():
        return {"version": 1, "entries": {}}
    with ledger_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Matching -- the two tiers of watcher/clustering.py's own algorithm,
# applied to the two different targets each tier's data actually supports.
# ---------------------------------------------------------------------------


def _card_citation_urls(card: Mapping[str, object]) -> set[str]:
    """Every normalized ``citations[].url`` on ``card`` -- the exact-URL
    match target for :func:`story_matches_card`. Missing/absent
    ``citations`` contributes an empty set, never a ``KeyError``.
    """
    return {
        normalize_url(str(citation["url"]))
        for citation in (card.get("citations", None) or [])
        if citation.get("url")
    }


def story_matches_card(
    item: Item,
    card: Mapping[str, object],
    *,
    threshold: float = MISSED_STORY_JACCARD_THRESHOLD,
) -> bool:
    """Does ``item`` (an HN story) match ``card`` (a published card)?

    True if either tier of clustering.py's own algorithm fires:
    exact-URL-normalization match against any of the card's own
    ``citations[].url`` (the story was literally one of the sources the
    analyst fetched and cited), or Jaccard similarity of ``item.title``
    against the card's own ``headline`` at or above ``threshold``
    (reusing ``_jaccard``/``tokenize_title`` directly, matching
    ``auditor.duplicates.title_similarity``'s own composition).
    """
    if normalize_url(item.url) in _card_citation_urls(card):
        return True
    headline = str(card.get("headline") or "")
    similarity = _jaccard(tokenize_title(item.title), tokenize_title(headline))
    return similarity >= threshold


def _ledger_entry_member_urls(entry: Mapping[str, object]) -> set[str]:
    """``entry``'s own ``member_urls`` as a set -- already normalized per
    ``ledger.schema.json``'s own description ("Normalized member URLs the
    cluster_hash was derived from"), but no assumption is made about
    that on the *item* side -- see :func:`story_matches_ledger_entry`,
    which still normalizes ``item.url`` before comparing.
    """
    return set(entry.get("member_urls") or [])


def story_matches_ledger_entry(item: Item, entry: Mapping[str, object]) -> bool:
    """Does ``item``'s own (normalized) URL appear among ``entry``'s
    ``member_urls``?

    The only matching tier available for a ledger entry -- see the module
    docstring for why Jaccard never applies here (no title text exists on
    the ledger side to Jaccard against).
    """
    return normalize_url(item.url) in _ledger_entry_member_urls(entry)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoryClassification:
    """The classification outcome for one HN story."""

    title: str
    url: str
    points: int | None
    classification: str  # "covered" | "seen_but_dropped" | "missed"
    matched_card_id: str | None = None
    matched_cluster_hash: str | None = None
    dropped_reason: str | None = None


def classify_story(
    item: Item,
    *,
    cards: Sequence[Mapping[str, object]],
    ledger_entries: Mapping[str, Mapping[str, object]],
    threshold: float = MISSED_STORY_JACCARD_THRESHOLD,
) -> StoryClassification:
    """Classify one HN story as ``covered``/``seen_but_dropped``/``missed``.

    Checks every published card first (see :func:`story_matches_card`) --
    any card match is an immediate ``covered``, regardless of what the
    ledger says, since a card match is the strongest possible evidence
    the story is already on the Wire. Only if no card matches does the
    ledger get consulted: a match against any entry whose ``status`` is
    not ``"dropped"`` (``"queued"`` or ``"published"`` -- the pipeline
    already knows about this story and has not declined it) is also
    ``covered``; a match against a ``"dropped"`` entry and *no* non-dropped
    entry is ``seen_but_dropped``; no match anywhere is ``missed``.

    ``ledger_entries`` is iterated in the order given (a plain ``dict``
    preserves its own insertion/JSON-load order), so which entry a
    multi-match story reports as ``matched_cluster_hash`` is deterministic
    for a given input, never dependent on hash-randomization or set
    iteration order.
    """
    for card in cards:
        if story_matches_card(item, card, threshold=threshold):
            return StoryClassification(
                title=item.title,
                url=item.url,
                points=item.points,
                classification="covered",
                matched_card_id=str(card.get("id")) if card.get("id") else None,
            )

    dropped_match: tuple[str, Mapping[str, object]] | None = None
    for cluster_hash, entry in ledger_entries.items():
        if not story_matches_ledger_entry(item, entry):
            continue
        if entry.get("status") != "dropped":
            return StoryClassification(
                title=item.title,
                url=item.url,
                points=item.points,
                classification="covered",
                matched_card_id=entry.get("card_id"),
                matched_cluster_hash=cluster_hash,
            )
        if dropped_match is None:
            dropped_match = (cluster_hash, entry)

    if dropped_match is not None:
        cluster_hash, entry = dropped_match
        verifier_outcome = entry.get("verifier_outcome") or {}
        return StoryClassification(
            title=item.title,
            url=item.url,
            points=item.points,
            classification="seen_but_dropped",
            matched_cluster_hash=cluster_hash,
            dropped_reason=verifier_outcome.get("dropped_reason"),
        )

    return StoryClassification(
        title=item.title,
        url=item.url,
        points=item.points,
        classification="missed",
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def audit_missed_stories(
    *,
    hn_items: Sequence[Item] | None = None,
    cards: list[dict] | None = None,
    ledger: Mapping[str, object] | None = None,
    session: requests.Session | None = None,
    now: datetime | None = None,
    lookback_hours: int = MISSED_STORY_HN_LOOKBACK_HOURS,
    top_n: int = MISSED_STORY_TOP_N,
    threshold: float = MISSED_STORY_JACCARD_THRESHOLD,
    cache_dir: Path = CACHE_DIR,
    ledger_path: Path = LEDGER_PATH,
) -> dict:
    """Run the full missed-story check and return a summary dict.

    ``hn_items``/``cards``/``ledger`` let a caller (or a test) pass
    explicit in-memory data directly, for testability with no real
    network/disk access needed -- per this turn's own instruction. Any
    left as ``None`` falls back to a live default: ``hn_items`` via
    :func:`fetch_weekly_top_hn_stories` (building a fresh
    ``watcher.http.build_session()`` first if ``session`` is also
    omitted), ``cards`` via ``auditor.linkrot.load_cards()``, ``ledger``
    via :func:`load_ledger`.

    Returns ``{checked_at, window_hours, top_n, total_checked, counts:
    {covered, seen_but_dropped, missed}, missed_stories: [...],
    seen_but_dropped_stories: [...], results: [...]}`` -- ``results`` is
    every :class:`StoryClassification` (as a plain dict via
    ``dataclasses.asdict``), in the same order ``hn_items`` was given/
    fetched in (i.e. by points descending); ``missed_stories``/
    ``seen_but_dropped_stories`` are the same records pre-filtered to
    their own bucket, matching CLAUDE.md's instruction that both are
    "logged as findings" -- a caller wiring this into
    ``data/audit/latest.json``/``IMPROVEMENT_BACKLOG.md`` doesn't have to
    re-filter ``results`` itself for either finding type. This is a
    provisional shape (no ``schemas/audit.schema.json`` exists yet),
    matching the same caveat already logged for
    ``auditor.linkrot.audit_link_rot``/``auditor.duplicates.audit_duplicates``.
    """
    if hn_items is None:
        if session is None:
            session = http.build_session()
        hn_items = fetch_weekly_top_hn_stories(
            session,
            now=now,
            lookback_hours=lookback_hours,
            top_n=top_n,
            cache_dir=cache_dir,
        )

    if cards is None:
        cards = load_cards()

    if ledger is None:
        ledger = load_ledger(ledger_path)
    ledger_entries = ledger.get("entries") or {}

    classifications = [
        classify_story(item, cards=cards, ledger_entries=ledger_entries, threshold=threshold)
        for item in hn_items
    ]

    counts = {"covered": 0, "seen_but_dropped": 0, "missed": 0}
    for classification in classifications:
        counts[classification.classification] += 1

    results = [asdict(c) for c in classifications]

    return {
        "checked_at": _utcnow_iso(),
        "window_hours": lookback_hours,
        "top_n": top_n,
        "total_checked": len(classifications),
        "counts": counts,
        "missed_stories": [r for r in results if r["classification"] == "missed"],
        "seen_but_dropped_stories": [
            r for r in results if r["classification"] == "seen_but_dropped"
        ],
        "results": results,
    }
