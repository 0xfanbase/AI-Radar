"""Ranking (Phase 1): scores clusters and selects the top ``MAX_QUEUE_SIZE``
for ``data/queue.json``.

    score = primary_source_weight x cross_source_count x hn_velocity_score

- **primary_source_weight**: the max over the cluster's member
  :class:`~watcher.models.Item`\\ s of ``PRIMARY_SOURCE_WEIGHTS[item.source_type]``
  (lab=3.0, arxiv=2.0, hn=1.0, per ``watcher/config.py``).
- **cross_source_count**: the count of *distinct* ``item.source_name``
  values in the cluster -- a cluster corroborated by two different named
  sources outranks one seen from a single source_name, holding the other
  factors equal.
- **hn_velocity_score**: ``points / max(age_hours, 1)`` for the cluster's
  HN item, if the cluster contains one -- else ``HN_VELOCITY_SCORE_FLOOR``
  (0.05, ``watcher/config.py``), per the approved plan's formula verbatim.
  If more than one HN item somehow lands in the same cluster, the
  highest-points one is used (the strongest signal available; keeps this
  a pure function of the cluster's contents, not of iteration order).

Clusters are sorted descending by score. Ties are broken, in order, by
(1) the earliest ``published_at`` across the cluster's member items, then
(2) a stable hash string derived from the cluster's own member URLs
(see ``_tie_break_hash`` below), ascending -- the final tie-break needed
for full determinism when even (1) also ties (e.g. two single-item
clusters with identical ``published_at`` and equal score). The top
``limit`` (default ``MAX_QUEUE_SIZE`` = 8) survive.

ASSUMPTION -- ``watcher/clustering.py`` may not exist as a real module yet
(it is being built concurrently, per the approved plan's Phase 1 commit
sequence). This module places zero import-time *hard* dependency on it: a
"cluster" is anything duck-typed with ``.items``, a non-empty iterable of
:class:`~watcher.models.Item` (or an Item-alike exposing
``source_type``/``source_name``/``published_at``/``points``/``url``).

For the tie-break, a cluster's own ``.cluster_hash`` string attribute is
used *when present* (``watcher/clustering.py``'s ``Cluster`` exposes one:
``sha256`` of its sorted normalized member URLs, per the plan's ledger-
idempotency bullet). When it's absent -- e.g. a minimal cluster-like
fixture that only has ``.items`` -- this module derives the equivalent
value itself from the cluster's member URLs (via
``watcher.models.normalize_url``, an already-stable Phase 1 module this
commit safely depends on), using the identical ``sha256``/sorted/
newline-joined formula. Either way the tie-break is a stable, real
``cluster_hash``-equivalent string, so :func:`rank_clusters` never
crashes on a bare-bones duck-typed cluster yet also never invents a
*different* hash than clustering.py's own for a real ``Cluster`` --
logged in ``IMPROVEMENT_BACKLOG.md``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

from watcher.config import (
    HN_VELOCITY_SCORE_FLOOR,
    MAX_QUEUE_SIZE,
    PRIMARY_SOURCE_WEIGHTS,
)
from watcher.models import normalize_url


# --------------------------------------------------------------------------
# Duck-typed interfaces (see module docstring's ASSUMPTION paragraph)
# --------------------------------------------------------------------------


@runtime_checkable
class ItemLike(Protocol):
    source_type: str
    source_name: str
    published_at: str
    points: int | None
    url: str


@runtime_checkable
class ClusterLike(Protocol):
    items: Iterable[Any]


# --------------------------------------------------------------------------
# hn_velocity_score's age floor
# --------------------------------------------------------------------------

# "points / max(age_hours, 1)" per the approved plan's ranking formula,
# verbatim. Deliberately distinct from watcher/sources/hn.py's own
# fetch-time age floor (1/60 hour) -- that one guards HN's own
# points-vs-velocity *candidacy* filter at fetch time; this one is the
# ranking-stage formula's own explicit floor, a separate constant serving
# a separate purpose.
_HN_AGE_HOURS_FLOOR = 1.0


def _parse_iso8601(value: str) -> datetime | None:
    """Best-effort ISO-8601 parse, tolerant of a trailing ``Z`` (as HN's
    Algolia API and arXiv's Atom feed both emit) and of an empty/missing
    string (as a lab Item's ``published_at`` may legitimately be, e.g. the
    DeepSeek article fetcher -- see IMPROVEMENT_BACKLOG.md). Returns
    ``None`` rather than raising on anything unparseable so one malformed
    upstream timestamp degrades gracefully instead of crashing ranking.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Scoring factors
# --------------------------------------------------------------------------


def primary_source_weight(cluster: ClusterLike) -> float:
    """Max ``PRIMARY_SOURCE_WEIGHTS[item.source_type]`` over the cluster's
    member items. An item whose ``source_type`` isn't in
    ``PRIMARY_SOURCE_WEIGHTS`` contributes 0.0 rather than raising --
    defensive only; every real fetcher emits one of ``lab``/``arxiv``/``hn``.
    """
    return max(
        PRIMARY_SOURCE_WEIGHTS.get(item.source_type, 0.0) for item in cluster.items
    )


def cross_source_count(cluster: ClusterLike) -> int:
    """Count of distinct ``source_name`` values among the cluster's items."""
    return len({item.source_name for item in cluster.items})


def hn_velocity_score(cluster: ClusterLike, *, now: datetime | None = None) -> float:
    """``points / max(age_hours, 1)`` for the cluster's (strongest) HN
    item, else ``HN_VELOCITY_SCORE_FLOOR`` when the cluster has no HN item.
    """
    now = now or datetime.now(timezone.utc)
    hn_items = [item for item in cluster.items if item.source_type == "hn"]
    if not hn_items:
        return HN_VELOCITY_SCORE_FLOOR

    hn_item = max(hn_items, key=lambda item: item.points or 0)
    published = _parse_iso8601(hn_item.published_at)
    if published is None:
        # Defensive only: watcher/sources/hn.py's own contract guarantees
        # a parseable published_at on every HN Item it emits (it skips
        # any hit missing created_at). Treated the same as "no usable HN
        # item" rather than crashing ranking on a malformed upstream
        # clustering result.
        return HN_VELOCITY_SCORE_FLOOR

    age_hours = max((now - published).total_seconds() / 3600.0, _HN_AGE_HOURS_FLOOR)
    points = hn_item.points or 0
    return points / age_hours


def score_cluster(cluster: ClusterLike, *, now: datetime | None = None) -> float:
    """``primary_source_weight x cross_source_count x hn_velocity_score``."""
    return (
        primary_source_weight(cluster)
        * cross_source_count(cluster)
        * hn_velocity_score(cluster, now=now)
    )


# --------------------------------------------------------------------------
# Tie-break key
# --------------------------------------------------------------------------

# Sentinel for a cluster whose items carry no parseable published_at at
# all -- sorts last among "earliest published_at" comparisons rather than
# winning a tie-break it has no real claim to. Timezone-aware so it never
# collides with a real (aware) parsed timestamp in a mixed sort key.
_UNKNOWN_PUBLISHED_AT = datetime.max.replace(tzinfo=timezone.utc)


def _earliest_published_at(cluster: ClusterLike) -> datetime:
    parsed = [
        dt for dt in (_parse_iso8601(item.published_at) for item in cluster.items)
        if dt is not None
    ]
    return min(parsed) if parsed else _UNKNOWN_PUBLISHED_AT


def _tie_break_hash(cluster: ClusterLike) -> str:
    """The cluster's ``cluster_hash``-equivalent string, used only to
    break a score+published_at tie so :func:`rank_clusters`'s output
    order never depends on input iteration order (full determinism).

    Prefers the cluster's own ``.cluster_hash`` attribute when present
    (``watcher/clustering.py``'s real ``Cluster`` exposes one). Falls
    back to computing the identical ``sha256``-of-sorted-normalized-
    member-URLs formula directly from ``cluster.items`` otherwise, so a
    minimal duck-typed cluster-like (e.g. this module's own tests, or any
    future caller) never crashes for lacking that attribute -- see the
    module docstring's ASSUMPTION paragraph.
    """
    existing = getattr(cluster, "cluster_hash", None)
    if isinstance(existing, str) and existing:
        return existing
    normalized_urls = sorted(normalize_url(item.url) for item in cluster.items)
    digest_input = "\n".join(normalized_urls).encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


# --------------------------------------------------------------------------
# rank_clusters
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedCluster:
    """One cluster plus its computed 1-indexed ``rank``/``score``/
    ``cluster_hash``, already in final descending order.

    Returning this small wrapper (rather than bare clusters) is a spec-
    silent judgment call -- the plan doesn't state ``rank_clusters``'s
    exact return shape -- made so a later ``queue_writer.py`` (out of this
    commit's scope) can write ``queue.schema.json``'s required
    ``cluster_hash``/``rank``/``score`` fields straight from this value
    without recomputing anything. ``cluster_hash`` here is exactly the
    same value :func:`rank_clusters` used for its own tie-break (see
    ``_tie_break_hash``) -- the cluster's own attribute when its type
    exposes one, else a same-formula fallback. Logged in
    IMPROVEMENT_BACKLOG.md.
    """

    rank: int
    score: float
    cluster_hash: str
    cluster: ClusterLike


def rank_clusters(
    clusters: Iterable[ClusterLike],
    *,
    now: datetime | None = None,
    limit: int = MAX_QUEUE_SIZE,
) -> list[RankedCluster]:
    """Score every cluster, sort descending by score (deterministic
    tie-break: earliest published_at, then a hash of member URLs), and
    return the top ``limit`` as :class:`RankedCluster` entries.
    """
    now = now or datetime.now(timezone.utc)
    scored = [(score_cluster(cluster, now=now), cluster) for cluster in clusters]
    scored.sort(
        key=lambda pair: (-pair[0], _earliest_published_at(pair[1]), _tie_break_hash(pair[1]))
    )
    return [
        RankedCluster(
            rank=index + 1,
            score=score,
            cluster_hash=_tie_break_hash(cluster),
            cluster=cluster,
        )
        for index, (score, cluster) in enumerate(scored[:limit])
    ]
