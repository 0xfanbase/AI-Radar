"""Tests for site/builders/method.py + site/templates/method.html --
the Method & Audit page (Phase 4, build-plan section 5).

The important edge case this file exists to prove: `data/audit/latest.json`
does not exist yet in this repo (Phase 5's `audit.yml` has never run), and
site/builders/method.py must render an honest placeholder for that --
never raise `FileNotFoundError`, never crash the page build. Every test in
the "MISSING data/audit/latest.json" section below exercises that path
explicitly, both at the loader level and all the way through a full page
render.

Also exercises the REAL, committed `data/ledger.json` (104 entries, all
`status: "queued"` -- no analyst run has happened for real yet) and
`data/verifier_stats.json` (`runs: []`) for the "basic pipeline stats"
half of the page, plus synthetic fixtures for the paths the real,
current-state data doesn't happen to exercise (a non-empty
verifier_stats run, and -- defensively -- a hypothetical present
`data/audit/latest.json`).

Loaded by explicit file path (matching `tests/test_board_builder.py`'s
own convention), since `site/` is deliberately not an importable package
-- see IMPROVEMENT_BACKLOG.md.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from markupsafe import escape

REPO_ROOT = Path(__file__).resolve().parent.parent
METHOD_BUILDER_PATH = REPO_ROOT / "site" / "builders" / "method.py"
LEDGER_CONTENT_PATH = REPO_ROOT / "data" / "ledger.json"
VERIFIER_STATS_CONTENT_PATH = REPO_ROOT / "data" / "verifier_stats.json"
AUDIT_LATEST_CONTENT_PATH = REPO_ROOT / "data" / "audit" / "latest.json"


def _load_module_by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


method = _load_module_by_path("frontier_wire_site_builders_method", METHOD_BUILDER_PATH)


def _load_real_ledger() -> dict:
    with LEDGER_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_real_verifier_stats() -> dict:
    with VERIFIER_STATS_CONTENT_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_LEDGER = _load_real_ledger()
REAL_VERIFIER_STATS = _load_real_verifier_stats()


# ---------------------------------------------------------------------------
# THE important edge case: MISSING data/audit/latest.json
# ---------------------------------------------------------------------------


def test_data_audit_latest_json_does_not_exist_in_this_repo_yet():
    # This is the precondition the rest of this section proves method.py
    # handles gracefully -- if this ever starts failing because Phase 5's
    # audit.yml has landed and produced a real file, that's good news,
    # but it means this test (and the module's own docstring) needs an
    # update, not that the graceful-missing-file path stops mattering.
    assert not AUDIT_LATEST_CONTENT_PATH.is_file()


def test_load_audit_latest_returns_none_for_the_real_missing_path():
    assert method.load_audit_latest() is None


def test_load_audit_latest_returns_none_for_an_explicit_nonexistent_path(tmp_path):
    missing_path = tmp_path / "does" / "not" / "exist.json"
    assert method.load_audit_latest(missing_path) is None


def test_load_audit_latest_does_not_raise_file_not_found_error():
    # The actual regression this test guards against: a naive
    # `path.open()` with no existence check would raise
    # FileNotFoundError here instead of returning None.
    try:
        result = method.load_audit_latest()
    except FileNotFoundError:
        pytest.fail(
            "load_audit_latest() must return None for a missing "
            "data/audit/latest.json, not raise FileNotFoundError"
        )
    assert result is None


def test_build_audit_section_for_none_reports_unavailable_with_honest_message():
    section = method.build_audit_section(None)
    assert section["available"] is False
    assert section["message"] == method.NO_AUDIT_MESSAGE
    assert "Phase 5" in section["message"]
    assert "audit.yml" in section["message"]


def test_build_method_context_with_missing_audit_does_not_crash():
    context = method.build_method_context(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert context["audit"]["available"] is False


def test_render_method_page_with_missing_audit_shows_placeholder_not_a_crash():
    html = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert html.count("<h1") == 1
    # Jinja autoescapes the message (e.g. "hasn't" -> "hasn&#39;t"), so
    # compare against the same escaped form -- matching
    # tests/test_primer_builder.py's own established convention for
    # asserting rendered prose containing apostrophes.
    assert str(escape(method.NO_AUDIT_MESSAGE)) in html
    assert "Phase 5" in html


def test_full_real_environment_render_end_to_end_with_the_actual_missing_file():
    # The full, real, no-mocking path: load every real data file exactly
    # as a caller would (data/audit/latest.json genuinely absent from
    # disk), and confirm the whole page still renders successfully.
    ledger = method.load_ledger()
    verifier_stats = method.load_verifier_stats()
    audit_latest = method.load_audit_latest()
    assert audit_latest is None
    html = method.render_method_page(ledger, verifier_stats, audit_latest)
    assert "No audit has run yet" in html


# ---------------------------------------------------------------------------
# A hypothetical PRESENT data/audit/latest.json -- defensive path, since
# schemas/audit.schema.json doesn't exist yet and this repo has never
# seen a real one.
# ---------------------------------------------------------------------------


def test_load_audit_latest_reads_a_present_file(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"generated_at": "2026-08-01T00:00:00Z", "findings": []}))
    assert method.load_audit_latest(path) == {
        "generated_at": "2026-08-01T00:00:00Z",
        "findings": [],
    }


def test_build_audit_section_for_a_present_file_reports_available():
    audit_latest = {
        "generated_at": "2026-08-01T00:00:00Z",
        "findings": [{"severity": "low"}, {"severity": "medium"}],
    }
    section = method.build_audit_section(audit_latest)
    assert section["available"] is True
    assert section["generated_at"] == "2026-08-01T00:00:00Z"
    assert section["findings_count"] == 2


def test_render_method_page_with_a_present_audit_shows_its_summary():
    audit_latest = {"generated_at": "2026-08-01T00:00:00Z", "findings": [{"a": 1}]}
    html = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, audit_latest)
    assert "No audit has run yet" not in html
    assert "2026-08-01T00:00:00Z" in html
    assert "1 finding" in html


# ---------------------------------------------------------------------------
# Ledger stats -- against REAL data/ledger.json
# ---------------------------------------------------------------------------


def test_build_ledger_stats_against_real_ledger():
    stats = method.build_ledger_stats(REAL_LEDGER)
    assert stats["total_clusters"] == len(REAL_LEDGER["entries"])
    assert stats["total_clusters"] == 104
    # As of this build stage the analyst has never run for real, so every
    # real entry is still queued.
    assert stats["queued"] == 104
    assert stats["published"] == 0
    assert stats["dropped"] == 0


def test_build_ledger_stats_counts_every_status_from_a_synthetic_ledger():
    ledger = {
        "version": 1,
        "entries": {
            "a": {"status": "queued", "card_id": None, "first_seen": "2026-01-01", "last_seen": "2026-01-01", "member_urls": ["https://example.com/a"]},
            "b": {"status": "published", "card_id": "2026-01-01-b", "first_seen": "2026-01-01", "last_seen": "2026-01-01", "member_urls": ["https://example.com/b"]},
            "c": {"status": "dropped", "card_id": None, "first_seen": "2026-01-01", "last_seen": "2026-01-01", "member_urls": ["https://example.com/c"]},
        },
    }
    stats = method.build_ledger_stats(ledger)
    assert stats == {
        "total_clusters": 3,
        "queued": 1,
        "published": 1,
        "dropped": 1,
    }


def test_build_ledger_stats_handles_empty_ledger_gracefully():
    stats = method.build_ledger_stats({"version": 1, "entries": {}})
    assert stats == {"total_clusters": 0, "queued": 0, "published": 0, "dropped": 0}


# ---------------------------------------------------------------------------
# Verifier stats summary -- against REAL (empty) data/verifier_stats.json,
# plus a synthetic non-empty fixture for the path real data doesn't
# exercise yet.
# ---------------------------------------------------------------------------


def test_build_verifier_summary_against_real_empty_runs_has_no_zero_division():
    assert REAL_VERIFIER_STATS["runs"] == []
    summary = method.build_verifier_summary(REAL_VERIFIER_STATS)
    assert summary["total_runs"] == 0
    assert summary["overall_pass_rate"] is None


def test_build_verifier_summary_aggregates_a_synthetic_multi_run_history():
    verifier_stats = {
        "version": 1,
        "runs": [
            {"date": "2026-07-01", "cards_drafted": 5, "confirmed": 3, "reported": 1, "dropped": 1, "pass_rate": 0.8},
            {"date": "2026-07-02", "cards_drafted": 5, "confirmed": 4, "reported": 0, "dropped": 1, "pass_rate": 0.8},
        ],
    }
    summary = method.build_verifier_summary(verifier_stats)
    assert summary["total_runs"] == 2
    assert summary["cards_drafted"] == 10
    assert summary["confirmed"] == 7
    assert summary["reported"] == 1
    assert summary["dropped"] == 2
    assert summary["overall_pass_rate"] == pytest.approx(0.8)


def test_render_method_page_shows_a_pass_rate_only_when_there_are_runs():
    html_no_runs = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert "Overall verifier pass rate" not in html_no_runs

    verifier_stats = {
        "version": 1,
        "runs": [
            {"date": "2026-07-01", "cards_drafted": 4, "confirmed": 2, "reported": 2, "dropped": 0, "pass_rate": 1.0},
        ],
    }
    html_with_runs = method.render_method_page(REAL_LEDGER, verifier_stats, None)
    assert "Overall verifier pass rate" in html_with_runs
    assert "100%" in html_with_runs


# ---------------------------------------------------------------------------
# Full page render -- own-words explanation present, landmarks present
# ---------------------------------------------------------------------------


def test_render_method_page_has_one_h1_and_main_landmark():
    html = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert html.count("<h1") == 1
    assert "Method" in html
    assert 'id="main-content"' in html
    assert "Skip to content" in html


def test_render_method_page_explains_confirmed_reported_and_the_verifier():
    html = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert "CONFIRMED" in html
    assert "REPORTED" in html
    assert "verifier" in html.lower()


def test_render_method_page_shows_ledger_stats():
    html = method.render_method_page(REAL_LEDGER, REAL_VERIFIER_STATS, None)
    assert ">104<" in html
