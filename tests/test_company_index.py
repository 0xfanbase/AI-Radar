"""Tests for scripts/update_company_index.py -- the
content/companies/index.json regenerator (Phase 8).

Covers, in order: regeneration correctness against a small set of
hand-built company-profile fixtures, the "profile removed from disk" case,
the empty-directory and not-yet-created-directory cases (both produce a
valid, schema-conformant empty index), the deterministic alphabetical-by-id
sort, index.json itself never being treated as one of its own inputs, the
load/save/write schema-valid round trip, and that a malformed on-disk
profile is a loud failure rather than a silent skip. Mirrors
tests/test_card_index.py's own structure/coverage for the sibling
content/cards/index.json regenerator.

Every test uses a `tmp_path`-based `companies_dir`/`index_path` -- never
the real `content/companies/` directory -- so this suite has no side
effects on the repo's own real, committed company registry.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import ValidationError

from scripts.update_company_index import (
    COMPANY_INDEX_VERSION,
    INDEX_FIELDS,
    build_company_index,
    iter_company_paths,
    load_company_index,
    save_company_index,
    write_company_index,
)
from watcher.schema_validate import validate


def _company(
    company_id: str,
    *,
    name: str = "Example Labs",
    hq_country: str = "US",
    hq_city: str = "San Francisco",
    hq_lat: float = 37.7749,
    hq_lng: float = -122.4194,
    status: str = "confirmed",
) -> dict[str, Any]:
    """A minimal, fully company.schema.json-valid profile dict -- only the
    fields this module's own tests care about are parameterized; every
    other required field gets a simple fixed placeholder value."""
    return {
        "id": company_id,
        "name": name,
        "aliases": [],
        "hq_country": hq_country,
        "hq_city": hq_city,
        "hq_lat": hq_lat,
        "hq_lng": hq_lng,
        "official_domains": ["example-labs.test"],
        "founded": "2020",
        "profile": {
            "overview": {
                "text": "An example company for testing.",
                "citations": [
                    {
                        "url": "https://example-labs.test/about",
                        "outlet": "Example Labs (official)",
                        "quote": "an example company",
                    }
                ],
            },
            "what_theyve_done": [],
            "strengths": [],
            "current_focus": {
                "text": "Testing.",
                "citations": [
                    {
                        "url": "https://example-labs.test/about",
                        "outlet": "Example Labs (official)",
                        "quote": "an example company",
                    }
                ],
            },
            "roadmap": [],
        },
        "status": status,
        "generated_at": "2026-07-09T07:15:00Z",
        "model": "claude-sonnet-4-5",
        "last_verified": "2026-07-09",
    }


def _write_company(companies_dir: Path, company: dict[str, Any]) -> Path:
    companies_dir.mkdir(parents=True, exist_ok=True)
    path = companies_dir / f"{company['id']}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(company, f)
    return path


# --------------------------------------------------------------------------
# empty / nonexistent directory
# --------------------------------------------------------------------------


def test_build_company_index_nonexistent_directory_is_empty_and_valid(tmp_path):
    companies_dir = tmp_path / "content" / "companies"
    assert not companies_dir.exists()

    index = build_company_index(companies_dir)

    assert index == {"version": COMPANY_INDEX_VERSION, "companies": []}
    validate(index, "company_index")


def test_build_company_index_empty_directory_is_empty_and_valid(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()

    index = build_company_index(companies_dir)

    assert index == {"version": COMPANY_INDEX_VERSION, "companies": []}
    validate(index, "company_index")


def test_iter_company_paths_nonexistent_directory_returns_empty_list(tmp_path):
    assert iter_company_paths(tmp_path / "does" / "not" / "exist") == []


# --------------------------------------------------------------------------
# regeneration correctness
# --------------------------------------------------------------------------


def test_build_company_index_extracts_exactly_the_index_fields(tmp_path):
    companies_dir = tmp_path / "companies"
    company = _company("example-labs", name="Example Labs", status="reported")
    _write_company(companies_dir, company)

    index = build_company_index(companies_dir)

    assert len(index["companies"]) == 1
    entry = index["companies"][0]
    assert set(entry.keys()) == set(INDEX_FIELDS)
    assert entry["id"] == "example-labs"
    assert entry["name"] == "Example Labs"
    assert entry["hq_country"] == "US"
    assert entry["hq_city"] == "San Francisco"
    assert entry["status"] == "reported"
    # Full profile.* prose/citations must NOT leak into the index.
    assert "profile" not in entry
    assert "aliases" not in entry
    validate(index, "company_index")


def test_build_company_index_alphabetical_by_id(tmp_path):
    companies_dir = tmp_path / "companies"
    _write_company(companies_dir, _company("zhipu-ai"))
    _write_company(companies_dir, _company("anthropic"))
    _write_company(companies_dir, _company("mistral"))

    index = build_company_index(companies_dir)

    assert [c["id"] for c in index["companies"]] == [
        "anthropic",
        "mistral",
        "zhipu-ai",
    ]


def test_index_json_itself_is_never_treated_as_a_company_input(tmp_path):
    companies_dir = tmp_path / "companies"
    _write_company(companies_dir, _company("anthropic"))
    (companies_dir / "index.json").write_text(
        json.dumps({"version": 1, "companies": []}), encoding="utf-8"
    )

    index = build_company_index(companies_dir)

    assert [c["id"] for c in index["companies"]] == ["anthropic"]


# --------------------------------------------------------------------------
# a profile removed from disk between regenerations
# --------------------------------------------------------------------------


def test_removed_company_file_is_simply_absent_from_the_next_index(tmp_path):
    companies_dir = tmp_path / "companies"
    kept_path = _write_company(companies_dir, _company("anthropic"))
    removed_path = _write_company(companies_dir, _company("openai"))

    first_index = build_company_index(companies_dir)
    assert {c["id"] for c in first_index["companies"]} == {"anthropic", "openai"}

    removed_path.unlink()
    assert kept_path.exists()

    second_index = build_company_index(companies_dir)

    assert [c["id"] for c in second_index["companies"]] == ["anthropic"]
    validate(second_index, "company_index")


def test_regenerating_over_a_previously_written_index_drops_removed_companies(tmp_path):
    companies_dir = tmp_path / "companies"
    index_path = tmp_path / "index.json"
    _write_company(companies_dir, _company("anthropic"))
    removed_path = _write_company(companies_dir, _company("openai"))

    write_company_index(companies_dir, index_path)
    on_disk_first = json.loads(index_path.read_text(encoding="utf-8"))
    assert {c["id"] for c in on_disk_first["companies"]} == {"anthropic", "openai"}

    removed_path.unlink()
    write_company_index(companies_dir, index_path)
    on_disk_second = json.loads(index_path.read_text(encoding="utf-8"))
    assert [c["id"] for c in on_disk_second["companies"]] == ["anthropic"]


# --------------------------------------------------------------------------
# load / save / write round trip
# --------------------------------------------------------------------------


def test_load_company_index_missing_file_returns_empty_index(tmp_path):
    index = load_company_index(tmp_path / "does-not-exist.json")
    assert index == {"version": COMPANY_INDEX_VERSION, "companies": []}


def test_save_then_load_round_trips(tmp_path):
    companies_dir = tmp_path / "companies"
    _write_company(companies_dir, _company("anthropic"))
    index_path = tmp_path / "nested" / "index.json"

    built = build_company_index(companies_dir)
    save_company_index(built, index_path)

    assert index_path.is_file()
    loaded = load_company_index(index_path)
    assert loaded == built
    validate(loaded, "company_index")


def test_save_company_index_rejects_invalid_payload(tmp_path):
    with pytest.raises(ValidationError):
        save_company_index(
            {"version": 1, "companies": [{"id": "missing-fields"}]},
            tmp_path / "index.json",
        )


def test_write_company_index_creates_parent_directory(tmp_path):
    companies_dir = tmp_path / "companies"
    _write_company(companies_dir, _company("anthropic"))
    index_path = tmp_path / "brand" / "new" / "dir" / "index.json"
    assert not index_path.parent.exists()

    write_company_index(companies_dir, index_path)

    assert index_path.is_file()


# --------------------------------------------------------------------------
# a malformed on-disk profile is a loud failure, not a silent skip
# --------------------------------------------------------------------------


def test_build_company_index_raises_on_malformed_company(tmp_path):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    bad_company = _company("bad-co")
    del bad_company["name"]  # required field missing
    _write_company(companies_dir, bad_company)

    with pytest.raises(ValidationError):
        build_company_index(companies_dir)


# --------------------------------------------------------------------------
# against the real, committed content/companies/ registry
# --------------------------------------------------------------------------


def test_build_company_index_against_the_real_seeded_registry():
    from watcher.config import REPO_ROOT

    real_companies_dir = REPO_ROOT / "content" / "companies"
    index = build_company_index(real_companies_dir)

    assert len(index["companies"]) == 13
    ids = {c["id"] for c in index["companies"]}
    assert "anthropic" in ids
    assert "deepseek" in ids
    validate(index, "company_index")
