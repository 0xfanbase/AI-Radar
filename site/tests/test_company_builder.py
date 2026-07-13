"""Tests for site/builders/company.py + site/templates/{company,
company_index}.html -- the per-company profile pages and the `/companies/`
index (Phase 8, world-map UI reshape).

Loaded by explicit file path, matching every other `site/tests/*.py` file's
own convention (`site/` is deliberately not an importable package -- see
IMPROVEMENT_BACKLOG.md).

Exercises the real, committed `content/companies/*.json` (13 seeded full
profiles, per Phase 6) and `content/frontier_board.json` for the
board-row/render assertions, plus small synthetic inputs for the pure
view-model unit tests, matching `site/tests/test_map_builder.py`'s own
"real content + synthetic fixtures" split.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPANY_PATH = REPO_ROOT / "site" / "builders" / "company.py"
COMPANIES_DIR = REPO_ROOT / "content" / "companies"
FRONTIER_BOARD_PATH = REPO_ROOT / "content" / "frontier_board.json"


def _load_company_module():
    spec = importlib.util.spec_from_file_location(
        "frontier_wire_site_builders_company", COMPANY_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


company_builder = _load_company_module()


def _load_real_companies() -> list[dict]:
    return company_builder.load_companies(COMPANIES_DIR)


def _load_real_board_rows() -> list[dict]:
    with FRONTIER_BOARD_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


REAL_COMPANIES = _load_real_companies()
REAL_BOARD_ROWS = _load_real_board_rows()


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def test_source_host_strips_www():
    assert company_builder.source_host("https://www.anthropic.com/news/a") == "anthropic.com"


def test_source_host_no_www_unchanged():
    assert company_builder.source_host("https://reuters.com/article") == "reuters.com"


def test_source_host_falls_back_to_full_url_when_unparseable():
    assert company_builder.source_host("not-a-url") == "not-a-url"


def test_format_context_window_none_is_not_disclosed():
    assert company_builder.format_context_window(None) == "not disclosed"


def test_format_context_window_formats_with_commas():
    assert company_builder.format_context_window(1000000) == "1,000,000"


def test_format_modality_joins_with_comma():
    assert company_builder.format_modality(["text", "image"]) == "text, image"


def test_official_site_url_uses_first_domain():
    assert company_builder.official_site_url(["anthropic.com", "claude.ai"]) == (
        "https://anthropic.com"
    )


def test_official_site_url_empty_list_returns_empty_string():
    assert company_builder.official_site_url([]) == ""


# ---------------------------------------------------------------------------
# build_cited_text_view
# ---------------------------------------------------------------------------


def test_build_cited_text_view():
    raw = {
        "text": "Some prose.",
        "citations": [{"url": "https://anthropic.com/x", "outlet": "Anthropic", "quote": "q"}],
    }
    view = company_builder.build_cited_text_view(raw)
    assert view.text == "Some prose."
    assert len(view.citations) == 1
    assert view.citations[0].url == "https://anthropic.com/x"


def test_build_cited_text_view_no_citations_key_defaults_to_empty():
    view = company_builder.build_cited_text_view({"text": "x"})
    assert view.citations == ()


# ---------------------------------------------------------------------------
# board_rows_for_company -- against real Board data
# ---------------------------------------------------------------------------


def test_board_rows_for_company_finds_the_real_anthropic_row():
    rows = company_builder.board_rows_for_company("anthropic", REAL_BOARD_ROWS)
    assert len(rows) == 1
    assert rows[0].model == "Claude Fable 5"
    assert rows[0].context_window_display == "1,000,000"


def test_board_rows_for_company_empty_for_unknown_company():
    assert company_builder.board_rows_for_company("no-such-company", REAL_BOARD_ROWS) == []


def test_board_rows_for_company_sorts_newest_release_first():
    rows = [
        {"company_id": "x", "release_date": "2025-01-01", "model": "old"},
        {"company_id": "x", "release_date": "2026-06-01", "model": "new"},
        {"company_id": "x", "release_date": "2025-12-01", "model": "mid"},
    ]
    ordered = company_builder.board_rows_for_company("x", rows)
    assert [r.model for r in ordered] == ["new", "mid", "old"]


# ---------------------------------------------------------------------------
# cards_for_company -- no cap, unlike map.py's popover version
# ---------------------------------------------------------------------------


def test_cards_for_company_empty_when_no_cards():
    assert company_builder.cards_for_company("anthropic", []) == []


def test_cards_for_company_filters_and_sorts_newest_first():
    cards = [
        {
            "id": "card-1",
            "date": "2026-06-01",
            "generated_at": "2026-06-01T00:00:00Z",
            "headline": "Old story",
            "status": "confirmed",
            "companies": ["anthropic"],
        },
        {
            "id": "card-2",
            "date": "2026-07-01",
            "generated_at": "2026-07-01T00:00:00Z",
            "headline": "New story",
            "status": "reported",
            "companies": ["anthropic", "openai"],
        },
        {
            "id": "card-3",
            "date": "2026-07-05",
            "generated_at": "2026-07-05T00:00:00Z",
            "headline": "Unrelated",
            "status": "confirmed",
            "companies": ["openai"],
        },
    ]
    views = company_builder.cards_for_company("anthropic", cards)
    assert [v.id for v in views] == ["card-2", "card-1"]
    assert views[0].href == "/wire/2026-07/#card-card-2"
    assert views[0].status_label == "REPORTED"


def test_cards_for_company_returns_every_match_no_cap():
    cards = [
        {
            "id": f"card-{i}",
            "date": f"2026-07-{i:02d}",
            "generated_at": f"2026-07-{i:02d}T00:00:00Z",
            "headline": f"Story {i}",
            "status": "confirmed",
            "companies": ["anthropic"],
        }
        for i in range(1, 6)
    ]
    views = company_builder.cards_for_company("anthropic", cards)
    assert len(views) == 5


# ---------------------------------------------------------------------------
# build_company_view / build_company_context -- against real seeded content
# ---------------------------------------------------------------------------


def _real_anthropic() -> dict:
    return company_builder.load_company("anthropic", COMPANIES_DIR)


def test_build_company_view_against_real_anthropic_profile():
    view = company_builder.build_company_view(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert view.id == "anthropic"
    assert view.name == "Anthropic"
    assert view.hq_city == "San Francisco"
    assert view.official_site_url == "https://anthropic.com"
    assert view.status_label == "CONFIRMED"
    assert view.status_chip_class == "chip chip--confirmed"
    assert len(view.what_theyve_done) == 3
    assert view.roadmap == ()
    assert len(view.board_rows) == 1
    assert view.cards == ()


def test_build_company_context_shape():
    context = company_builder.build_company_context(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert "company" in context
    assert context["empty_wire_history_message"] == company_builder.EMPTY_WIRE_HISTORY_MESSAGE
    assert "what the company says" in context["roadmap_heading"].lower()


def test_build_company_view_reported_status_maps_to_reported_chip():
    raw = dict(_real_anthropic())
    raw["status"] = "reported"
    view = company_builder.build_company_view(raw, REAL_BOARD_ROWS, [])
    assert view.status_chip_class == "chip chip--reported"


# ---------------------------------------------------------------------------
# sorted_companies / build_index_context
# ---------------------------------------------------------------------------


def test_sorted_companies_alphabetical_by_name():
    ordered = company_builder.sorted_companies(REAL_COMPANIES)
    names = [c["name"] for c in ordered]
    assert names == sorted(names, key=str.lower)


def test_build_index_context_against_real_companies():
    context = company_builder.build_index_context(REAL_COMPANIES)
    assert context["total_companies"] == 13
    assert len(context["companies"]) == 13
    anthropic_row = next(r for r in context["companies"] if r.id == "anthropic")
    assert anthropic_row.href == "/companies/anthropic/"


def test_build_index_context_empty_companies():
    context = company_builder.build_index_context([])
    assert context["total_companies"] == 0
    assert context["companies"] == []


# ---------------------------------------------------------------------------
# load_companies / load_company
# ---------------------------------------------------------------------------


def test_load_companies_returns_thirteen_real_profiles_excluding_index():
    companies = company_builder.load_companies(COMPANIES_DIR)
    assert len(companies) == 13
    assert all(c["id"] != "index" for c in companies)


def test_load_companies_missing_dir_returns_empty_list(tmp_path):
    assert company_builder.load_companies(tmp_path / "does-not-exist") == []


def test_load_company_loads_by_slug():
    company = company_builder.load_company("deepseek", COMPANIES_DIR)
    assert company["id"] == "deepseek"
    assert company["name"] == "DeepSeek"


# ---------------------------------------------------------------------------
# render_company_page / render_companies_index -- real templates, end to end
# ---------------------------------------------------------------------------


def test_render_company_page_against_the_real_template_does_not_crash():
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert "<h1>Anthropic</h1>" in html
    assert "San Francisco" in html
    assert 'href="https://anthropic.com"' in html
    assert "CONFIRMED" in html


def test_render_company_page_shows_empty_wire_history_message():
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert company_builder.EMPTY_WIRE_HISTORY_MESSAGE in html


def test_render_company_page_shows_board_row():
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert "Claude Fable 5" in html


def test_render_company_page_shows_roadmap_note_for_empty_roadmap():
    # anthropic.json's real roadmap[] is empty -- the honest "no roadmap
    # on record" copy must render, not a crash or a blank section.
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert "No publicly stated roadmap on record yet." in html


def test_render_company_page_includes_disclaimer_meta_line():
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert "AI-generated" in html
    assert "model: claude-sonnet-5" in html
    assert 'href="/corrections/"' in html


def test_render_company_page_links_back_to_companies_index():
    html = company_builder.render_company_page(_real_anthropic(), REAL_BOARD_ROWS, [])
    assert 'href="/companies/">' in html


def test_render_companies_index_against_real_companies():
    html = company_builder.render_companies_index(REAL_COMPANIES)
    assert "<h1>Companies</h1>" in html
    assert 'href="/companies/anthropic/">Anthropic</a>' in html
    assert "13" in html or "companies" in html.lower()


def test_render_companies_index_empty_state():
    html = company_builder.render_companies_index([])
    assert company_builder.EMPTY_COMPANIES_MESSAGE in html


# ---------------------------------------------------------------------------
# write_company_pages -- real end-to-end write
# ---------------------------------------------------------------------------


def test_write_company_pages_writes_index_and_every_profile(tmp_path):
    env = company_builder.build_jinja_env()
    written = company_builder.write_company_pages(
        env, REAL_COMPANIES, REAL_BOARD_ROWS, [], tmp_path
    )
    assert (tmp_path / "companies" / "index.html").is_file()
    assert (tmp_path / "companies" / "anthropic" / "index.html").is_file()
    assert (tmp_path / "companies" / "deepseek" / "index.html").is_file()
    # index.html + 13 profile pages
    assert len(written) == 14


def test_write_company_pages_handles_zero_companies(tmp_path):
    env = company_builder.build_jinja_env()
    written = company_builder.write_company_pages(env, [], REAL_BOARD_ROWS, [], tmp_path)
    assert len(written) == 1
    html = (tmp_path / "companies" / "index.html").read_text(encoding="utf-8")
    assert company_builder.EMPTY_COMPANIES_MESSAGE in html
