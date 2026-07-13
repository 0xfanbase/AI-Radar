"""Tests for scripts/pending_corrections.py -- the pending-corrections
intake helper.

Per this module's own docstring, the ANALYST LLM step does the actual
draining (fetch evidence_url, judge, append to content/corrections.json,
remove from pending[]) directly via its own Edit/Write tools -- nothing
here calls out to an LLM or a network. This suite covers only the pure-code
bookkeeping surface: the load/save schema-valid round trip (missing file ->
empty shape; a malformed payload is rejected at save time), append/drain
round trips (never mutating their input in place, matching this repo's
established ledger/queue-writer convention), the append-time duplicate-id
guard, and drain's idempotency when the target entry is already absent.
"""
from __future__ import annotations

import json

import pytest
from jsonschema import ValidationError

from scripts.pending_corrections import (
    PENDING_CORRECTIONS_VERSION,
    append_pending_correction,
    drain_pending_correction,
    empty_pending_corrections,
    find_pending_correction,
    load_pending_corrections,
    save_pending_corrections,
)
from watcher.schema_validate import validate


def _entry(
    entry_id: str,
    *,
    card_id: str = "2026-07-08-example-benchmark-paper",
    source: str = "audit",
) -> dict:
    return {
        "id": entry_id,
        "card_id": card_id,
        "issue_description": "Audit's link-rot check found the primary citation now 404s.",
        "evidence_url": "https://example-lab.test/blog/example-model-5",
        "flagged_at": "2026-07-09T08:00:00Z",
        "source": source,
    }


# --------------------------------------------------------------------------
# empty / load / save round trip
# --------------------------------------------------------------------------


def test_empty_pending_corrections_matches_phase_2_seed_shape():
    assert empty_pending_corrections() == {
        "version": PENDING_CORRECTIONS_VERSION,
        "pending": [],
    }
    validate(empty_pending_corrections(), "pending_corrections")


def test_load_pending_corrections_missing_file_returns_empty_shape(tmp_path):
    data = load_pending_corrections(tmp_path / "does-not-exist.json")
    assert data == empty_pending_corrections()


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "pending_corrections.json"
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))

    save_pending_corrections(data, path)
    loaded = load_pending_corrections(path)

    assert loaded == data
    validate(loaded, "pending_corrections")


def test_save_pending_corrections_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "pending_corrections.json"
    assert not path.parent.exists()

    save_pending_corrections(empty_pending_corrections(), path)

    assert path.is_file()


def test_save_pending_corrections_rejects_invalid_payload(tmp_path):
    invalid = {"version": 1, "pending": [{"id": "missing-fields"}]}
    with pytest.raises(ValidationError):
        save_pending_corrections(invalid, tmp_path / "p.json")


def test_load_pending_corrections_rejects_invalid_on_disk_payload(tmp_path):
    path = tmp_path / "pending_corrections.json"
    # source: "audit-team" is not in the schema's enum (audit|manual) --
    # same invalid shape as fixtures/schema_examples/invalid/pending_corrections.json.
    bad = _entry("c-1", source="audit-team")
    path.write_text(
        json.dumps({"version": 1, "pending": [bad]}), encoding="utf-8"
    )

    with pytest.raises(ValidationError):
        load_pending_corrections(path)


def test_save_pending_corrections_writes_pretty_deterministic_json(tmp_path):
    path = tmp_path / "pending_corrections.json"
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))

    save_pending_corrections(data, path)

    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    # indent=2, sort_keys=True -- matches every other committed artifact's
    # writer in this pipeline (watcher/ledger.py::save_ledger et al.).
    assert '"pending": [' in raw
    assert json.loads(raw) == data


# --------------------------------------------------------------------------
# find_pending_correction
# --------------------------------------------------------------------------


def test_find_pending_correction_returns_matching_entry():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))
    found = find_pending_correction(data, "c-1")
    assert found is not None
    assert found["id"] == "c-1"


def test_find_pending_correction_returns_none_when_absent():
    data = empty_pending_corrections()
    assert find_pending_correction(data, "does-not-exist") is None


# --------------------------------------------------------------------------
# append_pending_correction
# --------------------------------------------------------------------------


def test_append_pending_correction_adds_entry_without_mutating_input():
    data = empty_pending_corrections()

    new_data = append_pending_correction(data, _entry("c-1"))

    assert data["pending"] == []  # original untouched
    assert [e["id"] for e in new_data["pending"]] == ["c-1"]
    validate(new_data, "pending_corrections")


def test_append_pending_correction_preserves_existing_entries_order():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))
    data = append_pending_correction(data, _entry("c-2"))

    assert [e["id"] for e in data["pending"]] == ["c-1", "c-2"]
    validate(data, "pending_corrections")


