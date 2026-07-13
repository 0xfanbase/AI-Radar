"""Tests for `auditor/profile_staleness.py` -- Phase 9's weekly
company-profile-staleness check.

Covers `find_stale_companies` (the pure classification core: the exact
"more than `stale_days` days before `today`" bar, ties/missing/
unparseable `last_verified` handling, deterministic `company_id`-sorted
output) and `audit_profile_staleness` (the one disk-touching entry point,
exercised both with an explicit `companies=` list -- this repo's own
established "no real network/disk needed" test convention -- and once
against the real, committed `content/companies/*.json` registry).
"""
from __future__ import annotations

from datetime import date

from auditor import profile_staleness as ps


def _company(company_id: str, last_verified: str | None, name: str | None = None) -> dict:
    return {"id": company_id, "name": name or company_id.title(), "last_verified": last_verified}


# ---------------------------------------------------------------------------
# find_stale_companies
# ---------------------------------------------------------------------------


def test_find_stale_companies_flags_a_company_past_the_threshold():
    companies = [_company("anthropic", "2026-05-01")]
    results = ps.find_stale_companies(companies, date(2026, 7, 13), stale_days=45)
    assert len(results) == 1
    assert results[0].stale is True
    assert results[0].days_stale == (date(2026, 7, 13) - date(2026, 5, 1)).days


def test_find_stale_companies_exactly_at_threshold_is_not_stale():
    """`> stale_days`, not `>=` -- a profile exactly stale_days old is not
    yet stale, matching scripts.plan_run.find_stale_profile_candidate's
    own comparison."""
    today = date(2026, 7, 13)
    last_verified = today.fromordinal(today.toordinal() - 45)
    companies = [_company("anthropic", last_verified.isoformat())]
    results = ps.find_stale_companies(companies, today, stale_days=45)
    assert results[0].days_stale == 45
    assert results[0].stale is False


def test_find_stale_companies_one_day_past_threshold_is_stale():
    today = date(2026, 7, 13)
    last_verified = today.fromordinal(today.toordinal() - 46)
    companies = [_company("anthropic", last_verified.isoformat())]
    results = ps.find_stale_companies(companies, today, stale_days=45)
    assert results[0].days_stale == 46
    assert results[0].stale is True


def test_find_stale_companies_freshly_verified_is_not_stale():
    companies = [_company("anthropic", "2026-07-13")]
    results = ps.find_stale_companies(companies, date(2026, 7, 13), stale_days=45)
    assert results[0].stale is False
    assert results[0].days_stale == 0


def test_find_stale_companies_missing_last_verified_is_not_stale_but_reported():
    companies = [_company("anthropic", None)]
    results = ps.find_stale_companies(companies, date(2026, 7, 13))
    assert len(results) == 1
    assert results[0].stale is False
    assert results[0].days_stale is None
    assert results[0].last_verified is None


def test_find_stale_companies_unparseable_last_verified_is_not_stale_but_reported():
    companies = [_company("anthropic", "not-a-date")]
    results = ps.find_stale_companies(companies, date(2026, 7, 13))
    assert len(results) == 1
    assert results[0].stale is False
    assert results[0].days_stale is None
    assert results[0].last_verified == "not-a-date"


def test_find_stale_companies_missing_name_falls_back_to_id():
    companies = [{"id": "anthropic", "last_verified": "2026-07-13"}]
    results = ps.find_stale_companies(companies, date(2026, 7, 13))
    assert results[0].name == "anthropic"


def test_find_stale_companies_sorted_by_company_id_regardless_of_input_order():
    companies = [
        _company("openai", "2026-07-13"),
        _company("anthropic", "2026-07-13"),
        _company("deepseek", "2026-07-13"),
    ]
    results = ps.find_stale_companies(companies, date(2026, 7, 13))
    assert [r.company_id for r in results] == ["anthropic", "deepseek", "openai"]


def test_find_stale_companies_empty_list():
    assert ps.find_stale_companies([], date(2026, 7, 13)) == []


# ---------------------------------------------------------------------------
# audit_profile_staleness
# ---------------------------------------------------------------------------


def test_audit_profile_staleness_summary_shape():
    companies = [
        _company("anthropic", "2026-07-13"),
        _company("deepseek", "2026-04-01"),
    ]
    report = ps.audit_profile_staleness(companies, today=date(2026, 7, 13))

    assert set(report.keys()) == {
        "checked_at",
        "stale_days_threshold",
        "total_companies",
        "counts",
        "results",
    }
    assert report["stale_days_threshold"] == 45
    assert report["total_companies"] == 2
    assert report["counts"] == {"stale": 1, "fresh": 1}
    by_id = {r["company_id"]: r for r in report["results"]}
    assert by_id["anthropic"]["stale"] is False
    assert by_id["deepseek"]["stale"] is True


def test_audit_profile_staleness_with_no_companies_is_a_clean_zero_report(tmp_path):
    report = ps.audit_profile_staleness(
        [], companies_dir=tmp_path / "nonexistent", today=date(2026, 7, 13)
    )
    assert report["total_companies"] == 0
    assert report["counts"] == {"stale": 0, "fresh": 0}
    assert report["results"] == []


def test_audit_profile_staleness_custom_stale_days_threshold():
    companies = [_company("anthropic", "2026-07-01")]
    report = ps.audit_profile_staleness(
        companies, today=date(2026, 7, 13), stale_days=5
    )
    assert report["stale_days_threshold"] == 5
    assert report["counts"] == {"stale": 1, "fresh": 0}


def test_audit_profile_staleness_loads_real_registry_from_disk_when_none_passed():
    # No explicit companies= argument -- proves the default path really
    # does load the real, committed content/companies/*.json registry
    # (13 real profiles as of Phase 8, all last_verified "2026-07-13" per
    # the build brief's own verified facts -- none should be stale today).
    report = ps.audit_profile_staleness(today=date(2026, 7, 13))
    assert report["total_companies"] >= 13
    assert report["counts"]["stale"] == 0


def test_audit_profile_staleness_defaults_today_to_the_real_clock_when_omitted():
    """A caller/test that wants a frozen date passes `today` explicitly;
    the real, live default only matters for a genuine unmocked run --
    this test only proves the parameter is truly optional, not that any
    particular date comes back (that would time-bomb)."""
    report = ps.audit_profile_staleness([])
    assert report["total_companies"] == 0  # no companies passed -> nothing to check
