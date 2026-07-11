"""Schema tests for Phase 2's ledger extension and four new schemas.

Follows the same three-part convention as tests/test_schemas.py (schema
self-validation, a valid fixture passes, an invalid fixture fails), plus
extra coverage specific to this turn's scope:

- schemas/ledger.schema.json's Phase 2 extension (structured
  verifier_outcome, and the new if/then enforcing that a status=='dropped'
  entry's card_id stays null) is exercised with its own fixtures
  (fixtures/schema_examples/{valid,invalid}/ledger_dropped.json) in
  addition to the pre-existing ledger.json fixtures already covered by
  test_schemas.py.
- The real, committed data/ledger.json (Phase 1's 104-entry live-verified
  ledger; the plan's own build log records earlier snapshots at 54, before
  the arXiv robots.txt fix in round 2 grew it to 104 -- see PROGRESS.md)
  still validates unchanged against the extended schema, proving the
  extension is additive/backward-compatible rather than breaking.
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "schema_examples"
DATA_DIR = REPO_ROOT / "data"

# The four brand-new Phase 2 schemas. ledger's *extension* is covered by
# its own dedicated tests below, since it isn't a new schema name.
NEW_SCHEMA_NAMES = [
    "card_index",
    "run_plan",
    "verifier_stats",
    "pending_corrections",
]


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("schema_name", NEW_SCHEMA_NAMES)
def test_new_schema_file_is_valid_draft_2020_12(schema_name):
    schema = _load(SCHEMAS_DIR / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("schema_name", NEW_SCHEMA_NAMES)
def test_new_schema_valid_fixture_passes(schema_name):
    instance = _load(FIXTURES_DIR / "valid" / f"{schema_name}.json")
    validate(instance, schema_name)  # must not raise


@pytest.mark.parametrize("schema_name", NEW_SCHEMA_NAMES)
def test_new_schema_invalid_fixture_fails(schema_name):
    instance = _load(FIXTURES_DIR / "invalid" / f"{schema_name}.json")
    with pytest.raises(ValidationError):
        validate(instance, schema_name)


# --------------------------------------------------------------------------
# ledger.schema.json Phase 2 extension
# --------------------------------------------------------------------------


def test_ledger_schema_is_still_valid_draft_2020_12():
    schema = _load(SCHEMAS_DIR / "ledger.schema.json")
    Draft202012Validator.check_schema(schema)


def test_ledger_dropped_valid_fixture_passes():
    """A status='dropped' entry with card_id null and a structured
    verifier_outcome ({last_attempted_at, dropped_reason,
    demoted_from_confirmed}) validates."""
    instance = _load(FIXTURES_DIR / "valid" / "ledger_dropped.json")
    validate(instance, "ledger")


def test_ledger_dropped_with_nonnull_card_id_fails():
    """The new if/then enforces: status=='dropped' => card_id must stay
    null permanently. A dropped entry that somehow carries a non-null
    card_id is rejected."""
    instance = _load(FIXTURES_DIR / "invalid" / "ledger_dropped.json")
    with pytest.raises(ValidationError):
        validate(instance, "ledger")


def test_ledger_verifier_outcome_requires_last_attempted_at():
    """verifier_outcome, when present as a non-null object, must at least
    carry last_attempted_at -- dropped_reason/demoted_from_confirmed stay
    optional."""
    instance = {
        "version": 1,
        "entries": {
            "9f2c1e4d7a6b5c3e8f1a0d9b2c4e6f8a1b3d5c7e9f0a2b4c6d8e0f1a3b5c7d9e": {
                "card_id": None,
                "status": "dropped",
                "first_seen": "2026-07-05",
                "last_seen": "2026-07-06",
                "member_urls": ["https://example-news.test/2026/07/05/rumored-model"],
                "verifier_outcome": {
                    "dropped_reason": "no citation survived re-fetch",
                },
            }
        },
    }
    with pytest.raises(ValidationError):
        validate(instance, "ledger")


def test_ledger_verifier_outcome_demoted_from_confirmed_only():
    """A verifier_outcome with just last_attempted_at + demoted_from_confirmed
    (no dropped_reason -- the card wasn't dropped, only demoted) is valid,
    including with status still 'published' (a demoted-but-not-dropped
    card keeps its card_id and 'published' status)."""
    instance = {
        "version": 1,
        "entries": {
            "9f2c1e4d7a6b5c3e8f1a0d9b2c4e6f8a1b3d5c7e9f0a2b4c6d8e0f1a3b5c7d9e": {
                "card_id": "2026-07-06-example-card",
                "status": "published",
                "first_seen": "2026-07-05",
                "last_seen": "2026-07-06",
                "member_urls": ["https://example-news.test/2026/07/05/rumored-model"],
                "verifier_outcome": {
                    "last_attempted_at": "2026-07-06T10:00:00Z",
                    "demoted_from_confirmed": True,
                },
            }
        },
    }
    validate(instance, "ledger")  # must not raise


def test_ledger_entry_without_verifier_outcome_still_valid():
    """verifier_outcome remains fully optional -- an entry that never
    mentions the key at all (every Phase 1 entry) is still valid."""
    instance = {
        "version": 1,
        "entries": {
            "9f2c1e4d7a6b5c3e8f1a0d9b2c4e6f8a1b3d5c7e9f0a2b4c6d8e0f1a3b5c7d9e": {
                "card_id": None,
                "status": "queued",
                "first_seen": "2026-07-08",
                "last_seen": "2026-07-09",
                "member_urls": ["https://example-lab.test/blog/example-model-5"],
            }
        },
    }
    validate(instance, "ledger")  # must not raise


def test_real_committed_ledger_still_validates_unchanged():
    """The actual data/ledger.json committed by Phase 1 (a live-verified,
    54-entry initial run later regrown to 104 entries once the arXiv
    robots.txt fetch-discipline fix landed -- see PROGRESS.md's Phase 1
    round 2 checkpoint) must still validate, byte-for-byte unmodified by
    this schema extension, against the extended schema."""
    real_ledger = _load(DATA_DIR / "ledger.json")
    assert real_ledger["version"] == 1
    assert len(real_ledger["entries"]) > 0, "expected the real Phase 1 ledger to be non-empty"
    validate(real_ledger, "ledger")  # must not raise


def test_real_ledger_has_no_dropped_entries_yet():
    """Sanity check on the real data: Phase 2 hasn't run yet, so every
    real entry today is 'queued' (none 'published' or 'dropped') -- this
    is what makes the new if/then conditional safe to add without
    touching the committed file."""
    real_ledger = _load(DATA_DIR / "ledger.json")
    statuses = {entry["status"] for entry in real_ledger["entries"].values()}
    assert statuses == {"queued"}


# --------------------------------------------------------------------------
# seed data files
# --------------------------------------------------------------------------


def test_seed_verifier_stats_validates():
    instance = _load(DATA_DIR / "verifier_stats.json")
    assert instance == {"version": 1, "runs": []}
    validate(instance, "verifier_stats")


def test_seed_pending_corrections_validates():
    instance = _load(DATA_DIR / "pending_corrections.json")
    assert instance == {"version": 1, "pending": []}
    validate(instance, "pending_corrections")


def test_no_run_plan_seed_file_yet():
    """Per this turn's explicit scope: data/run_plan.json is NOT seeded --
    scripts/plan_run.py (a later Phase 2 commit) produces real ones."""
    assert not (DATA_DIR / "run_plan.json").exists()
