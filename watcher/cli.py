"""Watcher CLI entrypoint: ``python -m watcher.cli run``.

Wires together every pure-code Phase 1 piece built in earlier commits into
CLAUDE.md's daily-loop diagram, the "pure code" half of it:

    fetch all feeds -> cluster & rank -> diff vs data/ledger.json ->
    write data/queue.json (<=8 clusters, each with ALL source URLs) ->
    compute whats_moving.json from HN counts -> update ledger

Nothing in this module talks to an LLM -- it is exactly the
``watch.yml``-shaped pipeline, callable directly (:func:`run`) or via
``python -m watcher.cli run`` (:func:`main`).
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from watcher.clustering import cluster_items
from watcher.http import build_session
from watcher.ledger import LEDGER_PATH, apply_run, load_ledger, save_ledger
from watcher.models import Item
from watcher.queue_writer import QUEUE_PATH, write_queue
from watcher.ranking import rank_clusters
from watcher.sources.arxiv import fetch_arxiv_items
from watcher.sources.hn import fetch_hn_items
from watcher.sources.labs.registry import fetch_all_lab_items
from watcher.velocity import WHATS_MOVING_PATH, save_whats_moving

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunResult:
    """Summary of one :func:`run` invocation.

    Consumed by ``scripts/run_watcher_live.py`` for its before/after/delta
    reporting (the Phase 1 live acceptance-criterion proof tool) and by
    ``tests/test_cli.py`` to assert on orchestration outcomes without
    re-parsing the files :func:`run` just wrote. The three ``*_urls``
    fields (raw, per-source URL sets from this run's fetch) let a caller
    compute "new items per source" as an in-process diff against a
    previous ``RunResult`` -- no fetcher exposes a persisted "new since
    last call" concept of its own beyond the ETag cache/ledger, so that
    diff is the simplest reasonable way to report it; logged in
    IMPROVEMENT_BACKLOG.md.
    """

    hn_items: int
    arxiv_items: int
    lab_items: int
    clusters: int
    queue_size: int
    ledger_size_before: int
    ledger_size_after: int
    new_ledger_keys: int
    hn_urls: frozenset[str] = field(default_factory=frozenset)
    arxiv_urls: frozenset[str] = field(default_factory=frozenset)
    lab_urls: frozenset[str] = field(default_factory=frozenset)


def _fetch_source(name: str, fetch_fn, *args, **kwargs) -> list[Item]:
    """Call one top-level source fetcher, isolating its failure from the
    other two so a single source's outage never aborts the whole run.

    Each fetcher already degrades to ``[]`` on a ``robots.txt`` disallow
    (see their own docstrings), and ``fetch_all_lab_items`` already isolates
    each *individual* lab this same way (``watcher/sources/labs/registry.py``).
    But a genuine fetch failure surviving ``watcher.http.fetch``'s retries
    (e.g. arXiv's API returning a sustained 429) previously propagated
    straight out of HN/arXiv's fetchers, uncaught, and crashed the entire
    ``watch`` job -- so a transient rate-limit on one source zeroed out
    every source for the day, not just that one. This extends the same
    "skip a source cleanly rather than crash the whole run" rule one level
    up, across HN/arXiv/labs themselves, matching the registry's own
    per-lab precedent. Logged in IMPROVEMENT_BACKLOG.md.
    """
    try:
        return fetch_fn(*args, **kwargs)
    except Exception:
        logger.exception(
            "%s fetcher failed unexpectedly; skipping it for this run.", name
        )
        return []


def run(
    *,
    now: datetime | None = None,
    session: requests.Session | None = None,
    ledger_path: Path | str = LEDGER_PATH,
    queue_path: Path | str = QUEUE_PATH,
    whats_moving_path: Path | str = WHATS_MOVING_PATH,
) -> RunResult:
    """Run one full watcher pass and return a :class:`RunResult` summary.

    Order, matching CLAUDE.md's daily-loop diagram exactly:

    1. Fetch all three source categories (HN, arXiv, each registered lab)
       via :func:`_fetch_source`, which isolates each one's failure from
       the other two (see its own docstring) -- this function does not add
       any further safety net around fetching, only around the pipeline
       stages after it. Lab items are also windowed to
       ``config.LAB_RECENCY_WINDOW_DAYS`` here (``fetch_all_lab_items(session,
       now=now)`` -- Phase 1 PM checkpoint fix so an un-windowed
       archive-serving lab RSS feed can't flood the candidate pool; see
       ``watcher/sources/labs/registry.py``).
    2. Cluster every fetched item (``watcher.clustering.cluster_items``).
    3. Rank the *full* cluster pool -- deliberately uncapped
       (``limit=len(clusters)``), not ``MAX_QUEUE_SIZE``. Capping here
       first would let an already-published cluster occupying a top-8
       score slot crowd out a fresh, still-unpublished one before the
       ledger diff ever runs; the plan's own ordering ("rank -> diff vs
       ledger -> write queue.json (<=8)") puts the ledger diff before the
       final cap, so that's what this call preserves. Logged in
       IMPROVEMENT_BACKLOG.md.
    4. Load the ledger, write ``data/queue.json`` (``watcher.queue_writer.
       write_queue`` excludes already-carded clusters and applies the real
       ``MAX_QUEUE_SIZE`` cap *after* that exclusion), then separately
       upsert ledger entries for *every* surviving (not just top-8)
       unpublished cluster via ``watcher.ledger.apply_run`` -- a story
       that doesn't make today's queue still gets its ``first_seen``
       tracked, so it isn't treated as brand-new if it resurfaces later.
    5. Compute and write ``data/whats_moving.json`` from this run's HN
       items (pure code, no AI -- ``watcher.velocity``).

    ``now`` defaults to the real current UTC time; passing it explicitly
    (as every test in this project does) makes the whole run deterministic.
    """
    now = now or datetime.now(timezone.utc)
    session = session or build_session()

    hn_items: list[Item] = _fetch_source("HN", fetch_hn_items, session, now=now)
    arxiv_items: list[Item] = _fetch_source("arXiv", fetch_arxiv_items, session)
    lab_items: list[Item] = _fetch_source(
        "labs", fetch_all_lab_items, session, now=now
    )
    all_items: list[Item] = [*hn_items, *arxiv_items, *lab_items]

    clusters = cluster_items(all_items)
    ranked_clusters = rank_clusters(clusters, now=now, limit=max(len(clusters), 1))

    ledger = load_ledger(ledger_path)
    ledger_size_before = len(ledger.get("entries", {}))

    queue = write_queue(ranked_clusters, ledger, path=queue_path)

    _survivors, new_ledger = apply_run(ranked_clusters, ledger, now=now)
    save_ledger(new_ledger, ledger_path)
    ledger_size_after = len(new_ledger.get("entries", {}))

    save_whats_moving(hn_items, now=now, path=whats_moving_path)

    return RunResult(
        hn_items=len(hn_items),
        arxiv_items=len(arxiv_items),
        lab_items=len(lab_items),
        clusters=len(clusters),
        queue_size=len(queue),
        ledger_size_before=ledger_size_before,
        ledger_size_after=ledger_size_after,
        new_ledger_keys=ledger_size_after - ledger_size_before,
        hn_urls=frozenset(item.url for item in hn_items),
        arxiv_urls=frozenset(item.url for item in arxiv_items),
        lab_urls=frozenset(item.url for item in lab_items),
    )


def _format_summary(result: RunResult) -> str:
    return (
        f"hn={result.hn_items} arxiv={result.arxiv_items} lab={result.lab_items} "
        f"clusters={result.clusters} queue={result.queue_size} "
        f"ledger {result.ledger_size_before}->{result.ledger_size_after} "
        f"(+{result.new_ledger_keys} new)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m watcher.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Run one full watcher pass.")

    args = parser.parse_args(argv)

    if args.command == "run":
        result = run()
        print(_format_summary(result))
        return 0

    return 1  # pragma: no cover - argparse's `required=True` already exits earlier


if __name__ == "__main__":
    sys.exit(main())
