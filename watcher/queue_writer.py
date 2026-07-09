"""Queue writer (watcher/queue_writer.py): the final "diff vs ledger, cap at
<=8, write data/queue.json" step of CLAUDE.md's daily-loop diagram --
``... -> cluster & rank -> diff vs data/ledger.json -> write data/queue.json
(<=8 clusters, each with ALL source URLs) -> ...``.

Takes the full ranked cluster pool (``watcher/ranking.py``'s
:class:`~watcher.ranking.RankedCluster` list, deliberately *not* pre-capped
at ``MAX_QUEUE_SIZE`` by the caller -- see ``watcher/cli.py``) plus the
current ledger, and does three things:

1. **Excludes already-carded clusters.** Reuses ``watcher.ledger.
   unpublished_clusters`` (the ledger module's own filter: drop any cluster
   whose ledger entry already carries a non-null ``card_id``) rather than
   re-implementing the same rule a second time -- one definition of
   "already published," shared by both the ledger-upsert step and this one.
2. **Caps at ``MAX_QUEUE_SIZE`` (8).** Applied *after* the ledger filter,
   not before -- ``rank_clusters`` is called uncapped upstream precisely so
   that already-published clusters occupying high-score slots never crowd
   out fresh survivors from the top 8 (see ``watcher/cli.py``'s own
   docstring note; logged in IMPROVEMENT_BACKLOG.md).
3. **Builds & writes the schema-valid payload.** Each surviving, capped
   cluster becomes one ``queue.schema.json`` entry: ``cluster_hash``,
   ``rank`` (re-numbered 1..N *within this queue*, not the pre-filter rank
   from ``rank_clusters`` -- logged), ``score``, and ``sources`` (one entry
   per cluster member ``Item``, in the cluster's own deterministic order,
   carrying that member's raw ``url``/``title``/``source_type`` plus
   ``outlet``/``points`` -- see :func:`_source_entry` for exactly how those
   last two are derived). Validated against ``schemas/queue.schema.json``
   before ever touching disk, matching every other writer in this
   pipeline (``watcher/ledger.py``'s own pattern).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from watcher.config import MAX_QUEUE_SIZE, REPO_ROOT
from watcher.ledger import unpublished_clusters
from watcher.schema_validate import validate

QUEUE_PATH = REPO_ROOT / "data" / "queue.json"

__all__ = [
    "QUEUE_PATH",
    "build_queue",
    "load_queue",
    "save_queue",
    "write_queue",
]


# --------------------------------------------------------------------------
# One queue entry's "sources" array -- one dict per cluster member Item.
# --------------------------------------------------------------------------


def _domain(url: str) -> str | None:
    """Registrable-ish domain of ``url`` (netloc, lowercased, leading
    "www." stripped), or ``None`` if ``url`` carries no netloc at all.
    """
    netloc = urlsplit(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[len("www.") :]
    return netloc or None


def _source_entry(item: Any) -> dict[str, Any]:
    """One ``queue.schema.json`` ``sources[]`` entry for a single member Item.

    ``outlet``/``points`` per the schema's own field descriptions:
    "Publisher/outlet name where applicable (e.g. HN's linked domain);
    null for lab/arxiv sources" and "HN points at time of fetch, null for
    non-HN sources." Both are ``None`` for every non-HN ``source_type`` --
    a lab/arXiv item's own url *is* the primary source, so there's no
    separate "outlet" to name, and it never carries HN points. ``url`` is
    the raw, un-normalized url the fetcher captured (not ``normalize_url``'s
    clustering/ledger key) -- the analyst needs a real, followable link,
    not the deduplication key. Logged in IMPROVEMENT_BACKLOG.md.
    """
    is_hn = item.source_type == "hn"
    return {
        "url": item.url,
        "source_type": item.source_type,
        "title": item.title,
        "outlet": _domain(item.url) if is_hn else None,
        "points": item.points if is_hn else None,
    }


def _cluster_items(ranked_cluster: Any) -> list[Any]:
    """The member Items of one ranked cluster, tolerating the same
    duck-typed ``ClusterLike`` shape ``watcher/ranking.py`` and
    ``watcher/ledger.py`` already tolerate (a real cluster exposed either
    directly or via a ``.cluster`` wrapper attribute).
    """
    cluster = getattr(ranked_cluster, "cluster", ranked_cluster)
    return list(cluster.items)


# --------------------------------------------------------------------------
# build_queue -- filter, cap, and shape the payload (no disk I/O)
# --------------------------------------------------------------------------


def build_queue(
    ranked_clusters: Iterable[Any],
    ledger: dict[str, Any],
    *,
    limit: int = MAX_QUEUE_SIZE,
) -> list[dict[str, Any]]:
    """Filter out already-carded clusters, cap at ``limit``, and shape the
    survivors into a ``queue.schema.json``-valid array (not yet validated
    or written -- see :func:`save_queue`/:func:`write_queue` for that).

    ``ranked_clusters`` is expected in already-descending-by-score order
    (``watcher.ranking.rank_clusters``'s own output order); this function
    does not re-sort, it only filters and truncates, so the queue's
    ``rank`` field simply re-numbers the surviving order 1..N.
    """
    survivors = unpublished_clusters(ranked_clusters, ledger)[:limit]

    queue: list[dict[str, Any]] = []
    for index, ranked_cluster in enumerate(survivors, start=1):
        queue.append(
            {
                "cluster_hash": ranked_cluster.cluster_hash,
                "rank": index,
                "score": ranked_cluster.score,
                "sources": [_source_entry(item) for item in _cluster_items(ranked_cluster)],
            }
        )
    return queue


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as watcher/ledger.py)
# --------------------------------------------------------------------------


def load_queue(path: Path | str = QUEUE_PATH) -> list[dict[str, Any]]:
    """Load and schema-validate the queue at ``path``.

    A missing file returns ``[]`` -- the very first watcher run, or any run
    whose ledger diff leaves nothing new to queue, has no ``queue.json``
    yet (or an empty one), and that is not an error.
    """
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as f:
        queue = json.load(f)
    validate(queue, "queue")
    return queue


def save_queue(queue: list[dict[str, Any]], path: Path | str = QUEUE_PATH) -> None:
    """Schema-validate then write ``queue`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting ``watcher/ledger.py``
    uses for ``data/ledger.json``. Validating *before* writing means a
    malformed queue is never persisted.
    """
    validate(queue, "queue")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, sort_keys=True)
        f.write("\n")


def write_queue(
    ranked_clusters: Iterable[Any],
    ledger: dict[str, Any],
    *,
    path: Path | str = QUEUE_PATH,
    limit: int = MAX_QUEUE_SIZE,
) -> list[dict[str, Any]]:
    """Compose :func:`build_queue` + :func:`save_queue`: the single call
    ``watcher/cli.py`` makes to filter, cap, validate, and persist
    ``data/queue.json`` for one watcher run. Returns the written payload.
    """
    queue = build_queue(ranked_clusters, ledger, limit=limit)
    save_queue(queue, path)
    return queue
