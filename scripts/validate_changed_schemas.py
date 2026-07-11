#!/usr/bin/env python3
"""CI gate: schema-validate every changed JSON file under content/ or data/.

Companion to ``scripts/check_path_allowlist.py`` -- both run before the
commit step of the daily analyst/verifier workflow (per CLAUDE.md's schema
& test conventions: "All persisted JSON ... has a corresponding JSON
Schema under schemas/ and is validated with jsonschema before it is ever
committed"). This script maps each changed ``.json`` file to its
``schemas/<name>.schema.json`` and validates it with
``watcher.schema_validate.validate``, collecting every failure across every
changed file before reporting -- so a single run tells you about every
broken file at once, not just the first one found.

A changed ``.json`` file with no known mapping (e.g. a fixture under
``fixtures/``, or any other JSON file not named below) is silently skipped:
only the persisted content/data artifacts that actually have a schema are
checked here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema

# Allow running as `python scripts/validate_changed_schemas.py` (no package
# install / no `-m` needed) by putting the repo root on sys.path before
# importing the watcher package -- same convention as
# scripts/run_watcher_live.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from watcher.schema_validate import validate  # noqa: E402

# Exact repo-relative path -> schema name (schemas/<name>.schema.json).
# Checked before the content/cards/ prefix rule below, since
# content/cards/index.json would otherwise also match that prefix.
EXACT_PATH_SCHEMAS = {
    "content/cards/index.json": "card_index",
    "content/frontier_board.json": "frontier_board",
    "content/lexicon.json": "lexicon",
    "content/corrections.json": "corrections",
    "content/primer.json": "primer",
    "data/ledger.json": "ledger",
    "data/queue.json": "queue",
    "data/run_plan.json": "run_plan",
    "data/verifier_stats.json": "verifier_stats",
    "data/pending_corrections.json": "pending_corrections",
    # data/whats_moving.json isn't in this turn's task-provided mapping
    # table, but it is a real, already-committed data/ artifact with its
    # own pre-existing schema (schemas/whats_moving.schema.json, built in
    # Phase 1) -- omitting it would leave a real CI gap (watch.yml commits
    # this file daily). Added here as the simplest reasonable extension of
    # the given table; logged in IMPROVEMENT_BACKLOG.md.
    "data/whats_moving.json": "whats_moving",
}

# Any JSON file directly under content/cards/ (other than index.json,
# handled above) is a single published card.
CARDS_DIR_PREFIX = "content/cards/"


def schema_name_for_path(path: str) -> str | None:
    """Return the schema name (matching schemas/<name>.schema.json) that
    `path` maps to, or None if this JSON file has no known mapping."""
    normalized = path.replace("\\", "/")
    if normalized in EXACT_PATH_SCHEMAS:
        return EXACT_PATH_SCHEMAS[normalized]
    if normalized.startswith(CARDS_DIR_PREFIX) and normalized.endswith(".json"):
        return "card"
    return None


def get_changed_files(ref: str = "HEAD") -> list[str]:
    """Return the changed file paths in the working-tree diff against
    `ref`, via ``git diff --name-only --no-renames`` -- identical
    mechanism to ``check_path_allowlist.get_changed_files`` (see that
    module's docstring for why ``--no-renames`` matters here too: it keeps
    a renamed file's old and new paths as separate, independently-checked
    entries)."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", ref],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def validate_changed_files(
    paths: list[str], repo_root: Path = REPO_ROOT
) -> list[str]:
    """Validate every changed ``.json`` file that maps to a known schema.

    Returns a list of human-readable error strings, one per failing file --
    every changed file is checked before returning, so a caller can report
    every failure from one run rather than stopping at the first. A path
    that doesn't end in ``.json``, has no schema mapping, or no longer
    exists on disk (deleted, or the old half of a rename reported via
    ``--no-renames``) is silently skipped: there is nothing to validate.
    """
    errors: list[str] = []
    for path in paths:
        if not path.endswith(".json"):
            continue
        schema_name = schema_name_for_path(path)
        if schema_name is None:
            continue
        file_path = repo_root / path
        if not file_path.is_file():
            continue
        try:
            with file_path.open("r", encoding="utf-8") as f:
                instance = json.load(f)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}: invalid JSON ({exc})")
            continue
        try:
            validate(instance, schema_name)
        except jsonschema.ValidationError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def main() -> int:
    changed_files = get_changed_files()
    errors = validate_changed_files(changed_files)
    if errors:
        print("Schema validation failed for changed file(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
