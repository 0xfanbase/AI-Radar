"""Tests for `auditor/corrections_feed.py` -- Phase 9's
`data/pending_corrections.json` `target_type: "company"` feed.

Covers `build_staleness_candidates`/`build_hijack_candidates` (pure
builders turning a checker's own report dict into pending-correction
candidate dicts) and `feed_pending_corrections` (the one disk-touching
appender: idempotent-by-id, schema-valid round trip against a `tmp_path`
scratch file, never the real repo file). Also confirms -- this is the
"confirm it's actually wired end to end, don't just assume" proof this
phase's own build brief asked for -- that a real candidate this module
produces genuinely round-trips through `scripts.pending_corrections`'s
load/save and validates against `schemas/pending_corrections.schema.json`
with no `card_id` present at all.
"""
from __future__ import annotations

import json

import pytest
from jsonschema import ValidationError

from auditor import corrections_feed as feed
from scripts.pending_corrections import load_pending_corrections
from watcher.schema_validate import validate

FLAGGED_AT = "2026-07-13T00:00:00Z"


# ---------------------------------------------------------------------------
# build_staleness_candidates
# ---------------------------------------------------------------------------


def _staleness_report(*results):
    return {"stale_days_threshold": 45, "results": list(results)}


def test_build_staleness_candidates_only_for_stale_results():
    report = _staleness_report(
        {
            "company_id": "anthropic",
            "name": "Anthropic",
            "last_verified": "2026-07-13",
            "days_stale": 0,
            "stale": False,
        },
        {
            "company_id": "deepseek",
            "name": "DeepSeek",
            "last_verified": "2026-04-01",
            "days_stale": 103,
            "stale": True,
        },
    )
    companies = [
        {
            "id": "deepseek",
            "official_domains": ["deepseek.com"],
            "profile": {
                "overview": {
                    "text": "x",
                    "citations": [
                        {"url": "https://deepseek.com/about", "outlet": "DeepSeek", "quote": "q"}
                    ],
                }
            },
        }
    ]
    candidates = feed.build_staleness_candidates(report, companies, flagged_at=FLAGGED_AT)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["target_type"] == "company"
    assert candidate["target_id"] == "deepseek"
    assert "card_id" not in candidate
    assert candidate["evidence_url"] == "https://deepseek.com/about"
    assert candidate["source"] == "audit"
    assert candidate["flagged_at"] == FLAGGED_AT
    assert "103" in candidate["issue_description"]
    assert "45" in candidate["issue_description"]


def test_build_staleness_candidates_falls_back_to_official_domain_when_no_overview_citation():
    report = _staleness_report(
        {
            "company_id": "acme",
            "name": "Acme",
            "last_verified": "2026-01-01",
            "days_stale": 193,
            "stale": True,
        }
    )
    companies = [{"id": "acme", "official_domains": ["acme.example"], "profile": {}}]
    candidates = feed.build_staleness_candidates(report, companies, flagged_at=FLAGGED_AT)
    assert candidates[0]["evidence_url"] == "https://acme.example/"


def test_build_staleness_candidates_id_is_deterministic_and_date_stamped():
    report = _staleness_report(
        {
            "company_id": "acme",
            "name": "Acme",
            "last_verified": "2026-01-01",
            "days_stale": 193,
            "stale": True,
        }
    )
    companies = [{"id": "acme", "official_domains": ["acme.example"], "profile": {}}]
    candidates = feed.build_staleness_candidates(report, companies, flagged_at=FLAGGED_AT)
    assert candidates[0]["id"] == "audit-profile-staleness-acme-2026-07-13"


def test_build_staleness_candidates_empty_results():
    assert feed.build_staleness_candidates(_staleness_report(), [], flagged_at=FLAGGED_AT) == []


# ---------------------------------------------------------------------------
# build_hijack_candidates
# ---------------------------------------------------------------------------


def _hijack_report(*results):
    return {"results": list(results)}


def test_build_hijack_candidates_only_for_hijacked_results():
    report = _hijack_report(
        {
            "company_id": "anthropic",
            "url": "https://anthropic.com/ok",
            "status": "trusted",
            "final_url": "https://anthropic.com/ok",
            "detail": None,
        },
        {
            "company_id": "deepseek",
            "url": "https://deepseek.com/hijacked",
            "status": "hijacked",
            "final_url": "https://squatted.example.com/x",
            "detail": "host not trusted",
        },
        {
            "company_id": "openai",
            "url": "https://openai.com/down",
            "status": "unreachable",
            "final_url": None,
            "detail": "timeout",
        },
    )
    candidates = feed.build_hijack_candidates(report, flagged_at=FLAGGED_AT)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["target_type"] == "company"
    assert candidate["target_id"] == "deepseek"
    assert "card_id" not in candidate
    assert candidate["evidence_url"] == "https://deepseek.com/hijacked"
    assert "squatted.example.com" in candidate["issue_description"]
    assert candidate["source"] == "audit"


