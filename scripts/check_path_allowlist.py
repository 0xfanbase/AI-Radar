#!/usr/bin/env python3
"""CI gate: fail if any changed file falls outside content/ or data/.

Per CLAUDE.md's `/content` vs `/data` boundary rule, the automated daily
analyst/verifier job (and anything else that auto-commits) may touch only
``content/`` and ``data/`` -- a diff that touches workflows, ``watcher/``,
``schemas/``, ``scripts/``, or CLAUDE.md itself must fail this gate before
anything is ever committed. This is the concrete mechanism behind the
project's prompt-injection guarantee: at absolute worst, a hostile input can
influence the text of one card, never the pipeline that produces it.

This script computes the set of changed files via the shared
``scripts/_git_changes.py`` helper -- the working-tree diff against HEAD
*plus* every untracked, non-ignored file, since this runs pre-commit,
before ``analyze.yml``'s ``git add`` has staged anything (a brand-new
file is untracked at that point and a tracked-only diff would never see
it) -- and exits nonzero, printing every offending path, if any changed
file lies outside ``content/`` or ``data/``. Exits 0 if every changed
file is allowed (including the trivial case of no changes at all).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Allow running as `python scripts/check_path_allowlist.py` (no package
# install / no `-m` needed) -- same sys.path convention as every sibling
# script here (scripts/validate_changed_schemas.py, ...).
sys.path.insert(0, str(REPO_ROOT))

from scripts._git_changes import get_changed_files  # noqa: E402, F401

# Top-level directories the automated commit step may write to. Trailing
# slash is deliberate: it makes the prefix check exact-directory-scoped, so
# a near-miss like "contents/" or "datafoo/" is correctly rejected.
ALLOWED_PREFIXES = ("content/", "data/")


def is_allowed(path: str) -> bool:
    """True if `path` (a repo-relative, forward-slash-separated path as
    `git diff --name-only` reports it) lies inside one of the allowlisted
    top-level directories."""
    normalized = path.replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def find_violations(changed_files: list[str]) -> list[str]:
    """Return the subset of `changed_files` that fall outside the
    allowlist, preserving their original order. An empty input list (no
    changes at all) yields an empty (passing) result."""
    return [f for f in changed_files if f and not is_allowed(f)]


def main() -> int:
    changed_files = get_changed_files()
    violations = find_violations(changed_files)
    if violations:
        print(
            "Path allowlist violation: changed file(s) outside content/ "
            "and data/:",
            file=sys.stderr,
        )
        for path in violations:
            print(f"  {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
