"""Deterministic clustering pass (watcher/clustering.py).

Turns the pool of Items every fetcher (HN, arXiv, each lab) produces into
clusters -- groups of Items judged to be about the same underlying story
-- before watcher/ranking.py scores them for data/queue.json. Implements
the approved build plan's Phase 1 "Clustering" bullet exactly:

    exact-URL-normalization match, else Jaccard >= 0.35 over
    stopword-stripped title tokens.

Run in a fully deterministic order so the same input always produces the
same clusters -- membership *and* cluster order -- regardless of what
order each fetcher happened to yield items in. That determinism is what
lets watcher/ledger.py treat a same-day re-run as a no-op (Phase 1's
idempotency requirement) rather than a source of spurious new entries.

Algorithm:

1. Sort every input Item by ``(published_at, source_name, url)``
   ascending. This -- not fetch/arrival order -- is the only order the
   algorithm ever looks at.
2. Walk the sorted items once (single pass). For each item, in order:
   a. Exact match: if ``watcher.models.normalize_url(item.url)`` equals
      the normalized URL of *any* existing cluster's member, join that
      cluster (the first, i.e. earliest-created, cluster with a match)
      -- this short-circuits before Jaccard is even computed, so an
      exact URL match wins even when the two titles are otherwise
      completely dissimilar.
   b. Otherwise: compute the Jaccard similarity of the item's
      stopword-stripped lowercase title tokens
      (``watcher.models.tokenize_title``) against each existing
      cluster's *seed item* -- its earliest (first, by the sort key
      above) member. Join the first (earliest-created) cluster whose
      similarity is >= the applicable threshold: ``config.
      LAB_LAB_JACCARD_SIMILARITY_THRESHOLD`` (0.65) when *both* the
      candidate item and that cluster's seed are ``source_type ==
      "lab"``, else the general ``config.JACCARD_SIMILARITY_THRESHOLD``
      (0.35). See :func:`_merge_threshold`'s docstring for why lab-lab
      pairs get a stricter bar -- Phase 1 PM checkpoint fix for the
      "Introducing GPT-..." mega-cluster defect, logged in
      IMPROVEMENT_BACKLOG.md.
   c. Otherwise: start a new cluster containing just this item; it
      becomes that cluster's seed for future comparisons.

Comparing a new item against each cluster's *seed item* only (rather
than every member) is a spec-silent simplification -- the plan says only
"Jaccard similarity ... over title tokens", not which member of a
multi-item cluster to compare against -- logged in
IMPROVEMENT_BACKLOG.md. It keeps the pass genuinely single-pass
(O(items x clusters) title comparisons, not O(items x cluster_size)),
and stays deterministic: the seed is always the earliest-sorted member,
so which item a cluster gets compared against never depends on the
order in which later items happened to join it.

Each returned :class:`Cluster` also exposes ``cluster_hash`` --
``sha256`` of its sorted normalized member URLs, per the plan's separate
"Ledger idempotency" bullet and ``schemas/ledger.schema.json``'s own
description ("sha256 of sorted normalized member URLs, per
watcher/clustering.py"), which already names this module as the owner of
that computation. It lives here (not ``watcher/ledger.py``) because it
is a pure function of a cluster's own membership -- no ledger state
needed to compute it -- and ``watcher/ranking.py`` (built concurrently)
already duck-types every cluster it scores as exposing a
``.cluster_hash`` string attribute.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from watcher.config import JACCARD_SIMILARITY_THRESHOLD, LAB_LAB_JACCARD_SIMILARITY_THRESHOLD
from watcher.models import Item, normalize_url, tokenize_title


@dataclass
class Cluster:
    """One group of Items judged to be the same underlying story.

    ``items`` is kept in the same deterministic sort order clustering
    itself consumed -- ``items[0]`` is the cluster's "seed": the
    earliest item (by the ``(published_at, source_name, url)`` sort
    key) added to the cluster, used as the representative for Jaccard
    comparisons against later candidate items.
    """

    items: list[Item] = field(default_factory=list)

    @property
    def seed(self) -> Item:
        return self.items[0]

    @property
    def normalized_urls(self) -> frozenset[str]:
        """Normalized URLs of every member -- backs the exact-match check."""
        return frozenset(normalize_url(item.url) for item in self.items)

    @property
    def cluster_hash(self) -> str:
        """``sha256`` hex digest of this cluster's sorted normalized member
        URLs -- the idempotency key ``data/ledger.json``/``data/queue.json``
        both key on (see the module docstring). Sorting first means a
        cluster's hash never depends on the order members joined in, only
        on *which* URLs ended up in it.
        """
        return compute_cluster_hash(self.normalized_urls)


def compute_cluster_hash(normalized_urls: frozenset[str] | set[str] | list[str]) -> str:
    """``sha256`` hex digest of ``sorted(normalized_urls)``, newline-joined.

    Standalone so callers (e.g. ``watcher/ledger.py``, re-hashing a
    previously-persisted ``member_urls`` list read back from JSON) can
    recompute the identical hash without needing a live :class:`Cluster`
    instance. Newline-joining before hashing is a simple, unambiguous
    encoding for "sorted list of strings" -- logged in
    IMPROVEMENT_BACKLOG.md, since the plan only says "sha256 of sorted
    normalized member URLs" without specifying a serialization.
    """
    joined = "\n".join(sorted(normalized_urls))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _sort_key(item: Item) -> tuple[str, str, str]:
    return (item.published_at, item.source_name, item.url)


def _merge_threshold(item: Item, seed: Item) -> float:
    """The Jaccard bar ``item`` must clear against ``seed`` to merge.

    ``LAB_LAB_JACCARD_SIMILARITY_THRESHOLD`` (0.65) when both are
    ``source_type == "lab"``, else the general ``JACCARD_SIMILARITY_
    THRESHOLD`` (0.35). Phase 1 PM checkpoint fix: lab announcement
    titles are short and heavily templated ("Introducing GPT-5.4",
    "Introducing GPT-5.5", "Introducing gpt-oss-safeguard", ...), so two
    or three shared boilerplate tokens are already enough to clear 0.35
    even between titles about genuinely different releases -- this is
    what chained the real "Introducing GPT-..." mega-cluster reported at
    the Phase 1 PM checkpoint (17 members spanning 2.5 years, later also
    bounded by ``config.LAB_RECENCY_WINDOW_DAYS``). A lab item compared
    against a non-lab seed (or vice versa) keeps the general 0.35 bar
    unchanged -- cross-source corroboration is exactly what that
    comparison exists to catch, and a non-lab title isn't templated the
    same way. Logged in IMPROVEMENT_BACKLOG.md, including why this was
    chosen over switching seed-only comparison to max-over-members (that
    alternative only ever makes merging *more* permissive, never less --
    it cannot fix an over-merging defect on its own).
    """
    if item.source_type == "lab" and seed.source_type == "lab":
        return LAB_LAB_JACCARD_SIMILARITY_THRESHOLD
    return JACCARD_SIMILARITY_THRESHOLD


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two token sets.

    Two empty sets (e.g. both titles reduce to zero meaningful tokens
    after stopword-stripping) return 0.0 rather than an undefined 0/0 --
    empty title content is never treated as a similarity match.
    """
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def cluster_items(items: list[Item]) -> list[Cluster]:
    """Deterministically sort then greedily cluster ``items``.

    See the module docstring for the full algorithm. Returns clusters in
    creation order (i.e. in order of each cluster's seed item's sort
    key), each cluster's ``items`` list itself also in overall sorted
    order. Calling this repeatedly on the same input (in any input
    order) always yields the same result.
    """
    sorted_items = sorted(items, key=_sort_key)

    clusters: list[Cluster] = []
    for item in sorted_items:
        item_url = normalize_url(item.url)

        matched = next(
            (cluster for cluster in clusters if item_url in cluster.normalized_urls),
            None,
        )

        if matched is None:
            item_tokens = tokenize_title(item.title)
            matched = next(
                (
                    cluster
                    for cluster in clusters
                    if _jaccard(item_tokens, tokenize_title(cluster.seed.title))
                    >= _merge_threshold(item, cluster.seed)
                ),
                None,
            )

        if matched is not None:
            matched.items.append(item)
        else:
            clusters.append(Cluster(items=[item]))

    return clusters
