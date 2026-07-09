"""Schema tests for every Phase 1 data artifact.

For each schema under schemas/*.schema.json this module checks three
things:

1. The schema itself is a well-formed Draft 2020-12 schema
   (``Draft202012Validator.check_schema``).
2. A valid fixture instance (fixtures/schema_examples/valid/<name>.json)
   passes validation.
3. An invalid fixture instance missing a required field
   (fixtures/schema_examples/invalid/<name>.json) fails validation.

``watcher.schema_validate.validate`` is exercised directly rather than
calling jsonschema ourselves, so this suite also doubles as coverage for
that thin wrapper (including that the raised error names the artifact).
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "schema_examples"

# Every Phase 1 schema: the three spec sec-5 exact schemas plus the four
# spec-silent ones whose shapes are decided in schemas/*.schema.json's own
# "description" field and logged in IMPROVEMENT_BACKLOG.md.
SCHEMA_NAMES = [
    "card",
    "frontier_board",
    "lexicon",
    "whats_moving",
    "ledger",
    "queue",
    "corrections",
]


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_schema_file_is_valid_draft_2020_12(schema_name):
    schema = _load(SCHEMAS_DIR / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_valid_fixture_passes(schema_name):
    instance = _load(FIXTURES_DIR / "valid" / f"{schema_name}.json")
    validate(instance, schema_name)  # must not raise


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_invalid_fixture_fails_on_missing_required_field(schema_name):
    instance = _load(FIXTURES_DIR / "invalid" / f"{schema_name}.json")
    with pytest.raises(ValidationError):
        validate(instance, schema_name)


def test_validation_error_names_the_artifact():
    instance = _load(FIXTURES_DIR / "invalid" / "card.json")
    with pytest.raises(ValidationError) as exc_info:
        validate(instance, "card")
    assert "card" in str(exc_info.value)


def test_unknown_schema_name_raises_file_not_found():
    from watcher.schema_validate import load_schema

    with pytest.raises(FileNotFoundError):
        load_schema("not_a_real_schema")