def test_build_hijack_candidates_ids_are_distinct_per_url_for_the_same_company():
    report = _hijack_report(
        {
            "company_id": "deepseek",
            "url": "https://deepseek.com/a",
            "status": "hijacked",
            "final_url": "https://squatted.example.com/a",
            "detail": "x",
        },
        {
            "company_id": "deepseek",
            "url": "https://deepseek.com/b",
            "status": "hijacked",
            "final_url": "https://squatted.example.com/b",
            "detail": "x",
        },
    )
    candidates = feed.build_hijack_candidates(report, flagged_at=FLAGGED_AT)
    ids = [c["id"] for c in candidates]
    assert len(ids) == 2
    assert len(set(ids)) == 2  # distinct, never colliding


def test_build_hijack_candidates_empty_results():
    assert feed.build_hijack_candidates(_hijack_report(), flagged_at=FLAGGED_AT) == []


# ---------------------------------------------------------------------------
# candidate dicts are genuinely schema-valid with no fabricated card_id --
# the "confirm it's actually wired end to end" proof.
# ---------------------------------------------------------------------------


def test_staleness_candidate_validates_against_pending_corrections_schema_with_no_card_id():
    report = _staleness_report(
        {
            "company_id": "deepseek",
            "name": "DeepSeek",
            "last_verified": "2026-04-01",
            "days_stale": 103,
            "stale": True,
        }
    )
    companies = [
        {
            "id": "deepseek",
            "official_domains": ["deepseek.com"],
            "profile": {
                "overview": {
                    "text": "x",
                    "citations": [
                        {"url": "https://deepseek.com/about", "outlet": "DeepSeek", "quote": "q"}
                    ],
                }
            },
        }
    ]
    candidate = feed.build_staleness_candidates(report, companies, flagged_at=FLAGGED_AT)[0]
    validate({"version": 1, "pending": [candidate]}, "pending_corrections")  # must not raise
    assert "card_id" not in candidate


def test_hijack_candidate_validates_against_pending_corrections_schema_with_no_card_id():
    report = _hijack_report(
        {
            "company_id": "deepseek",
            "url": "https://deepseek.com/hijacked",
            "status": "hijacked",
            "final_url": "https://squatted.example.com/x",
            "detail": "host not trusted",
        }
    )
    candidate = feed.build_hijack_candidates(report, flagged_at=FLAGGED_AT)[0]
    validate({"version": 1, "pending": [candidate]}, "pending_corrections")  # must not raise
    assert "card_id" not in candidate


def test_a_card_only_pending_entry_still_requires_card_id():
    """The schema fix only relaxes card_id for target_type: 'company' --
    an ordinary card-targeted (or untargeted) entry with no card_id must
    still fail, exactly as before Phase 9."""
    entry = {
        "id": "c-1",
        "issue_description": "y",
        "evidence_url": "https://example.test/x",
        "flagged_at": FLAGGED_AT,
        "source": "audit",
    }
    with pytest.raises(ValidationError):
        validate({"version": 1, "pending": [entry]}, "pending_corrections")


# ---------------------------------------------------------------------------
# feed_pending_corrections
# ---------------------------------------------------------------------------


def _candidate(entry_id: str, target_id: str = "deepseek") -> dict:
    return {
        "id": entry_id,
        "target_type": "company",
        "target_id": target_id,
        "issue_description": "x",
        "evidence_url": "https://deepseek.com/about",
        "flagged_at": FLAGGED_AT,
        "source": "audit",
    }


def test_feed_pending_corrections_appends_new_candidates(tmp_path):
    path = tmp_path / "pending_corrections.json"
    added = feed.feed_pending_corrections([_candidate("c-1"), _candidate("c-2")], path=path)

    assert added == 2
    data = load_pending_corrections(path)
    assert [e["id"] for e in data["pending"]] == ["c-1", "c-2"]
    validate(data, "pending_corrections")


def test_feed_pending_corrections_is_idempotent_by_id(tmp_path):
    path = tmp_path / "pending_corrections.json"
    feed.feed_pending_corrections([_candidate("c-1")], path=path)
    added_again = feed.feed_pending_corrections([_candidate("c-1")], path=path)

    assert added_again == 0
    data = load_pending_corrections(path)
    assert [e["id"] for e in data["pending"]] == ["c-1"]  # not duplicated


def test_feed_pending_corrections_empty_candidates_never_touches_disk(tmp_path):
    path = tmp_path / "pending_corrections.json"
    added = feed.feed_pending_corrections([], path=path)

    assert added == 0
    assert not path.exists()


def test_feed_pending_corrections_mixes_new_and_already_present(tmp_path):
    path = tmp_path / "pending_corrections.json"
    feed.feed_pending_corrections([_candidate("c-1")], path=path)

    added = feed.feed_pending_corrections([_candidate("c-1"), _candidate("c-2")], path=path)

    assert added == 1
    data = load_pending_corrections(path)
    assert [e["id"] for e in data["pending"]] == ["c-1", "c-2"]


def test_feed_pending_corrections_preserves_pre_existing_unrelated_entries(tmp_path):
    path = tmp_path / "pending_corrections.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "pending": [
                    {
                        "id": "manual-1",
                        "card_id": "2026-07-01-some-card",
                        "issue_description": "manual note",
                        "evidence_url": "https://example.test/x",
                        "flagged_at": "2026-07-01T00:00:00Z",
                        "source": "manual",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    feed.feed_pending_corrections([_candidate("c-1")], path=path)

    data = load_pending_corrections(path)
    assert [e["id"] for e in data["pending"]] == ["manual-1", "c-1"]
    validate(data, "pending_corrections")
