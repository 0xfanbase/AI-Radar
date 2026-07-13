#!/usr/bin/env python3
"""Company index regenerator (``scripts/update_company_index.py``, Phase 8).

Mirrors ``scripts/update_card_index.py``'s own architecture exactly (same
glob-then-rebuild-from-scratch shape, same load/save/write three-function
layering): after the PROFILER step has written or updated a
``content/companies/<id>.json`` full profile, this script regenerates
``content/companies/index.json`` from scratch by globbing every
currently-existing ``content/companies/*.json`` file (excluding
``index.json`` itself) and extracting the flat summary-manifest subset of
fields ``schemas/company_index.schema.json`` defines: ``{id, name,
hq_country, hq_city, hq_lat, hq_lng, status}``. Full profile prose/
citations stay in the per-company file; the index exists so the map
homepage's marker list (``site/builders/map.py``) and the ``/companies/``
listing page never need to open every full profile just to plot/list
companies.

A company profile removed from disk (shouldn't happen in the real
pipeline -- no writer in this codebase deletes a ``content/companies/
<id>.json`` file -- but handled the same defensive way
``update_card_index.py`` handles a dropped card) is simply absent from the
regenerated index, with no special-case code: :func:`iter_company_paths`
only ever globs *currently-existing* files.

This module is called directly by ``scripts/reconcile_run.py`` (never
re-implemented there) for the "regenerate content/companies/index.json"
half of its own post-run reconciliation, alongside its pre-existing
``content/cards/index.json`` regeneration.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Allow running as `python scripts/update_company_index.py` (no package
# install / no `-m` needed) -- same sys.path trick every other script in
# this repo uses (scripts/plan_run.py, scripts/update_card_index.py, ...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from watcher.config import REPO_ROOT  # noqa: E402
from watcher.schema_validate import validate  # noqa: E402

logger = logging.getLogger(__name__)

__all__ = [
    "COMPANIES_DIR",
    "COMPANY_INDEX_PATH",
    "COMPANY_INDEX_VERSION",
    "INDEX_FIELDS",
    "iter_company_paths",
    "build_company_index",
    "load_company_index",
    "save_company_index",
    "write_company_index",
    "main",
]

COMPANIES_DIR = REPO_ROOT / "content" / "companies"
COMPANY_INDEX_PATH = COMPANIES_DIR / "index.json"
COMPANY_INDEX_VERSION = 1

# The subset of schemas/company.schema.json's fields the summary index
# carries -- everything the map homepage's marker list and the
# /companies/ index page need to plot/list a company, deliberately
# excluding the full profile.* prose/citations that stay in the per-
# company file.
INDEX_FIELDS = ("id", "name", "hq_country", "hq_city", "hq_lat", "hq_lng", "status")


# --------------------------------------------------------------------------
# glob content/companies/*.json -- the sole source of truth for "which
# company profiles currently exist" (never a diff against a previous
# index, matching update_card_index.py's own "no separately-tracked
# dropped list" convention).
# --------------------------------------------------------------------------


def iter_company_paths(companies_dir: Path | str = COMPANIES_DIR) -> list[Path]:
    """Every ``content/companies/<id>.json`` file currently on disk under
    ``companies_dir``, sorted by filename for a deterministic read order
    (final index ordering is re-sorted by id in :func:`build_company_index`
    regardless).

    Excludes ``index.json`` itself -- that file is this module's own
    *output*, never one of its own inputs.

    Returns an empty list if ``companies_dir`` doesn't exist yet at all,
    or exists but is empty -- neither is an error, matching every sibling
    "load every X" loader's own graceful-missing-directory convention.
    """
    companies_dir = Path(companies_dir)
    if not companies_dir.is_dir():
        return []
    return sorted(p for p in companies_dir.glob("*.json") if p.name != "index.json")


def _index_entry(company: dict[str, Any]) -> dict[str, Any]:
    return {field: company[field] for field in INDEX_FIELDS}


def build_company_index(companies_dir: Path | str = COMPANIES_DIR) -> dict[str, Any]:
    """Regenerate the ``company_index.schema.json``-shaped payload from
    scratch: every currently-existing ``content/companies/<id>.json``
    under ``companies_dir``, each schema-validated against
    ``company.schema.json`` (a malformed on-disk profile is a real, loud
    bug -- this deliberately raises rather than silently skipping it,
    matching ``update_card_index.py::build_card_index``'s own "validate
    before it's ever trusted" convention) and reduced to
    :data:`INDEX_FIELDS`.

    Entries are sorted alphabetically by ``id`` -- a simple, deterministic
    order (unlike the card index's most-recent-first convention, a
    company registry has no natural recency axis of its own to sort by).

    Does not itself validate or write the *returned* payload -- see
    :func:`save_company_index`/:func:`write_company_index` for that.
    """
    companies = []
    for path in iter_company_paths(companies_dir):
        with path.open("r", encoding="utf-8") as f:
            company = json.load(f)
        validate(company, "company")
        companies.append(_index_entry(company))

    companies.sort(key=lambda c: c["id"])
    return {"version": COMPANY_INDEX_VERSION, "companies": companies}


# --------------------------------------------------------------------------
# load / save -- schema-valid round trip (same pattern as
# scripts/update_card_index.py's load_card_index/save_card_index).
# --------------------------------------------------------------------------


def load_company_index(path: Path | str = COMPANY_INDEX_PATH) -> dict[str, Any]:
    """Load and schema-validate ``content/companies/index.json`` at
    ``path``.

    A missing file returns the empty-index shape (``{"version": 1,
    "companies": []}``) -- matches every sibling "not generated yet is
    not an error" loader convention for a same-shaped committed artifact.
    """
    path = Path(path)
    if not path.is_file():
        return {"version": COMPANY_INDEX_VERSION, "companies": []}
    with path.open("r", encoding="utf-8") as f:
        index = json.load(f)
    validate(index, "company_index")
    return index


def save_company_index(
    index: dict[str, Any], path: Path | str = COMPANY_INDEX_PATH
) -> None:
    """Schema-validate then write ``index`` to ``path`` as pretty,
    deterministically-ordered JSON (indent=2, sorted keys, trailing
    newline) -- same committed-artifact formatting every other writer in
    this pipeline uses. Validating *before* writing means a malformed
    index is never persisted. Creates ``path``'s parent directory
    (``content/companies/``) if it doesn't exist yet.
    """
    validate(index, "company_index")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, sort_keys=True)
        f.write("\n")


def write_company_index(
    companies_dir: Path | str = COMPANIES_DIR, path: Path | str = COMPANY_INDEX_PATH
) -> dict[str, Any]:
    """Compose :func:`build_company_index` + :func:`save_company_index`:
    the single call ``scripts/reconcile_run.py`` (and this module's own
    :func:`main`) makes to regenerate, validate, and persist
    ``content/companies/index.json`` for one run. Returns the written
    payload.
    """
    index = build_company_index(companies_dir)
    save_company_index(index, path)
    return index


# --------------------------------------------------------------------------
# CLI entrypoint
# --------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    index = write_company_index()
    print(f"content/companies/index.json written: {len(index['companies'])} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
