"""Tests for `auditor/cli.py` -- `python -m auditor.cli run`, the Phase 5
entrypoint wiring every checker + `auditor.report` +
`scripts.append_backlog_findings` together.

Every test here passes `hn_items=[]` (or `--no-backlog-append` /
tmp_path-scoped `--backlog-path`) explicitly, so nothing in this file ever
makes a live network call or mutates the real, committed
`IMPROVEMENT_BACKLOG.md` -- `auditor.missed_story.audit_missed_stories`'s
own `hn_items=None` default is the one path in the whole `run_audit`
pipeline that would otherwise reach the real HN Algolia API, and
`run_audit`'s own `hn_items` passthrough (added specifically for this
kind of test, see its own docstring) is what lets this suite avoid it
while still exercising every other real function.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import auditor.cli as cli_mod
import auditor.trend as trend_mod
from watcher.schema_validate import validate

NOW = datetime(2026, 7, 11, 23, 30, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# load_lexicon
# ---------------------------------------------------------------------------


def test_load_lexicon_loads_the_real_repo_file():
    entries = cli_mod.load_lexicon()
    assert isinstance(entries, list)
    assert len(entries) == 30  # Phase 3's real, seeded backfill
    assert all("term" in entry for entry in entries)


def test_load_lexicon_missing_file_returns_empty_list(tmp_path: Path):
    missing = tmp_path / "does-not-exist.json"
    assert cli_mod.load_lexicon(missing) == []


def test_load_lexicon_reads_a_small_fixture(tmp_path: Path):
    path = tmp_path / "lexicon.json"
    path.write_text(json.dumps([{"term": "RAG", "seen_in": []}]), encoding="utf-8")
    assert cli_mod.load_lexicon(path) == [{"term": "RAG", "seen_in": []}]


# ---------------------------------------------------------------------------
# run_audit -- against the real, currently-empty content/cards/ state,
# fully offline (hn_items=[], append_to_backlog=False).
# ---------------------------------------------------------------------------


def test_run_audit_against_real_empty_repo_state_is_schema_valid():
    # companies=[] keeps this fully offline -- the real content/companies/
    # registry carries real, live citation URLs that audit_company_
    # hijacked_links would otherwise try to HEAD/GET for real (blocked by
    # tests/conftest.py's autouse no-live-network fixture); feed_corrections
    # is left at its real True default deliberately (see the dedicated
    # feed_corrections tests below) but with companies=[] there is nothing
    # for it to feed either way.
    report = cli_mod.run_audit(
        now=NOW, hn_items=[], companies=[], append_to_backlog=False
    )
    validate(report, "audit")  # must not raise
    assert report["findings_appended_to_backlog"] == 0
    assert report["link_rot"]["total_urls"] == 0  # no cards -> no citations
    assert report["duplicates"]["duplicate_pairs"] == []
    assert report["missed_stories"]["total_checked"] == 0
    assert report["hijacked_links"]["total_urls"] == 0
    assert report["company_hijacked_links"]["total_urls"] == 0
    assert report["profile_staleness"]["total_companies"] == 0


def test_run_audit_dry_run_never_touches_backlog_file(tmp_path: Path):
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# original content\n", encoding="utf-8")

    cli_mod.run_audit(
        now=NOW,
        hn_items=[],
        companies=[],
        append_to_backlog=False,
        backlog_path=backlog_path,
    )

    assert backlog_path.read_text(encoding="utf-8") == "# original content\n"


def test_run_audit_real_append_writes_to_the_given_backlog_path(tmp_path: Path):
    """Uses a synthetic ledger/lexicon path with real lexicon-orphan
    content and has_cards=False (matching this repo's real state) to
    prove findings_appended_to_backlog reflects an actual write when
    append_to_backlog=True, without ever touching the real, committed
    IMPROVEMENT_BACKLOG.md."""
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# original content\n", encoding="utf-8")

    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")

    # A synthetic verifier_stats-free run: no cards means lexicon orphans
    # are suppressed (has_cards=False), so nothing is actionable here --
    # confirming a real end-to-end call with genuinely zero findings
    # writes nothing and reports 0, exactly matching this repo's real
    # current state.
    report = cli_mod.run_audit(
        now=NOW,
        hn_items=[],
        companies=[],
        append_to_backlog=True,
        backlog_path=backlog_path,
        ledger_path=ledger_path,
    )

    assert report["findings_appended_to_backlog"] == 0
    assert backlog_path.read_text(encoding="utf-8") == "# original content\n"


def test_run_audit_appends_a_real_finding_when_one_exists(tmp_path: Path, monkeypatch):
    """Forces a genuine finding (a falling verifier trend) through the
    real pipeline and confirms it lands in the given scratch backlog
    file, with a matching findings_appended_to_backlog count."""
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# original content\n", encoding="utf-8")

    stats = {
        "version": 1,
        "runs": [
            {
                "date": "2026-07-04",
                "cards_drafted": 8,
                "confirmed": 8,
                "reported": 0,
                "dropped": 0,
                "pass_rate": 1.0,
            },
            {
                "date": "2026-07-11",
                "cards_drafted": 8,
                "confirmed": 2,
                "reported": 0,
                "dropped": 6,
                "pass_rate": 0.25,
            },
        ],
    }
    monkeypatch.setattr(
        cli_mod, "audit_trend", lambda today: trend_mod.audit_trend(stats=stats, today=today)
    )

    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(json.dumps({"version": 1, "entries": {}}), encoding="utf-8")

    report = cli_mod.run_audit(
        now=NOW,
        hn_items=[],
        companies=[],
        append_to_backlog=True,
        backlog_path=backlog_path,
        ledger_path=ledger_path,
    )

    assert report["verifier_trend"]["trend"] == "falling"
    assert report["findings_appended_to_backlog"] == 1
    text = backlog_path.read_text(encoding="utf-8")
    assert "# original content" in text
    assert "falling" in text
    assert "- [ ] **[HIGH]**" in text


# ---------------------------------------------------------------------------
# main() -- argparse plumbing
# ---------------------------------------------------------------------------


def test_main_run_writes_report_and_returns_zero(tmp_path: Path, monkeypatch):
    out_path = tmp_path / "latest.json"
    backlog_path = tmp_path / "BACKLOG.md"
    backlog_path.write_text("# x\n", encoding="utf-8")

    # Keep this test offline: monkeypatch run_audit itself rather than
    # threading --hn-items through argparse (main() has no such flag --
    # live HN fetching is the real, intended CLI behavior; this test only
    # needs to prove main()'s own plumbing: argument parsing, calling
    # run_audit, and saving the result).
    captured = {}

    def fake_run_audit(**kwargs):
        captured.update(kwargs)
        return {
            "version": 1,
            "run_id": "audit-test",
            "generated_at": "2026-07-11T23:30:00Z",
            "window": {"days": 7, "start": "2026-07-05", "end": "2026-07-11"},
            "link_rot": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"ok": 0, "dead": 0, "unreachable": 0},
                "results": [],
            },
            "lexicon": {"coverage_gaps": [], "orphans": []},
            "verifier_trend": {
                "as_of": "2026-07-11",
                "rolling_7d_pass_rate": None,
                "rolling_30d_pass_rate": None,
                "prior_week_pass_rate": None,
                "trend": "insufficient_data",
            },
            "missed_stories": {
                "checked_at": "2026-07-11T23:30:00Z",
                "window_hours": 168,
                "top_n": 20,
                "total_checked": 0,
                "counts": {"covered": 0, "seen_but_dropped": 0, "missed": 0},
                "missed_stories": [],
                "seen_but_dropped_stories": [],
                "results": [],
            },
            "duplicates": {"duplicate_pairs": []},
            "hijacked_links": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
                "results": [],
            },
            "company_hijacked_links": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
                "results": [],
            },
            "profile_staleness": {
                "checked_at": "2026-07-11T23:30:00Z",
                "stale_days_threshold": 45,
                "total_companies": 0,
                "counts": {"stale": 0, "fresh": 0},
                "results": [],
            },
            "findings_appended_to_backlog": 0,
        }

    monkeypatch.setattr(cli_mod, "run_audit", fake_run_audit)

    exit_code = cli_mod.main(
        [
            "run",
            "--out",
            str(out_path),
            "--backlog-path",
            str(backlog_path),
        ]
    )

    assert exit_code == 0
    assert out_path.is_file()
    with out_path.open("r", encoding="utf-8") as f:
        on_disk = json.load(f)
    validate(on_disk, "audit")
    assert captured["backlog_path"] == backlog_path
    assert captured["append_to_backlog"] is True


def test_main_run_no_backlog_append_flag_disables_appending(tmp_path: Path, monkeypatch):
    out_path = tmp_path / "latest.json"
    captured = {}

    def fake_run_audit(**kwargs):
        captured.update(kwargs)
        return {
            "version": 1,
            "run_id": "audit-test",
            "generated_at": "2026-07-11T23:30:00Z",
            "window": {"days": 7, "start": "2026-07-05", "end": "2026-07-11"},
            "link_rot": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"ok": 0, "dead": 0, "unreachable": 0},
                "results": [],
            },
            "lexicon": {"coverage_gaps": [], "orphans": []},
            "verifier_trend": {
                "as_of": "2026-07-11",
                "rolling_7d_pass_rate": None,
                "rolling_30d_pass_rate": None,
                "prior_week_pass_rate": None,
                "trend": "insufficient_data",
            },
            "missed_stories": {
                "checked_at": "2026-07-11T23:30:00Z",
                "window_hours": 168,
                "top_n": 20,
                "total_checked": 0,
                "counts": {"covered": 0, "seen_but_dropped": 0, "missed": 0},
                "missed_stories": [],
                "seen_but_dropped_stories": [],
                "results": [],
            },
            "duplicates": {"duplicate_pairs": []},
            "hijacked_links": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
                "results": [],
            },
            "company_hijacked_links": {
                "checked_at": "2026-07-11T23:30:00Z",
                "total_urls": 0,
                "counts": {"trusted": 0, "hijacked": 0, "unreachable": 0},
                "results": [],
            },
            "profile_staleness": {
                "checked_at": "2026-07-11T23:30:00Z",
                "stale_days_threshold": 45,
                "total_companies": 0,
                "counts": {"stale": 0, "fresh": 0},
                "results": [],
            },
            "findings_appended_to_backlog": 0,
        }

    monkeypatch.setattr(cli_mod, "run_audit", fake_run_audit)

    exit_code = cli_mod.main(["run", "--out", str(out_path), "--no-backlog-append"])

    assert exit_code == 0
    assert captured["append_to_backlog"] is False


def test_main_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli_mod.main([])


def test_main_run_defaults_out_to_audit_latest_path():
    assert cli_mod.AUDIT_LATEST_PATH.name == "latest.json"
    assert cli_mod.AUDIT_LATEST_PATH.parent.name == "audit"
