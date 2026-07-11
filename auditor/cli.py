"""Auditor CLI entrypoint: ``python -m auditor.cli run``.

Wires every Phase 5 checker (`auditor.linkrot`, `auditor.lexicon_audit`,
`auditor.trend`, `auditor.missed_story`, `auditor.duplicates`) plus
`auditor.report` and `scripts.append_backlog_findings` together into
CLAUDE.md's `audit.yml` "weekly" bullet -- exactly the way `watcher/cli.py`'s
own `python -m watcher.cli run` wires every Phase 1 pure-code piece into
the daily watcher pass. Nothing in this module talks to an LLM: every one
of the five checks plus the backlog-append step is deterministic Python.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from auditor.duplicates import audit_duplicates
from auditor.lexicon_audit import audit_lexicon
from auditor.linkrot import CARDS_DIR, audit_link_rot, load_cards
from auditor.missed_story import LEDGER_PATH, audit_missed_stories, load_ledger
from auditor.report import AUDIT_LATEST_PATH, build_report, make_run_id, save_report
from auditor.trend import audit_trend
from scripts.append_backlog_findings import (
    BACKLOG_PATH,
    append_findings_to_backlog,
    derive_findings,
)
from watcher.config import REPO_ROOT
from watcher.http import build_session
from watcher.models import Item

logger = logging.getLogger(__name__)

LEXICON_PATH = REPO_ROOT / "content" / "lexicon.json"


def load_lexicon(path: Path = LEXICON_PATH) -> list[dict[str, Any]]:
    """Load `content/lexicon.json`'s own top-level array.

    No sibling `auditor.*` module owns a lexicon-file loader today
    (`auditor.lexicon_audit` is deliberately pure/filesystem-free, taking
    already-loaded `cards`/`lexicon_entries` -- see its own module
    docstring), so this CLI -- the first real disk-touching caller of
    that module -- is the natural place for the one loader it needs.
    Returns `[]` if the file doesn't exist, matching every sibling
    loader's own graceful-missing-file convention rather than crashing
    (shouldn't happen in the real repo -- Phase 3 seeded 30 real entries
    -- but a fresh/test checkout may not have one).
    """
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def run_audit(
    *,
    now: datetime | None = None,
    session=None,
    cards_dir: Path = CARDS_DIR,
    lexicon_path: Path = LEXICON_PATH,
    ledger_path: Path = LEDGER_PATH,
    backlog_path: Path = BACKLOG_PATH,
    append_to_backlog: bool = True,
    hn_items: Sequence[Item] | None = None,
) -> dict[str, Any]:
    """Run every Phase 5 checker once and return the assembled
    `schemas/audit.schema.json`-shaped report.

    This is the one function both `main()` (the real `python -m
    auditor.cli run` entrypoint) and any test/caller that wants the full
    pipeline without going through argparse should call.

    Cards, the lexicon, and the ledger are each loaded from disk exactly
    once here and threaded into every checker that needs them (`link_rot`,
    `lexicon`, `duplicates`, and `missed_stories` all consume `cards`) --
    avoiding several separate re-reads of the same `content/cards/*.json`
    files a naive "let every `audit_*(cards=None)` load its own copy" call
    would do.

    `hn_items` is threaded straight through to `auditor.missed_story.
    audit_missed_stories`'s own `hn_items` parameter, unchanged -- passing
    an explicit list (e.g. `[]` or a small fixture list) keeps a caller/
    test fully offline and deterministic; leaving it `None` (the real
    `python -m auditor.cli run` default) triggers that function's own live
    top-20-HN-stories-of-the-week fetch, exactly as it would for a genuine
    weekly audit run.

    `append_to_backlog` (default `True`, matching `audit.yml`'s own real
    intended behavior of auto-appending findings every run) can be set
    `False` for a dry run that computes everything but never touches
    `IMPROVEMENT_BACKLOG.md` -- and `backlog_path` can point anywhere
    (e.g. a scratch file), so a caller can exercise the real end-to-end
    pipeline without mutating the real, committed backlog file. The
    returned report's own `findings_appended_to_backlog` is `0` whenever
    `append_to_backlog` is `False` -- that field's contract is "how many
    findings were *actually* written to the backlog file this run," so a
    dry run honestly reports zero even if findings were computed.
    """
    now = now or datetime.now(timezone.utc)
    session = session or build_session()

    cards = load_cards(cards_dir)
    lexicon_entries = load_lexicon(lexicon_path)
    ledger = load_ledger(ledger_path)

    link_rot = audit_link_rot(cards=cards, session=session)
    lexicon = audit_lexicon(cards, lexicon_entries)
    verifier_trend = audit_trend(today=now.date())
    missed_stories = audit_missed_stories(
        hn_items=hn_items, cards=cards, ledger=ledger, session=session, now=now
    )
    duplicates = audit_duplicates(cards=cards)

    findings = derive_findings(
        link_rot=link_rot,
        lexicon=lexicon,
        verifier_trend=verifier_trend,
        missed_stories=missed_stories,
        duplicates=duplicates,
        has_cards=bool(cards),
    )

    findings_appended = 0
    if append_to_backlog:
        findings_appended = append_findings_to_backlog(
            findings,
            run_id=make_run_id(now),
            generated_at=now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            path=backlog_path,
        )

    return build_report(
        link_rot=link_rot,
        lexicon=lexicon,
        verifier_trend=verifier_trend,
        missed_stories=missed_stories,
        duplicates=duplicates,
        now=now,
        findings_appended_to_backlog=findings_appended,
    )


def _format_summary(report: dict[str, Any]) -> str:
    lr = report["link_rot"]["counts"]
    ms = report["missed_stories"]["counts"]
    return (
        f"run_id={report['run_id']} "
        f"link_rot(ok={lr['ok']} dead={lr['dead']} unreachable={lr['unreachable']}) "
        f"lexicon(coverage_gaps={len(report['lexicon']['coverage_gaps'])} "
        f"orphans={len(report['lexicon']['orphans'])}) "
        f"verifier_trend={report['verifier_trend']['trend']} "
        f"missed_stories(covered={ms['covered']} "
        f"seen_but_dropped={ms['seen_but_dropped']} missed={ms['missed']}) "
        f"duplicates={len(report['duplicates']['duplicate_pairs'])} "
        f"findings_appended_to_backlog={report['findings_appended_to_backlog']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auditor.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run every Phase 5 weekly check once."
    )
    run_parser.add_argument(
        "--out",
        type=Path,
        default=AUDIT_LATEST_PATH,
        help="Where to write the assembled report (default: data/audit/latest.json).",
    )
    run_parser.add_argument(
        "--backlog-path",
        type=Path,
        default=BACKLOG_PATH,
        help=(
            "IMPROVEMENT_BACKLOG.md path to append findings to "
            "(default: the real repo file)."
        ),
    )
    run_parser.add_argument(
        "--no-backlog-append",
        action="store_true",
        help="Compute findings but never write them to IMPROVEMENT_BACKLOG.md (dry run).",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        report = run_audit(
            backlog_path=args.backlog_path,
            append_to_backlog=not args.no_backlog_append,
        )
        save_report(report, path=args.out)
        print(_format_summary(report))
        print(f"wrote {args.out}")
        return 0

    return 1  # pragma: no cover - argparse's `required=True` already exits earlier


if __name__ == "__main__":
    sys.exit(main())