def test_append_pending_correction_rejects_duplicate_id():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))

    with pytest.raises(ValueError):
        append_pending_correction(data, _entry("c-1", card_id="a-different-card"))


def test_append_pending_correction_defaults_version_when_absent():
    # A caller-built dict with no "version" key at all still gets the
    # canonical version stamped on the returned dict.
    new_data = append_pending_correction({"pending": []}, _entry("c-1"))
    assert new_data["version"] == PENDING_CORRECTIONS_VERSION


# --------------------------------------------------------------------------
# drain_pending_correction
# --------------------------------------------------------------------------


def test_drain_pending_correction_removes_entry_without_mutating_input():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))
    data = append_pending_correction(data, _entry("c-2"))

    new_data = drain_pending_correction(data, "c-1")

    assert [e["id"] for e in data["pending"]] == ["c-1", "c-2"]  # original untouched
    assert [e["id"] for e in new_data["pending"]] == ["c-2"]
    validate(new_data, "pending_corrections")


def test_drain_pending_correction_is_idempotent_when_entry_already_absent():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))
    drained_once = drain_pending_correction(data, "c-1")

    drained_twice = drain_pending_correction(drained_once, "c-1")

    assert drained_once == drained_twice == {
        "version": PENDING_CORRECTIONS_VERSION,
        "pending": [],
    }


def test_drain_pending_correction_on_unknown_id_never_present_is_a_noop():
    data = append_pending_correction(empty_pending_corrections(), _entry("c-1"))

    new_data = drain_pending_correction(data, "never-existed")

    assert new_data == data


# --------------------------------------------------------------------------
# append -> drain -> save -> load: the full lifecycle a pending correction
# goes through once the analyst (elsewhere) has judged it, expressed purely
# through this module's own bookkeeping functions.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Phase 9: target_type: "company" entries -- card_id is required only when
# target_type is absent or "card" (schemas/pending_corrections.schema.json's
# own if/then/else conditional). See auditor/corrections_feed.py and its
# own tests for the real producer this schema fix unblocked.
# --------------------------------------------------------------------------


def _company_entry(entry_id: str, target_id: str = "anthropic") -> dict:
    return {
        "id": entry_id,
        "target_type": "company",
        "target_id": target_id,
        "issue_description": "Profile has not been re-verified recently.",
        "evidence_url": "https://anthropic.com/news",
        "flagged_at": "2026-07-13T00:00:00Z",
        "source": "audit",
    }


def test_company_targeted_entry_validates_with_no_card_id():
    data = append_pending_correction(empty_pending_corrections(), _company_entry("c-1"))
    validate(data, "pending_corrections")  # must not raise
    assert "card_id" not in data["pending"][0]


def test_card_less_entry_with_no_target_type_still_requires_card_id():
    entry = _company_entry("c-1")
    del entry["target_type"]
    del entry["target_id"]
    data = {"version": 1, "pending": [entry]}
    with pytest.raises(ValidationError):
        validate(data, "pending_corrections")


def test_card_less_entry_with_target_type_card_still_requires_card_id():
    entry = _company_entry("c-1")
    entry["target_type"] = "card"
    entry["target_id"] = "2026-07-01-some-card"
    data = {"version": 1, "pending": [entry]}
    with pytest.raises(ValidationError):
        validate(data, "pending_corrections")


def test_company_targeted_entry_may_still_carry_a_card_id_if_ever_present():
    """The schema fix only makes card_id conditionally NOT required for a
    company target -- it never forbids it (additionalProperties already
    allows the field; this just confirms the if/then/else doesn't
    accidentally add a `not` constraint)."""
    entry = _company_entry("c-1")
    entry["card_id"] = "2026-07-01-some-card"
    data = {"version": 1, "pending": [entry]}
    validate(data, "pending_corrections")  # must not raise


def test_append_then_drain_then_save_then_load_round_trip(tmp_path):
    path = tmp_path / "pending_corrections.json"

    data = load_pending_corrections(path)  # missing file -> empty shape
    data = append_pending_correction(data, _entry("c-1"))
    data = append_pending_correction(data, _entry("c-2"))
    save_pending_corrections(data, path)

    reloaded = load_pending_corrections(path)
    assert [e["id"] for e in reloaded["pending"]] == ["c-1", "c-2"]

    # The analyst "actions" c-1 (confirmed-and-corrected, or rejected --
    # either way it's drained) but leaves c-2 pending for a later run.
    reloaded = drain_pending_correction(reloaded, "c-1")
    save_pending_corrections(reloaded, path)

    final = load_pending_corrections(path)
    assert [e["id"] for e in final["pending"]] == ["c-2"]
    validate(final, "pending_corrections")
