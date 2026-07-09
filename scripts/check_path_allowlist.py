#!/usr/bin/env python3
"""CI gate: fail if any changed file falls outside content/ or data/.

Per CLAUDE.md's `/content` vs `/data` boundary rule, the automated daily
analyst/verifier job (and anything else that auto-commits) may touch only
``content/`` and ``data/`` -- a diff that touches workflows, ``watcher/``,
``schemas/``, ``scripts/``, or CLAUDE.md itself must fail this gate before
anything is ever committed. This is the concrete mechanism behind the
project's prompt-injection guarantee: at absolute worst, a hostile input can
influence the text of one card, never the pipeline that produces it.

This script computes the working-tree diff of changed files against HEAD
(``git diff --name-only``, since this is meant to run pre-commit -- i.e.
after changes are staged but before they're committed) and exits nonzero,
printing every offending path, if any changed file lies outside
``content/`` or ``data/``. Exits 0 if every changed file is allowed
(including the trivial case of no changes at all).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

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


def get_changed_files(ref: str = "HEAD") -> list[str]:
    """Return the changed file paths in the working-tree diff against
    `ref`, via ``git diff --name-only --no-renames``.

    ``--no-renames`` is deliberate: without it, git may collapse a renamed
    file to a single "new path only" line (governed by the repo's/user's
    rename-detection settings), which would let a file moved *out* of the
    allowlist silently escape this check if only the new path were ever
    inspected. With ``--no-renames``, a rename is reported as a plain
    delete-of-old-path + add-of-new-path pair, so both the old and the new
    path are independently checked against the allowlist.
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


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
