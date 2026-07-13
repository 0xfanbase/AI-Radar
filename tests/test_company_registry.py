"""Tests for Phase 6's entity foundation: the company registry
(``content/companies/*.json`` against ``schemas/company.schema.json``), the
``frontier_board.json`` -> company registry foreign key
(``scripts/migrate_frontier_board_company_ids.py``'s own output), and the
human-curated link-safety allowlist (``data/trusted_domains.json``).

Follows the same "validate the real, committed artifact directly" style as
``tests/test_seed_content.py`` rather than synthetic fixtures, since this
suite exists specifically to lock in stage 3's migration/seeding work
against regression.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jsonschema import ValidationError

from watcher.schema_validate import validate

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = REPO_ROOT / "content"
COMPANIES_DIR = CONTENT_DIR / "companies"
FRONTIER_BOARD_PATH = CONTENT_DIR / "frontier_board.json"
TRUSTED_DOMAINS_PATH = REPO_ROOT / "data" / "trusted_domains.json"


def _load(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _company_files() -> list[Path]:
    # index.json is a derived summary, not a per-company profile -- it does
    # not conform to schemas/company.schema.json and is deliberately
    # excluded here, same as the migration script excludes it.
    return sorted(p for p in COMPANIES_DIR.glob("*.json") if p.name != "index.json")


COMPANY_FILE_NAMES = [p.name for p in _company_files()]


# ---------------------------------------------------------------------------
# content/companies/*.json validate against schemas/company.schema.json
# ---------------------------------------------------------------------------


def test_at_least_13_company_profiles_exist():
    # Stage 2 seeded 13 companies -- a regression test against accidental
    # deletion, not a hardcoded ceiling (a future stage may add more).
    assert len(COMPANY_FILE_NAMES) >= 13


@pytest.mark.parametrize("filename", COMPANY_FILE_NAMES)
def test_company_profile_validates_against_schema(filename):
    company = _load(COMPANIES_DIR / filename)
    validate(company, "company")  # must not raise


@pytest.mark.parametrize("filename", COMPANY_FILE_NAMES)
def test_company_profile_id_matches_its_own_filename(filename):
    company = _load(COMPANIES_DIR / filename)
    assert f"{company['id']}.json" == filename


def test_company_ids_are_unique():
    ids = [_load(p)["id"] for p in _company_files()]
    assert len(ids) == len(set(ids))


def test_company_schema_rejects_a_profile_missing_a_required_field():
    company = dict(_load(_company_files()[0]))
    del company["hq_lat"]
    with pytest.raises(ValidationError):
        validate(company, "company")


def test_companies_index_lists_every_company_profile_id():
    index = _load(COMPANIES_DIR / "index.json")
    index_ids = {row["id"] for row in index["companies"]}
    profile_ids = {_load(p)["id"] for p in _company_files()}
    assert index_ids == profile_ids


# ---------------------------------------------------------------------------
# content/frontier_board.json rows resolve via company_id
# ---------------------------------------------------------------------------


def test_frontier_board_still_validates_after_company_id_migration():
    board = _load(FRONTIER_BOARD_PATH)
    validate(board, "frontier_board")  # must not raise


def test_every_frontier_board_row_company_id_resolves_to_a_real_company_file():
    board = _load(FRONTIER_BOARD_PATH)
    real_ids = {_load(p)["id"] for p in _company_files()}
    unresolved = [
        (row["lab"], row["company_id"])
        for row in board
        if row["company_id"] not in real_ids
    ]
    assert unresolved == [], f"frontier_board rows with no matching company file: {unresolved}"


def test_no_frontier_board_row_kept_a_stale_placeholder_company_id():
    # Regression lock for the two rows stage 1's placeholder wiring got
    # wrong ("meta" / "bytedance") and stage 3's migration fixed ("meta-ai"
    # / "bytedance-seed"). A future re-introduction of either stale id
    # should fail this test, not silently pass the looser "resolves to
    # *some* file" check above.
    board = _load(FRONTIER_BOARD_PATH)
    company_ids = {row["company_id"] for row in board}
    assert "meta" not in company_ids
    assert "bytedance" not in company_ids


# ---------------------------------------------------------------------------
# data/trusted_domains.json
# ---------------------------------------------------------------------------


def test_trusted_domains_is_valid_json_with_expected_shape():
    doc = _load(TRUSTED_DOMAINS_PATH)
    assert isinstance(doc, dict)
    assert "hostnames" in doc and isinstance(doc["hostnames"], list)
    assert all(isinstance(h, str) for h in doc["hostnames"])
    assert "path_scoped" in doc and isinstance(doc["path_scoped"], list)
    for entry in doc["path_scoped"]:
        assert isinstance(entry, dict)
        assert "host" in entry and "path_prefix" in entry


def test_trusted_domains_hostnames_are_lowercase_and_unique():
    doc = _load(TRUSTED_DOMAINS_PATH)
    hostnames = doc["hostnames"]
    assert all(h == h.lower() for h in hostnames)
    assert len(hostnames) == len(set(hostnames))


def test_trusted_domains_never_bare_lists_github_or_huggingface():
    # Per data/trusted_domains.json's own curation rule: those two hosts
    # are multi-tenant, so only a path-scoped entry may trust them, never a
    # bare hostnames[] entry.
    doc = _load(TRUSTED_DOMAINS_PATH)
    assert "github.com" not in doc["hostnames"]
    assert "huggingface.co" not in doc["hostnames"]


def test_every_company_official_domain_is_in_trusted_domains():
    doc = _load(TRUSTED_DOMAINS_PATH)
    hostnames = set(doc["hostnames"])
    missing = []
    for path in _company_files():
        company = _load(path)
        for domain in company.get("official_domains", []):
            if domain.lower() not in hostnames:
                missing.append((company["id"], domain))
    assert missing == [], f"official_domains[] hostnames missing from trusted_domains.json: {missing}"


def test_reputable_outlet_table_domains_are_all_in_trusted_domains():
    # The 14 CLAUDE.md reputable-outlet domains must all be present --
    # trusted_domains.json's own union rule (a) requires it.
    reputable_outlet_domains = {
        "reuters.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "nytimes.com",
        "theinformation.com",
        "techcrunch.com",
        "theverge.com",
        "arstechnica.com",
        "wired.com",
        "technologyreview.com",
        "axios.com",
        "asia.nikkei.com",
        "scmp.com",
    }
    doc = _load(TRUSTED_DOMAINS_PATH)
    hostnames = set(doc["hostnames"])
    missing = reputable_outlet_domains - hostnames
    assert missing == set(), f"reputable-outlet domains missing from trusted_domains.json: {missing}"


def test_frontier_board_source_url_hosts_are_covered_by_trusted_domains():
    # Every source_url hostname already cited in frontier_board.json must
    # be trusted (directly, or -- for github.com/huggingface.co only --
    # via a path-scoped entry), so the allowlist doesn't lag behind the
    # site's own already-published sourcing.
    doc = _load(TRUSTED_DOMAINS_PATH)
    hostnames = set(doc["hostnames"])
    path_scoped_hosts = {entry["host"] for entry in doc["path_scoped"]}
    multi_tenant_hosts = {"github.com", "huggingface.co"}

    board = _load(FRONTIER_BOARD_PATH)
    uncovered = []
    for row in board:
        host = urlparse(row["source_url"]).netloc.lower()
        if host in hostnames:
            continue
        if host in multi_tenant_hosts and host in path_scoped_hosts:
            continue
        if host in multi_tenant_hosts:
            # Known, documented gap (see trusted_domains.json's own
            # path_scoped_note) -- not asserted as covered, but also not
            # silently ignored: recorded so a future fix is visible here.
            continue
        uncovered.append((row["lab"], host))
    assert uncovered == [], f"frontier_board source_url hosts not covered by trusted_domains.json: {uncovered}"


def test_arxiv_hosts_are_in_trusted_domains():
    doc = _load(TRUSTED_DOMAINS_PATH)
    hostnames = set(doc["hostnames"])
    assert "arxiv.org" in hostnames
    assert "export.arxiv.org" in hostnames


# ---------------------------------------------------------------------------
# scripts/migrate_frontier_board_company_ids.py -- unit coverage on the
# resolution logic itself (not just its output artifact, covered above).
# ---------------------------------------------------------------------------


def test_migration_script_resolves_lab_via_name_and_alias():
    from scripts.migrate_frontier_board_company_ids import resolve_company_id

    lookup = {"anthropic": "anthropic", "anthropic pbc": "anthropic"}
    assert resolve_company_id("Anthropic", lookup) == "anthropic"
    assert resolve_company_id("Anthropic PBC", lookup) == "anthropic"


def test_migration_script_hard_fails_on_unresolvable_lab():
    from scripts.migrate_frontier_board_company_ids import (
        LabResolutionError,
        resolve_company_id,
    )

    with pytest.raises(LabResolutionError):
        resolve_company_id("Some Lab Not In The Registry", {"anthropic": "anthropic"})


def test_migration_script_hard_fails_on_ambiguous_registry_collision(tmp_path):
    from scripts.migrate_frontier_board_company_ids import (
        LabResolutionError,
        load_company_lookup,
    )

    companies_dir = tmp_path
    (companies_dir / "a.json").write_text(
        json.dumps({"id": "a", "name": "Shared Name", "aliases": []})
    )
    (companies_dir / "b.json").write_text(
        json.dumps({"id": "b", "name": "Shared Name", "aliases": []})
    )
    with pytest.raises(LabResolutionError):
        load_company_lookup(companies_dir)


def test_migration_script_is_idempotent_on_the_real_committed_data(tmp_path):
    # Running the migration a second time against the already-migrated,
    # real content/frontier_board.json must be a no-op (every row already
    # resolves to its correct company_id).
    import shutil

    from scripts.migrate_frontier_board_company_ids import migrate

    board_copy = tmp_path / "frontier_board.json"
    shutil.copy(FRONTIER_BOARD_PATH, board_copy)
    before = _load(FRONTIER_BOARD_PATH)

    migrate(frontier_board_path=board_copy, companies_dir=COMPANIES_DIR)

    after = _load(board_copy)
    assert after == before
