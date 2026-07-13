"""Tests for `auditor/report.py` -- the Phase 5 weekly audit report
assembler (`schemas/audit.schema.json`'s own producer).

Exercises `make_run_id`/`compute_window` (pure date/time helpers, always
against an explicit `now`, never the real clock), `build_report` (pure
assembly of the five checkers' own already-computed dicts into one
schema-shaped envelope), and `save_report`/`load_report` (the only two
disk-touching functions in this module) against a `tmp_path`, plus one
integration-flavored smoke test running every real Phase 5 checker
against this repo's actual, currently-empty `content/cards/`/
`data/verifier_stats.json` state and confirming the assembled report is
schema-valid end to end.

Imported the same way every other `tests/test_auditor_*.py` file already
imports its own module -- `auditor/` ships with an explicit `__init__.py`
as of this turn (see `auditor/__init__.py`'s own docstring for why), but
even before that, Python's implicit namespace-package handling already
made `from auditor.report import ...` resolve directly with no
`sys.path` manipulation, given `python -m pytest` is run from the repo
root (this repo's own established convention).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from jsonschema import ValidationError

import auditor.report as report_mod
from auditor.duplicates import audit_duplicates
from auditor.lexicon_audit import audit_lexicon
from auditor.linkrot import (
    audit_company_hijacked_links,
    audit_hijacked_links,
    audit_link_rot,
)
from auditor.missed_story import audit_missed_stories
from auditor.profile_staleness import audit_profile_staleness
from auditor.trend import audit_trend
from watcher.schema_validate import validate

NOW = datetime(2026, 7, 11, 23, 30, 0, tzinfo=timezone.utc)

EMPTY_LINK_ROT = {
    "checked_at": "2026-07-11T23:30:01Z",
    "total_urls": 0,
    "counts": {"ok": 0, "dead": 0, "unreachable": 0},
    "results": [],
}
EMPTY_LEXICON = {"coverage_gaps": [], "orphans": []}
EMPTY_TREND = {
    "as_of": "2026-07-11",
    "rolling_7d_pass_rate": None,
    "rolling_30d_pass_rate": None,
    "prior_week_pass_rate": None,
    "trend": "insufficient_data",
}
EMPTY_MISSED_STORIES = {
    "checked_at": "2026-07-11T23:30:02Z",
    "window_hours": 168,
    "top_n": 20,
    "total_checked": 0,
    "counts": {"covered": 0, "seen_but_dropped": 0, "missed": 0},
    "missed_stories": [],
    "seen_but_dropped_stories": [],
    "results": [],
}
EMPTY_DUPLICATES = {"duplicate_pairs": []}
EMPTY_HIJACKED_LINKS = {
    "checked_at": "2026-07-11T23:30:03Z",
    "total_urls": 0,
    "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
    "results": [],
}
EMPTY_COMPANY_HIJACKED_LINKS = {
    "checked_at": "2026-07-11T23:30:04Z",
    "total_urls": 0,
    "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
    "results": [],
}
EMPTY_PROFILE_STALENESS = {
    "checked_at": "2026-07-11T23:30:05Z",
    "stale_days_threshold": 45,
    "total_companies": 0,
    "counts": {"stale": 0, "fresh": 0},
    "results": [],
}


# ---------------------------------------------------------------------------
# make_run_id / compute_window
# ---------------------------------------------------------------------------


def test_make_run_id_is_deterministic_given_now():
    assert report_mod.make_run_id(NOW) == "audit-20260711T233000Z"
    # Calling twice with the identical `now` gives the identical id -- no
    # hidden randomness (e.g. no UUID) anywhere in this function.
    assert report_mod.make_run_id(NOW) == report_mod.make_run_id(NOW)


def test_make_run_id_normalizes_a_non_utc_timezone():
    from datetime import timedelta, timezone as tz

    hkt = datetime(2026, 7, 12, 7, 30, 0, tzinfo=tz(timedelta(hours=8)))
    # 2026-07-12T07:30:00+08:00 == 2026-07-11T23:30:00Z
    assert report_mod.make_run_id(hkt) == "audit-20260711T233000Z"


def test_compute_window_default_seven_days_inclusive_of_end():
    window = report_mod.compute_window(NOW)
    assert window == {"days": 7, "start": "2026-07-05", "end": "2026-07-11"}


def test_compute_window_custom_days():
    window = report_mod.compute_window(NOW, days=1)
    assert window == {"days": 1, "start": "2026-07-11", "end": "2026-07-11"}


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def _build_empty_report(**overrides):
    kwargs = dict(
        link_rot=EMPTY_LINK_ROT,
        lexicon=EMPTY_LEXICON,
        verifier_trend=EMPTY_TREND,
        missed_stories=EMPTY_MISSED_STORIES,
        duplicates=EMPTY_DUPLICATES,
        hijacked_links=EMPTY_HIJACKED_LINKS,
        company_hijacked_links=EMPTY_COMPANY_HIJACKED_LINKS,
        profile_staleness=EMPTY_PROFILE_STALENESS,
        now=NOW,
    )
    kwargs.update(overrides)
    return report_mod.build_report(**kwargs)


def test_build_report_assembles_every_field():
    rep = _build_empty_report()
    assert rep["version"] == 1
    assert rep["run_id"] == "audit-20260711T233000Z"
    assert rep["generated_at"] == "2026-07-11T23:30:00Z"
    assert rep["window"] == {"days": 7, "start": "2026-07-05", "end": "2026-07-11"}
    assert rep["link_rot"] is EMPTY_LINK_ROT
    assert rep["lexicon"] is EMPTY_LEXICON
    assert rep["verifier_trend"] is EMPTY_TREND
    assert rep["missed_stories"] is EMPTY_MISSED_STORIES
    assert rep["duplicates"] is EMPTY_DUPLICATES
    assert rep["hijacked_links"] is EMPTY_HIJACKED_LINKS
    assert rep["company_hijacked_links"] is EMPTY_COMPANY_HIJACKED_LINKS
    assert rep["profile_staleness"] is EMPTY_PROFILE_STALENESS
    assert rep["findings_appended_to_backlog"] == 0


def test_build_report_never_mutates_or_recomputes_checker_dicts():
    """The five checker dicts are passed through by identity, never
    copied/re-derived -- build_report is pure assembly, not a second,
    parallel computation of anything a checker already computed."""
    rep = _build_empty_report()
    assert rep["link_rot"] is EMPTY_LINK_ROT
    assert rep["duplicates"] is EMPTY_DUPLICATES


def test_build_report_honors_explicit_findings_appended_count():
    rep = _build_empty_report(findings_appended_to_backlog=5)
    assert rep["findings_appended_to_backlog"] == 5


def test_build_report_is_schema_valid():
    rep = _build_empty_report()
    validate(rep, "audit")  # must not raise


# ---------------------------------------------------------------------------
# save_report / load_report
# ---------------------------------------------------------------------------


def test_save_report_writes_schema_valid_json(tmp_path: Path):
    rep = _build_empty_report()
    out = tmp_path / "latest.json"
    report_mod.save_report(rep, path=out)

    assert out.is_file()
    with out.open("r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk == rep
    validate(on_disk, "audit")


def test_save_report_creates_parent_directories(tmp_path: Path):
    rep = _build_empty_report()
    out = tmp_path / "nested" / "audit" / "latest.json"
    report_mod.save_report(rep, path=out)
    assert out.is_file()


def test_save_report_rejects_a_malformed_report(tmp_path: Path):
    rep = _build_empty_report()
    del rep["run_id"]  # required field
    out = tmp_path / "latest.json"
    with pytest.raises(ValidationError):
        report_mod.save_report(rep, path=out)
    assert not out.exists()


def test_load_report_round_trips(tmp_path: Path):
    rep = _build_empty_report(findings_appended_to_backlog=2)
    out = tmp_path / "latest.json"
    report_mod.save_report(rep, path=out)

    loaded = report_mod.load_report(path=out)
    assert loaded == rep


def test_load_report_returns_none_for_missing_file(tmp_path: Path):
    missing = tmp_path / "does" / "not" / "exist.json"
    assert report_mod.load_report(path=missing) is None


def test_load_report_raises_on_schema_invalid_file(tmp_path: Path):
    out = tmp_path / "latest.json"
    with out.open("w", encoding="utf-8") as f:
        json.dump({"version": 1}, f)  # missing everything else required
    with pytest.raises(ValidationError):
        report_mod.load_report(path=out)


# ---------------------------------------------------------------------------
# Integration: every real Phase 5 checker -> build_report -> schema-valid,
# against this repo's actual, currently-empty content/cards state.
# ---------------------------------------------------------------------------


def test_end_to_end_against_real_empty_repo_state_is_schema_valid():
    """Runs every real checker (with `cards=[]`, matching this repo's
    real current state -- no analyst run has happened yet) and confirms
    `build_report` produces a schema-valid report even in that all-empty
    edge case. `auditor.missed_story.audit_missed_stories` is the one
    checker that would otherwise reach out to the live HN Algolia API by
    default, so its `hn_items` is passed explicitly here (empty) to keep
    this test fully offline/deterministic, matching this project's "no
    live network calls in the default test run" rule.
    """
    link_rot = audit_link_rot(cards=[])
    lexicon = audit_lexicon([], [])
    verifier_trend = audit_trend(stats={"version": 1, "runs": []}, today=date(2026, 7, 11))
    missed_stories = audit_missed_stories(
        hn_items=[], cards=[], ledger={"version": 1, "entries": {}}
    )
    duplicates = audit_duplicates(cards=[])
    hijacked_links = audit_hijacked_links(cards=[])
    company_hijacked_links = audit_company_hijacked_links(companies=[])
    profile_staleness = audit_profile_staleness(companies=[], today=date(2026, 7, 11))

    rep = report_mod.build_report(
        link_rot=link_rot,
        lexicon=lexicon,
        verifier_trend=verifier_trend,
        missed_stories=missed_stories,
        duplicates=duplicates,
        hijacked_links=hijacked_links,
        company_hijacked_links=company_hijacked_links,
        profile_staleness=profile_staleness,
        now=NOW,
    )

    validate(rep, "audit")  # must not raise
    assert rep["link_rot"]["total_urls"] == 0
    assert rep["lexicon"] == {"coverage_gaps": [], "orphans": []}
    assert rep["verifier_trend"]["trend"] == "insufficient_data"
    assert rep["missed_stories"]["total_checked"] == 0
    assert rep["duplicates"]["duplicate_pairs"] == []
    assert rep["hijacked_links"]["total_urls"] == 0
    assert rep["company_hijacked_links"]["total_urls"] == 0
    assert rep["profile_staleness"]["total_companies"] == 0
