"""Schema tests for `schemas/audit.schema.json` (Phase 5's `auditor/report.py`
artifact), following the same three-part convention every other schema in
this repo already uses (`tests/test_schemas.py`, `tests/test_p2_schemas.py`):
schema self-validation, a valid fixture passes, an invalid fixture fails.

`fixtures/schema_examples/valid/audit.json` is a full, hand-built
`data/audit/latest.json`-shaped instance covering every one of the eight
checkers' own nested shapes at least once (the original five, plus
Phase 9's `hijacked_links`/`company_hijacked_links`/`profile_staleness`;
including a `null` `http_status`/`matched_card_id`/etc. case and a
non-trivial `dead`/`missed`/`duplicate_pairs`/`hijacked`/`stale` finding
each), so this also doubles as a second, independent proof (beyond
`tests/test_auditor_report.py`'s own `build_report`-produced instance)
that the schema's nested `$defs` and `additionalProperties: false`
constraints are actually satisfiable by real data, not just internally
self-consistent.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "schema_examples"


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_audit_schema_file_is_valid_draft_2020_12():
    schema = _load(SCHEMAS_DIR / "audit.schema.json")
    Draft202012Validator.check_schema(schema)


def test_audit_valid_fixture_passes():
    instance = _load(FIXTURES_DIR / "valid" / "audit.json")
    validate(instance, "audit")  # must not raise


def test_audit_invalid_fixture_fails_on_missing_required_field():
    instance = _load(FIXTURES_DIR / "invalid" / "audit.json")
    with pytest.raises(ValidationError):
        validate(instance, "audit")


def test_audit_invalid_fixture_is_missing_run_id_specifically():
    """Pin down *which* required field the invalid fixture omits, so this
    test doesn't silently keep passing if a future edit accidentally
    removes a different, unrelated field instead."""
    instance = _load(FIXTURES_DIR / "invalid" / "audit.json")
    assert "run_id" not in instance


def test_audit_valid_fixture_rejects_extra_top_level_field():
    """`additionalProperties: false` is enforced at the top level -- a
    stray extra key should fail validation, not be silently ignored."""
    instance = _load(FIXTURES_DIR / "valid" / "audit.json")
    instance["unexpected_field"] = "should not be allowed"
    with pytest.raises(ValidationError):
        validate(instance, "audit")


def test_audit_schema_rejects_bad_trend_enum_value():
    instance = _load(FIXTURES_DIR / "valid" / "audit.json")
    instance["verifier_trend"]["trend"] = "not_a_real_value"
    with pytest.raises(ValidationError):
        validate(instance, "audit")
