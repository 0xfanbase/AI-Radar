"""Tests for the 2026-07 schema-hardening pass.

Covers three related fixes:

1. ``watcher.schema_validate.validate`` now always passes a
   ``FormatChecker`` -- previously every ``"format"`` keyword (``uri``,
   ``date-time``, ...) was a silent no-op at the commit gate, so garbage
   citation URLs and timestamps validated clean. Requires the
   ``jsonschema[format]`` extras pinned in requirements.txt (the
   ``uri``/``date-time`` checkers come from rfc3987/rfc3339-validator).
2. ``schemas/trusted_domains.schema.json`` -- data/trusted_domains.json
   was the one persisted data/ artifact with no schema (every site build
   logged a warning). Same three-part convention as tests/test_schemas.py,
   plus a check that the real committed file validates.
3. Pattern locks: ``run_plan``'s ``proposed_card_id`` (concatenated into
   a filesystem path by scripts/reconcile_run.py -- traversal sequences
   must fail validation) and ``card``'s ``date`` (fed to
   ``date.fromisoformat`` by site/builders/wire.py -- non-ISO values must
   fail validation, not crash a build).
"""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
FIXTURES_DIR = REPO_ROOT / "fixtures" / "schema_examples"
DATA_DIR = REPO_ROOT / "data"


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 1. Format enforcement is live, not a no-op.
# --------------------------------------------------------------------------


def test_format_extras_are_installed():
    # If the jsonschema[format] extras are missing, FormatChecker has no
    # "uri"/"date-time" checkers registered and silently skips both --
    # the exact silent no-op this pass fixed. Fail loudly here instead.
    checkers = FormatChecker().checkers
    assert "uri" in checkers, "rfc3987 missing -- install jsonschema[format]"
    assert "date-time" in checkers, (
        "rfc3339-validator missing -- install jsonschema[format]"
    )


def _valid_card() -> dict:
    return _load(FIXTURES_DIR / "valid" / "card.json")


def test_garbage_date_time_now_fails_card_validation():
    card = _valid_card()
    card["generated_at"] = "definitely not a timestamp"
    with pytest.raises(ValidationError):
        validate(card, "card")


def test_out_of_range_date_time_now_fails_card_validation():
    card = _valid_card()
    card["generated_at"] = "2026-99-99T99:99:99Z"
    with pytest.raises(ValidationError):
        validate(card, "card")


def test_garbage_uri_now_fails_card_validation():
    card = _valid_card()
    card["citations"][0]["url"] = "not a url at all"
    with pytest.raises(ValidationError):
        validate(card, "card")


def test_real_timestamp_shapes_still_pass():
    # Both timestamp shapes the pipeline actually emits (offset and Z).
    for stamp in ("2026-07-19T01:22:55.157733+00:00", "2026-07-19T01:22:37Z"):
        card = _valid_card()
        card["generated_at"] = stamp
        validate(card, "card")  # must not raise


# --------------------------------------------------------------------------
# 2. trusted_domains schema -- same three-part convention as
#    tests/test_schemas.py, plus the real committed file.
# --------------------------------------------------------------------------


def test_trusted_domains_schema_is_valid_draft_2020_12():
    schema = _load(SCHEMAS_DIR / "trusted_domains.schema.json")
    Draft202012Validator.check_schema(schema)


def test_trusted_domains_valid_fixture_passes():
    instance = _load(FIXTURES_DIR / "valid" / "trusted_domains.json")
    validate(instance, "trusted_domains")  # must not raise


def test_trusted_domains_invalid_fixture_fails():
    instance = _load(FIXTURES_DIR / "invalid" / "trusted_domains.json")
    with pytest.raises(ValidationError):
        validate(instance, "trusted_domains")


def test_real_committed_trusted_domains_file_validates():
    instance = _load(DATA_DIR / "trusted_domains.json")
    validate(instance, "trusted_domains")  # must not raise


def test_trusted_domains_is_mapped_in_changed_schema_gate():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import validate_changed_schemas as gate

    assert (
        gate.EXACT_PATH_SCHEMAS.get("data/trusted_domains.json")
        == "trusted_domains"
    )


# --------------------------------------------------------------------------
# 3. Pattern locks.
# --------------------------------------------------------------------------


def _valid_run_plan() -> dict:
    return _load(FIXTURES_DIR / "valid" / "run_plan.json")


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../../etc/passwd",
        "2026-07-24-../escape",
        "no-date-prefix",
        "2026-07-24-UPPER-CASE",
        "2026-07-24-slash/inside",
    ],
)
def test_run_plan_rejects_non_slug_proposed_card_id(bad_id):
    plan = _valid_run_plan()
    plan["clusters"][0]["proposed_card_id"] = bad_id
    with pytest.raises(ValidationError):
        validate(plan, "run_plan")


def test_run_plan_accepts_real_computed_card_id():
    # Exactly what scripts/plan_run.py::compute_proposed_card_id emits:
    # <YYYY-MM-DD>-<kebab-slug>-<cluster_hash[:6]>.
    plan = _valid_run_plan()
    plan["clusters"][0]["proposed_card_id"] = "2026-07-24-some-model-release-9f2c1e"
    validate(plan, "run_plan")  # must not raise


@pytest.mark.parametrize("bad_date", ["07/24/2026", "2026-7-4", "20260724", "garbage"])
def test_card_rejects_non_iso_date(bad_date):
    card = _valid_card()
    card["date"] = bad_date
    with pytest.raises(ValidationError):
        validate(card, "card")
