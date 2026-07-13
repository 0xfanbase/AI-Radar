#!/usr/bin/env python3
"""One-off migration: stamp/refresh `company_id` on every
``content/frontier_board.json`` row from the ``content/companies/*.json``
registry (Phase 6 stage 3).

``schemas/frontier_board.schema.json`` (Phase 6 stage 1) already made
``company_id`` a required field, and a placeholder value was hand-stamped
onto every row at that point -- before the real company registry existed
(Phase 6 stage 2 seeded ``content/companies/*.json`` afterwards). Some of
those placeholders do not actually match any real company id (e.g. a row's
``company_id`` of ``"meta"`` when the registry's actual slug is
``"meta-ai"``, or ``"bytedance"`` vs. the registry's ``"bytedance-seed"``).
This script is the one-time reconciliation: for every row in
``content/frontier_board.json`` it re-derives ``company_id`` from scratch by
matching the row's ``lab`` string against every company's ``name`` and
``aliases[]`` (case-insensitive, exact match against the whole ``lab``
string -- never a substring/fuzzy match, so it can't silently misattribute
a row), and overwrites whatever ``company_id`` was already there with the
resolved value.

**Hard-fail behavior (deliberate, per this migration's own build brief):**
if any row's ``lab`` does not resolve to *exactly one* company, this script
raises ``LabResolutionError`` and exits non-zero -- it never skips a row,
never leaves a stale/guessed ``company_id`` in place, and never silently
picks one candidate out of an ambiguous multi-match. A row that can't
resolve means either the registry is missing that company (add it, sourced
per stage 2's discipline) or two companies' name/aliases collide on the
same string (fix the registry, not this script). Run as::

    python scripts/migrate_frontier_board_company_ids.py

It rewrites ``content/frontier_board.json`` in place (schema-validated
before the write) and prints a one-line summary of every row's
``lab -> company_id`` resolution.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from watcher.schema_validate import validate  # noqa: E402

FRONTIER_BOARD_PATH = REPO_ROOT / "content" / "frontier_board.json"
COMPANIES_DIR = REPO_ROOT / "content" / "companies"


class LabResolutionError(RuntimeError):
    """Raised when a frontier_board.json row's `lab` does not resolve to
    exactly one company in the registry (zero or more than one match)."""


def load_company_lookup(companies_dir: Path = COMPANIES_DIR) -> dict[str, str]:
    """Build a case-insensitive {name_or_alias -> company id} lookup from
    every content/companies/<id>.json file (index.json is skipped -- it's a
    derived summary, not a per-company profile). Raises LabResolutionError
    if two companies' name/aliases collide on the same lookup key, since
    that would make resolution ambiguous for any lab string matching it.
    """
    lookup: dict[str, str] = {}
    collisions: dict[str, set[str]] = {}
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        with path.open("r", encoding="utf-8") as f:
            company = json.load(f)
        company_id = company["id"]
        keys = {company["name"], *company.get("aliases", [])}
        for key in keys:
            normalized = key.strip().lower()
            if not normalized:
                continue
            if normalized in lookup and lookup[normalized] != company_id:
                collisions.setdefault(normalized, {lookup[normalized]}).add(company_id)
            lookup[normalized] = company_id
    if collisions:
        details = "; ".join(
            f"{key!r} -> {sorted(ids)}" for key, ids in sorted(collisions.items())
        )
        raise LabResolutionError(
            f"Company registry has ambiguous name/alias collisions, fix "
            f"content/companies/*.json before running this migration: {details}"
        )
    return lookup


def resolve_company_id(lab: str, lookup: dict[str, str]) -> str:
    """Resolve a frontier_board.json row's `lab` string to exactly one
    company id via case-insensitive exact match against the lookup built
    by load_company_lookup(). Raises LabResolutionError if there is no
    match at all."""
    normalized = lab.strip().lower()
    company_id = lookup.get(normalized)
    if company_id is None:
        raise LabResolutionError(
            f"lab {lab!r} does not resolve to exactly one company in "
            f"content/companies/*.json (no name/alias match). Either add "
            f"this company to the registry (sourced per stage 2's "
            f"discipline) or fix the row's `lab` string -- this migration "
            f"refuses to guess."
        )
    return company_id


def migrate(
    frontier_board_path: Path = FRONTIER_BOARD_PATH,
    companies_dir: Path = COMPANIES_DIR,
) -> list[dict]:
    """Load frontier_board.json, stamp a freshly-resolved `company_id` onto
    every row, validate the result against schemas/frontier_board.schema.json,
    write it back, and return the updated list of rows. Raises
    LabResolutionError (propagated from resolve_company_id /
    load_company_lookup) if any row can't be resolved -- nothing is written
    in that case."""
    with frontier_board_path.open("r", encoding="utf-8") as f:
        board = json.load(f)

    lookup = load_company_lookup(companies_dir)

    resolutions: list[tuple[str, str, str | None]] = []
    for row in board:
        new_company_id = resolve_company_id(row["lab"], lookup)
        old_company_id = row.get("company_id")
        row["company_id"] = new_company_id
        resolutions.append((row["lab"], new_company_id, old_company_id))

    validate(board, "frontier_board")

    with frontier_board_path.open("w", encoding="utf-8") as f:
        json.dump(board, f, indent=2, ensure_ascii=False)
        f.write("\n")

    for lab, new_id, old_id in resolutions:
        marker = "unchanged" if old_id == new_id else f"CHANGED from {old_id!r}"
        print(f"  {lab!r} -> company_id={new_id!r} ({marker})")

    return board


def main() -> int:
    try:
        board = migrate()
    except LabResolutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Migrated {len(board)} content/frontier_board.json row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
