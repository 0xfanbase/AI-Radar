"""Auditor CLI entrypoint: ``python -m auditor.cli run``.

Wires every Phase 5 checker (`auditor.linkrot`, `auditor.lexicon_audit`,
`auditor.trend`, `auditor.missed_story`, `auditor.duplicates`) plus every
Phase 9 checker (`auditor.linkrot.audit_hijacked_links`/
`audit_company_hijacked_links`, `auditor.profile_staleness`) plus
`auditor.report`, `scripts.append_backlog_findings`, and
`auditor.corrections_feed` together into CLAUDE.md's `audit.yml` "weekly"
bullet -- exactly the way `watcher/cli.py`'s own `python -m watcher.cli
run` wires every Phase 1 pure-code piece into the daily watcher pass.
Nothing in this module talks to an LLM: every check plus the
backlog-append/pending-corrections-feed steps is deterministic Python.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from auditor.corrections_feed import (
    build_hijack_candidates,
    build_staleness_candidates,
    feed_pending_corrections,
)
from auditor.duplicates import audit_duplicates
from auditor.lexicon_audit import audit_lexicon
from auditor.linkrot import (
    CARDS_DIR,
    COMPANIES_DIR,
    audit_company_hijacked_links,
    audit_hijacked_links,
    audit_link_rot,
    load_cards,
)
from auditor.missed_story import LEDGER_PATH, audit_missed_stories, load_ledger
from auditor.profile_staleness import audit_profile_staleness
from auditor.report import AUDIT_LATEST_PATH, build_report, make_run_id, save_report
from auditor.trend import audit_trend
from scripts.append_backlog_findings import (
    BACKLOG_PATH,
    append_findings_to_backlog,
    derive_findings,
)
from scripts.pending_corrections import PENDING_CORRECTIONS_PATH
from scripts.plan_run import load_company_registry
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
    companies_dir: Path = COMPANIES_DIR,
    companies: list[dict[str, Any]] | None = None,
    lexicon_path: Path = LEXICON_PATH,
    ledger_path: Path = LEDGER_PATH,
    backlog_path: Path = BACKLOG_PATH,
    pending_corrections_path: Path = PENDING_CORRECTIONS_PATH,
    append_to_backlog: bool = True,
    feed_corrections: bool = True,
    hn_items: Sequence[Item] | None = None,
) -> dict[str, Any]:
    """Run every checker once (the original Phase 5 five, plus Phase 9's
    `hijacked_links`/`company_hijacked_links`/`profile_staleness`) and
    return the assembled `schemas/audit.schema.json`-shaped report.

    This is the one function both `main()` (the real `python -m
    auditor.cli run` entrypoint) and any test/caller that wants the full
    pipeline without going through argparse should call.

    Cards, companies, the lexicon, and the ledger are each loaded from
    disk exactly once here and threaded into every checker that needs
    them (`link_rot`/`hijacked_links` consume `cards`; `company_hijacked_
    links`/`profile_staleness` consume `companies`) -- avoiding several
    separate re-reads of the same `content/cards/*.json` /
    `content/companies/*.json` files a naive "let every
    `audit_*(cards=None)` load its own copy" call would do.

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

    `companies` (mirroring `hn_items`'s own "explicit list keeps a test
    fully offline" convention -- real `content/companies/*.json` profiles
    carry real, live citation URLs that `audit_company_hijacked_links`
    would otherwise try to HEAD/GET for real): passing an explicit list
    (e.g. `[]`) keeps a caller/test fully offline and deterministic;
    leaving it `None` (the real `python -m auditor.cli run` default)
    loads the real registry from `companies_dir` via
    `scripts.plan_run.load_company_registry`, exactly as a genuine weekly
    audit run needs.

    `feed_corrections` (default `True`, mirroring `append_to_backlog`'s
    own default) governs whether stale-profile/hijacked-company-citation
    findings are actually appended to `data/pending_corrections.json`
    (via `auditor.corrections_feed.feed_pending_corrections`) -- `False`
    for a dry run against `pending_corrections_path` left untouched, same
    "compute everything, write nothing" shape `append_to_backlog=False`
    already gives for the backlog file. This function does not report a
    count of corrections fed anywhere in the returned report (unlike
    `findings_appended_to_backlog`) -- see `auditor.corrections_feed`'s
    own module docstring for why this is a separate, additive feed rather
    than a `schemas/audit.schema.json` field.
    """
    now = now or datetime.now(timezone.utc)
    session = session or build_session()

    cards = load_cards(cards_dir)
    if companies is None:
        companies = load_company_registry(companies_dir)
    lexicon_entries = load_lexicon(lexicon_path)
    ledger = load_ledger(ledger_path)

    link_rot = audit_link_rot(cards=cards, session=session)
    lexicon = audit_lexicon(cards, lexicon_entries)
    verifier_trend = audit_trend(today=now.date())
    missed_stories = audit_missed_stories(
        hn_items=hn_items, cards=cards, ledger=ledger, session=session, now=now
    )
    duplicates = audit_duplicates(cards=cards)
    hijacked_links = audit_hijacked_links(cards=cards, session=session)
    company_hijacked_links = audit_company_hijacked_links(
        companies=companies, session=session
    )
    profile_staleness = audit_profile_staleness(companies=companies, today=now.date())

    if feed_corrections:
        flagged_at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates = build_staleness_candidates(
            profile_staleness, companies, flagged_at=flagged_at
        ) + build_hijack_candidates(company_hijacked_links, flagged_at=flagged_at)
        feed_pending_corrections(candidates, path=pending_corrections_path)

    findings = derive_findings(
        link_rot=link_rot,
        lexicon=lexicon,
        verifier_trend=verifier_trend,
        missed_stories=missed_stories,
        duplicates=duplicates,
        hijacked_links=hijacked_links,
        company_hijacked_links=company_hijacked_links,
        profile_staleness=profile_staleness,
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
        hijacked_links=hijacked_links,
        company_hijacked_links=company_hijacked_links,
        profile_staleness=profile_staleness,
        now=now,
        findings_appended_to_backlog=findings_appended,
    )


def _format_summary(report: dict[str, Any]) -> str:
    lr = report["link_rot"]["counts"]
    ms = report["missed_stories"]["counts"]
    hl = report["hijacked_links"]["counts"]
    chl = report["company_hijacked_links"]["counts"]
    ps = report["profile_staleness"]["counts"]
    return (
        f"run_id={report['run_id']} "
        f"link_rot(ok={lr['ok']} dead={lr['dead']} unreachable={lr['unreachable']}) "
        f"hijacked_links(trusted={hl['trusted']} hijacked={hl['hijacked']} "
        f"unreachable={hl['unreachable']}) "
        f"company_hijacked_links(trusted={chl['trusted']} hijacked={chl['hijacked']} "
        f"unreachable={chl['unreachable']}) "
        f"profile_staleness(stale={ps['stale']} fresh={ps['fresh']}) "
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
        "run", help="Run every weekly check once (Phase 5 + Phase 9)."
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
    run_parser.add_argument(
        "--pending-corrections-path",
        type=Path,
        default=PENDING_CORRECTIONS_PATH,
        help=(
            "data/pending_corrections.json path to feed with target_type: "
            "'company' candidates (default: the real repo file)."
        ),
    )
    run_parser.add_argument(
        "--no-corrections-feed",
        action="store_true",
        help=(
            "Compute stale-profile/hijacked-citation findings but never write "
            "them to data/pending_corrections.json (dry run)."
        ),
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        report = run_audit(
            backlog_path=args.backlog_path,
            append_to_backlog=not args.no_backlog_append,
            pending_corrections_path=args.pending_corrections_path,
            feed_corrections=not args.no_corrections_feed,
        )
        save_report(report, path=args.out)
        print(_format_summary(report))
        print(f"wrote {args.out}")
        return 0

    return 1  # pragma: no cover - argparse's `required=True` already exits earlier


if __name__ == "__main__":
    sys.exit(main())
