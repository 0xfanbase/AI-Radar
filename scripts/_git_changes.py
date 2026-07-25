#!/usr/bin/env python3
"""Shared "which files changed?" helper for the pre-commit CI gates.

One implementation, used by all three gates
(``scripts/check_path_allowlist.py``, ``scripts/validate_changed_schemas.py``,
``scripts/check_outbound_links.py``) instead of the three previously
duplicated copies -- so a correctness fix here lands in every gate at once.

Why this is not just ``git diff --name-only HEAD``: that diff only reports
changes to *already-tracked* files. In ``analyze.yml`` the gates run
*before* the commit step's ``git add content/ data/``, so a brand-new file
(e.g. the first ``content/cards/<id>.json`` a run ever writes) is still
untracked at gate time and would be completely invisible to a
tracked-files-only diff -- its path never allowlist-checked, its schema
never validated, its citation URLs never vetted. That silently defeats the
exact mechanism CLAUDE.md names as the project's prompt-injection
guarantee. This helper therefore returns the union of:

1. ``git diff --name-only --no-renames <ref>`` -- every tracked file whose
   working-tree content differs from ``ref``. ``--no-renames`` is
   deliberate: without it, git may collapse a renamed file to a single
   "new path only" line (governed by rename-detection settings), which
   would let a file moved *out* of the allowlist silently escape a check
   that only ever saw the new path. With it, a rename is reported as a
   plain delete-of-old-path + add-of-new-path pair, so both paths are
   independently checked.
2. ``git ls-files --others --exclude-standard`` -- every untracked file
   that is not ignored. ``--exclude-standard`` applies the normal
   ``.gitignore`` rules, so build caches (``data/.cache/``) and other
   ignored artifacts stay out; anything a run newly created that *would*
   be swept up by the commit step's ``git add`` is in.

Order is preserved (tracked diff first, then untracked), deduplicated.
Untracked files are ref-independent -- they exist in no ref's tree -- so
including them is correct for any ``ref`` a caller passes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_lines(args: list[str], repo_root: Path) -> list[str]:
    """Run ``git <args>`` in `repo_root` and return non-blank output lines."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def get_changed_files(ref: str = "HEAD", repo_root: Path = REPO_ROOT) -> list[str]:
    """Every repo-relative path a pre-commit gate must inspect: the
    working-tree diff of tracked files against `ref` (``--no-renames``,
    see module docstring) plus every untracked, non-ignored file --
    deduplicated, diff order first, then untracked order."""
    tracked = _git_lines(["diff", "--name-only", "--no-renames", ref], repo_root)
    untracked = _git_lines(["ls-files", "--others", "--exclude-standard"], repo_root)
    seen: set[str] = set()
    combined: list[str] = []
    for path in (*tracked, *untracked):
        if path not in seen:
            seen.add(path)
            combined.append(path)
    return combined
