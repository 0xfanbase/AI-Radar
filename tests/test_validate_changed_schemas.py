"""Tests for scripts/validate_changed_schemas.py.

Exercises the pure `schema_name_for_path` mapping function directly, then
`validate_changed_files` against a small on-disk fixture tree (`tmp_path`
standing in for the repo root) built from the very same
fixtures/schema_examples/{valid,invalid}/<name>.json fixtures already used
by tests/test_schemas.py and tests/test_p2_schemas.py -- one valid/invalid
pair per mapped path, plus coverage for unmapped JSON (fixtures, skipped),
non-.json files (skipped), deletes (changed path no longer on disk --
skipped, not a failure), renames (only the surviving new path is checked),
and multiple simultaneous failures all being collected before reporting.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "fixtures" / "schema_examples"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import validate_changed_schemas as mod  # noqa: E402
from validate_changed_schemas import (  # noqa: E402
    get_changed_files,
    schema_name_for_path,
    validate_changed_files,
)

# path -> (schema_name, fixture_basename under fixtures/schema_examples/)
MAPPED_PATHS = {
    "content/cards/2026-07-09-example-card.json": ("card", "card"),
    "content/cards/index.json": ("card_index", "card_index"),
    "content/frontier_board.json": ("frontier_board", "frontier_board"),
    "content/lexicon.json": ("lexicon", "lexicon"),
    "content/corrections.json": ("corrections", "corrections"),
    "data/ledger.json": ("ledger", "ledger"),
    "data/queue.json": ("queue", "queue"),
    "data/run_plan.json": ("run_plan", "run_plan"),
    "data/verifier_stats.json": ("verifier_stats", "verifier_stats"),
    "data/pending_corrections.json": ("pending_corrections", "pending_corrections"),
    "data/whats_moving.json": ("whats_moving", "whats_moving"),
}


# --------------------------------------------------------------------------
# schema_name_for_path
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected", [(p, s) for p, (s, _) in MAPPED_PATHS.items()]
)
def test_schema_name_for_path_maps_known_paths(path, expected):
    assert schema_name_for_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "content/cards/README.md",  # cards dir, but not .json
        "content/primer.json",  # not in the mapping table (no schema exists yet)
        "watcher/config.py",
        "schemas/card.schema.json",
        ".github/workflows/analyze.yml",
        "fixtures/schema_examples/valid/card.json",
        "data/.cache/somehash.json",
    ],
)
def test_schema_name_for_path_returns_none_for_unmapped(path):
    assert schema_name_for_path(path) is None


# --------------------------------------------------------------------------
# validate_changed_files -- per-schema valid/invalid fixture pairs
# --------------------------------------------------------------------------


def _write_fixture(repo_root: Path, rel_path: str, flavor: str, fixture_name: str) -> None:
    src = FIXTURES_DIR / flavor / f"{fixture_name}.json"
    dest = repo_root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


@pytest.mark.parametrize("path, spec", list(MAPPED_PATHS.items()))
def test_valid_fixture_for_every_mapped_path_passes(tmp_path, path, spec):
    _, fixture_name = spec
    _write_fixture(tmp_path, path, "valid", fixture_name)
    errors = validate_changed_files([path], repo_root=tmp_path)
    assert errors == []


@pytest.mark.parametrize("path, spec", list(MAPPED_PATHS.items()))
def test_invalid_fixture_for_every_mapped_path_fails(tmp_path, path, spec):
    _, fixture_name = spec
    _write_fixture(tmp_path, path, "invalid", fixture_name)
    errors = validate_changed_files([path], repo_root=tmp_path)
    assert len(errors) == 1
    assert path in errors[0]


# --------------------------------------------------------------------------
# skip cases: unmapped JSON, non-JSON, deletes, renames
# --------------------------------------------------------------------------


def test_unmapped_json_fixture_is_skipped_even_if_malformed(tmp_path):
    # A fixture-like JSON file with no schema mapping is skipped outright
    # -- not even parsed -- so it doesn't matter that its content isn't
    # even valid JSON.
    path = "fixtures/some_other_fixture.json"
    dest = tmp_path / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("not even valid json {{{", encoding="utf-8")
    errors = validate_changed_files([path], repo_root=tmp_path)
    assert errors == []


def test_non_json_file_is_skipped(tmp_path):
    errors = validate_changed_files(["watcher/config.py"], repo_root=tmp_path)
    assert errors == []


def test_deleted_mapped_file_is_skipped_not_a_failure(tmp_path):
    # "data/queue.json" is a known mapping, but the file doesn't exist in
    # this tmp_path tree (as it wouldn't on disk after a real delete) --
    # nothing to validate, so this must not be reported as a failure.
    errors = validate_changed_files(["data/queue.json"], repo_root=tmp_path)
    assert errors == []


def test_renamed_file_only_checks_surviving_new_path(tmp_path):
    # --no-renames reports a rename as old-path-gone + new-path-present.
    # The old path here is unmapped anyway (so would be skipped either
    # way); only the new path needs to exist and validate.
    old_path = "content/lexicon_old_name.json"
    new_path = "content/lexicon.json"
    _write_fixture(tmp_path, new_path, "valid", "lexicon")
    errors = validate_changed_files([old_path, new_path], repo_root=tmp_path)
    assert errors == []


def test_renamed_mapped_file_missing_new_path_is_skipped(tmp_path):
    # A rename between two *mapped* paths where the new path's file
    # wasn't actually materialized in this tmp tree -- still skipped
    # (deleted/missing), not a crash.
    errors = validate_changed_files(
        ["data/ledger.json", "data/queue.json"], repo_root=tmp_path
    )
    assert errors == []


def test_multiple_failures_all_collected_before_reporting(tmp_path):
    _write_fixture(tmp_path, "content/lexicon.json", "invalid", "lexicon")
    _write_fixture(tmp_path, "data/queue.json", "invalid", "queue")
    _write_fixture(tmp_path, "content/frontier_board.json", "valid", "frontier_board")
    errors = validate_changed_files(
        [
            "content/lexicon.json",
            "data/queue.json",
            "content/frontier_board.json",
        ],
        repo_root=tmp_path,
    )
    assert len(errors) == 2
    assert any("content/lexicon.json" in e for e in errors)
    assert any("data/queue.json" in e for e in errors)


def test_malformed_json_on_a_mapped_path_is_reported(tmp_path):
    path = "content/lexicon.json"
    dest = tmp_path / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("{not valid json", encoding="utf-8")
    errors = validate_changed_files([path], repo_root=tmp_path)
    assert len(errors) == 1
    assert path in errors[0]


# --------------------------------------------------------------------------
# main() -- exit code / stderr wiring
# --------------------------------------------------------------------------


def test_main_returns_zero_when_no_changed_files(monkeypatch):
    monkeypatch.setattr(mod, "get_changed_files", lambda: [])
    assert mod.main() == 0


def test_main_returns_nonzero_and_prints_errors(monkeypatch, capsys):
    # main() wires get_changed_files() -> validate_changed_files(...) ->
    # report-and-exit-nonzero; patch both collaborators directly so this
    # test only exercises that wiring, not repo_root/default-argument
    # binding details already covered by the validate_changed_files tests
    # above.
    monkeypatch.setattr(mod, "get_changed_files", lambda: ["content/lexicon.json"])
    monkeypatch.setattr(
        mod, "validate_changed_files", lambda paths: ["content/lexicon.json: boom"]
    )
    exit_code = mod.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "content/lexicon.json" in captured.err


# --------------------------------------------------------------------------
# get_changed_files -- real git smoke test
# --------------------------------------------------------------------------


def test_get_changed_files_returns_list_of_strings():
    # Smoke test against this repo's own last real commit (rather than the
    # live working tree) so the test's outcome doesn't depend on whatever
    # local edits happen to be pending when it runs. Skipped, not failed,
    # when only one commit is reachable (e.g. a shallow `actions/checkout`
    # with fetch-depth: 1, or any other truncated-history checkout) --
    # HEAD~1 simply doesn't exist there, and that's an environment fact,
    # not a bug in get_changed_files itself.
    has_prior_commit = (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD~1"],
            cwd=REPO_ROOT,
            capture_output=True,
        ).returncode
        == 0
    )
    if not has_prior_commit:
        pytest.skip("HEAD~1 not reachable in this checkout (shallow clone?)")
    changed = get_changed_files(ref="HEAD~1")
    assert isinstance(changed, list)
    assert all(isinstance(p, str) and p for p in changed)
