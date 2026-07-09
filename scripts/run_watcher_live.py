#!/usr/bin/env python3
"""Live acceptance-criterion proof tool: ``python scripts/run_watcher_live.py
--runs N``.

Invokes ``watcher.cli.run()`` ``N`` times in one process against REAL live
endpoints (HN Algolia, arXiv, each registered lab site) -- no mocking, no
fixtures, real ``data/ledger.json``/``data/queue.json``/
``data/whats_moving.json`` paths. Prints a clear before/after/delta summary
after every run so a human (or a future CI step) can visually confirm the
Phase 1 live acceptance criterion: a second run against unchanged upstream
data adds zero new ledger keys.

This script is deliberately thin: it holds no pipeline logic of its own,
only argument parsing and the printed report -- every actual fetch/cluster/
rank/diff/write step lives in ``watcher.cli.run``, so there is exactly one
implementation of the pipeline to keep correct.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/run_watcher_live.py` (no package install
# / no `-m` needed) by putting the repo root on sys.path before importing
# the watcher package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher.cli import RunResult, run  # noqa: E402


def _format_new_per_source(previous: RunResult, current: RunResult) -> str:
    """"New items per source" since the previous run in this same process
    -- an in-process URL-set diff (see ``watcher.cli.RunResult``'s own
    docstring for why this, rather than a second independent fetch or some
    persisted "seen before" state, is what "new" means here).
    """
    new_hn = len(current.hn_urls - previous.hn_urls)
    new_arxiv = len(current.arxiv_urls - previous.arxiv_urls)
    new_lab = len(current.lab_urls - previous.lab_urls)
    return f"hn=+{new_hn} arxiv=+{new_arxiv} lab=+{new_lab}"


def _print_run_summary(
    run_number: int, result: RunResult, previous: RunResult | None
) -> None:
    print(f"--- Run {run_number} ---")
    print(
        f"  Sources fetched:  hn={result.hn_items}  arxiv={result.arxiv_items}  "
        f"lab={result.lab_items}"
    )
    print(f"  Clusters formed:  {result.clusters}")
    print(f"  Queue size:       {result.queue_size}")
    print(
        f"  Ledger entries:   {result.ledger_size_before} -> "
        f"{result.ledger_size_after}  (+{result.new_ledger_keys} new)"
    )
    if previous is not None:
        print(f"  New items per source (vs. previous run): "
              f"{_format_new_per_source(previous, result)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the watcher pipeline against REAL live endpoints N times "
            "in one process, printing a before/after/delta summary each "
            "time -- the Phase 1 live acceptance-criterion proof."
        )
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of times to invoke watcher.cli.run() (default: 1).",
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be >= 1")

    results: list[RunResult] = []
    for run_number in range(1, args.runs + 1):
        result = run()
        previous = results[-1] if results else None
        _print_run_summary(run_number, result, previous)
        results.append(result)

    if len(results) > 1:
        first, last = results[0], results[-1]
        print("--- Summary across all runs ---")
        print(
            f"  Ledger entries:  {first.ledger_size_before} -> "
            f"{last.ledger_size_after}  "
            f"(+{last.ledger_size_after - first.ledger_size_before} total new)"
        )
        print(f"  Queue size (final run): {last.queue_size}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
