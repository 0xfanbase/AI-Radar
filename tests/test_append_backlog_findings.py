"""Tests for `scripts/append_backlog_findings.py` -- Phase 5's
findings-to-`IMPROVEMENT_BACKLOG.md` promotion step.

Covers `derive_findings`'s severity mapping and "what's actionable" rules
(including the zero-published-cards lexicon-orphan-suppression guard --
this repo's own real, current state), `format_finding_line`'s exact
Markdown shape, and `append_findings_to_backlog`'s append-only,
skip-when-empty disk behavior against a `tmp_path` scratch file (never the
real `IMPROVEMENT_BACKLOG.md`).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.append_backlog_findings as backlog_mod

EMPTY_LINK_ROT = {"results": []}
EMPTY_LEXICON = {"coverage_gaps": [], "orphans": []}
EMPTY_TREND = {
    "as_of": "2026-07-11",
    "rolling_7d_pass_rate": None,
    "prior_week_pass_rate": None,
    "trend": "insufficient_data",
}
EMPTY_MISSED_STORIES = {"missed_stories": []}
EMPTY_DUPLICATES = {"duplicate_pairs": []}
EMPTY_HIJACKED_LINKS = {"results": []}
EMPTY_COMPANY_HIJACKED_LINKS = {"results": []}
EMPTY_PROFILE_STALENESS = {"results": []}


def _derive(**overrides):
    kwargs = dict(
        link_rot=EMPTY_LINK_ROT,
        lexicon=EMPTY_LEXICON,
        verifier_trend=EMPTY_TREND,
        missed_stories=EMPTY_MISSED_STORIES,
        duplicates=EMPTY_DUPLICATES,
        hijacked_links=EMPTY_HIJACKED_LINKS,
        company_hijacked_links=EMPTY_COMPANY_HIJACKED_LINKS,
        profile_staleness=EMPTY_PROFILE_STALENESS,
        has_cards=True,
    )
    kwargs.update(overrides)
    return backlog_mod.derive_findings(**kwargs)


# ---------------------------------------------------------------------------
# _fmt_pct
# ---------------------------------------------------------------------------


def test_fmt_pct_formats_a_fraction_as_a_percentage():
    assert backlog_mod._fmt_pct(0.823) == "82.3%"


def test_fmt_pct_none_is_not_available():
    assert backlog_mod._fmt_pct(None) == "n/a"


# ---------------------------------------------------------------------------
# derive_findings -- clean/all-empty case
# ---------------------------------------------------------------------------


def test_derive_findings_all_clean_returns_empty_list():
    assert _derive() == []


# ---------------------------------------------------------------------------
# derive_findings -- link rot
# ---------------------------------------------------------------------------


def test_derive_findings_dead_link_is_low_severity():
    link_rot = {
        "results": [
            {"url": "https://example.com/gone", "status": "dead", "http_status": 404}
        ]
    }
    findings = _derive(link_rot=link_rot)
    assert len(findings) == 1
    assert findings[0]["severity"] == "low"
    assert findings[0]["category"] == "link_rot"
    assert "404" in findings[0]["summary"]
    assert "https://example.com/gone" in findings[0]["summary"]


def test_derive_findings_unreachable_link_is_not_a_finding():
    """`unreachable` (403/5xx/timeout) is deliberately not promoted --
    only a confirmed-dead (404/410) link is actionable -- see the module
    docstring."""
    link_rot = {
        "results": [
            {"url": "https://example.com/blocked", "status": "unreachable", "http_status": 403}
        ]
    }
    assert _derive(link_rot=link_rot) == []


def test_derive_findings_ok_link_is_not_a_finding():
    link_rot = {
        "results": [{"url": "https://example.com/fine", "status": "ok", "http_status": 200}]
    }
    assert _derive(link_rot=link_rot) == []


# ---------------------------------------------------------------------------
# derive_findings -- lexicon
# ---------------------------------------------------------------------------


def test_derive_findings_coverage_gap_is_low_severity():
    lexicon = {
        "coverage_gaps": [{"card_id": "card-1", "missing_terms": ["RAG", "MoE"]}],
        "orphans": [],
    }
    findings = _derive(lexicon=lexicon)
    assert len(findings) == 1
    assert findings[0]["severity"] == "low"
    assert findings[0]["category"] == "lexicon_coverage_gap"
    assert "card-1" in findings[0]["summary"]
    assert "RAG" in findings[0]["summary"] and "MoE" in findings[0]["summary"]


def test_derive_findings_orphan_is_low_severity_when_cards_exist():
    lexicon = {"coverage_gaps": [], "orphans": ["quantization"]}
    findings = _derive(lexicon=lexicon, has_cards=True)
    assert len(findings) == 1
    assert findings[0]["severity"] == "low"
    assert findings[0]["category"] == "lexicon_orphan"
    assert "quantization" in findings[0]["summary"]


def test_derive_findings_orphan_suppressed_when_zero_cards_published():
    """This repo's own real, current state: content/cards/ doesn't exist
    yet, and every one of the real 30 content/lexicon.json entries has an
    empty seen_in[] -- flagging all 30 as 'orphan' findings on day zero
    would be noise, not signal. See the module docstring's own guard
    reasoning."""
    lexicon = {"coverage_gaps": [], "orphans": ["quantization", "distillation"]}
    findings = _derive(lexicon=lexicon, has_cards=False)
    assert findings == []


def test_derive_findings_coverage_gap_not_suppressed_by_has_cards_false():
    """A coverage gap can't structurally occur with zero cards in
    practice, but the suppression guard is specifically scoped to orphans
    only -- confirm it doesn't accidentally also swallow a coverage gap
    finding if one were ever passed in alongside has_cards=False."""
    lexicon = {
        "coverage_gaps": [{"card_id": "card-1", "missing_terms": ["RAG"]}],
        "orphans": ["quantization"],
    }
    findings = _derive(lexicon=lexicon, has_cards=False)
    categories = [f["category"] for f in findings]
    assert "lexicon_coverage_gap" in categories
    assert "lexicon_orphan" not in categories


# ---------------------------------------------------------------------------
# derive_findings -- verifier trend
# ---------------------------------------------------------------------------


def test_derive_findings_falling_trend_is_high_severity():
    trend = {
        "as_of": "2026-07-11",
        "rolling_7d_pass_rate": 0.6,
        "prior_week_pass_rate": 0.9,
        "trend": "falling",
    }
    findings = _derive(verifier_trend=trend)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["category"] == "verifier_trend"
    assert "60.0%" in findings[0]["summary"]
    assert "90.0%" in findings[0]["summary"]
    assert "2026-07-11" in findings[0]["summary"]


@pytest.mark.parametrize("trend_label", ["rising", "flat", "insufficient_data"])
def test_derive_findings_non_falling_trend_is_not_a_finding(trend_label):
    trend = {
        "as_of": "2026-07-11",
        "rolling_7d_pass_rate": 0.8,
        "prior_week_pass_rate": 0.8,
        "trend": trend_label,
    }
    assert _derive(verifier_trend=trend) == []


# ---------------------------------------------------------------------------
# derive_findings -- missed stories / duplicates
# ---------------------------------------------------------------------------


def test_derive_findings_missed_story_is_medium_severity():
    missed_stories = {
        "missed_stories": [
            {"title": "Some new AI story", "url": "https://example.com/story"}
        ]
    }
    findings = _derive(missed_stories=missed_stories)
    assert len(findings) == 1
    assert findings[0]["severity"] == "medium"
    assert findings[0]["category"] == "missed_story"
    assert "Some new AI story" in findings[0]["summary"]
    assert "https://example.com/story" in findings[0]["summary"]


def test_derive_findings_duplicate_pair_is_medium_severity():
    duplicates = {
        "duplicate_pairs": [
            {
                "card_a": "card-1",
                "card_b": "card-2",
                "similarity": 0.421,
                "shared_topics": ["funding", "chips"],
            }
        ]
    }
    findings = _derive(duplicates=duplicates)
    assert len(findings) == 1
    assert findings[0]["severity"] == "medium"
    assert findings[0]["category"] == "duplicate_topic"
    assert "card-1" in findings[0]["summary"] and "card-2" in findings[0]["summary"]
    assert "0.42" in findings[0]["summary"]
    assert "funding" in findings[0]["summary"] and "chips" in findings[0]["summary"]


def test_derive_findings_duplicate_pair_without_shared_topics():
    duplicates = {
        "duplicate_pairs": [
            {"card_a": "card-1", "card_b": "card-2", "similarity": 0.5, "shared_topics": []}
        ]
    }
    findings = _derive(duplicates=duplicates)
    assert len(findings) == 1
    assert "shared topics" not in findings[0]["summary"]


# ---------------------------------------------------------------------------
# derive_findings -- Phase 9: hijacked_links / company_hijacked_links /
# profile_staleness
# ---------------------------------------------------------------------------


def test_derive_findings_hijacked_link_is_high_severity():
    hijacked_links = {
        "results": [
            {
                "url": "https://anthropic.com/hijacked",
                "status": "hijacked",
                "final_url": "https://squatted.example.com/x",
                "detail": "not trusted",
            }
        ]
    }
    findings = _derive(hijacked_links=hijacked_links)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["category"] == "hijacked_citation"
    assert "https://anthropic.com/hijacked" in findings[0]["summary"]
    assert "https://squatted.example.com/x" in findings[0]["summary"]


def test_derive_findings_hijacked_link_with_no_redirect_is_worded_honestly():
    # final_url == url: never actually redirected -- just off the allowlist.
    # "now redirects to <same URL>" would be self-contradictory.
    hijacked_links = {
        "results": [
            {
                "url": "https://huggingface.co/some/model",
                "status": "hijacked",
                "final_url": "https://huggingface.co/some/model",
                "detail": "not trusted",
            }
        ]
    }
    findings = _derive(hijacked_links=hijacked_links)
    assert len(findings) == 1
    summary = findings[0]["summary"]
    assert "now redirects to" not in summary
    assert "no redirect" in summary
    assert "https://huggingface.co/some/model" in summary


def test_derive_findings_hijacked_link_trusted_or_unreachable_is_not_a_finding():
    hijacked_links = {
        "results": [
            {"url": "https://a.test/ok", "status": "trusted", "final_url": "https://a.test/ok", "detail": None},
            {"url": "https://a.test/down", "status": "unreachable", "final_url": None, "detail": "timeout"},
        ]
    }
    assert _derive(hijacked_links=hijacked_links) == []


def test_derive_findings_company_hijacked_link_is_high_severity():
    company_hijacked_links = {
        "results": [
            {
                "company_id": "anthropic",
                "url": "https://anthropic.com/hijacked",
                "status": "hijacked",
                "final_url": "https://squatted.example.com/x",
                "detail": "not trusted",
            }
        ]
    }
    findings = _derive(company_hijacked_links=company_hijacked_links)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["category"] == "hijacked_company_citation"
    assert "anthropic" in findings[0]["summary"]
    assert "https://anthropic.com/hijacked" in findings[0]["summary"]


def test_derive_findings_company_hijacked_link_with_no_redirect_is_worded_honestly():
    company_hijacked_links = {
        "results": [
            {
                "company_id": "moonshot-ai",
                "url": "https://huggingface.co/moonshotai/Kimi-K2.6",
                "status": "hijacked",
                "final_url": "https://huggingface.co/moonshotai/Kimi-K2.6",
                "detail": "not trusted",
            }
        ]
    }
    findings = _derive(company_hijacked_links=company_hijacked_links)
    assert len(findings) == 1
    summary = findings[0]["summary"]
    assert "now redirects to" not in summary
    assert "no redirect" in summary
    assert "moonshot-ai" in summary


def test_derive_findings_company_hijacked_link_trusted_is_not_a_finding():
    company_hijacked_links = {
        "results": [
            {
                "company_id": "anthropic",
                "url": "https://anthropic.com/ok",
                "status": "trusted",
                "final_url": "https://anthropic.com/ok",
                "detail": None,
            }
        ]
    }
    assert _derive(company_hijacked_links=company_hijacked_links) == []


def test_derive_findings_stale_profile_is_low_severity():
    profile_staleness = {
        "results": [
            {
                "company_id": "anthropic",
                "name": "Anthropic",
                "last_verified": "2026-05-01",
                "days_stale": 73,
                "stale": True,
            }
        ]
    }
    findings = _derive(profile_staleness=profile_staleness)
    assert len(findings) == 1
    assert findings[0]["severity"] == "low"
    assert findings[0]["category"] == "profile_staleness"
    assert "anthropic" in findings[0]["summary"]
    assert "73" in findings[0]["summary"]


def test_derive_findings_fresh_profile_is_not_a_finding():
    profile_staleness = {
        "results": [
            {
                "company_id": "anthropic",
                "name": "Anthropic",
                "last_verified": "2026-07-10",
                "days_stale": 3,
                "stale": False,
            }
        ]
    }
    assert _derive(profile_staleness=profile_staleness) == []


def test_derive_findings_phase9_checkers_default_to_no_findings_when_omitted():
    """An existing call site that predates Phase 9 -- omitting
    hijacked_links/company_hijacked_links/profile_staleness entirely --
    still works and simply contributes no Phase 9 findings, never raises."""
    findings = backlog_mod.derive_findings(
        link_rot=EMPTY_LINK_ROT,
        lexicon=EMPTY_LEXICON,
        verifier_trend=EMPTY_TREND,
        missed_stories=EMPTY_MISSED_STORIES,
        duplicates=EMPTY_DUPLICATES,
        has_cards=True,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# derive_findings -- deterministic ordering across categories
# ---------------------------------------------------------------------------


def test_derive_findings_deterministic_category_order():
    link_rot = {
        "results": [{"url": "https://example.com/gone", "status": "dead", "http_status": 404}]
    }
    hijacked_links = {
        "results": [
            {
                "url": "https://a.test/hijacked",
                "status": "hijacked",
                "final_url": "https://squatted.example.com/x",
                "detail": "not trusted",
            }
        ]
    }
    company_hijacked_links = {
        "results": [
            {
                "company_id": "anthropic",
                "url": "https://anthropic.com/hijacked",
                "status": "hijacked",
                "final_url": "https://squatted.example.com/y",
                "detail": "not trusted",
            }
        ]
    }
    lexicon = {
        "coverage_gaps": [{"card_id": "card-1", "missing_terms": ["RAG"]}],
        "orphans": ["quantization"],
    }
    trend = {
        "as_of": "2026-07-11",
        "rolling_7d_pass_rate": 0.5,
        "prior_week_pass_rate": 0.9,
        "trend": "falling",
    }
    missed_stories = {
        "missed_stories": [{"title": "Missed", "url": "https://example.com/missed"}]
    }
    duplicates = {
        "duplicate_pairs": [
            {"card_a": "card-1", "card_b": "card-2", "similarity": 0.5, "shared_topics": []}
        ]
    }
    profile_staleness = {
        "results": [
            {
                "company_id": "anthropic",
                "name": "Anthropic",
                "last_verified": "2026-05-01",
                "days_stale": 73,
                "stale": True,
            }
        ]
    }
    findings = _derive(
        link_rot=link_rot,
        hijacked_links=hijacked_links,
        company_hijacked_links=company_hijacked_links,
        lexicon=lexicon,
        verifier_trend=trend,
        missed_stories=missed_stories,
        duplicates=duplicates,
        profile_staleness=profile_staleness,
        has_cards=True,
    )
    categories = [f["category"] for f in findings]
    assert categories == [
        "link_rot",
        "hijacked_citation",
        "hijacked_company_citation",
        "lexicon_coverage_gap",
        "lexicon_orphan",
        "verifier_trend",
        "missed_story",
        "duplicate_topic",
        "profile_staleness",
    ]


# ---------------------------------------------------------------------------
# format_finding_line
# ---------------------------------------------------------------------------


def test_format_finding_line_shape():
    line = backlog_mod.format_finding_line({"severity": "high", "summary": "Something bad."})
    assert line == "- [ ] **[HIGH]** Something bad."


def test_format_finding_line_unknown_severity_falls_back_to_uppercase():
    line = backlog_mod.format_finding_line({"severity": "critical", "summary": "X."})
    assert line == "- [ ] **[CRITICAL]** X."


# ---------------------------------------------------------------------------
# append_findings_to_backlog
# ---------------------------------------------------------------------------


def test_append_findings_to_backlog_empty_list_writes_nothing(tmp_path: Path):
    path = tmp_path / "BACKLOG.md"
    path.write_text("# existing content\n", encoding="utf-8")

    count = backlog_mod.append_findings_to_backlog(
        [], run_id="audit-1", generated_at="2026-07-11T23:30:00Z", path=path
    )

    assert count == 0
    assert path.read_text(encoding="utf-8") == "# existing content\n"


def test_append_findings_to_backlog_writes_header_and_lines(tmp_path: Path):
    path = tmp_path / "BACKLOG.md"
    path.write_text("# existing content\n", encoding="utf-8")

    findings = [
        {"severity": "high", "category": "verifier_trend", "summary": "Trend falling."},
        {"severity": "medium", "category": "missed_story", "summary": "Missed a story."},
    ]
    count = backlog_mod.append_findings_to_backlog(
        findings,
        run_id="audit-20260711T233000Z",
        generated_at="2026-07-11T23:30:00Z",
        path=path,
    )

    assert count == 2
    text = path.read_text(encoding="utf-8")
    assert "# existing content" in text  # never rewrites/removes existing content
    assert "## Audit findings -- audit-20260711T233000Z (2026-07-11T23:30:00Z)" in text
    assert "- [ ] **[HIGH]** Trend falling." in text
    assert "- [ ] **[MEDIUM]** Missed a story." in text
    # Order preserved: HIGH line appears before MEDIUM line.
    assert text.index("[HIGH]") < text.index("[MEDIUM]")


def test_append_findings_to_backlog_appends_a_second_section_separately(tmp_path: Path):
    path = tmp_path / "BACKLOG.md"
    path.write_text("# existing content\n", encoding="utf-8")

    findings_1 = [{"severity": "low", "category": "link_rot", "summary": "First run finding."}]
    findings_2 = [{"severity": "low", "category": "link_rot", "summary": "Second run finding."}]

    backlog_mod.append_findings_to_backlog(
        findings_1, run_id="audit-1", generated_at="2026-07-11T00:00:00Z", path=path
    )
    backlog_mod.append_findings_to_backlog(
        findings_2, run_id="audit-2", generated_at="2026-07-18T00:00:00Z", path=path
    )

    text = path.read_text(encoding="utf-8")
    assert "First run finding." in text
    assert "Second run finding." in text
    assert text.index("audit-1") < text.index("audit-2")


def test_append_findings_to_backlog_return_value_matches_findings_written(tmp_path: Path):
    path = tmp_path / "BACKLOG.md"
    path.write_text("", encoding="utf-8")
    findings = [
        {"severity": "low", "category": "link_rot", "summary": "A."},
        {"severity": "low", "category": "link_rot", "summary": "B."},
        {"severity": "low", "category": "link_rot", "summary": "C."},
    ]
    count = backlog_mod.append_findings_to_backlog(
        findings, run_id="audit-1", generated_at="2026-07-11T00:00:00Z", path=path
    )
    assert count == 3


def test_backlog_path_points_at_the_real_repo_file():
    assert backlog_mod.BACKLOG_PATH.name == "IMPROVEMENT_BACKLOG.md"
    assert backlog_mod.BACKLOG_PATH.parent == backlog_mod.REPO_ROOT
