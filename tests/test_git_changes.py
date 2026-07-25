"""Tests for scripts/_git_changes.py -- the shared changed-files helper
behind all three pre-commit CI gates.

These exercise *real git state* in a throwaway repo (init/commit/modify/
delete/untracked/ignored), because the bug this helper exists to fix --
``git diff HEAD`` being structurally blind to never-yet-tracked files, so
a brand-new ``content/cards/<id>.json`` sailed past every gate -- was
invisible to any test that only fed fixture path-lists to the gates'
downstream functions. The end-to-end tests at the bottom prove the two
cheap gates now actually see an untracked file (the outbound-link gate
shares the identical helper import and needs the network, so it is
covered by the same mechanism without a live test here).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from scripts._git_changes import get_changed_files  # noqa: E402

import check_path_allowlist  # noqa: E402
import validate_changed_schemas  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def temp_repo(tmp_path: Path) -> Path:
    """A real git repo with one committed file (tracked.txt) at HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_clean_tree_reports_nothing(temp_repo: Path):
    assert get_changed_files(repo_root=temp_repo) == []


def test_modified_tracked_file_is_reported(temp_repo: Path):
    (temp_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert get_changed_files(repo_root=temp_repo) == ["tracked.txt"]


def test_deleted_tracked_file_is_reported(temp_repo: Path):
    (temp_repo / "tracked.txt").unlink()
    assert get_changed_files(repo_root=temp_repo) == ["tracked.txt"]


def test_untracked_new_file_is_reported(temp_repo: Path):
    # The load-bearing regression test: a brand-new, never-committed file
    # (what every first-time card/lexicon/board artifact is at gate time,
    # since analyze.yml's `git add` runs *after* the gates) must appear.
    # A plain `git diff --name-only HEAD` reports nothing for it.
    (temp_repo / "brand_new.json").write_text("{}\n", encoding="utf-8")
    assert get_changed_files(repo_root=temp_repo) == ["brand_new.json"]


def test_untracked_file_in_untracked_directory_is_reported(temp_repo: Path):
    # First-ever analyst run: content/cards/ itself doesn't exist yet, so
    # both the directory and the file are new. `git ls-files --others`
    # must expand the untracked directory to its individual files.
    cards = temp_repo / "content" / "cards"
    cards.mkdir(parents=True)
    (cards / "2026-07-24-first-card.json").write_text("{}\n", encoding="utf-8")
    assert get_changed_files(repo_root=temp_repo) == [
        "content/cards/2026-07-24-first-card.json"
    ]


def test_gitignored_untracked_file_is_excluded(temp_repo: Path):
    (temp_repo / ".gitignore").write_text("ignored_dir/\n", encoding="utf-8")
    _git(temp_repo, "add", ".gitignore")
    _git(temp_repo, "commit", "-m", "add gitignore")
    ignored = temp_repo / "ignored_dir"
    ignored.mkdir()
    (ignored / "cache_blob.json").write_text("{}\n", encoding="utf-8")
    assert get_changed_files(repo_root=temp_repo) == []


def test_tracked_and_untracked_union_dedup_and_order(temp_repo: Path):
    (temp_repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (temp_repo / "new_file.txt").write_text("new\n", encoding="utf-8")
    changed = get_changed_files(repo_root=temp_repo)
    # Tracked diff entries first, untracked after; no duplicates.
    assert changed == ["tracked.txt", "new_file.txt"]
    assert len(changed) == len(set(changed))


def test_ref_parameter_diffs_against_named_ref(temp_repo: Path):
    (temp_repo / "second.txt").write_text("second\n", encoding="utf-8")
    _git(temp_repo, "add", "second.txt")
    _git(temp_repo, "commit", "-m", "second commit")
    changed = get_changed_files(ref="HEAD~1", repo_root=temp_repo)
    assert changed == ["second.txt"]


# --------------------------------------------------------------------------
# End-to-end: the actual gates now see untracked files.
# --------------------------------------------------------------------------


def test_path_allowlist_gate_catches_untracked_file_outside_allowlist(temp_repo: Path):
    # An automated run dropping a brand-new file outside content//data/
    # (e.g. a hostile prompt writing a new workflow) must now be caught
    # even though the file is untracked at gate time.
    workflows = temp_repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "evil.yml").write_text("on: push\n", encoding="utf-8")
    changed = check_path_allowlist.get_changed_files(repo_root=temp_repo)
    assert ".github/workflows/evil.yml" in changed
    violations = check_path_allowlist.find_violations(changed)
    assert violations == [".github/workflows/evil.yml"]


def test_schema_gate_validates_untracked_first_time_card(temp_repo: Path):
    # A first-ever card (untracked file in an untracked directory) must be
    # picked up by the changed-files helper and schema-validated -- the
    # exact combination that previously sailed through unchecked.
    cards = temp_repo / "content" / "cards"
    cards.mkdir(parents=True)
    (cards / "2026-07-24-bogus.json").write_text(
        '{"definitely": "not a valid card"}\n', encoding="utf-8"
    )
    changed = validate_changed_schemas.get_changed_files(repo_root=temp_repo)
    assert changed == ["content/cards/2026-07-24-bogus.json"]
    errors = validate_changed_schemas.validate_changed_files(
        changed, repo_root=temp_repo
    )
    assert len(errors) == 1
    assert "content/cards/2026-07-24-bogus.json" in errors[0]
