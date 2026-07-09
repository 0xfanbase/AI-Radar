"""Tests for scripts/check_path_allowlist.py.

Exercises the pure `is_allowed` / `find_violations` functions directly
against fixture diff lists -- lists of repo-relative path strings standing
in for what `git diff --name-only --no-renames HEAD` would report -- per
this turn's task scope: content/data-only changes (pass), a change
touching .github/workflows/*.yml or watcher/*.py (fail), renames, and
deletes. A couple of `main()`-level tests (with `get_changed_files`
monkeypatched) confirm the exit code and stderr wiring, and one smoke test
exercises the real `git diff` invocation against this repo's own history.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_path_allowlist as mod  # noqa: E402
from check_path_allowlist import find_violations, get_changed_files, is_allowed  # noqa: E402


# --------------------------------------------------------------------------
# is_allowed
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "content/cards/2026-07-09-example.json",
        "content/cards/index.json",
        "content/frontier_board.json",
        "content/lexicon.json",
        "content/corrections.json",
        "data/ledger.json",
        "data/queue.json",
        "data/whats_moving.json",
        "data/.cache/somehash.json",
        "data/audit/history/2026-07-05.json",
    ],
)
def test_is_allowed_true_for_content_and_data_paths(path):
    assert is_allowed(path)


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/analyze.yml",
        ".github/workflows/watch.yml",
        ".github/workflows/ci.yml",
        "watcher/cli.py",
        "watcher/sources/hn.py",
        "schemas/card.schema.json",
        "scripts/check_path_allowlist.py",
        "scripts/validate_changed_schemas.py",
        "CLAUDE.md",
        "PROGRESS.md",
        "requirements.txt",
        "site/generate.py",
        "contents/not_actually_content.json",  # near-miss prefix
        "datafoo/not_actually_data.json",  # near-miss prefix
    ],
)
def test_is_allowed_false_for_paths_outside_allowlist(path):
    assert not is_allowed(path)


# --------------------------------------------------------------------------
# find_violations -- fixture diff lists
# --------------------------------------------------------------------------


def test_find_violations_empty_diff_passes():
    assert find_violations([]) == []


def test_find_violations_content_and_data_only_changes_pass():
    diff = [
        "content/cards/2026-07-09-example.json",
        "content/cards/index.json",
        "content/lexicon.json",
        "content/frontier_board.json",
        "data/ledger.json",
        "data/queue.json",
        "data/verifier_stats.json",
    ]
    assert find_violations(diff) == []


def test_find_violations_workflow_change_fails():
    diff = ["content/lexicon.json", ".github/workflows/analyze.yml"]
    assert find_violations(diff) == [".github/workflows/analyze.yml"]


def test_find_violations_watcher_code_change_fails():
    diff = ["data/queue.json", "watcher/ranking.py"]
    assert find_violations(diff) == ["watcher/ranking.py"]


def test_find_violations_schema_change_fails():
    diff = ["content/lexicon.json", "schemas/lexicon.schema.json"]
    assert find_violations(diff) == ["schemas/lexicon.schema.json"]


def test_find_violations_claude_md_change_fails():
    diff = ["data/ledger.json", "CLAUDE.md"]
    assert find_violations(diff) == ["CLAUDE.md"]


def test_find_violations_multiple_offenders_all_reported_in_order():
    diff = [
        "watcher/cli.py",
        "content/lexicon.json",
        ".github/workflows/watch.yml",
        "CLAUDE.md",
    ]
    assert find_violations(diff) == [
        "watcher/cli.py",
        ".github/workflows/watch.yml",
        "CLAUDE.md",
    ]


def test_find_violations_rename_within_allowlist_passes():
    # --no-renames reports a rename as old-path-gone + new-path-added; both
    # land inside content/, so nothing is flagged.
    diff = ["content/cards/2026-07-08-old-id.json", "content/cards/2026-07-09-new-id.json"]
    assert find_violations(diff) == []


def test_find_violations_rename_to_outside_allowlist_fails():
    # A file "moved" from content/ to watcher/ -- the new path lands
    # outside the allowlist and must be caught.
    diff = ["content/cards/2026-07-08-old-id.json", "watcher/new_module.py"]
    assert find_violations(diff) == ["watcher/new_module.py"]


def test_find_violations_rename_from_outside_allowlist_still_flagged():
    # The old path (outside content/data) is itself part of the diff
    # (thanks to --no-renames), so its disappearance from outside the
    # allowlist is still caught even though the new path is fine.
    diff = ["watcher/old_module.py", "content/cards/2026-07-09-new-id.json"]
    assert find_violations(diff) == ["watcher/old_module.py"]


def test_find_violations_delete_within_allowlist_passes():
    diff = ["data/queue.json"]  # deleted, but was inside data/
    assert find_violations(diff) == []


def test_find_violations_delete_outside_allowlist_fails():
    diff = ["watcher/old_module.py"]  # deleted, was outside content/data
    assert find_violations(diff) == ["watcher/old_module.py"]


def test_find_violations_mixed_renames_and_deletes():
    diff = [
        "data/queue.json",  # deleted, inside data/ -- fine
        "watcher/dead_module.py",  # deleted, outside -- violation
        "content/cards/2026-07-08-a.json",  # renamed away (old half)
        "content/cards/2026-07-09-a.json",  # renamed to (new half) -- fine
        ".github/workflows/ci.yml",  # modified -- violation
    ]
    assert find_violations(diff) == ["watcher/dead_module.py", ".github/workflows/ci.yml"]


# --------------------------------------------------------------------------
# main() -- exit code / stderr wiring
# --------------------------------------------------------------------------


def test_main_returns_zero_when_no_violations(monkeypatch):
    monkeypatch.setattr(
        mod, "get_changed_files", lambda: ["content/lexicon.json", "data/queue.json"]
    )
    assert mod.main() == 0


def test_main_returns_nonzero_and_prints_violations(monkeypatch, capsys):
    monkeypatch.setattr(
        mod, "get_changed_files", lambda: ["watcher/cli.py", "content/lexicon.json"]
    )
    exit_code = mod.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "watcher/cli.py" in captured.err


def test_main_empty_diff_passes(monkeypatch):
    monkeypatch.setattr(mod, "get_changed_files", lambda: [])
    assert mod.main() == 0


# --------------------------------------------------------------------------
# get_changed_files -- real git smoke test
# --------------------------------------------------------------------------


def test_get_changed_files_returns_list_of_strings():
    # Smoke test against this repo's own last real commit (rather than the
    # live working tree) so the test's outcome doesn't depend on whatever
    # local edits happen to be pending when it runs.
    changed = get_changed_files(ref="HEAD~1")
    assert isinstance(changed, list)
    assert all(isinstance(p, str) and p for p in changed)
